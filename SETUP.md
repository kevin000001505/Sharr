# Sharr — Setup Guide

Sharr lets three trusted friends browse each other's **Radarr/Sonarr** libraries
and pull movies/shows directly over a private **WireGuard** tunnel. There is no
central server — every friend runs the same stack and talks peer-to-peer.

This guide goes in order. Do it once per machine (all three friends).

```
You ──browse──▶ your Sharr ──tunnel──▶ friend's Sharr ──▶ friend's Radarr/Sonarr
You ──request─▶ your Sharr ──tunnel──▶ friend's Sharr ──▶ friend's worker rsyncs the file to you
```

---

## 0. Prerequisites

On each of the three machines you need:

- **Docker + Docker Compose**
- **Radarr and/or Sonarr already running**, with your media on disk
- Your media mounted at a consistent path. **This is critical:** Radarr/Sonarr
  report absolute file paths (e.g. `/data/movies/...`), and Sharr rsyncs that
  exact path. Radarr, Sonarr, and Sharr must all see the library at the **same
  path**. Throughout this guide that path is `/data`.

Pick a tunnel IP for each friend up front:

| Friend | Tunnel IP   |
|--------|-------------|
| You    | `10.0.0.1`  |
| Friend B | `10.0.0.2` |
| Friend C | `10.0.0.3` |

---

## 1. WireGuard — build the tunnel

WireGuard is the encrypted mesh that connects the three machines. Each peer has
a keypair; everyone lists everyone else as a `[Peer]`.

### 1a. Generate a keypair on each machine
```bash
wg genkey | tee privatekey | wg pubkey > publickey
```
Keep `privatekey` secret. Share `publickey` with the other two friends.

### 1b. Write `wg-data/wg0.conf` on each machine

Example for **You (10.0.0.1)** — fill in the real keys and the friends'
public IP/port (the machine's real internet address, not the tunnel IP):

```ini
[Interface]
Address = 10.0.0.1/24
PrivateKey = <YOUR_PRIVATE_KEY>
ListenPort = 51820

# Friend B
[Peer]
# alice
PublicKey = <FRIEND_B_PUBLIC_KEY>
AllowedIPs = 10.0.0.2/32
Endpoint = <FRIEND_B_PUBLIC_IP>:51820
PersistentKeepalive = 25

# Friend C
[Peer]
# bob
PublicKey = <FRIEND_C_PUBLIC_KEY>
AllowedIPs = 10.0.0.3/32
Endpoint = <FRIEND_C_PUBLIC_IP>:51820
PersistentKeepalive = 25
```

Notes:
- The `# alice` / `# bob` comment line **is the friendly name** Sharr shows in
  the UI. Put it right above each `[Peer]`.
- Each friend's `wg0.conf` uses *their* address under `[Interface]` and lists
  the *other two* as peers.

### 1c. Verify the tunnel is up

After `docker-compose up` (Step 4) you can check:
```bash
docker exec wireguard wg show          # should list your peers
docker exec wireguard ping -c1 10.0.0.2   # should reach friend B
```
If pings don't cross, fix WireGuard before going further — nothing else works
until the tunnel does.

---

## 2. SSH — let friends receive your files

Transfers are **rsync over SSH**. The `ssh` sidecar (in `docker-compose.yml`)
is the receiving end: it listens on the tunnel at `<your-ip>:2222`, accepts your
friends' keys, and writes into `/data`.

### 2a. Generate a transfer keypair on each machine
```bash
mkdir -p keys
ssh-keygen -t ed25519 -f keys/wg_peer_key -N ""   # no passphrase (BatchMode)
chmod 600 keys/wg_peer_key
```
This creates `keys/wg_peer_key` (private — used to *send*) and
`keys/wg_peer_key.pub` (public — give to friends so they can send *to you*).

### 2b. Collect your friends' public keys into `keys/authorized_keys`

On each machine, put the **other two friends'** `wg_peer_key.pub` contents into
`keys/authorized_keys` (one key per line):
```bash
cat friend_b_wg_peer_key.pub friend_c_wg_peer_key.pub > keys/authorized_keys
```
The `ssh` sidecar loads this file, so anyone holding a matching private key can
rsync into your `/data` as user `sharr` — and nobody else.

> **Why this is safe:** keys only, no passwords (`BatchMode=yes`), and the only
> machines that can even reach port 2222 are the ones inside your WireGuard
> tunnel.

---

## 3. Configure `.env`

```bash
cp .env.example .env
```
Then edit `.env`. The values you'll definitely change:

```ini
# This machine's own Radarr/Sonarr (keys never leave this box)
RADARR_URL=http://<radarr-host>:7878
RADARR_API_KEY=<from Radarr → Settings → General>
SONARR_URL=http://<sonarr-host>:8989
SONARR_API_KEY=<from Sonarr → Settings → General>

# Where received media lands (must be under /data, and ideally a Radarr/Sonarr root folder)
MOVIES_DEST_DIR=/data/movies
TV_DEST_DIR=/data/tv

# Host folder that maps to /data inside the containers
DATA_DIR=/path/to/your/media
```
Leave the infra defaults (Redis/RabbitMQ/ports) unless you have a reason to
change them. Change `RABBITMQ_USER`/`RABBITMQ_PASSWORD` off `guest` if this is
anything more than a private test.

> If `RADARR_URL` points to a Radarr running in *another* Docker network or on
> the host, use a reachable address (e.g. the host's LAN IP or
> `host.docker.internal`), not `localhost`.

---

## 4. Start the stack

```bash
docker-compose up -d --build
```
This brings up: `wireguard`, `ssh`, `redis`, `rabbitmq`, `api`, `worker`.

The `api` and `worker` share the WireGuard netns, so they ride the tunnel.
Open the UI at:
```
http://localhost:8000
```

---

## 5. Verify end to end

1. **Friends list** — the dropdown (top right) shows your two friends with a
   green dot when reachable. No friends? → WireGuard (Step 1) or `wg0.conf`
   names.
2. **Browse a friend's library** — pick a friend, open the **Movies** tab. You
   should see their poster wall. Empty / error? → that friend's `RADARR_URL`
   /key, or the `api` can't reach them on `:8000` over the tunnel.
3. **Request something** — click a movie → **Request**. Then open **Downloads**
   and watch the progress bar. It should go `queued → running → completed`.
4. **The file lands** in your `MOVIES_DEST_DIR` / `TV_DEST_DIR`.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| No friends in dropdown | Tunnel down, or `[Peer]` blocks / `# name` comments missing in `wg0.conf` |
| "Peer unreachable" browsing a library | Friend's stack down, or `api` not on the tunnel / port 8000 blocked |
| Library loads but is empty | `RADARR_URL`/`SONARR_URL` or API key wrong; or nothing is on disk yet |
| Transfer stuck at `queued` | Worker not consuming — check `docker logs worker`, RabbitMQ up |
| Transfer → `failed` immediately | SSH: key not in friend's `keys/authorized_keys`, wrong `SSH_PORT`, or `keys/wg_peer_key` perms not `600` |
| `rsync: ... Permission denied` on receive | The `ssh` sidecar's user can't write `/data` — check `PUID/PGID` vs the folder owner |
| Path-confinement error on request | Radarr/Sonarr path doesn't match Sharr's `/data` mount — see Prerequisites |

Useful logs:
```bash
docker logs api
docker logs worker
docker exec wireguard wg show
```

---

## Security recap

- **Identity is the tunnel IP.** A request is trusted because it arrives from a
  known WireGuard peer (`is_valid_peer`) — there are no passwords or accounts.
- **Radarr/Sonarr API keys never leave their machine.** Friends call your Sharr,
  not your Radarr.
- **Paths are confined.** Sharr only ever reads/writes inside `ALLOWED_BASE_DIR`
  (`/data`); a friend cannot request a file outside it or choose where it lands
  on your disk.
- **SSH is key-only**, reachable only from inside the tunnel.
