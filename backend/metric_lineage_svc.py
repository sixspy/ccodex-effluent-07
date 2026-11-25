"""指标血缘边。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from models import LineageIn

router = APIRouter(tags=["lineage"])

EDGE_PARENT_CHILD = "parent_child"
EDGE_TYPES = (EDGE_PARENT_CHILD, "复测续批", "准排锚点")
EDGE_FORMAL = "正式"
EDGE_SHADOW = "影子"


def formal_parent_edge(conn, sample_id: int):
    """该批作为子边的正式父子边（血统依据）。"""
    return conn.execute(
        """SELECT * FROM metric_lineage
           WHERE to_sample_id=? AND edge_type=? AND status=?""",
        (sample_id, EDGE_PARENT_CHILD, EDGE_FORMAL),
    ).fetchone()


def is_root_sample(conn, sample) -> bool:
    """排放口首批（无更早同口批）为根批。"""
    earlier = conn.execute(
        "SELECT id FROM sample_batches WHERE outfall_id=? AND id<? LIMIT 1",
        (sample["outfall_id"], sample["id"]),
    ).fetchone()
    return earlier is None


def lineage_complete(conn, sample) -> bool:
    """根批或持正式父子边的批才算血统齐全；影子边不计。"""
    return is_root_sample(conn, sample) or formal_parent_edge(conn, sample["id"]) is not None


@router.get("/api/lineage")
def list_lineage(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.*, f.sample_code from_code, t.sample_code to_code
               FROM metric_lineage e
               JOIN sample_batches f ON f.id=e.from_sample_id
               LEFT JOIN sample_batches t ON t.id=e.to_sample_id
               WHERE e.status=?
               ORDER BY e.id DESC""",
            (EDGE_FORMAL,),
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/lineage")
def create_edge(body: LineageIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    if body.edge_type not in EDGE_TYPES:
        raise HTTPException(400, f"边类型非法：{body.edge_type}（枚举 {list(EDGE_TYPES)}）")
    with connect() as conn:
        fr = conn.execute(
            "SELECT * FROM sample_batches WHERE id=?", (body.from_sample_id,)
        ).fetchone()
        if not fr:
            raise HTTPException(404, "源采样不存在")
        if body.to_sample_id:
            to = conn.execute(
                "SELECT id FROM sample_batches WHERE id=?", (body.to_sample_id,)
            ).fetchone()
            if not to:
                raise HTTPException(404, "目标采样不存在")
            if body.edge_type == EDGE_PARENT_CHILD and formal_parent_edge(
                conn, body.to_sample_id
            ):
                raise HTTPException(409, "目标批已持正式父子边，禁止重复挂父")
        cur = conn.execute(
            """INSERT INTO metric_lineage(from_sample_id,to_sample_id,edge_type,detail,status,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                body.from_sample_id,
                body.to_sample_id,
                body.edge_type,
                body.detail,
                EDGE_FORMAL,
                now_iso(),
            ),
        )
        write_audit(
            conn,
            user,
            "lineage_edge",
            fr["sample_code"],
            body.model_dump(),
        )
        conn.commit()
        return {"id": cur.lastrowid, "status": EDGE_FORMAL, **body.model_dump()}
