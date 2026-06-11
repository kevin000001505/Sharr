import os
from pathlib import Path


def browse_folder(path: str) -> dict:
    base = Path(path)
    allowed_base = Path(os.getenv("ALLOWED_BASE_DIR", "/data"))

    try:
        resolved = base.resolve()
        if not str(resolved).startswith(str(allowed_base)):
            return {"error": "Access denied: path outside allowed directory"}
    except Exception:
        return {"error": "Invalid path"}

    if not resolved.is_dir():
        return {"error": "Not a directory"}

    entries = []

    parent = resolved.parent
    if parent != resolved:
        entries.append({
            "name": "..",
            "path": str(parent),
            "type": "directory",
        })

    try:
        dirs = []
        files = []
        for item in resolved.iterdir():
            if item.name.startswith("."):
                continue
            entry = {
                "name": item.name,
                "path": str(item),
                "type": "directory" if item.is_dir() else "file",
            }
            if item.is_dir():
                dirs.append(entry)
            else:
                files.append(entry)
        dirs.sort(key=lambda e: e["name"])
        files.sort(key=lambda e: e["name"])
        entries.extend(dirs)
        entries.extend(files)
    except PermissionError:
        return {"error": "Permission denied"}

    return {"path": str(resolved), "entries": entries}


def list_files(folder: str) -> dict:
    base = Path(folder)
    allowed_base = Path(os.getenv("ALLOWED_BASE_DIR", "/data"))

    try:
        resolved = base.resolve()
        if not str(resolved).startswith(str(allowed_base)):
            return {"error": "Access denied: path outside allowed directory"}
    except Exception:
        return {"error": "Invalid path"}

    if not resolved.is_dir():
        return {"error": "Not a directory"}

    files = []
    try:
        for item in resolved.iterdir():
            if item.is_file() and not item.name.startswith("."):
                files.append({
                    "name": item.name,
                    "path": str(item),
                    "size": item.stat().st_size,
                })
        files.sort(key=lambda f: f["name"])
    except PermissionError:
        return {"error": "Permission denied"}

    return {"path": str(resolved), "files": files}
