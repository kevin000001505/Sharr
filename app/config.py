import os
from functools import lru_cache
from typing import Optional


class Settings:
    redis_host: str = os.getenv("REDIS_HOST", "localhost")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_db: int = int(os.getenv("REDIS_DB", "0"))

    rabbitmq_host: str = os.getenv("RABBITMQ_HOST", "localhost")
    rabbitmq_port: int = int(os.getenv("RABBITMQ_PORT", "5672"))
    rabbitmq_queue: str = os.getenv("RABBITMQ_QUEUE", "transfers")
    rabbitmq_user: str = os.getenv("RABBITMQ_USER", "guest")
    rabbitmq_password: str = os.getenv("RABBITMQ_PASSWORD", "guest")

    wg_conf_path: str = os.getenv("WG_CONF_PATH", "/etc/wireguard/wg0.conf")
    ssh_key_path: str = os.getenv("SSH_KEY_PATH", "/root/.ssh/wg_peer_key")
    ssh_port: int = int(os.getenv("SSH_PORT", "22"))

    allowed_base_dir: str = os.getenv("ALLOWED_BASE_DIR", "/data")
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")

    ping_timeout: float = float(os.getenv("PING_TIMEOUT", "2.0"))


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
