import sys
import traceback
from datetime import datetime, timezone

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
    consume_jobs(handle_job)


if __name__ == "__main__":
    main()
