from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, SessionToken, Judge
from app.auth import get_current_user_optional, create_session

router = APIRouter(tags=["Auth"])


class LoginRequest(BaseModel):
    email: str


@router.post("/api/auth/login")
def api_login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email.ilike(email)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email '{email}' not found",
        )

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
