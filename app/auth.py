import secrets
from typing import Optional
from fastapi import Request, HTTPException, status, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, SessionToken, Judge, PasswordCredential
from app.passwords import demo_enabled


def extract_token_from_request(request: Request) -> Optional[str]:
    # 1. Check Authorization header: Bearer <token>
    auth_header = request.headers.get("Authorization")
    if auth_header:
        parts = auth_header.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        elif len(parts) == 1:
            return parts[0]

    # 2. Check Cookie: session=<token>
    session_cookie = request.cookies.get("session")
    if session_cookie:
        return session_cookie

    return None


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    token_str = extract_token_from_request(request)
    if not token_str:
        return None
    if not demo_enabled() and token_str in {"token_admin", "adm_9a11", "token_organizer", "org_7f2a", "token_judge_a", "jdg_a_91bc", "token_judge_b", "jdg_b_44de", "token_participant", "prt_2e88"}:
        return None

    session_record = db.query(SessionToken).filter(SessionToken.token == token_str).first()
    if not session_record:
        return None

    user = db.query(User).filter(User.id == session_record.user_id).first()
    credential = db.get(PasswordCredential, user.id) if user else None
    if not demo_enabled() and credential and credential.demo:
        return None
    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_role(*allowed_roles: str):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: requires one of {allowed_roles}, got {current_user.role}",
            )
        return current_user

    return role_checker


require_admin = require_role("admin")
require_organizer = require_role("organizer", "admin")
require_judge_or_organizer = require_role("judge", "organizer", "admin")
require_participant_or_organizer = require_role("participant", "organizer", "admin")


def create_session(user_id: str, db: Session) -> str:
    token = secrets.token_hex(16)
    session_token = SessionToken(token=token, user_id=user_id)
    db.add(session_token)
    db.commit()
    return token
