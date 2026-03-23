import httpx
import asyncio
from typing import List, Dict, Any
from .config import settings
import logging
import json
import os
from io import BytesIO
from pathlib import Path
import re

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


async def parse_file_by_serviceB(file_url: str) -> Dict[str, Any]:
    """下载 PDF -> 使用 marker-pdf 转为 markdown ->
    将 PDF 和 markdown 存入 Minio -> 调用 workflow/run(query=markdown)。

    返回字典：{"text": ..., "upload_file_id": None, "ai_result": ..., "pdf_path": ..., "markdown_path": ...}
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

    # 2) 上传原始 PDF 到 Minio，文件名用简短 Name.pdf
    filename = file_url.split("/")[-1] or "file.pdf"
    # 简化文件名，去除复杂字符
    name_no_ext = os.path.splitext(filename)[0]
    safe_name = re.sub(r"[^0-9A-Za-z_.-]", "_", name_no_ext) or "file"
    pdf_object_name = f"pdf/{safe_name}.pdf"
    loop = asyncio.get_running_loop()
    pdf_path = await loop.run_in_executor(None, upload_bytes, content, pdf_object_name, "application/pdf")

    # 3) 使用 marker-pdf 将 PDF 内容转为 markdown（本地模型）
    def _convert_to_markdown(data: bytes) -> str:
        # 延迟导入，避免应用启动时加载重模型
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.config.parser import ConfigParser
        from marker.output import text_from_rendered

        base_dir = Path(__file__).resolve().parents[1]
        model_dir = os.environ.get("SURYA_MODEL_DIR") or os.environ.get("MARKER_CACHE_DIR") or str(base_dir / "models")
        os.environ["SURYA_MODEL_DIR"] = model_dir
        os.environ["MARKER_CACHE_DIR"] = model_dir

        cfg = {
            "output_format": "markdown",
            "use_llm": False,
            "model_dir": model_dir,
        }
        config_parser = ConfigParser(cfg)
        converter_cls = config_parser.get_converter_cls()
        models = create_model_dict()
        converter = converter_cls(
            config=config_parser.generate_config_dict(),
            artifact_dict=models,
            processor_list=config_parser.get_processors(),
            renderer=config_parser.get_renderer(),
            llm_service=config_parser.get_llm_service(),
        )
        rendered = converter(BytesIO(data))
        try:
            return rendered.markdown
        except AttributeError:
            text, _, _ = text_from_rendered(rendered)
            return text

    markdown_text = await loop.run_in_executor(None, _convert_to_markdown, content)

    # 4) 将 markdown 存入 Minio
    md_object_name = f"markdown/{safe_name}.md"
    markdown_bytes = (markdown_text or "").encode("utf-8")
    markdown_path = await loop.run_in_executor(None, upload_bytes, markdown_bytes, md_object_name, "text/markdown")

    # 5) 调用 workflow/run，输入为 markdown 全文
    async with await _client() as client:
        run_url = f"{settings.WORKFLOW_BASE_URL.rstrip('/')}{settings.WORKFLOW_RUN_PATH}"
        payload = {
            "inputs": {
                "query": markdown_text or "",
            },
            "response_mode": "blocking",
        }
        try:
            resp = await client.post(run_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("workflow run failed %s", run_url)
            return {
                "text": f"[PARSE-ERROR] workflow 调用失败",
                "upload_file_id": None,
                "ai_result": None,
                "pdf_path": pdf_path,
                "markdown_path": markdown_path,
            }

    # 从返回结果中抽取文本（逻辑保持不变）
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

    # text 为字符串化的 json，将其序列化为 json
    ai_result = None
    if isinstance(text, str):
        try:
            ai_result = json.loads(text)
        except Exception:
            logger.warning("unable to serialize text to json for file %s", file_url)

    logger.info(
        "parse result text_len=%s ai_result_present=%s pdf_path=%s markdown_path=%s",
        (len(text) if isinstance(text, str) else 0),
        bool(ai_result),
        pdf_path,
        markdown_path,
    )

    return {
        "text": text,
        "upload_file_id": None,
        "ai_result": ai_result,
        "pdf_path": pdf_path,
        "markdown_path": markdown_path,
    }


