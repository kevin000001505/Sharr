import sys
import time
import traceback
from datetime import datetime, timezone

import pika.exceptions

from app.queue import consume_jobs
from app.redis_client import save_job, publish_progress
from app.schemas import ProgressEvent, TransferJob
from app.transfer import run_transfer


def handle_job(job: TransferJob) -> None:
    try:
        run_transfer(job)
    except Exception:
        job.error = str(sys.exc_info()[1])
        job.status = "failed"
        job.updated_at = datetime.now(timezone.utc).isoformat()
        save_job(job)
        publish_progress(ProgressEvent(
            job_id=job.job_id,
            progress=job.progress,
            bytes_sent=job.bytes_sent,
            speed=job.speed,
            status="failed",
        ))
        traceback.print_exc()
        raise


def main() -> None:
    print(f"Worker starting on Python {sys.version}", flush=True)
    # `depends_on` only waits for RabbitMQ's container to start, not for the
    # broker to accept connections. Retry with backoff so the worker rides out
    # that startup window instead of crash-looping on the restart policy.
    deadline = time.monotonic() + 120
    while True:
        try:
            consume_jobs(handle_job)
            return
        except pika.exceptions.AMQPConnectionError:
            if time.monotonic() >= deadline:
                raise
            print("RabbitMQ not ready yet, retrying in 3s…", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
