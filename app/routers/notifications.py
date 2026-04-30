"""Notifications router — bell icon backend."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.notification import Notification
from app.models.user import User
from app.schemas import NotificationOut, NotificationStats


router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    only_unread: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    q = db.query(Notification).filter(Notification.user_id == cu.id)
    if only_unread:
        q = q.filter(Notification.is_read == False)  # noqa: E712
    return q.order_by(desc(Notification.created_at)).limit(limit).all()


@router.get("/stats", response_model=NotificationStats)
def stats(db: Session = Depends(get_db), cu: User = Depends(get_current_user)):
    total = db.query(func.count(Notification.id)).filter(
        Notification.user_id == cu.id
    ).scalar() or 0
    unread = db.query(func.count(Notification.id)).filter(
        Notification.user_id == cu.id,
        Notification.is_read == False,  # noqa: E712
    ).scalar() or 0
    return NotificationStats(total=total, unread=unread)


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    n = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == cu.id,
    ).first()
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    n.is_read = True
    db.commit()
    db.refresh(n)
    return n


@router.post("/mark-all-read", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(db: Session = Depends(get_db), cu: User = Depends(get_current_user)):
    db.query(Notification).filter(
        Notification.user_id == cu.id,
        Notification.is_read == False,  # noqa: E712
    ).update({"is_read": True})
    db.commit()


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(
    notification_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    n = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == cu.id,
    ).first()
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    db.delete(n)
    db.commit()
