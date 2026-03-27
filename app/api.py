from fastapi import FastAPI, BackgroundTasks, HTTPException
from typing import Dict, Any
from . import db
from .config import settings
from .worker import start_background, retry_contract
import asyncio
import re
import logging
import httpx

from pydantic import BaseModel

logger = logging.getLogger("app.api")

app = FastAPI(title="Service")


class ParserUrlPayload(BaseModel):
    url: str


@app.post("/parser/v1/parser/url")
async def parser_url(payload: ParserUrlPayload):
    """接收 PDF 链接，将 PDF 下载后以 form-data 转发到外部解析服务，返回解析接口的 content_list.json 字段。"""
    file_url = payload.url
    if not file_url:
        raise HTTPException(status_code=400, detail="missing url")

    parser_api = settings.PARSER_API_URL
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT) as client:
        try:
            r = await client.get(file_url)
            r.raise_for_status()
            content = r.content
        except Exception as e:
            logging.exception("failed to download pdf %s", file_url)
            raise HTTPException(status_code=502, detail=f"failed to download file: {e}")

        filename = file_url.split("/")[-1] or "file.pdf"
        files = {"file": (filename, content, "application/pdf")}
        try:
            # increase timeout for parser POST (files may be large)
            resp = await client.post(parser_api, files=files, timeout=120.0)
            resp.raise_for_status()
            j = resp.json()
            result = j.get("data", {}).get("content_list.json")
            return {"result": result}
        except httpx.HTTPStatusError as e:
            # log response body if available for debugging
            try:
                body = e.response.text
            except Exception:
                body = None
            logging.exception("parser API status error %s status=%s body=%s", parser_api, getattr(e.response, 'status_code', None), body)
            raise HTTPException(status_code=502, detail=f"parser api error: {e}")
        except Exception as e:
            logging.exception("parser proxy failed: %s", e)
            raise HTTPException(status_code=502, detail=str(e))

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
        # set current contract type so DB layer uses per-type table (MySQL) or per-file sqlite if still used
        db.set_contract_type(safe)
        # if using sqlite fallback, create a dedicated DB file
        if not settings.USE_MYSQL:
            db_path = f"data/contracts_{safe}.db"
            db.set_db_path(db_path)
        # ensure DB/table initialized for this type before background tasks run
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
