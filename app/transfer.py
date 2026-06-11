import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.redis_client import save_job, publish_progress, is_cancelled
from app.schemas import ProgressEvent, TransferJob


def _validate_path_confinement(path: str, label: str) -> str:
    allowed = Path(settings.allowed_base_dir).resolve()
    resolved = Path(path).resolve()
    # Use is_relative_to (not str.startswith) so "/data" does not match
    # sibling paths like "/database".
    if resolved != allowed and not resolved.is_relative_to(allowed):
        raise ValueError(f"{label} path outside allowed directory: {path}")
    return str(resolved)


def new_job(req) -> TransferJob:
    now = datetime.now(timezone.utc).isoformat()
    return TransferJob(
        job_id=str(uuid.uuid4()),
        target_peer_ip=req.target_peer_ip,
        source_path=_validate_path_confinement(req.source_path, "source"),
        dest_path=_validate_path_confinement(req.dest_path, "dest"),
        status="queued",
        created_at=now,
        updated_at=now,
    )


def build_rsync_command(job: TransferJob) -> list[str]:
    ssh_key = settings.ssh_key_path
    ssh_port = settings.ssh_port
    cmd = [
        "rsync",
        "-a",
        "--info=progress2",
        "--partial",
        "-e",
        f"ssh -i {ssh_key} "
        f"-o BatchMode=yes "
        f"-o StrictHostKeyChecking=no "
        f"-o UserKnownHostsFile=/dev/null "
        f"-p {ssh_port}",
        job.source_path,
        f"{job.target_peer_ip}:{job.dest_path}",
    ]
    return cmd


def parse_rsync_progress(line: str) -> ProgressEvent | None:
    patterns = [
        r"(\d{1,3}(?:,\d{3})*)\s+(\d+)%\s+(\d+(?:\.\d+)?\s*[A-Za-z]+/s)\s+(\d+:\d+:\d+)",
        r"(\d{1,3}(?:,\d{3})*)\s+(\d+)%\s+(\d+(?:\.\d+)?\s*[A-Za-z]+/s)",
    ]
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            groups = match.groups()
            raw_bytes = groups[0].replace(",", "")
            bytes_sent = int(raw_bytes)
            percent = float(groups[1])
            speed = groups[2].strip()
            return ProgressEvent(
                job_id="",
                progress=percent,
                bytes_sent=bytes_sent,
                speed=speed,
                status="running",
            )
    return None


def run_transfer(job: TransferJob) -> None:
    save_job(job)
    job.status = "running"
    job.updated_at = datetime.now(timezone.utc).isoformat()
    save_job(job)

    cmd = build_rsync_command(job)

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        total_bytes = 0

        for line in iter(process.stdout.readline, ""):
            if is_cancelled(job.job_id):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
                job.status = "cancelled"
                job.updated_at = datetime.now(timezone.utc).isoformat()
                save_job(job)
                publish_progress(ProgressEvent(
                    job_id=job.job_id,
                    progress=job.progress,
                    bytes_sent=job.bytes_sent,
                    speed=job.speed,
                    status="cancelled",
                ))
                return

            progress = parse_rsync_progress(line)
            if progress:
                progress.job_id = job.job_id
                total_bytes = max(total_bytes, progress.bytes_sent)
                job.progress = progress.progress
                job.bytes_sent = progress.bytes_sent
                job.speed = progress.speed
                job.updated_at = datetime.now(timezone.utc).isoformat()
                save_job(job)
                publish_progress(progress)

        return_code = process.wait()

        if return_code == 0:
            job.status = "completed"
            job.progress = 100.0
            job.total_bytes = total_bytes
            job.updated_at = datetime.now(timezone.utc).isoformat()
            save_job(job)
            publish_progress(ProgressEvent(
                job_id=job.job_id,
                progress=100.0,
                bytes_sent=total_bytes,
                speed=job.speed,
                status="completed",
            ))
        else:
            job.status = "failed"
            job.error = f"rsync exited with code {return_code}"
            job.total_bytes = total_bytes
            job.updated_at = datetime.now(timezone.utc).isoformat()
            save_job(job)
            publish_progress(ProgressEvent(
                job_id=job.job_id,
                progress=job.progress,
                bytes_sent=total_bytes,
                speed=job.speed,
                status="failed",
            ))

    except FileNotFoundError:
        job.status = "failed"
        job.error = "rsync not found"
        job.updated_at = datetime.now(timezone.utc).isoformat()
        save_job(job)
        raise
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.updated_at = datetime.now(timezone.utc).isoformat()
        save_job(job)
        raise
