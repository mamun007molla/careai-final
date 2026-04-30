"""FastAPI auth dependencies + role guards.

`require_roles(...)` factory keeps router code clean:
    @router.post("/x", dependencies=[Depends(require_roles("DOCTOR"))])
"""
from typing import Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import User


bearer_scheme = HTTPBearer(auto_error=True)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token payload")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


def require_roles(*allowed: str):
    """Returns a dependency that 403s if the current user's role is not in `allowed`."""
    allowed_set = set(allowed)

    def _checker(cu: User = Depends(get_current_user)) -> User:
        if cu.role not in allowed_set:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This action requires role: {', '.join(sorted(allowed_set))}",
            )
        return cu

    return _checker


def stamp_audit(record, user: User) -> None:
    """Mutate `record` in-place to set audit fields. Use right before db.add()."""
    if hasattr(record, "created_by_user_id"):
        record.created_by_user_id = user.id
    if hasattr(record, "created_by_role"):
        record.created_by_role = user.role
    if hasattr(record, "created_by_name"):
        record.created_by_name = user.name
