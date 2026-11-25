"""effluent-07 争用红测：两路会话同一父批下抢续采，须抢批锁，后到争用只记痕。

钉住：两路同父批续采无锁双成（败方应零边零批）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from test_effluent_lineage_edge import (  # noqa: F401  (g41_db 为 pytest 夹具)
    FIXTURE_PARENT_CODE,
    FIXTURE_PERMIT,
    auth_headers,
    formal_edges_from,
    g41_db,
    sample_by_code,
)


def test_zheng_same_parent_continue_needs_batch_lock(g41_db):
    """会话一持父批锁，会话二同父批续采 ⇒ 409 只记痕，败方零边零批。"""
    c = TestClient(app)
    h_admin = auth_headers(c, "admin", "admin123")
    h_writer = auth_headers(c, "writer", "writer123")
    parent = sample_by_code(c, h_admin, FIXTURE_PARENT_CODE)
    lock_key = f"sample:{parent['id']}"
    hold = c.post(f"/api/locks/acquire?lock_key={lock_key}", headers=h_writer)
    assert hold.status_code == 200

    before_samples = len(c.get("/api/samples", headers=h_admin).json()["items"])
    before_edges = len(formal_edges_from(g41_db, parent["id"]))
    r = c.post(
        "/api/samples",
        headers=h_admin,
        json={
            "outfall_id": parent["outfall_id"],
            "permit_ref": FIXTURE_PERMIT,
            "sample_code": "SM-G41-XU-Z1",
            "parent_sample_id": parent["id"],
            "flow_m3h": 1500,
            "metrics": {"cod": 25.0, "nh3n": 2.0, "tp": 0.2},
        },
    )
    assert r.status_code == 409, r.text

    after_samples = c.get("/api/samples", headers=h_admin).json()["items"]
    assert len(after_samples) == before_samples, "败方零批"
    assert all(s["sample_code"] != "SM-G41-XU-Z1" for s in after_samples)
    assert len(formal_edges_from(g41_db, parent["id"])) == before_edges, "败方零边"

    locks = c.get("/api/locks", headers=h_admin).json()
    assert any(
        x["lock_key"] == lock_key and x["action"] == "争用失败"
        for x in locks["contention"]
    ), "后到争用只记痕"
