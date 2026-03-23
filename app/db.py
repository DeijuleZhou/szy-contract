import sqlite3
import os
import json
import asyncio
import functools
from typing import Any, Dict, List
from datetime import datetime
from .config import settings
import logging

logger = logging.getLogger("app.db")

DB_PATH = settings.DB_PATH


def set_db_path(path: str):
    """Set the module DB_PATH to a new path and ensure directory exists."""
    global DB_PATH
    DB_PATH = path
    ensure_db_dir()
    logger.info("set DB_PATH to %s", DB_PATH)

def ensure_db_dir():
    dirpath = os.path.dirname(DB_PATH)
    if dirpath and not os.path.exists(dirpath):
        os.makedirs(dirpath, exist_ok=True)

def init_db():
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id TEXT UNIQUE,
        file_name TEXT,
        file_id TEXT,
        file_url TEXT,
        pdf_path TEXT,
        markdown_path TEXT,
        file_upload_id TEXT,
        preview_url TEXT,
        parse_text TEXT,
        ai_result TEXT,
        status TEXT,
        attempts INTEGER DEFAULT 0,
        last_error TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    # 增量添加可能缺失的列（兼容已经存在的旧 DB）
    cur.execute("PRAGMA table_info(contracts)")
    cols = [r[1] for r in cur.fetchall()]
    if "file_upload_id" not in cols:
        cur.execute("ALTER TABLE contracts ADD COLUMN file_upload_id TEXT")
    if "preview_url" not in cols:
        cur.execute("ALTER TABLE contracts ADD COLUMN preview_url TEXT")
    if "pdf_path" not in cols:
        cur.execute("ALTER TABLE contracts ADD COLUMN pdf_path TEXT")
    if "markdown_path" not in cols:
        cur.execute("ALTER TABLE contracts ADD COLUMN markdown_path TEXT")
    conn.commit()
    conn.close()
    logger.info("initialized db at %s", DB_PATH)


def _now():
    return datetime.utcnow().isoformat()


def _exec(sql: str, params=()):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    conn.close()


def upsert_contract(contract: Dict[str, Any]):
    now = _now()
    contract_code = contract.get("contract_id") or contract.get("Code") or contract.get("code") or contract.get("Name")
    file_name = contract.get("file_name") or contract.get("Name") or contract.get("name")
    file_id = contract.get("file_id") or contract.get("Code") or contract.get("code")
    sql = """
    INSERT INTO contracts (contract_id, file_name, file_id, file_url, status, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(contract_id) DO UPDATE SET file_name=excluded.file_name, file_id=excluded.file_id, file_url=excluded.file_url, updated_at=excluded.updated_at
    """
    params = (
        contract_code,
        file_name,
        file_id,
        contract.get("file_url"),
        "pending",
        now,
        now,
    )
    _exec(sql, params)
    logger.debug("upsert_contract %s", contract.get("contract_id"))


def update_status(contract_id: str, status: str, **kwargs):
    now = _now()
    fields = ["status = ?", "updated_at = ?"]
    params = [status, now]
    if "last_error" in kwargs:
        fields.append("last_error = ?")
        params.append(kwargs["last_error"])
    if "parse_text" in kwargs:
        fields.append("parse_text = ?")
        params.append(kwargs["parse_text"])
    if "ai_result" in kwargs:
        fields.append("ai_result = ?")
        params.append(json.dumps(kwargs["ai_result"], ensure_ascii=False))
    if "file_url" in kwargs:
        fields.append("file_url = ?")
        params.append(kwargs["file_url"])
    if "file_upload_id" in kwargs:
        fields.append("file_upload_id = ?")
        params.append(kwargs["file_upload_id"])
    if "preview_url" in kwargs:
        fields.append("preview_url = ?")
        params.append(kwargs["preview_url"])
    if "pdf_path" in kwargs:
        fields.append("pdf_path = ?")
        params.append(kwargs["pdf_path"])
    if "markdown_path" in kwargs:
        fields.append("markdown_path = ?")
        params.append(kwargs["markdown_path"])
    if "increment_attempt" in kwargs and kwargs["increment_attempt"]:
        fields.append("attempts = attempts + 1")

    sql = f"UPDATE contracts SET {', '.join(fields)} WHERE contract_id = ?"
    params.append(contract_id)
    _exec(sql, tuple(params))
    logger.debug("update_status %s -> %s", contract_id, status)


def get_pending_contracts(limit: int = 100) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT contract_id, file_name, file_id, file_url, file_upload_id, preview_url, status, attempts, last_error FROM contracts WHERE status IN ('pending','failed') ORDER BY created_at LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    keys = ["contract_id", "file_name", "file_id", "file_url", "file_upload_id", "preview_url", "status", "attempts", "last_error"]
    return [dict(zip(keys, r)) for r in rows]


def get_stats() -> Dict[str, int]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(1) FROM contracts GROUP BY status")
    rows = cur.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

# Async wrappers
async def ainit_db():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, init_db)

async def aupsert_contract(contract: Dict[str, Any]):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, upsert_contract, contract)

async def aupdate_status(contract_id: str, status: str, **kwargs):
    loop = asyncio.get_running_loop()
    # run_in_executor doesn't accept keyword args directly; use functools.partial
    func = functools.partial(update_status, contract_id, status, **kwargs)
    await loop.run_in_executor(None, func)

async def aget_pending_contracts(limit: int = 100):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_pending_contracts, limit)

async def aget_stats():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_stats)


def get_contract(contract_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT contract_id, file_name, file_id, file_url, file_upload_id, preview_url, parse_text, ai_result, status, attempts, last_error FROM contracts WHERE contract_id = ?", (contract_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    keys = ["contract_id", "file_name", "file_id", "file_url", "file_upload_id", "preview_url", "parse_text", "ai_result", "status", "attempts", "last_error"]
    res = dict(zip(keys, row))
    if res.get("ai_result"):
        try:
            res["ai_result"] = json.loads(res["ai_result"])
        except Exception:
            pass
    return res


async def aget_contract(contract_id: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_contract, contract_id)
