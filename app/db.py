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
# current contract type (safe name) -> used to create per-type table
CURRENT_CONTRACT_TYPE: str | None = None

try:
    import pymysql
except Exception:
    pymysql = None


def set_db_path(path: str):
    """Set the module DB_PATH to a new path and ensure directory exists."""
    global DB_PATH
    DB_PATH = path
    ensure_db_dir()
    logger.info("set DB_PATH to %s", DB_PATH)


def set_contract_type(contract_type: str | None):
    """Set the current contract type (safe string). When using MySQL this controls which table to use."""
    global CURRENT_CONTRACT_TYPE
    if contract_type:
        safe = ''.join(c if c.isalnum() or c in ('_', '-') else '_' for c in str(contract_type)).lower()
        CURRENT_CONTRACT_TYPE = safe
    else:
        CURRENT_CONTRACT_TYPE = None
    logger.info("set contract_type to %s", CURRENT_CONTRACT_TYPE)

def _table_name_for(contract_type: str | None):
    """Return the table name to use for MySQL operations.

    - If MySQL is not enabled, always use the default `contracts` table.
    - If a specific `contract_type` is provided, sanitize and use
      `contracts_<safe>`.
    - Otherwise fall back to the module-level `CURRENT_CONTRACT_TYPE`.
    - If no type is available, return `contracts`.
    """
    if not settings.USE_MYSQL:
        return "contracts"
    if contract_type:
        safe = ''.join(c if c.isalnum() or c in ('_', '-') else '_' for c in str(contract_type)).lower()
    else:
        safe = CURRENT_CONTRACT_TYPE
    if not safe:
        return "contracts"
    return f"contracts_{safe}"

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
        file_upload_id TEXT,
        preview_url TEXT,
        parse_text TEXT,
        ai_result TEXT,
        contract_overview TEXT,
        signing_date TEXT,
        project_category TEXT,
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
    if "contract_overview" not in cols:
        cur.execute("ALTER TABLE contracts ADD COLUMN contract_overview TEXT")
    if "signing_date" not in cols:
        cur.execute("ALTER TABLE contracts ADD COLUMN signing_date TEXT")
    if "project_category" not in cols:
        cur.execute("ALTER TABLE contracts ADD COLUMN project_category TEXT")
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
    logger.info("upsert_contract %s", contract.get("contract_id"))


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
    if "contract_overview" in kwargs:
        fields.append("contract_overview = ?")
        params.append(kwargs["contract_overview"])
    if "signing_date" in kwargs:
        fields.append("signing_date = ?")
        params.append(kwargs["signing_date"])
    if "project_category" in kwargs:
        fields.append("project_category = ?")
        params.append(kwargs["project_category"])
    if "increment_attempt" in kwargs and kwargs["increment_attempt"]:
        fields.append("attempts = attempts + 1")

    sql = f"UPDATE contracts SET {', '.join(fields)} WHERE contract_id = ?"
    params.append(contract_id)
    _exec(sql, tuple(params))
    logger.info("update_status %s -> %s", contract_id, status)


def get_pending_contracts(limit: int = 100) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT contract_id, file_name, file_id, file_url, file_upload_id, preview_url, status, attempts, last_error FROM contracts WHERE status IN ('pending','failed') ORDER BY created_at LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    keys = ["contract_id", "file_name", "file_id", "file_url", "file_upload_id", "preview_url", "status", "attempts", "last_error"]
    return [dict(zip(keys, r)) for r in rows]


def get_attempts_zero_contracts(limit: int = 100) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT contract_id, file_name, file_id, file_url, file_upload_id, preview_url, status, attempts, last_error FROM contracts WHERE attempts = 0 ORDER BY created_at LIMIT ?", (limit,))
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
    if settings.USE_MYSQL:
        await loop.run_in_executor(None, mysql_upsert_contract, contract)
    else:
        await loop.run_in_executor(None, upsert_contract, contract)

async def aupdate_status(contract_id: str, status: str, **kwargs):
    loop = asyncio.get_running_loop()
    # run_in_executor doesn't accept keyword args directly; use functools.partial
    if settings.USE_MYSQL:
        func = functools.partial(mysql_update_status, contract_id, status, **kwargs)
    else:
        func = functools.partial(update_status, contract_id, status, **kwargs)
    await loop.run_in_executor(None, func)

async def aget_pending_contracts(limit: int = 100):
    loop = asyncio.get_running_loop()
    if settings.USE_MYSQL:
        return await loop.run_in_executor(None, mysql_get_pending_contracts, limit)
    return await loop.run_in_executor(None, get_pending_contracts, limit)


async def aget_attempts_zero_contracts(limit: int = 100):
    loop = asyncio.get_running_loop()
    if settings.USE_MYSQL:
        return await loop.run_in_executor(None, mysql_get_attempts_zero_contracts, limit)
    return await loop.run_in_executor(None, get_attempts_zero_contracts, limit)

async def aget_stats():
    loop = asyncio.get_running_loop()
    if settings.USE_MYSQL:
        return await loop.run_in_executor(None, mysql_get_stats)
    return await loop.run_in_executor(None, get_stats)


def get_contract(contract_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT contract_id, file_name, file_id, file_url, file_upload_id, preview_url, parse_text, ai_result, contract_overview, signing_date, project_category, status, attempts, last_error FROM contracts WHERE contract_id = ?", (contract_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    keys = ["contract_id", "file_name", "file_id", "file_url", "file_upload_id", "preview_url", "parse_text", "ai_result", "contract_overview", "signing_date", "project_category", "status", "attempts", "last_error"]
    res = dict(zip(keys, row))
    if res.get("ai_result"):
        try:
            res["ai_result"] = json.loads(res["ai_result"])
        except Exception:
            pass
    return res


async def aget_contract(contract_id: str):
    loop = asyncio.get_running_loop()
    if settings.USE_MYSQL:
        return await loop.run_in_executor(None, mysql_get_contract, contract_id)
    return await loop.run_in_executor(None, get_contract, contract_id)


######################
# MySQL-specific helpers
######################

def mysql_connect():
    if pymysql is None:
        raise RuntimeError("PyMySQL not available")
    return pymysql.connect(host=settings.MYSQL_HOST, port=settings.MYSQL_PORT, user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD, database=settings.MYSQL_DB, charset='utf8mb4')


def mysql_ensure_table(table: str):
    try:
        conn = mysql_connect()
        cur = conn.cursor()
        sql = f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            contract_id VARCHAR(255) UNIQUE,
            file_name TEXT,
            file_id TEXT,
            file_url TEXT,
            pdf_path TEXT,
            file_upload_id TEXT,
            preview_url TEXT,
            parse_text LONGTEXT,
            ai_result LONGTEXT,
            contract_overview TEXT,
            signing_date DATE,
            project_category TEXT,
            status TEXT,
            attempts INTEGER DEFAULT 0,
            last_error TEXT,
            created_at TEXT,
            updated_at TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        logger.debug("mysql_ensure_table executing SQL for %s", table)
        cur.execute(sql)
        conn.commit()
    except Exception as e:
        logger.exception("mysql_ensure_table failed for %s: %s", table, e)
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        raise
    else:
        cur.close()
        conn.close()


def mysql_upsert_contract(contract: Dict[str, Any]):
    contract_code = contract.get("contract_id") or contract.get("Code") or contract.get("code") or contract.get("Name")
    file_name = contract.get("file_name") or contract.get("Name") or contract.get("name")
    file_id = contract.get("file_id") or contract.get("Code") or contract.get("code")
    contract_type = contract.get("ContractType") or contract.get("contract_type") or CURRENT_CONTRACT_TYPE
    table = _table_name_for(contract_type)
    mysql_ensure_table(table)
    now = _now()
    sql = f"INSERT INTO {table} (contract_id, file_name, file_id, file_url, status, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE file_name=VALUES(file_name), file_id=VALUES(file_id), file_url=VALUES(file_url), updated_at=VALUES(updated_at)"
    params = (contract_code, file_name, file_id, contract.get("file_url"), 'pending', now, now)
    try:
        logger.debug("mysql_upsert_contract SQL=%s params=%s", sql, params)
        conn = mysql_connect()
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    except Exception as e:
        logger.exception("mysql_upsert_contract failed for %s: %s", contract_code, e)
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        raise
    else:
        cur.close()
        conn.close()


def mysql_update_status(contract_id: str, status: str, **kwargs):
    table = _table_name_for(None)
    parts = ["status=%s", "updated_at=%s"]
    params = [status, _now()]
    if "last_error" in kwargs:
        parts.append("last_error=%s"); params.append(kwargs["last_error"])
    if "parse_text" in kwargs:
        parts.append("parse_text=%s"); params.append(kwargs["parse_text"])
    if "ai_result" in kwargs:
        parts.append("ai_result=%s"); params.append(json.dumps(kwargs["ai_result"], ensure_ascii=False))
    if "file_url" in kwargs:
        parts.append("file_url=%s"); params.append(kwargs["file_url"])
    if "file_upload_id" in kwargs:
        parts.append("file_upload_id=%s"); params.append(kwargs["file_upload_id"])
    if "preview_url" in kwargs:
        parts.append("preview_url=%s"); params.append(kwargs["preview_url"])
    if "pdf_path" in kwargs:
        parts.append("pdf_path=%s"); params.append(kwargs["pdf_path"])
    # markdown_path removed; no-op
    if "contract_overview" in kwargs:
        parts.append("contract_overview=%s"); params.append(kwargs["contract_overview"])
    if "signing_date" in kwargs:
        parts.append("signing_date=%s"); params.append(kwargs["signing_date"])
    if "project_category" in kwargs:
        parts.append("project_category=%s"); params.append(kwargs["project_category"])
    if "increment_attempt" in kwargs and kwargs["increment_attempt"]:
        parts.append("attempts = attempts + 1")
    sql = f"UPDATE {table} SET {', '.join(parts)} WHERE contract_id=%s"
    params.append(contract_id)
    try:
        logger.info("mysql_update_status SQL=%s params=%s", sql, tuple(params))
        conn = mysql_connect()
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        conn.commit()
    except Exception as e:
        logger.exception("mysql_update_status failed for %s: %s", contract_id, e)
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        raise
    else:
        cur.close()
        conn.close()


def mysql_get_pending_contracts(limit: int = 100):
    table = _table_name_for(None)
    mysql_ensure_table(table)
    sql = f"SELECT contract_id, file_name, file_id, file_url, file_upload_id, preview_url, status, attempts, last_error FROM {table} WHERE status IN ('pending','failed') ORDER BY created_at LIMIT %s"
    conn = mysql_connect()
    cur = conn.cursor()
    cur.execute(sql, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    keys = ["contract_id", "file_name", "file_id", "file_url", "file_upload_id", "preview_url", "status", "attempts", "last_error"]
    return [dict(zip(keys, r)) for r in rows]


def mysql_get_attempts_zero_contracts(limit: int = 100):
    table = _table_name_for(None)
    mysql_ensure_table(table)
    sql = f"SELECT contract_id, file_name, file_id, file_url, file_upload_id, preview_url, status, attempts, last_error FROM {table} WHERE attempts = 0 ORDER BY created_at LIMIT %s"
    conn = mysql_connect()
    cur = conn.cursor()
    cur.execute(sql, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    keys = ["contract_id", "file_name", "file_id", "file_url", "file_upload_id", "preview_url", "status", "attempts", "last_error"]
    return [dict(zip(keys, r)) for r in rows]


def mysql_get_stats():
    table = _table_name_for(None)
    mysql_ensure_table(table)
    sql = f"SELECT status, COUNT(1) FROM {table} GROUP BY status"
    conn = mysql_connect()
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r[0]: r[1] for r in rows}


def mysql_get_contract(contract_id: str):
    table = _table_name_for(None)
    mysql_ensure_table(table)
    sql = f"SELECT contract_id, file_name, file_id, file_url, file_upload_id, preview_url, parse_text, ai_result, contract_overview, signing_date, project_category, status, attempts, last_error FROM {table} WHERE contract_id = %s"
    conn = mysql_connect()
    cur = conn.cursor()
    cur.execute(sql, (contract_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    keys = ["contract_id", "file_name", "file_id", "file_url", "file_upload_id", "preview_url", "parse_text", "ai_result", "contract_overview", "signing_date", "project_category", "status", "attempts", "last_error"]
    res = dict(zip(keys, row))
    if res.get("ai_result"):
        try:
            res["ai_result"] = json.loads(res["ai_result"])
        except Exception:
            pass
    return res
