from pydantic_settings import BaseSettings
import logging
import sys
import os
from logging.handlers import RotatingFileHandler


class Settings(BaseSettings):
    # 数据库默认路径（可在运行时覆盖）
    DB_PATH: str = "data/contracts.db"

    # 并发与重试
    CONCURRENCY: int = 10
    RETRY_LIMIT: int = 3

    # 外部服务地址（可通过环境变量覆盖）
    MARKET_BASE_URL: str = "http://10.40.84.88:8050"
    MARKET_CONTRACTS_PATH: str = "/Market/Basic/ManageContract/GetContractPerformenceList"

    FILE_BASE_URL: str = "http://10.40.84.88:8050"
    FILE_GET_PATH: str = "/MvcConfig/ViewFile/GetFileUrl"

    # 解析/上传服务（workflow）
    UPLOAD_BASE_URL: str = "http://10.40.88.55:1801/api/v1"
    UPLOAD_PATH: str = "/file/upload"
    WORKFLOW_BASE_URL: str = "http://10.40.88.55:1801/api/v1"
    WORKFLOW_RUN_PATH: str = "/workflows/run"

    # 可选的 API key，用于需要鉴权的外部服务
    EXTERNAL_API_KEY: str = "wf-uAG414wnjtxXtEXpOZ7U0fKw"

    # HTTP 超时（秒）
    HTTP_TIMEOUT: int = 30

    # logging
    LOG_LEVEL: str = "INFO"
    LOG_PATH: str = "C:\\File\\PycharmProjects\\szy-contract\\logs\\serviceA.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5


settings = Settings()



