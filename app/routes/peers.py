from fastapi import APIRouter

from app.peers import list_peers

router = APIRouter(prefix="/api/peers", tags=["peers"])


@router.get("")
def get_peers():
    peers = list_peers()
    return [p.model_dump() for p in peers]
