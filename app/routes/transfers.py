from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.peers import is_valid_peer
from app.queue import publish_job
from app.redis_client import (
    save_job,
    load_job,
    list_jobs,
    subscribe_progress,
    request_cancel,
)
from app.schemas import TransferRequest
from app.transfer import new_job

router = APIRouter(prefix="/api/transfers", tags=["transfers"])


@router.post("")
def create_transfer(req: TransferRequest):
    if not is_valid_peer(req.target_peer_ip):
        raise HTTPException(status_code=400, detail="Invalid target peer")

    try:
        job = new_job(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    save_job(job)
    publish_job(job)
    return {"job_id": job.job_id}


@router.get("")
def list_transfers():
    jobs = list_jobs()
    return [j.model_dump() for j in jobs]


@router.get("/{job_id}")
def get_transfer(job_id: str):
    job = load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.model_dump()


@router.get("/{job_id}/stream")
async def stream_transfer(job_id: str):
    async def event_stream():
        async for event in subscribe_progress(job_id):
            yield f"data: {event.model_dump_json()}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )


@router.post("/{job_id}/cancel")
def cancel_transfer(job_id: str):
    request_cancel(job_id)
    return {"status": "requested"}
