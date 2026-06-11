# Backend Development Plan — Secure P2P Large File Transfer

> **Audience:** A developer who follows this guide step-by-step. Every function below lists its **file**, **signature**, **purpose**, **inputs**, **outputs**, and **how it connects** to the rest of the system. Build them in the order given. Do not skip the "Connects to" notes — that is how the parts wire together.

---

## 1. What We Are Building (Plain Description)

Three trusted friends each run the **complete, identical stack** on their own machine. There is **no central server**. When Friend A sends a file to Friend B:

1. The browser tells **A's FastAPI** to start a transfer.
2. FastAPI drops a **job** into **RabbitMQ**.
3. A **worker** picks up the job and runs **rsync over SSH**, going **directly to B** through the **WireGuard** tunnel.
4. The worker publishes **progress** to **Redis**.
5. The browser watches progress live through an **SSE** stream from FastAPI.

Identity = the sender's WireGuard tunnel IP. No login, no user table.

---

## 2. System Components (Already Decided)

| Component | Role |
|-----------|------|
| WireGuard | Encrypted mesh tunnel between the 3 peers (own Docker container, holds `NET_ADMIN`) |
| FastAPI | HTTP API + SSE endpoints |
| RabbitMQ | Durable job queue (transfer requests) |
| Redis | Live progress pub/sub + transient job state |
| rsync over SSH | The actual file movement (key-only auth) |
| Worker | Long-running process that consumes jobs and runs rsync |

```
Browser ──HTTP──▶ FastAPI ──publish──▶ RabbitMQ ──consume──▶ Worker
   ▲                  ▲                                          │
   └────SSE───────────┘◀────read progress──── Redis ◀──publish──┘
                                                                 │
                                              rsync/SSH over WireGuard ──▶ Remote Peer
```

---

## 3. File / Module Layout

```
app/
├── main.py            # FastAPI app + route registration (entrypoint)
├── config.py          # Settings (env vars only) — ALREADY EXISTS
├── schemas.py         # Pydantic request/response models — PARTIALLY EXISTS
├── filesystem.py      # Folder/file browsing logic — ALREADY EXISTS
├── routes/
│   ├── filesystem.py  # /api/filesystem endpoints — ALREADY EXISTS
│   ├── transfers.py   # /api/transfers endpoints (NEW)
│   └── peers.py       # /api/peers endpoints (NEW)
├── queue.py           # RabbitMQ connection + publish/consume helpers (NEW)
├── redis_client.py    # Redis connection + progress pub/sub helpers (NEW)
├── transfer.py        # Job model + rsync command builder + state helpers (NEW)
├── worker.py          # Standalone worker process (NEW)
└── peers.py           # Peer registry from wg0.conf (NEW)
```

`models.py` stays reserved for a future DB layer. We are **not** adding one.

---

## 4. The API We Expose

### 4.1 Filesystem (already built — listed for completeness)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/filesystem` | Browse folders (sorted, non-hidden, parent nav) |
| GET | `/api/filesystem/files` | List files in a folder (name, path, size) |

### 4.2 Peers (NEW)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/peers` | List the other 2 peers (name, tunnel IP, online status) |

### 4.3 Transfers (NEW — the core)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/transfers` | Start a transfer. Returns a `job_id` |
| GET | `/api/transfers` | List recent/active transfers |
| GET | `/api/transfers/{job_id}` | Get one transfer's current state |
| GET | `/api/transfers/{job_id}/stream` | **SSE** live progress stream |
| POST | `/api/transfers/{job_id}/cancel` | Request cancellation |

---

## 5. Data Models (`schemas.py`)

Build these first — everything else references them.

```python
class Peer(BaseModel):
    name: str            # friendly label, e.g. "alice"
    tunnel_ip: str       # WireGuard IP, e.g. "10.0.0.2"
    online: bool         # last-known reachability

class TransferRequest(BaseModel):
    target_peer_ip: str  # which peer to send to
    source_path: str     # absolute path on THIS machine
    dest_path: str       # absolute path on the TARGET machine

class TransferJob(BaseModel):
    job_id: str
    target_peer_ip: str
    source_path: str
    dest_path: str
    status: str          # queued | running | completed | failed | cancelled
    progress: float = 0  # 0–100
    bytes_sent: int = 0
    total_bytes: int = 0
    speed: str = ""      # human string, e.g. "11.2MB/s"
    error: str = ""      # populated on failure
    created_at: str
    updated_at: str

class ProgressEvent(BaseModel):
    job_id: str
    progress: float
    bytes_sent: int
    speed: str
    status: str
```

---

## 6. Every Function You Must Write

> Follow this list top-to-bottom. Each one is small. Once all exist and are wired per "Connects to", the system works end to end.

### 6.1 `redis_client.py` — progress backbone

| Function | Signature | Purpose | Connects to |
|----------|-----------|---------|-------------|
| `get_redis()` | `() -> redis.Redis` | Create/return a single shared Redis connection using `config`. | Used by everything that touches Redis. |
| `save_job(job)` | `(job: TransferJob) -> None` | Write full job state to a Redis key `job:{job_id}` (JSON), with a TTL. | Called by `main` POST handler and by the worker on every update. |
| `load_job(job_id)` | `(job_id: str) -> TransferJob \| None` | Read and parse one job from Redis. | Called by GET `/transfers/{id}` and the SSE handler. |
| `list_jobs()` | `() -> list[TransferJob]` | Scan `job:*` keys and return all jobs, newest first. | Called by GET `/transfers`. |
| `publish_progress(event)` | `(event: ProgressEvent) -> None` | Publish a JSON progress event to Redis channel `progress:{job_id}`. | Called by the worker during rsync. |
| `subscribe_progress(job_id)` | `(job_id: str) -> AsyncIterator[ProgressEvent]` | Async generator that yields progress events for one job until a terminal status. | Consumed by the SSE endpoint. |

### 6.2 `queue.py` — job dispatch

| Function | Signature | Purpose | Connects to |
|----------|-----------|---------|-------------|
| `get_channel()` | `() -> Channel` | Open/return a RabbitMQ channel; declare a **durable** queue `transfers`. | Used by publisher and consumer. |
| `publish_job(job)` | `(job: TransferJob) -> None` | Serialize the job to JSON and publish it to the `transfers` queue (persistent message). | Called by POST `/transfers` after `save_job`. |
| `consume_jobs(handler)` | `(handler: Callable[[TransferJob], None]) -> None` | Block forever, pull messages, deserialize into `TransferJob`, call `handler`, ack only on success. | Called once by `worker.py` at startup. |

### 6.3 `peers.py` — who can I send to

| Function | Signature | Purpose | Connects to |
|----------|-----------|---------|-------------|
| `parse_wg_peers()` | `() -> list[Peer]` | Read `wg0.conf`, extract each `[Peer]` block's allowed IP, return peers (excluding self). | Called by `list_peers`. |
| `ping_peer(ip)` | `(ip: str) -> bool` | Check reachability over the tunnel (ping or a TCP probe to the SSH port). | Called by `list_peers` to set `online`. |
| `list_peers()` | `() -> list[Peer]` | Combine parse + ping into the response list. | Called by GET `/api/peers`. |
| `is_valid_peer(ip)` | `(ip: str) -> bool` | Confirm a target IP is one of our known peers. **Security gate.** | Called by POST `/transfers` before queuing — rejects unknown targets. |

### 6.4 `transfer.py` — job lifecycle + rsync

| Function | Signature | Purpose | Connects to |
|----------|-----------|---------|-------------|
| `new_job(req)` | `(req: TransferRequest) -> TransferJob` | Create a `TransferJob`: generate `job_id` (uuid4), set `status="queued"`, timestamps. | Called by POST `/transfers`. |
| `build_rsync_command(job)` | `(job: TransferJob) -> list[str]` | Build the rsync argv: `rsync -a --info=progress2 --partial -e "ssh -i <key> -o BatchMode=yes" <src> <peer_ip>:<dest>`. Return as a **list** (never shell string) to avoid injection. | Called by `run_transfer`. |
| `parse_rsync_progress(line)` | `(line: str) -> ProgressEvent \| None` | Parse one rsync `--info=progress2` stdout line into percent / bytes / speed. Return `None` for non-progress lines. | Called inside `run_transfer`'s read loop. |
| `run_transfer(job)` | `(job: TransferJob) -> None` | The heart. Mark `running`; spawn the rsync subprocess; read stdout line-by-line; for each parsed event update the job and call `save_job` + `publish_progress`; on exit set `completed`/`failed` and persist. Watch the cancel flag. | Called by the worker's handler. |
| `request_cancel(job_id)` | `(job_id: str) -> None` | Set a Redis flag `cancel:{job_id}` that `run_transfer` polls. | Called by POST `/transfers/{id}/cancel`. |
| `is_cancelled(job_id)` | `(job_id: str) -> bool` | Read the cancel flag. | Polled inside `run_transfer`. |

### 6.5 `worker.py` — the consumer process

| Function | Signature | Purpose | Connects to |
|----------|-----------|---------|-------------|
| `handle_job(job)` | `(job: TransferJob) -> None` | Thin wrapper: call `run_transfer(job)`; let exceptions mark the job failed and re-raise so the message is not acked blindly. | Passed into `consume_jobs`. |
| `main()` | `() -> None` | Entrypoint: connect Redis + RabbitMQ, then `consume_jobs(handle_job)`. Runs forever in its own container. | The worker container's command. |

### 6.6 `routes/transfers.py` — HTTP surface

| Function (route handler) | Method/Path | Purpose | Connects to |
|--------------------------|-------------|---------|-------------|
| `create_transfer(req)` | POST `/api/transfers` | Validate target with `is_valid_peer`; `new_job` → `save_job` → `publish_job`; return `job_id`. | Entry point for starting work. |
| `list_transfers()` | GET `/api/transfers` | Return `list_jobs()`. | Dashboard. |
| `get_transfer(job_id)` | GET `/api/transfers/{job_id}` | Return `load_job(job_id)` or 404. | Polling fallback. |
| `stream_transfer(job_id)` | GET `/api/transfers/{job_id}/stream` | Return a `StreamingResponse` (media type `text/event-stream`) that iterates `subscribe_progress(job_id)` and formats each as `data: {json}\n\n`. | Live UI bar. |
| `cancel_transfer(job_id)` | POST `/api/transfers/{job_id}/cancel` | Call `request_cancel(job_id)`; return acknowledgement. | Cancel button. |

### 6.7 `routes/peers.py`

| Function | Method/Path | Purpose | Connects to |
|----------|-------------|---------|-------------|
| `get_peers()` | GET `/api/peers` | Return `list_peers()`. | Target dropdown in UI. |

### 6.8 `main.py`

| Function | Purpose |
|----------|---------|
| `create_app()` | Build the FastAPI app, include the three route routers (`filesystem`, `peers`, `transfers`), configure CORS for the local browser, expose `app`. |

---

## 7. End-to-End Flow (Trace One Transfer)

1. **UI** GET `/api/peers` → `get_peers` → `list_peers` → `parse_wg_peers` + `ping_peer`.
2. **UI** GET `/api/filesystem/files` to pick a source file.
3. **UI** POST `/api/transfers` → `create_transfer`:
   - `is_valid_peer(target)` ✅
   - `new_job(req)` → `save_job` (Redis) → `publish_job` (RabbitMQ) → returns `job_id`.
4. **UI** opens GET `/api/transfers/{job_id}/stream` → `stream_transfer` → `subscribe_progress`.
5. **Worker** `consume_jobs` delivers the job → `handle_job` → `run_transfer`:
   - `build_rsync_command` → subprocess → read loop → `parse_rsync_progress` → `save_job` + `publish_progress` each tick.
   - Polls `is_cancelled`.
   - On finish: status `completed`/`failed`, final `save_job` + `publish_progress`.
6. **UI** receives terminal event over SSE, closes the stream.

---

## 8. Build Order Checklist

1. [ ] `schemas.py` models (Section 5)
2. [ ] `redis_client.py` (6.1)
3. [ ] `queue.py` (6.2)
4. [ ] `peers.py` (6.3)
5. [ ] `transfer.py` (6.4)
6. [ ] `worker.py` (6.5)
7. [ ] `routes/peers.py` (6.7) + `routes/transfers.py` (6.6)
8. [ ] `main.py` wiring (6.8)
9. [ ] Tests: unit (`parse_rsync_progress`, `build_rsync_command`, `is_valid_peer`) + `TestClient` HTTP tests
10. [ ] `docker-compose.yml`: services for `api`, `worker` (`network_mode: "service:wireguard"`), `wireguard`, `rabbitmq`, `redis`

---

## 9. Security Rules (Non-Negotiable)

- **Validate the target peer** (`is_valid_peer`) before queuing anything. Never rsync to an arbitrary IP.
- **rsync command must be an argv list**, never a shell string — prevents path/command injection.
- SSH is **key-only**, `BatchMode=yes` (no password prompt, fails fast).
- `NET_ADMIN` stays in the WireGuard container only; the worker shares its netns via `network_mode: "service:wireguard"`.
- Treat `source_path` / `dest_path` as untrusted: confine them to an allowed base directory before use.
