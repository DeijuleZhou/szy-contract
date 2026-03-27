import io
from typing import Optional

from minio import Minio
from minio.error import S3Error

from .config import settings
import logging

logger = logging.getLogger("app.storage_minio")

_client: Optional[Minio] = None


def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        # 确保桶存在
        found = _client.bucket_exists(settings.MINIO_BUCKET)
        if not found:
            _client.make_bucket(settings.MINIO_BUCKET)
            logger.info("created minio bucket %s", settings.MINIO_BUCKET)
    return _client


def _object_url(object_name: str) -> str:
    scheme = "https" if settings.MINIO_SECURE else "http"
    return f"{scheme}://{settings.MINIO_ENDPOINT}/{settings.MINIO_BUCKET}/{object_name}"


def upload_bytes(data: bytes, object_name: str, content_type: str) -> str:
    """同步上传字节到 Minio，返回可访问 URL（或路径）。"""
    client = get_client()
    data_stream = io.BytesIO(data)
    size = len(data)
    try:
        client.put_object(
            settings.MINIO_BUCKET,
            object_name,
            data_stream,
            length=size,
            content_type=content_type,
        )
        url = _object_url(object_name)
        # logger.info("uploaded object %s to minio -> %s", object_name, url)
        return url
    except S3Error:
        logger.exception("failed to upload %s to minio", object_name)
        raise
