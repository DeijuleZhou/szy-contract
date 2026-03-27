import asyncio
import json
import logging
from datetime import datetime
from typing import Dict
from .config import settings
from . import db
from . import services_external as external

logger = logging.getLogger("app.worker")

semaphore = asyncio.Semaphore(settings.CONCURRENCY)

async def process_single(contract: Dict):
    contract_id = contract.get("Code") or contract.get("code") or contract.get("Name")
    logger.info("start processing contract %s", contract_id)
    await db.aupsert_contract(contract)
    logger.debug("upserted contract %s", contract_id)
    await db.aupdate_status(contract_id, "fetching_link")
    try:
        # 获取文件链接：优先使用 ContractAttachment 字段（可能为逗号分隔的附件名列表），否则回退到 file_id/file_name
        attachments = []
        att_field = contract.get("ContractAttachment") or contract.get("contract_attachment")
        if att_field:
            if isinstance(att_field, str):
                attachments = [a.strip() for a in att_field.split(",") if a.strip()]
            elif isinstance(att_field, list):
                attachments = att_field
        # fallback
        if not attachments:
            fid = contract.get("file_id") or contract.get("fileName") or contract.get("file_name")
            if fid:
                attachments = [fid]

        logger.info("fetching file url for %s attachments=%s", contract_id, attachments)
        file_meta_map = await external.get_file_url(attachments)
        logger.info("file_meta_map for %s: %s", contract_id, file_meta_map)
        # 取第一个有效的 download_url
        file_url = None
        preview_url = None
        for name, meta in (file_meta_map.items() if file_meta_map else []):
            file_url = meta.get("download_url") or meta.get("DownUrl")
            preview_url = meta.get("preview_url") or meta.get("PreviewUrl")
            if file_url:
                break
        if not file_url:
            raise RuntimeError(f"no file url for contract {contract_id}")
        logger.info("found file_url for %s: %s", contract_id, file_url)
        await db.aupdate_status(contract_id, "parsing", file_url=file_url, preview_url=preview_url)

        # 调用解析服务 B（先上传再触发 workflow），得到文本与 upload_file_id
        logger.info("calling parse service for %s", contract_id)
        parse_res = await external.parse_file_by_serviceB(file_url, contract_code=contract.get("Code"))
        if isinstance(parse_res, dict):
            text = parse_res.get("text")
            upload_id = parse_res.get("upload_file_id")
            pdf_path = parse_res.get("pdf_path")
        else:
            text = parse_res
            upload_id = None
            pdf_path = None
        logger.info("parse result for %s upload_id=%s text_len=%s", contract_id, upload_id, (len(text) if text else 0))
        await db.aupdate_status(
            contract_id,
            "ai_pending",
            # parse_text=text,
            file_upload_id=upload_id,
            pdf_path=pdf_path,
        )

        # 解析服务已可能返回结构化 ai_result，直接保存；若无则保持为空
        ai_result = None
        overview_text = None
        signing_date = None
        project_category = None
        if isinstance(parse_res, dict):
            ai_result = parse_res.get("ai_result")
            if isinstance(ai_result, dict):
                overview_text = _serialize_contract_overview(ai_result.get("contract_overview"))
                signing_date = _normalize_signing_date(ai_result.get("signing_date"))
                project_category = _clean_text(ai_result.get("project_category"))
        logger.info("saving ai_result for %s", contract_id)
        await db.aupdate_status(
            contract_id,
            "done",
            ai_result=ai_result,
            contract_overview=overview_text,
            signing_date=signing_date,
            project_category=project_category,
        )
        logger.info("finished processing contract %s", contract_id)
    except Exception as e:
        logger.exception("error processing contract %s: %s", contract_id, e)
        await db.aupdate_status(contract_id, "failed", last_error=str(e), increment_attempt=True)


async def _worker_task(item: Dict):
    async with semaphore:
        await process_single(item)


async def process_contracts(limit: int = 100, query: Dict | None = None):
    # 拉取合同列表（分页/替换为真实接口）
    items = await external.list_contracts(limit, query=query)
    logger.info("fetched %d contracts to process", len(items))
    # upsert 并并发处理
    tasks = [asyncio.create_task(_worker_task(it)) for it in items]
    await asyncio.gather(*tasks)


async def retry_contract(contract_id: str):
    """Fetch contract from DB and re-process it once."""
    item = await db.aget_contract(contract_id)
    if not item:
        # nothing to do
        logger.warning("retry requested but contract not found: %s", contract_id)
        return False
    # set to pending so it shows up in status
    logger.info("retrying contract %s", contract_id)
    await db.aupdate_status(contract_id, "pending")
    # process single contract
    await process_single(item)
    return True


# helper to be used by API background
def start_background(loop, limit: int = 100, query: Dict | None = None):
    return loop.create_task(process_contracts(limit, query=query))


if __name__ == "__main__":
    import asyncio

    async def main():
        await db.ainit_db()
        await process_contracts(20)
        print("done")

    asyncio.run(main())


def _serialize_contract_overview(data):
    if not isinstance(data, dict):
        return None
    preferred_order = [
        "项目工程规模",
        "估算总投资",
        "工程建安费",
        "工程设计费",
        "支付方式",
    ]
    parts = []
    for key in preferred_order:
        value = data.get(key)
        if value:
            parts.append(f"{key}:{str(value).strip()}")
    for key, value in data.items():
        if key in preferred_order:
            continue
        if value:
            parts.append(f"{key}:{str(value).strip()}")
    return " | ".join(parts) if parts else None


def _normalize_signing_date(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    candidates = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"]
    for fmt in candidates:
        try:
            return datetime.strptime(text[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _clean_text(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
