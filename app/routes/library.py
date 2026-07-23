import posixpath

from fastapi import APIRouter, HTTPException, Request

from app import library
from app.peers import is_valid_peer
from app.queue import publish_job
from app.redis_client import save_job
from app.schemas import LibraryRequest
from app.transfer import new_outgoing_job

router = APIRouter(prefix="/api/library", tags=["library"])


# ---- Read this node's own library (also called by peers via their proxy) ----

@router.get("/movies")
def get_movies():
    try:
        return library.list_movies()
    except library.LibraryError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/shows")
def get_shows():
    try:
        return library.list_shows()
    except library.LibraryError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/shows/{show_id}")
def get_show(show_id: str):
    try:
        return library.show_detail(show_id)
    except library.LibraryError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ---- Accept a pull request from a peer and queue the transfer ----

@router.post("/request")
def request_item(req: LibraryRequest, request: Request):
    # Identity = the WireGuard tunnel IP this request arrived from.
    requester_ip = request.client.host if request.client else ""
    if not is_valid_peer(requester_ip):
        raise HTTPException(status_code=403, detail="Unknown peer")

    try:
        src, dest_rel = library.resolve_request(
            req.kind, req.id, req.season, req.episode_path
        )
    except library.LibraryError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # One rsync job for the whole folder (or single episode file). Trailing
    # slash: dest is the directory to copy INTO, mirroring the owner's layout.
    dest = posixpath.join(req.dest_base, dest_rel) + "/"
    try:
        job = new_outgoing_job(requester_ip, src, dest)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    save_job(job)
    publish_job(job)
    return {"job_ids": [job.job_id], "count": 1}
