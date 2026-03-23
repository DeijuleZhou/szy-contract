import httpx
import asyncio
from typing import List, Dict, Any
from .config import settings
import logging
import json

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


async def parse_file_by_serviceB(file_url: str) -> Dict[str, Any]:
    """先下载文件并上传到解析服务得到 upload_file_id，再触发 workflow 并尝试返回解析出的文本。
    返回字典：{"text": ..., "upload_file_id": ...}
    如果未配置 PARSE_BASE_URL 则退回模拟解析，upload_file_id 为 None。
    """

    async with await _client() as client:
        # 1) 下载文件内容
        try:
            async with client.stream("GET", file_url) as r:
                r.raise_for_status()
                content = await r.aread()
        except Exception:
            logger.exception("failed to download file %s", file_url)
            return {"text": f"[PARSE-ERROR] 无法下载文件：{file_url}", "upload_file_id": None}

        # 2) 上传文件到解析服务的 upload 接口
        upload_url = f"{settings.UPLOAD_BASE_URL.rstrip('/')}{settings.UPLOAD_PATH}"
        filename = file_url.split("/")[-1] or "file.pdf"
        files = {"files": (filename, content, "application/octet-stream")}
        try:
            resp = await client.post(upload_url, files=files)
            resp.raise_for_status()
            upload_resp = resp.json()
        except Exception:
            logger.exception("upload failed to %s", upload_url)
            return {"text": f"[PARSE-ERROR] 上传失败：{upload_url}", "upload_file_id": None}

        # 解析 upload_resp，期望是列表或 dict 包含 id
        upload_resp_body = upload_resp.get("data")
        upload_id = upload_resp_body[0].get("id")

        # 3) 调用 workflow/run 使用 upload_file_id
        run_url = f"{settings.WORKFLOW_BASE_URL.rstrip('/')}{settings.WORKFLOW_RUN_PATH}"
        payload = {
            "inputs": {
                "file": 
                    {"type": "pdf", "transfer_method": "local_file", "url": "", "upload_file_id": upload_id}
                
            },
            "response_mode": "blocking", 
        }
        try:
            resp = await client.post(run_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("workflow run failed %s", run_url)
            return {"text": f"[PARSE-ERROR] workflow 调用失败", "upload_file_id": upload_id}

    # 从返回结果中抽取文本
    text = None
    if isinstance(data, dict):
        text = data.get("data").get("outputs").get("output").get("answer")

    # text为字符串化的json，将其序列化为json
    ai_result = None
    try:
        ai_result = json.loads(text)
    except Exception:
        logger.warning("unable to serialize text to json for file %s", file_url)

    logger.info("parse result upload_id=%s text_len=%s ai_result_present=%s", upload_id, (len(text) if text else 0), bool(ai_result))

    return {"text": text, "upload_file_id": upload_id, "ai_result": ai_result}


