"""effluent-07 乙路径红测：影子连边未升正，永不得被血缘页当正式链。

钉住：影子连边（status=影子）未升正却被血缘页当正式链。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from test_effluent_lineage_edge import (  # noqa: F401  (g41_db 为 pytest 夹具)
    EDGE_FORMAL,
    FIXTURE_KEY,
    FIXTURE_PERMIT,
    SHADOW_DETAIL_MARK,
    auth_headers,
    g41_db,
)


def test_yi_shadow_edge_not_formal_on_lineage_page(g41_db):
    """血缘页只展正式链：影子连边草稿不得出现，且全量边均须为正式。"""
    c = TestClient(app)
    h = auth_headers(c)
    items = c.get("/api/lineage", headers=h).json()["items"]
    shadow = [e for e in items if SHADOW_DETAIL_MARK in e["detail"]]
    assert shadow == [], "影子连边未升正却被血缘页当正式链"
    assert all(e["status"] == EDGE_FORMAL for e in items)


def test_yi_shadow_edge_not_counted_in_reconcile(g41_db):
    """影子边不计血统：SM-G41-写边 仍属无边批，血统齐全计数照扣。"""
    c = TestClient(app)
    h = auth_headers(c)
    data = c.get(f"/api/reconcile/{FIXTURE_PERMIT}", headers=h).json()
    assert FIXTURE_KEY in data["lineage_missing"]
