"""effluent-07 甲路径红测：采样批落库瞬间血缘边同事务写入，否则整笔回退拒收。

钉住：正式批在册而血缘边行数为零（假绿夹具 SM-G41-写边）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from test_effluent_lineage_edge import (  # noqa: F401  (g41_db 为 pytest 夹具)
    EDGE_PARENT_CHILD,
    FIXTURE_KEY,
    FIXTURE_PARENT_CODE,
    FIXTURE_PERMIT,
    auth_headers,
    g41_db,
    sample_by_code,
)


def test_jia_reconcile_deducts_edgeless_batch(g41_db):
    """假绿检测：对账血统齐全计数必须扣掉无边批 SM-G41-写边。"""
    c = TestClient(app)
    h = auth_headers(c)
    data = c.get(f"/api/reconcile/{FIXTURE_PERMIT}", headers=h).json()
    assert FIXTURE_KEY in data["lineage_missing"]
    assert data["lineage_ok_count"] == data["lineage_total"] - 1


def test_jia_ingest_writes_parent_child_edge_same_tx(g41_db):
    """续采入库成功 ⇒ 同事务写入正式父子边，血缘页必须出现父子边编号。"""
    c = TestClient(app)
    h = auth_headers(c)
    parent = sample_by_code(c, h, FIXTURE_PARENT_CODE)
    r = c.post(
        "/api/samples",
        headers=h,
        json={
            "outfall_id": parent["outfall_id"],
            "permit_ref": FIXTURE_PERMIT,
            "sample_code": "SM-G41-XU-01",
            "parent_sample_id": parent["id"],
            "flow_m3h": 1500,
            "metrics": {"cod": 25.0, "nh3n": 2.0, "tp": 0.2},
        },
    )
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["lineage_edge_id"]
    edges = c.get("/api/lineage", headers=h).json()["items"]
    edge = next((e for e in edges if e["id"] == created["lineage_edge_id"]), None)
    assert edge is not None, "血缘页必须出现父子边编号"
    assert edge["edge_type"] == EDGE_PARENT_CHILD
    assert edge["from_code"] == FIXTURE_PARENT_CODE
    assert edge["to_code"] == "SM-G41-XU-01"


def test_jia_ingest_autolinks_latest_parent(g41_db):
    """未显式给父批 ⇒ 自动衔接同排放口最新批；非根批入库必须带出血缘父子边。"""
    c = TestClient(app)
    h = auth_headers(c)
    g41 = sample_by_code(c, h, FIXTURE_KEY)
    r = c.post(
        "/api/samples",
        headers=h,
        json={
            "outfall_id": g41["outfall_id"],
            "permit_ref": FIXTURE_PERMIT,
            "sample_code": "SM-G41-XU-02",
            "flow_m3h": 1500,
            "metrics": {"cod": 25.0, "nh3n": 2.0, "tp": 0.2},
        },
    )
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["parent_sample_id"] == g41["id"]
    assert created["lineage_edge_id"]


def test_jia_ingest_rejected_when_parent_missing(g41_db):
    """父批不存在 ⇒ 整笔回退拒收：零批零边。"""
    c = TestClient(app)
    h = auth_headers(c)
    g41 = sample_by_code(c, h, FIXTURE_KEY)
    before = len(c.get("/api/samples", headers=h).json()["items"])
    r = c.post(
        "/api/samples",
        headers=h,
        json={
            "outfall_id": g41["outfall_id"],
            "permit_ref": FIXTURE_PERMIT,
            "sample_code": "SM-G41-XU-03",
            "parent_sample_id": 999999,
            "flow_m3h": 1500,
            "metrics": {"cod": 25.0, "nh3n": 2.0, "tp": 0.2},
        },
    )
    assert r.status_code in (400, 404)
    after = c.get("/api/samples", headers=h).json()["items"]
    assert len(after) == before
    assert all(s["sample_code"] != "SM-G41-XU-03" for s in after)


def test_jia_seal_rejects_edgeless_batch(g41_db):
    """封批页拒绝对无边批升代。"""
    c = TestClient(app)
    h = auth_headers(c)
    g41 = sample_by_code(c, h, FIXTURE_KEY)
    r = c.post("/api/seals", headers=h, json={"sample_id": g41["id"]})
    assert r.status_code == 400
