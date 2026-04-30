"""File storage in PostgreSQL (decision #3).

Endpoints:
- GET  /files/{id}         — stream content with proper mime type, supports range requests
- GET  /files/{id}/meta    — JSON metadata (no body)
- DELETE /files/{id}       — owner or uploader only

Helpers (used by other routers):
- save_file_to_db()
- check_file_access()
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.file import File
from app.models.user import PatientLink, User
from app.schemas import FileMetaOut


router = APIRouter(prefix="/files", tags=["Files"])


# ─── Helpers (used by other routers) ──────────────────────────────────────────
async def save_upload_to_db(
    db: Session,
    upload: UploadFile,
    *,
    owner_id: str,
    uploaded_by: str,
    purpose: str,
    max_mb: Optional[int] = None,
) -> File:
    """Read an UploadFile and persist it as a File row. Returns the File."""
    content = await upload.read()
    cap = (max_mb or settings.MAX_IMAGE_SIZE_MB) * 1024 * 1024
    if len(content) > cap:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"File too large (max {max_mb or settings.MAX_IMAGE_SIZE_MB}MB)")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    f = File(
        filename=upload.filename or "upload.bin",
        mime_type=upload.content_type or "application/octet-stream",
        size_bytes=len(content),
        content=content,
        purpose=purpose,
        owner_id=owner_id,
        uploaded_by=uploaded_by,
    )
    db.add(f)
    db.flush()           # populate f.id without committing yet
    return f


def save_bytes_to_db(
    db: Session,
    data: bytes,
    *,
    filename: str,
    mime_type: str,
    owner_id: str,
    uploaded_by: str,
    purpose: str,
) -> File:
    """Persist already-read bytes (used by the fall-detection pipeline)."""
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty content")
    f = File(
        filename=filename, mime_type=mime_type, size_bytes=len(data),
        content=data, purpose=purpose, owner_id=owner_id, uploaded_by=uploaded_by,
    )
    db.add(f)
    db.flush()
    return f


def check_file_access(db: Session, file: File, cu: User) -> None:
    """Owner, uploader, or anyone linked to the owner can access. Else 403."""
    if cu.id == file.owner_id or cu.id == file.uploaded_by:
        return
    # Anyone linked to the owner (caregiver / doctor) gets read access
    linked = db.query(PatientLink).filter(
        PatientLink.patient_id == file.owner_id,
        PatientLink.linked_id == cu.id,
    ).first()
    if linked:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot access this file")


# ─── Endpoints ────────────────────────────────────────────────────────────────
@router.get("/{file_id}/meta", response_model=FileMetaOut)
def get_meta(
    file_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    f = db.query(File).filter(File.id == file_id).first()
    if not f:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    check_file_access(db, f, cu)
    return f


@router.get("/{file_id}")
def download_file(
    file_id: str,
    range: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Stream file content. Honors HTTP Range for video/audio playback."""
    f = db.query(File).filter(File.id == file_id).first()
    if not f:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    check_file_access(db, f, cu)

    content = f.content
    total = len(content)

    if range and range.startswith("bytes="):
        try:
            spec = range.replace("bytes=", "").split("-")
            start = int(spec[0]) if spec[0] else 0
            end = int(spec[1]) if len(spec) > 1 and spec[1] else total - 1
            end = min(end, total - 1)
            chunk = content[start:end + 1]
            return Response(
                content=chunk,
                status_code=status.HTTP_206_PARTIAL_CONTENT,
                headers={
                    "Content-Range":  f"bytes {start}-{end}/{total}",
                    "Accept-Ranges":  "bytes",
                    "Content-Length": str(len(chunk)),
                    "Content-Type":   f.mime_type,
                },
            )
        except (ValueError, IndexError):
            pass  # fall through to full response

    return Response(
        content=content,
        media_type=f.mime_type,
        headers={
            "Content-Length":      str(total),
            "Accept-Ranges":       "bytes",
            "Content-Disposition": f'inline; filename="{f.filename}"',
        },
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    file_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    f = db.query(File).filter(File.id == file_id).first()
    if not f:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    if cu.id not in (f.owner_id, f.uploaded_by):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owner / uploader can delete")
    db.delete(f)
    db.commit()
