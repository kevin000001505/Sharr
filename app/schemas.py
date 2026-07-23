from typing import Optional

from pydantic import BaseModel


class Peer(BaseModel):
    name: str
    tunnel_ip: str
    online: bool


class TransferRequest(BaseModel):
    target_peer_ip: str
    source_path: str
    dest_path: str


class TransferJob(BaseModel):
    job_id: str
    target_peer_ip: str
    source_path: str
    dest_path: str
    status: str
    progress: float = 0
    bytes_sent: int = 0
    total_bytes: int = 0
    speed: str = ""
    error: str = ""
    created_at: str
    updated_at: str


class ProgressEvent(BaseModel):
    job_id: str
    progress: float
    bytes_sent: int
    speed: str
    status: str


class LibraryRequest(BaseModel):
    """Received by the OWNER from a peer — 'send me this media'."""
    kind: str                                # movie | episode | season | series
    id: str                                  # folder name under the owner's movie/tv root
    season: Optional[int] = None
    episode_path: Optional[str] = None       # path relative to the owner's TV root
    dest_base: str                           # category root on the REQUESTER's machine


class RemoteRequest(BaseModel):
    """Sent by the REQUESTER's browser to its own Sharr to pull from a peer."""
    kind: str
    id: str
    season: Optional[int] = None
    episode_path: Optional[str] = None
