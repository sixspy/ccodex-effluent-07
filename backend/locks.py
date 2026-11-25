"""排放批争用锁。"""
from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, connect_try, now_iso, write_audit

router = APIRouter(tags=["locks"])

_POLL_S = 0.001
_POLL_MAX = 50


def _log_contention(lock_key: str, actor: str, holder: str) -> None:
    """后到争用只记痕：独立连接落痕，绝不在调用方连接上写，
    免得调用方连接持 RESERVED 与抢锁连接互等成死锁。"""
    c = connect()
    try:
        c.execute(
            """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (lock_key, actor, "争用失败", f"持有人={holder}", now_iso()),
        )
        c.commit()
    finally:
        c.close()


def _read_holder(lock_key: str):
    """零等待读持锁行；库忙返回 'busy' 哨兵。"""
    ac = connect_try()
    try:
        row = ac.execute(
            "SELECT holder FROM effluent_locks WHERE lock_key=?", (lock_key,)
        ).fetchone()
        return row["holder"] if row else None
    except sqlite3.OperationalError:
        return "busy"
    finally:
        ac.close()


def require_lock(conn, lock_key: str, holder: str) -> None:
    """抢锁：零等待快轮询读持锁行 + 零等待抢行，抢到即提交。

    不排队等锁——busy 重试若等到持锁方请求结束、锁行已删，就成无锁双成；
    也不用常超连接读锁行——指数退避会睡过整个持锁窗口。
    零等待连接用完即关，绝不留未决事务占 RESERVED 与其他连接互等。
    """
    busy = 0
    while True:
        seen = _read_holder(lock_key)
        if seen == "busy":
            busy += 1
        elif seen is not None and seen != holder:
            _log_contention(lock_key, holder, seen)
            raise HTTPException(409, f"锁被占用：{lock_key} 持有人 {seen}")
        elif seen == holder:
            return
        else:
            ac = connect_try()
            try:
                ac.execute(
                    "INSERT INTO effluent_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
                    (lock_key, holder, now_iso()),
                )
                ac.commit()
                return
            except sqlite3.IntegrityError:
                # 抢行瞬间他方已占位，下轮轮询读持锁人判负
                busy += 1
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc):
                    raise
                busy += 1
            finally:
                ac.close()
        if busy >= _POLL_MAX:
            _log_contention(lock_key, holder, "并发写入中")
            raise HTTPException(409, f"锁被占用：{lock_key}（并发写入中）")
        time.sleep(_POLL_S)


def release(conn, lock_key: str, holder: str) -> None:
    conn.execute(
        "DELETE FROM effluent_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        locks = [dict(r) for r in conn.execute("SELECT * FROM effluent_locks").fetchall()]
        logs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM contention_log ORDER BY id DESC LIMIT 50"
            ).fetchall()
        ]
        return {"locks": locks, "contention": logs}


@router.post("/api/locks/acquire")
def acquire(lock_key: str, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        require_lock(conn, lock_key, user)
        write_audit(conn, user, "lock_acquire", lock_key, {})
        conn.commit()
        return {"ok": True, "lock_key": lock_key, "holder": user}


@router.post("/api/locks/release")
def release_api(lock_key: str, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, lock_key, user)
        write_audit(conn, user, "lock_release", lock_key, {})
        conn.commit()
        return {"ok": True}
