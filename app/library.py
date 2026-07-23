"""Scan this node's media folders and enrich them with TMDB metadata.

Expected layout (roots configurable via MOVIES_DIR / TV_DIR):

    <MOVIES_DIR>/<Movie Name (Year)>/<file>.mkv
    <TV_DIR>/<Show Name (Year)>/season1/<file>.mkv

Peers reach this data through the Sharr `/api/library/*` endpoints, gated by
tunnel-IP identity. The TMDB key is only used by the owning node — peers get
ready-made poster URLs (image.tmdb.org), which the browser loads directly.
"""
import json
import re
from pathlib import Path
from typing import Optional

import httpx

from app.config import settings
from app.redis_client import get_redis


class LibraryError(RuntimeError):
    pass


VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".ts", ".webm"}

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w342"
META_TTL = 7 * 86400  # cache TMDB lookups for a week

# "The Matrix (1999)" -> ("The Matrix", 1999)
_NAME_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<year>\d{4})\)$")
# "season1", "Season 02", "s3" -> season number
_SEASON_RE = re.compile(r"^(?:season|s)[ _]*(\d+)$", re.I)
# "S01E04 ..." / "E04 ..." -> episode number
_EPISODE_RE = re.compile(r"[Ee](\d{1,3})")


def _parse_folder_name(name: str) -> tuple[str, Optional[int]]:
    m = _NAME_RE.match(name.strip())
    if m:
        return m.group("title"), int(m.group("year"))
    return name.strip(), None


def _video_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )


def _all_video_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )


def _dir_size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


def _root(path: str, label: str) -> Path:
    root = Path(path).resolve()
    if not root.is_dir():
        raise LibraryError(f"{label} folder not found: {path}")
    return root


def _safe_child(root: Path, name: str) -> Path:
    """Resolve a peer-supplied folder name under root, refusing traversal."""
    child = (root / name).resolve()
    if child == root or not child.is_relative_to(root):
        raise LibraryError(f"Unknown title: {name}")
    return child


# ---------------- TMDB metadata ----------------

def _tmdb_lookup(title: str, year: Optional[int], kind: str) -> dict:
    """Return {"poster", "overview", "year"} for a title, cached in Redis.

    kind is "movie" or "tv". Returns {} when TMDB is not configured, the
    lookup fails, or nothing matches — the library still works, just without
    posters.
    """
    if not settings.tmdb_api_key:
        return {}

    cache_key = f"tmdb:{kind}:{title}:{year or ''}"
    try:
        cached = get_redis().get(cache_key)
        if cached is not None:
            return json.loads(cached)
    except Exception:
        cached = None  # Redis unavailable — look up without caching

    params = {"api_key": settings.tmdb_api_key, "query": title}
    if year:
        # TMDB names the year filter differently per media type.
        params["year" if kind == "movie" else "first_air_date_year"] = year
    try:
        r = httpx.get(f"{TMDB_API}/search/{kind}", params=params,
                      timeout=settings.http_timeout)
        r.raise_for_status()
        results = r.json().get("results") or []
    except httpx.HTTPError:
        return {}  # transient failure — don't cache, retry next browse

    meta = {}
    if results:
        top = results[0]
        date = top.get("release_date") or top.get("first_air_date") or ""
        meta = {
            "poster": f"{TMDB_IMG}{top['poster_path']}" if top.get("poster_path") else "",
            "overview": top.get("overview", ""),
            "year": int(date[:4]) if date[:4].isdigit() else None,
        }
    try:
        get_redis().set(cache_key, json.dumps(meta), ex=META_TTL)
    except Exception:
        pass
    return meta


# ---------------- Movies ----------------

def list_movies() -> list[dict]:
    root = _root(settings.movies_dir, "Movies")
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not _all_video_files(d):
            continue
        title, year = _parse_folder_name(d.name)
        meta = _tmdb_lookup(title, year, "movie")
        out.append({
            "id": d.name,
            "title": title,
            "year": year or meta.get("year"),
            "poster": meta.get("poster", ""),
            "size": _dir_size(d),
            "overview": meta.get("overview", ""),
        })
    out.sort(key=lambda x: x["title"].lower())
    return out


# ---------------- TV ----------------

def _season_dirs(show_dir: Path) -> list[tuple[int, Path]]:
    seasons = []
    for sub in show_dir.iterdir():
        if not sub.is_dir():
            continue
        m = _SEASON_RE.match(sub.name)
        if m:
            seasons.append((int(m.group(1)), sub))
    return sorted(seasons)


def list_shows() -> list[dict]:
    root = _root(settings.tv_dir, "TV")
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        episodes = _all_video_files(d)
        if not episodes:
            continue
        title, year = _parse_folder_name(d.name)
        meta = _tmdb_lookup(title, year, "tv")
        out.append({
            "id": d.name,
            "title": title,
            "year": year or meta.get("year"),
            "poster": meta.get("poster", ""),
            "size": _dir_size(d),
            "episode_count": len(episodes),
            "season_count": len(_season_dirs(d)),
            "overview": meta.get("overview", ""),
        })
    out.sort(key=lambda x: x["title"].lower())
    return out


def show_detail(show_id: str) -> dict:
    root = _root(settings.tv_dir, "TV")
    show_dir = _safe_child(root, show_id)
    if not show_dir.is_dir():
        raise LibraryError(f"Unknown title: {show_id}")
    title, year = _parse_folder_name(show_dir.name)
    meta = _tmdb_lookup(title, year, "tv")

    seasons = []
    for season_num, season_dir in _season_dirs(show_dir):
        episodes = []
        for i, f in enumerate(_video_files(season_dir), start=1):
            m = _EPISODE_RE.search(f.stem)
            episodes.append({
                "episode_path": str(f.relative_to(root)),
                "season": season_num,
                "episode": int(m.group(1)) if m else i,
                "title": f.stem,
            })
        episodes.sort(key=lambda e: e["episode"])
        seasons.append({"season": season_num, "episodes": episodes})

    return {
        "id": show_dir.name,
        "title": title,
        "year": year or meta.get("year"),
        "poster": meta.get("poster", ""),
        "seasons": seasons,
    }


# ---------------- Request resolution ----------------

def resolve_request(kind: str, item_id: str, season: Optional[int] = None,
                    episode_path: Optional[str] = None) -> tuple[str, str]:
    """Resolve a peer's request to (source_path, dest_rel).

    source_path is the folder/file to rsync; dest_rel is where it belongs
    relative to the requester's category root, so the receiver ends up with
    the same layout (e.g. an episode of "Show/season1/ep.mkv" lands under
    <their TV dir>/Show/season1/).
    """
    if kind == "movie":
        root = _root(settings.movies_dir, "Movies")
        src = _safe_child(root, item_id)
    elif kind == "series":
        root = _root(settings.tv_dir, "TV")
        src = _safe_child(root, item_id)
    elif kind == "season":
        root = _root(settings.tv_dir, "TV")
        show_dir = _safe_child(root, item_id)
        for num, season_dir in _season_dirs(show_dir):
            if num == season:
                src = season_dir
                break
        else:
            raise LibraryError(f"Season {season} not found for {item_id}")
    elif kind == "episode":
        root = _root(settings.tv_dir, "TV")
        src = _safe_child(root, episode_path or "")
        if not src.is_file():
            raise LibraryError(f"Episode not found: {episode_path}")
    else:
        raise LibraryError(f"Unknown request kind: {kind}")

    if not src.exists():
        raise LibraryError(f"Not found on disk: {item_id}")
    rel = src.parent.relative_to(root)
    return str(src), "" if str(rel) == "." else str(rel)
