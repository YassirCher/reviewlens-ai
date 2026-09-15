from __future__ import annotations

from functools import lru_cache

from redis import Redis

from app.config import settings


class RedisConfigurationError(RuntimeError):
    pass


@lru_cache
def get_redis() -> Redis:
    if not settings.redis_url:
        raise RedisConfigurationError("REDIS_URL is required for V2 services")
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=30,
    )
