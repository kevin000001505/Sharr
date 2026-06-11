from fastapi import APIRouter, Query

from app.filesystem import browse_folder, list_files

router = APIRouter(prefix="/api/filesystem", tags=["filesystem"])


@router.get("")
def browse():
    path = Query("/", description="Folder path to browse")
    result = browse_folder(path)
    if "error" in result:
        return {"error": result["error"]}
    return result


@router.get("/files")
def get_files():
    folder = Query("/", description="Folder path to list files")
    result = list_files(folder)
    if "error" in result:
        return {"error": result["error"]}
    return result
