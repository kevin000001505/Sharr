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
    ssh_port: int = int(os.getenv("SSH_PORT", "2222"))
    ssh_user: str = os.getenv("SSH_USER", "sharr")  # login user on the receiving peer

    allowed_base_dir: str = os.getenv("ALLOWED_BASE_DIR", "/data")
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")

    ping_timeout: float = float(os.getenv("PING_TIMEOUT", "2.0"))

    # Media library roots on THIS node (under allowed_base_dir). Both browsing
    # and incoming transfers use these, so pulled media lands in the library.
    movies_dir: str = os.getenv("MOVIES_DIR", "/data/movie")
    tv_dir: str = os.getenv("TV_DIR", "/data/tv_show/tv")

    # TMDB API key (v3 auth) for poster/overview lookups — themoviedb.org →
    # Settings → API. Optional: without it the library works, minus posters.
    tmdb_api_key: str = os.getenv("TMDB_API_KEY", "")

    # Port each peer's Sharr API listens on, reachable over the WireGuard tunnel
    sharr_peer_port: int = int(os.getenv("SHARR_PEER_PORT", "8000"))
    http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "15.0"))


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
