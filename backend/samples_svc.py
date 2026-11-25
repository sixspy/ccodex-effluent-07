"""采样批次入库与超标裁定。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from metric_lineage_svc import EDGE_FORMAL, EDGE_PARENT_CHILD, lineage_complete
from models import SampleIn
from validators import (
    adjudicate_exceed,
    metric_summary,
    validate_flow_cap,
    validate_metrics_nonneg,
    validate_outfall_open,
    validate_plant_running,
)

router = APIRouter(tags=["samples"])


def _next_sample_code(conn, outfall_id: int) -> str:
    outfall = conn.execute(
        "SELECT outfall_code FROM outfalls WHERE id=?", (outfall_id,)
    ).fetchone()
    prefix = outfall["outfall_code"] if outfall else f"OF-{outfall_id}"
    n = conn.execute(
        "SELECT COUNT(*) c FROM sample_batches WHERE outfall_id=?", (outfall_id,)
    ).fetchone()["c"]
    return f"SM-{prefix}-{n + 1:03d}"


def _resolve_parent(conn, body: SampleIn):
    """续采父批：显式指定须存在且同口；缺省自动衔接同排放口最新批；首批为根。"""
    if body.parent_sample_id is not None:
        parent = conn.execute(
            "SELECT * FROM sample_batches WHERE id=?", (body.parent_sample_id,)
        ).fetchone()
        if not parent:
            raise HTTPException(404, "父批不存在，入库拒收")
        if parent["outfall_id"] != body.outfall_id:
            raise HTTPException(400, "父批与续采排放口不一致，入库拒收")
        return parent
    return conn.execute(
        "SELECT * FROM sample_batches WHERE outfall_id=? ORDER BY id DESC LIMIT 1",
        (body.outfall_id,),
    ).fetchone()


@router.get("/api/samples")
def list_samples(authorization: AuthHeader = None, status: str | None = None):
    require_user(authorization)
    with connect() as conn:
        q = """SELECT s.*, o.outfall_code, p.plant_code FROM sample_batches s
               JOIN outfalls o ON o.id=s.outfall_id
               JOIN plants p ON p.id=o.plant_id"""
        if status:
            rows = conn.execute(
                q + " WHERE s.status=? ORDER BY s.id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(q + " ORDER BY s.id DESC").fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(d["metrics"] or "{}")
            d["lineage_ok"] = lineage_complete(conn, r)
            items.append(d)
        return {"items": items}


@router.get("/api/samples/{sample_id}")
def get_sample(sample_id: int, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT s.*, o.outfall_code, o.cod_limit, o.nh3n_limit, o.tp_limit,
                      p.name plant_name
               FROM sample_batches s
               JOIN outfalls o ON o.id=s.outfall_id
               JOIN plants p ON p.id=o.plant_id
               WHERE s.id=?""",
            (sample_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "采样批次不存在")
        data = dict(row)
        data["metrics"] = json.loads(data["metrics"] or "{}")
        holds = conn.execute(
            "SELECT * FROM exceed_holds WHERE sample_id=? ORDER BY id DESC",
            (sample_id,),
        ).fetchall()
        seals = conn.execute(
            "SELECT * FROM discharge_seals WHERE sample_id=? ORDER BY id DESC",
            (sample_id,),
        ).fetchall()
        releases = conn.execute(
            "SELECT * FROM discharge_releases WHERE sample_id=? ORDER BY id DESC",
            (sample_id,),
        ).fetchall()
        parent_edge = conn.execute(
            """SELECT e.*, f.sample_code from_code FROM metric_lineage e
               JOIN sample_batches f ON f.id=e.from_sample_id
               WHERE e.to_sample_id=? AND e.edge_type=? AND e.status=?""",
            (sample_id, EDGE_PARENT_CHILD, EDGE_FORMAL),
        ).fetchone()
        return {
            "sample": data,
            "exceed_holds": [dict(h) for h in holds],
            "discharge_seals": [dict(s) for s in seals],
            "discharge_releases": [dict(r) for r in releases],
            "parent_edge": dict(parent_edge) if parent_edge else None,
        }


@router.post("/api/samples")
def create_sample(body: SampleIn, authorization: AuthHeader = None):
    user = require_user(authorization)  # 令牌解析无库操作，先定持锁人
    lock_key = f"outfall:{body.outfall_id}"
    with connect() as conn:
        # 抢锁必须是本请求第一个库操作：前置查询若被 busy 拖过持锁窗口，
        # 锁行已删，即成无锁双成。
        require_lock(conn, lock_key, user)
        parent_lock = None
        try:
            require_writer(authorization)  # 角色校验（只读账号禁止写入）
            outfall = conn.execute(
                """SELECT o.*, p.status plant_status, p.design_cap_m3d
                   FROM outfalls o JOIN plants p ON p.id=o.plant_id WHERE o.id=?""",
                (body.outfall_id,),
            ).fetchone()
            if not outfall:
                raise HTTPException(404, "排放口不存在")
            validate_outfall_open(outfall["status"])
            validate_plant_running(outfall["plant_status"])
            metrics = body.metrics.model_dump()
            validate_metrics_nonneg(metrics)
            validate_flow_cap(body.flow_m3h, float(outfall["design_cap_m3d"]))
            permit = conn.execute(
                "SELECT * FROM discharge_permits WHERE permit_code=?",
                (body.permit_ref,),
            ).fetchone()
            if not permit:
                raise HTTPException(400, "排放许可不存在")
            if permit["status"] != "有效":
                raise HTTPException(400, "排放许可未生效")
            exceeded, metric, measured, limit = adjudicate_exceed(
                metrics,
                {
                    "cod": outfall["cod_limit"],
                    "nh3n": outfall["nh3n_limit"],
                    "tp": outfall["tp_limit"],
                },
            )
            code = body.sample_code or _next_sample_code(conn, body.outfall_id)
            if conn.execute(
                "SELECT id FROM sample_batches WHERE sample_code=?", (code,)
            ).fetchone():
                raise HTTPException(400, "采样编号已存在")
            parent = _resolve_parent(conn, body)
            if parent is not None:
                parent_lock = f"sample:{parent['id']}"
                require_lock(conn, parent_lock, user)
            status = "旁路挂起" if exceeded else "已裁定"
            adjudicated = None if exceeded else now_iso()
            cur = conn.execute(
                """INSERT INTO sample_batches(sample_code,outfall_id,permit_ref,metrics,
                   flow_m3h,generation,status,adjudicated_at,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    code,
                    body.outfall_id,
                    body.permit_ref,
                    json.dumps(metrics, ensure_ascii=False),
                    body.flow_m3h,
                    1,
                    status,
                    adjudicated,
                    now_iso(),
                ),
            )
            sid = cur.lastrowid
            edge_id = None
            if parent is not None:
                # 甲路径：采样批落库瞬间血缘父子边同事务写入，失败则整笔回退拒收。
                cur_edge = conn.execute(
                    """INSERT INTO metric_lineage(from_sample_id,to_sample_id,edge_type,
                       detail,status,created_at) VALUES (?,?,?,?,?,?)""",
                    (
                        parent["id"],
                        sid,
                        EDGE_PARENT_CHILD,
                        f"续采父子边 {parent['sample_code']}→{code}",
                        EDGE_FORMAL,
                        now_iso(),
                    ),
                )
                edge_id = cur_edge.lastrowid
            hold = None
            if exceeded:
                hold_key = f"EX-{code}-{metric}"
                conn.execute(
                    """INSERT INTO exceed_holds(sample_id,hold_key,exceed_metric,measured,
                       limit_value,status,opened_at,note)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        sid,
                        hold_key,
                        metric,
                        measured,
                        limit,
                        "开放",
                        now_iso(),
                        "入库自动挂起",
                    ),
                )
                hold = hold_key
            write_audit(
                conn,
                user,
                "sample_create",
                code,
                {
                    "status": status,
                    "metrics": metrics,
                    "summary": metric_summary(metrics),
                    "exceed": exceeded,
                    "parent_sample_id": parent["id"] if parent else None,
                    "lineage_edge_id": edge_id,
                },
            )
            conn.commit()
            return {
                "id": sid,
                "sample_code": code,
                "status": status,
                "exceed": exceeded,
                "hold_key": hold,
                "parent_sample_id": parent["id"] if parent else None,
                "lineage_edge_id": edge_id,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            if parent_lock:
                release(conn, parent_lock, user)
            release(conn, lock_key, user)
            conn.commit()
