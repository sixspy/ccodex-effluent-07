"""effluent-07 争用并发脚本 ping_parent_sample_lock：

两路会话（admin / writer）并发双请求打同一父批续采，须抢批锁，
后到争用只记痕，败方零边零批。独立临时库起真实服务验证。

用法：python scripts/ping_parent_sample_lock.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = int(os.environ.get("PING_PORT", "0")) or _free_port()
BASE = f"http://127.0.0.1:{PORT}"
PARENT_CODE = "SM-OF-N3-A-001"
PERMIT = "DP-2026-N03"


def _wait_health(deadline: float = 30.0) -> None:
    t0 = time.time()
    while time.time() - t0 < deadline:
        try:
            if httpx.get(f"{BASE}/api/health", timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            time.sleep(0.3)
    raise SystemExit("服务未就绪")


def _login(user: str, password: str) -> dict:
    tok = httpx.post(
        f"{BASE}/api/login", json={"username": user, "password": password}, timeout=5
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="effluent07-ping-")
    env = dict(
        os.environ,
        EFFLUENT_DB_PATH=str(Path(tmp) / "ping.db"),
        PYTHONPATH=str(ROOT / "backend"),
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--app-dir",
            str(ROOT / "backend"),
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_health()
        h_admin = _login("admin", "admin123")
        h_writer = _login("writer", "writer123")
        samples = httpx.get(f"{BASE}/api/samples", headers=h_admin, timeout=5).json()[
            "items"
        ]
        parent = next(s for s in samples if s["sample_code"] == PARENT_CODE)
        before_codes = {s["sample_code"] for s in samples}
        before_edges = httpx.get(f"{BASE}/api/lineage", headers=h_admin, timeout=5).json()[
            "items"
        ]

        barrier = threading.Barrier(2)
        results: dict[str, int] = {}

        def fire(tag: str, headers: dict, code: str) -> None:
            barrier.wait()
            try:
                r = httpx.post(
                    f"{BASE}/api/samples",
                    headers=headers,
                    json={
                        "outfall_id": parent["outfall_id"],
                        "permit_ref": PERMIT,
                        "sample_code": code,
                        "parent_sample_id": parent["id"],
                        "flow_m3h": 1500,
                        "metrics": {"cod": 25.0, "nh3n": 2.0, "tp": 0.2},
                    },
                    timeout=30,
                )
                results[tag] = r.status_code
            except httpx.HTTPError as exc:
                results[tag] = f"连接异常:{exc.__class__.__name__}"

        threads = [
            threading.Thread(target=fire, args=("A-admin", h_admin, "SM-PING-A")),
            threading.Thread(target=fire, args=("B-writer", h_writer, "SM-PING-B")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ok = sorted(results.items())
        wins = [k for k, v in ok if v == 200]
        loses = [k for k, v in ok if v == 409]
        after = httpx.get(f"{BASE}/api/samples", headers=h_admin, timeout=5).json()["items"]
        new_codes = {s["sample_code"] for s in after} - before_codes
        after_edges = httpx.get(f"{BASE}/api/lineage", headers=h_admin, timeout=5).json()[
            "items"
        ]
        new_edges = [e for e in after_edges if e["id"] not in {x["id"] for x in before_edges}]
        contention = httpx.get(f"{BASE}/api/locks", headers=h_admin, timeout=5).json()[
            "contention"
        ]
        hits = [x for x in contention if x["action"] == "争用失败"]

        print(f"两路结果: {ok}")
        print(f"新批: {sorted(new_codes)} 新边: {[e['id'] for e in new_edges]}")
        print(f"争用记痕: {[(x['lock_key'], x['actor']) for x in hits]}")

        checks = {
            "恰好一路成功": len(wins) == 1,
            "败方409": len(loses) == 1,
            "败方零批": len(new_codes) == 1,
            "败方零边": len(new_edges) == 1,
            "争用只记痕": len(hits) >= 1,
        }
        for name, passed in checks.items():
            print(f"{'PASS' if passed else 'FAIL'} {name}")
        return 0 if all(checks.values()) else 1
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
