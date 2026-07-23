"""Requester-side proxy: browse a peer's library and pull media from it.

The browser only ever talks to its own Sharr (one origin). These routes forward
to the peer's Sharr over the WireGuard tunnel, gated by tunnel-IP identity.
"""
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import settings
from app.peers import is_valid_peer
from app.schemas import RemoteRequest

router = APIRouter(prefix="/api/remote", tags=["remote"])


def _peer_base(peer_ip: str) -> str:
    if not is_valid_peer(peer_ip):
        raise HTTPException(status_code=404, detail="Unknown peer")
    return f"http://{peer_ip}:{settings.sharr_peer_port}"


def _proxy_get(peer_ip: str, path: str):
    base = _peer_base(peer_ip)
    try:
        r = httpx.get(base + path, timeout=settings.http_timeout)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code,
                            detail=f"Peer error: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Peer unreachable: {e}")


@router.get("/{peer_ip}/movies")
def remote_movies(peer_ip: str):
    return _proxy_get(peer_ip, "/api/library/movies")


@router.get("/{peer_ip}/shows")
def remote_shows(peer_ip: str):
    return _proxy_get(peer_ip, "/api/library/shows")


@router.get("/{peer_ip}/shows/{show_id}")
def remote_show(peer_ip: str, show_id: str):
    return _proxy_get(peer_ip, f"/api/library/shows/{show_id}")


@router.post("/{peer_ip}/request")
def remote_request(peer_ip: str, body: RemoteRequest):
    base = _peer_base(peer_ip)
    # Destination is chosen here (on the requester) from our own config, so the
    # peer never picks where files land on our disk. Requested media lands in
    # our own library folders and shows up in our library on the next browse.
    dest_base = settings.movies_dir if body.kind == "movie" else settings.tv_dir
    payload = {
        "kind": body.kind,
        "id": body.id,
        "season": body.season,
        "episode_path": body.episode_path,
        "dest_base": dest_base,
    }
    try:
        r = httpx.post(base + "/api/library/request", json=payload,
                       timeout=settings.http_timeout)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.json().get("detail", str(e)) if e.response.content else str(e)
        raise HTTPException(status_code=e.response.status_code, detail=detail)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Peer unreachable: {e}")


@router.get("/{peer_ip}/transfers/{job_id}")
def remote_job(peer_ip: str, job_id: str):
    return _proxy_get(peer_ip, f"/api/transfers/{job_id}")


@router.get("/{peer_ip}/transfers/{job_id}/stream")
async def remote_stream(peer_ip: str, job_id: str):
    base = _peer_base(peer_ip)

    async def gen():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET", base + f"/api/transfers/{job_id}/stream"
                ) as resp:
                    async for chunk in resp.aiter_raw():
                        yield chunk
        except httpx.HTTPError:
            return

    return StreamingResponse(gen(), media_type="text/event-stream")
