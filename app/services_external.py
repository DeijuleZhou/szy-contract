import httpx
import asyncio
from typing import List, Dict, Any, Optional
from .config import settings
import logging
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

PARSE_SERIAL_LOCK: Optional[asyncio.Lock] = None


def _get_parse_serial_lock() -> asyncio.Lock:
    global PARSE_SERIAL_LOCK
    if PARSE_SERIAL_LOCK is None:
        PARSE_SERIAL_LOCK = asyncio.Lock()
    return PARSE_SERIAL_LOCK

from .storage_minio import upload_bytes

logger = logging.getLogger("app.services_external")

async def _client() -> httpx.AsyncClient:
    headers = {}
    if settings.EXTERNAL_API_KEY:
        headers["Authorization"] = f"Bearer {settings.EXTERNAL_API_KEY}"
    return httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT, headers=headers)


async def list_contracts(limit: int = 50, query: Dict | None = None) -> List[Dict[str, Any]]:
    """
    调用//ManageContract/GetContractPerformenceList获取合同列表，返回统一的合同 dict 列表。
    如果未配置 MARKET_BASE_URL，则退回到模拟实现以便本地测试。
    """

    url = f"{settings.MARKET_BASE_URL.rstrip('/')}{settings.MARKET_CONTRACTS_PATH}"
    logger.info("POST %s payload=%s", url, {k: v for k, v in (query or {}).items() if k != 'queryFormData' or True})
    async with await _client() as client:
        resp = await client.post(url, json=query or {})
        try:
            resp.raise_for_status()
        except Exception as e:
            logger.exception("market list_contracts request failed: %s", e)
            raise
        data = resp.json()

    # 兼容不同的返回结构：如果是列表直接返回，或从 data 字段中取
    if isinstance(data, list):
        # each item may wrap code/msg/data
        out = []
        for item in data:
            if isinstance(item, dict) and "Data" in item:
                d = item.get("Data")
                if isinstance(d, dict):
                    out.append(d)
                elif isinstance(d, list):
                    out.extend(d)
            elif isinstance(item, dict):
                out.append(item)
        return out
    if isinstance(data, dict) and "Data" in data:
        return data["Data"] if isinstance(data["Data"], list) else [data["Data"]]
    return []


async def get_file_url(attachment_names: List[str]) -> Dict[str, Dict[str, str]]:
    """调用文件服务获取下载/预览链接，返回 mapping: name -> {download_url, preview_url}
    接口期望 POST 一个 filename 数组。
    如果未配置 FILE_BASE_URL 则返回模拟链接。
    """
    url = f"{settings.FILE_BASE_URL.rstrip('/')}{settings.FILE_GET_PATH}"
    logger.info("POST %s attachments=%s", url, attachment_names)
    async with await _client() as client:
        # 按 README 要求直接发送数组
        resp = await client.post(url, json=attachment_names)
        try:
            resp.raise_for_status()
        except Exception as e:
            logger.exception("get_file_url request failed: %s", e)
            raise
        data = resp.json()

    # 返回 {Code, Msg, Data:[{name: {DownlUrl,PreviewUrl}}, ...]}
    out: Dict[str, Dict[str, str]] = {}
    # 支持大写 Data 字段或小写 data
    data_field = None
    if isinstance(data, dict):
        data_field = data.get("Data") or data.get("data")
    if isinstance(data_field, list):
        for entry in data_field:
            if isinstance(entry, dict):
                for name, meta in entry.items():
                    if isinstance(meta, dict):
                        down = meta.get("DownUrl") or meta.get("DownloadUrl") or meta.get("download_url")
                        prev = meta.get("PreviewUrl") or meta.get("preview_url")
                        out[name] = {"download_url": down, "preview_url": prev}
    elif isinstance(data, dict):
        # fallback: try mapping in root
        for k, v in data.items():
            if isinstance(v, dict):
                down = v.get("DownUrl") or v.get("DownloadUrl") or v.get("download_url")
                prev = v.get("PreviewUrl") or v.get("preview_url")
                if down:
                    out[k] = {"download_url": down, "preview_url": prev}
    return out


async def parse_file_by_serviceB(file_url: str, contract_code: Optional[str] = None) -> Dict[str, Any]:
    """新的解析流程：不使用 marker_pdf，直接将来自文件服务的 `file_url` 传给 `/workflows/run`。

    同时为了保留改名后的文件引用，会下载原文件并上传到 Minio（object name 使用可预测命名），
    上传后返回的 `pdf_path` 为去掉 scheme+host:port 的 path 部分（只保留桶和对象路径）。
    """

    lock = _get_parse_serial_lock()
    async with lock:
        # 1) 调用 workflow/run，传入 file_url（按 README 要求）
        run_url = f"{settings.WORKFLOW_BASE_URL.rstrip('/')}{settings.WORKFLOW_RUN_PATH}"
        payload = {
            "inputs": {"file_url": file_url},
            "response_mode": "blocking",
        }
        async with await _client() as client:
            # retry logic with exponential backoff
            max_attempts = max(1, int(getattr(settings, 'RETRY_LIMIT', 3)))
            attempt = 0
            delay = 1.0
            data = None
            last_exc = None
            while attempt < max_attempts:
                attempt += 1
                try:
                    resp = await client.post(run_url, json=payload, timeout=300.0)
                    resp.raise_for_status()
                    data = resp.json()
                    break
                except Exception as e:
                    last_exc = e
                    status = None
                    body = None
                    try:
                        if hasattr(e, 'response') and e.response is not None:
                            status = getattr(e.response, 'status_code', None)
                            body = e.response.text
                    except Exception:
                        pass
                    logger.warning("workflow run attempt %s/%s failed status=%s body=%s error=%s", attempt, max_attempts, status, body, e)
                    if attempt < max_attempts:
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 30)
                    else:
                        logger.exception("workflow run failed after %s attempts %s status=%s body=%s error=%s", max_attempts, run_url, status, body, last_exc)
                        return {
                            "text": f"[PARSE-ERROR] workflow 调用失败",
                            "upload_file_id": None,
                            "ai_result": None,
                            "pdf_path": None,
                        }

        # 2) 从 workflow 返回中抽取文本/ai_result（逻辑与此前保持一致）
        text = None
        if isinstance(data, dict):
            try:
                text = (
                    data.get("data", {})
                    .get("outputs", {})
                    .get("output", {})
                    .get("answer")
                )
            except Exception:
                logger.exception("failed to parse workflow response")

        ai_result = None
        if isinstance(text, str):
            try:
                ai_result = json.loads(text)
            except Exception:
                logger.warning("unable to serialize text to json for file %s", file_url)

        # 3) 为了保持存档，下载原始 PDF 并上传到 Minio，生成可控的 object name
        try:
            async with await _client() as client:
                async with client.stream("GET", file_url) as r:
                    r.raise_for_status()
                    content = await r.aread()
        except Exception:
            logger.exception("failed to download file %s for storage", file_url)
            # 即使下载失败，也返回 workflow 的解析结果（如果有）
            return {"text": text, "upload_file_id": None, "ai_result": ai_result, "pdf_path": None}

        filename = file_url.split("/")[-1] or "file.pdf"
        name_no_ext = os.path.splitext(filename)[0]
        safe_name_source = contract_code or name_no_ext
        safe_name = re.sub(r"[^0-9A-Za-z_.-]", "_", safe_name_source) or "file"

        loop = asyncio.get_running_loop()
        pdf_object_name = f"pdf/{safe_name}.pdf"
        try:
            full_pdf_url = await loop.run_in_executor(None, upload_bytes, content, pdf_object_name, "application/pdf")
        except Exception:
            logger.exception("failed to upload pdf to storage for %s", file_url)
            return {"text": text, "upload_file_id": None, "ai_result": ai_result, "pdf_path": None}

        # strip scheme+host:port, keep path (bucket + object)
        try:
            parsed = urlparse(full_pdf_url)
            stripped = parsed.path.lstrip('/')
        except Exception:
            stripped = full_pdf_url

        logger.info(
            "parse result text_len=%s ai_result_present=%s pdf_path=%s",
            (len(text) if isinstance(text, str) else 0),
            bool(ai_result),
            stripped,
        )

        return {"text": text, "upload_file_id": None, "ai_result": ai_result, "pdf_path": stripped}


