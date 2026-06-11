from typing import AsyncIterator, Optional

import redis
import redis.asyncio as aioredis

from app.config import settings
from app.schemas import ProgressEvent, TransferJob

# Synchronous client — used by the worker (blocking rsync loop) and the HTTP
# request handlers. Pub/sub streaming needs async iteration, so the SSE
# endpoint uses the separate async client below.
_redis: Optional[redis.Redis] = None
_async_redis: Optional[aioredis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True,
            socket_connect_timeout=5,
        )
    return _redis


def get_async_redis() -> aioredis.Redis:
    global _async_redis
    if _async_redis is None:
        _async_redis = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            decode_responses=True,
            socket_connect_timeout=5,
        )
    return _async_redis


def save_job(job: TransferJob) -> None:
    r = get_redis()
    r.set(f"job:{job.job_id}", job.model_dump_json(), ex=86400)


def load_job(job_id: str) -> Optional[TransferJob]:
    r = get_redis()
    data = r.get(f"job:{job_id}")
    if data is None:
        return None
    return TransferJob.model_validate_json(data)


def list_jobs() -> list[TransferJob]:
    r = get_redis()
    jobs = []
    for key in r.scan_iter(match="job:*", count=100):
        data = r.get(key)
        if data:
            try:
                jobs.append(TransferJob.model_validate_json(data))
            except Exception:
                pass
    jobs.sort(key=lambda j: j.updated_at, reverse=True)
    return jobs


def publish_progress(event: ProgressEvent) -> None:
    r = get_redis()
    r.publish(f"progress:{event.job_id}", event.model_dump_json())


def request_cancel(job_id: str) -> None:
    r = get_redis()
    r.set(f"cancel:{job_id}", "1", ex=3600)


def is_cancelled(job_id: str) -> bool:
    r = get_redis()
    return r.exists(f"cancel:{job_id}") > 0


async def subscribe_progress(job_id: str) -> AsyncIterator[ProgressEvent]:
    # If the job already finished before the client subscribed, pub/sub has no
    # replay — emit the persisted terminal state and stop.
    initial = load_job(job_id)
    if initial is not None and initial.status in ("completed", "failed", "cancelled"):
        yield ProgressEvent(
            job_id=job_id,
            progress=initial.progress,
            bytes_sent=initial.bytes_sent,
            speed=initial.speed,
            status=initial.status,
        )
        return

    r = get_async_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(f"progress:{job_id}")
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=30.0
            )
            if message is None:
                # No live event for 30s. Re-check persisted state in case the
                # job finished in the gap between our initial load and the
                # subscribe (pub/sub does not replay missed messages).
                current = load_job(job_id)
                if current is not None and current.status in (
                    "completed",
                    "failed",
                    "cancelled",
                ):
                    yield ProgressEvent(
                        job_id=job_id,
                        progress=current.progress,
                        bytes_sent=current.bytes_sent,
                        speed=current.speed,
                        status=current.status,
                    )
                    return
                # Otherwise emit a keep-alive so proxies don't drop the stream.
                yield ProgressEvent(
                    job_id=job_id,
                    progress=current.progress if current else 0.0,
                    bytes_sent=current.bytes_sent if current else 0,
                    speed="",
                    status="running",
                )
                continue
            if message["type"] != "message":
                continue
            try:
                event = ProgressEvent.model_validate_json(message["data"])
            except Exception:
                continue
            yield event
            if event.status in ("completed", "failed", "cancelled"):
                return
    finally:
        await pubsub.unsubscribe(f"progress:{job_id}")
        await pubsub.aclose()
