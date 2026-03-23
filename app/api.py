from fastapi import FastAPI, BackgroundTasks, HTTPException
from typing import Dict, Any
from . import db
from .config import settings
from .worker import start_background, retry_contract
import asyncio
import re
import logging

logger = logging.getLogger("app.api")

app = FastAPI(title="Service")

@app.on_event("startup")
async def startup():
    await db.ainit_db()

@app.post("/start")
async def start_job(background_tasks: BackgroundTasks, limit: int = 50, query: Dict[str, Any] | None = None):
    loop = None
    try:
        loop = __import__('asyncio').get_running_loop()
    except RuntimeError:
        loop = None
    logger.info("start_job called limit=%s query=%s", limit, query)
    # 如果提供了 contract type，则把 DB 路径切换到按类别命名的文件
    contract_type = None
    if query:
        qf = query.get("queryFormData") if isinstance(query, dict) and "queryFormData" in query else query
        if isinstance(qf, dict):
            contract_type = qf.get("$EQ$ContractType") or qf.get("ContractType")

    if contract_type:
        safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(contract_type)).lower()
        db_path = f"data/contracts_{safe}.db"
        db.set_db_path(db_path)
        # ensure DB initialized for this path before background tasks run
        await db.ainit_db()

    if loop:
        start_background(loop, limit, query)
        return {"status": "started", "limit": limit, "contract_type": contract_type}
    else:
        # fallback (shouldn't happen under uvicorn)
        background_tasks.add_task(db.ainit_db)
        return {"status": "scheduled", "limit": limit}

@app.get("/status")
async def status():
    stats = await db.aget_stats()
    return {"stats": stats}

@app.get("/pending")
async def pending(limit: int = 20):
    items = await db.aget_pending_contracts(limit)
    return {"pending": items}


@app.post("/retry/{contract_id}")
async def api_retry(contract_id: str):
    # mark pending and enqueue retry
    exists = await db.aget_contract(contract_id)
    if not exists:
        raise HTTPException(status_code=404, detail="contract not found")
    loop = asyncio.get_running_loop()
    loop.create_task(retry_contract(contract_id))
    await db.aupdate_status(contract_id, "pending")
    return {"status": "scheduled", "contract_id": contract_id}
