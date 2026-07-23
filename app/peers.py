import ipaddress
import subprocess

from app.config import settings
from app.schemas import Peer


def _ip_from_cidr(value: str) -> str | None:
    """Parse the first address out of an `AllowedIPs`/`Address` value."""
    ip_part = value.split(",")[0].strip()
    try:
        if "/" in ip_part:
            return str(ipaddress.ip_interface(ip_part).ip)
        return str(ipaddress.ip_address(ip_part))
    except ValueError:
        return None


def _get_self_ip() -> str:
    """This node's own tunnel IP, read from the `[Interface]` Address in wg0.conf.

    `wg show` does not report the interface address (that is set with `ip addr`),
    so the config file is the reliable source. Falls back to 10.0.0.1 only if the
    file is unreadable or has no Address line.
    """
    try:
        with open(settings.wg_conf_path, "r") as f:
            in_interface = False
            for line in f:
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    in_interface = stripped.lower() == "[interface]"
                    continue
                if in_interface and stripped.lower().startswith("address"):
                    ip = _ip_from_cidr(stripped.split("=", 1)[1])
                    if ip:
                        return ip
    except (FileNotFoundError, PermissionError):
        pass
    return "10.0.0.1"


def parse_wg_peers() -> list[Peer]:
    try:
        with open(settings.wg_conf_path, "r") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return []

    self_ip = _get_self_ip()
    peers: list[Peer] = []

    in_peer = False
    name = ""
    allowed_ips = ""
    seen_field = False  # have we passed the first key=value line in this block?

    def flush():
        if not allowed_ips:
            return
        ip_str = _ip_from_cidr(allowed_ips)
        if ip_str is None or ip_str == self_ip:
            return
        peers.append(Peer(name=name or ip_str, tunnel_ip=ip_str, online=False))

    for raw in lines:
        stripped = raw.strip()
        # Section header — [Peer] starts a new block, anything else ends the
        # current peer block. Splitting on markers (rather than str.split on
        # "[Peer]") keeps each peer's trailing comment from leaking into the
        # next block and mislabeling it.
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_peer:
                flush()
            in_peer = stripped.lower() == "[peer]"
            name = ""
            allowed_ips = ""
            seen_field = False
            continue
        if not in_peer:
            continue
        if not stripped:
            continue
        if stripped.startswith("#"):
            # The friendly name is the first comment after [Peer], before any
            # field. A comment appearing after the fields belongs to the next
            # peer's header and is ignored.
            if not seen_field and not name:
                name = stripped.lstrip("# ").strip()
            continue
        seen_field = True
        if stripped.startswith("AllowedIPs"):
            allowed_ips = stripped.split("=", 1)[1].strip()

    if in_peer:
        flush()

    return peers


def ping_peer(ip: str) -> bool:
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(settings.ping_timeout), ip],
            capture_output=True,
            timeout=int(settings.ping_timeout) + 2,
        )
        return result.returncode == 0
    except Exception:
        return False


def list_peers() -> list[Peer]:
    peers = parse_wg_peers()
    for peer in peers:
        peer.online = ping_peer(peer.tunnel_ip)
    return peers


def is_valid_peer(ip: str) -> bool:
    known = parse_wg_peers()
    return any(p.tunnel_ip == ip for p in known)
