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
    EXTERNAL_API_KEY: str = "wf-8TFne5bCrsWTJ9Ha6JDZ5Dqz"

    # HTTP 超时（秒）
    HTTP_TIMEOUT: int = 30

    # logging
    LOG_LEVEL: str = "INFO"
    LOG_PATH: str = "serviceA.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5

    # Minio 存储配置
    MINIO_ENDPOINT: str = "127.0.0.1:9000"  # 可通过环境变量覆盖
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "contracts"
    MINIO_SECURE: bool = False

    # External parser endpoint (used by /parser/v1/parser/url)
    PARSER_API_URL: str = "http://10.40.88.55:31838/parser/v1/parser"

    # MySQL 配置（若启用 MySQL 则使用 MySQL；否则保留 SQLite）
    USE_MYSQL: bool = True
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "whsz"
    MYSQL_PASSWORD: str = "whsz"
    MYSQL_DB: str = "szy_contracts"


settings = Settings()



