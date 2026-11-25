"""effluent-07 入库写边：共享夹具与常量（假绿夹具键 SM-G41-写边）。

假绿复现状态：SM-G41-写边 入库已成功（已裁定、正式批在册），但
metric_lineage 中以其为子边的正式 parent_child 边行数为零 —— 血缘断链，
对账却仍把该批算作血统齐全。
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db

FIXTURE_KEY = "SM-G41-写边"
FIXTURE_PERMIT = "DP-2026-N03"
FIXTURE_PARENT_CODE = "SM-OF-N3-A-001"
EDGE_PARENT_CHILD = "parent_child"
EDGE_FORMAL = "正式"
EDGE_SHADOW = "影子"
SHADOW_DETAIL_MARK = "影子连边草稿"


@pytest.fixture()
def g41_db(tmp_path, monkeypatch):
    db_file = tmp_path / "g41.db"
    monkeypatch.setattr("db.DB_PATH", db_file)
    init_db()
    return db_file


def auth_headers(c: TestClient, user="admin", password="admin123") -> dict:
    tok = c.post("/api/login", json={"username": user, "password": password}).json()[
        "token"
    ]
    return {"Authorization": "Bearer " + tok}


def sample_by_code(c: TestClient, headers: dict, code: str) -> dict:
    items = c.get("/api/samples", headers=headers).json()["items"]
    return next(s for s in items if s["sample_code"] == code)


def _rows(db_file, sql: str, args: tuple) -> list:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def formal_child_edges(db_file, sample_id: int) -> list:
    """该批作为子边的正式父子边（血统依据）。"""
    return _rows(
        db_file,
        "SELECT * FROM metric_lineage WHERE to_sample_id=? AND edge_type=? AND status=?",
        (sample_id, EDGE_PARENT_CHILD, EDGE_FORMAL),
    )


def formal_edges_from(db_file, sample_id: int) -> list:
    """该批作为源（父）发出的正式父子边。"""
    return _rows(
        db_file,
        "SELECT * FROM metric_lineage WHERE from_sample_id=? AND edge_type=? AND status=?",
        (sample_id, EDGE_PARENT_CHILD, EDGE_FORMAL),
    )


def test_fixture_false_green_reproduced(g41_db):
    """复现假绿：SM-G41-写边 正式批在册，而血缘父子边行数为零。"""
    c = TestClient(app)
    h = auth_headers(c)
    sample = sample_by_code(c, h, FIXTURE_KEY)
    assert sample["status"] == "已裁定"
    assert formal_child_edges(g41_db, sample["id"]) == []
