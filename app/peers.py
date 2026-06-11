import ipaddress
import subprocess

from app.config import settings
from app.schemas import Peer


def _get_self_ip() -> str:
    try:
        result = subprocess.run(
            ["wg", "show", "interface", "wg0", "pretty"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":").lower()
                if key == "address" and len(parts) >= 2:
                    addr = parts[1]
                    try:
                        return str(ipaddress.ip_interface(addr).ip)
                    except ValueError:
                        pass
    except Exception:
        pass
    return "10.0.0.1"


def _parse_preshared_key(block_content: str) -> str:
    for line in block_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("PresharedKey"):
            return f"peer-{stripped[:16]}"
    return ""


def parse_wg_peers() -> list[Peer]:
    try:
        with open(settings.wg_conf_path, "r") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError):
        return []

    self_ip = _get_self_ip()
    peers = []
    blocks = content.split("[Peer]")
    for block in blocks:
        lines = block.strip().splitlines()
        allowed_ips = ""
        peer_name = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("AllowedIPs"):
                allowed_ips = stripped.split("=", 1)[1].strip()
            elif stripped.startswith("#"):
                peer_name = stripped.lstrip("# ").strip()
        if not allowed_ips:
            continue
        try:
            ip_part = allowed_ips.split(",")[0].strip()
            if "/" in ip_part:
                ip_str = str(ipaddress.ip_interface(ip_part).ip)
            else:
                ip_str = str(ipaddress.ip_address(ip_part))
            if ip_str == self_ip:
                continue
            name = peer_name if peer_name else ip_str
            peers.append(Peer(name=name, tunnel_ip=ip_str, online=False))
        except ValueError:
            continue

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
