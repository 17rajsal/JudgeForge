from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, ConfigDict
import secrets
import re
from app.models import PasswordCredential, TeamMember
from app.passwords import hash_password, verify_password, demo_enabled
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, SessionToken, Judge
from app.auth import get_current_user_optional, create_session

router = APIRouter(tags=["Auth"])


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(LoginRequest):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=256)


@router.post("/api/auth/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not payload.name.strip():
        raise HTTPException(400, "Valid email and name required")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "Account already exists")
    user = User(id="usr_" + secrets.token_hex(12), email=email, name=payload.name.strip(), role="participant")
    db.add(user)
    db.flush()
    db.add(PasswordCredential(user_id=user.id, password_hash=hash_password(payload.password)))
    # An invitation is not proof of email ownership. Membership is not auto-claimed.
    db.commit()
    return {"id": user.id, "role": user.role}



@router.post("/api/auth/login")
def api_login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email.ilike(email)).first()
    credential = db.get(PasswordCredential, user.id) if user else None
    if (not credential or (credential.demo and not demo_enabled())
            or not verify_password(payload.password, credential.password_hash)):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_session(user.id, db)
    response.set_cookie(key="session", value=token, httponly=True, samesite="lax")

    judge_id = user.judge_profile.id if user.judge_profile else None
    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "judge_id": judge_id,
        },
    }


@router.get("/api/auth/me")
def api_me(user: User = Depends(get_current_user_optional)):
    if not user:
        return {"authenticated": False, "user": None}

    judge_id = user.judge_profile.id if user.judge_profile else None
    return {
        "authenticated": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "judge_id": judge_id,
        },
    }


@router.post("/api/auth/logout")
def api_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth_header = request.headers.get("Authorization")
    token_str = None
    if auth_header and "bearer" in auth_header.lower():
        token_str = auth_header.strip().split()[-1]
    if not token_str:
        token_str = request.cookies.get("session")

    if token_str:
        db.query(SessionToken).filter(SessionToken.token == token_str).delete()
        db.commit()

    response.delete_cookie("session")
    return {"message": "Logged out successfully"}
