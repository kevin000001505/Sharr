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
