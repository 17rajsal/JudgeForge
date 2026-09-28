from typing import Optional, List, Dict
import secrets
from app.models import PasswordCredential
from app.passwords import hash_password
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Judge, Project, Score, Track, RubricCriterion, AuditLog, Event
from app.auth import get_current_user, get_current_user_optional, require_organizer
from app.services.scoring import compute_leaderboard, generate_results_csv

router = APIRouter(tags=["Organizer"])
templates = Jinja2Templates(directory="app/templates")


class RubricUpdateSchema(BaseModel):
    weights: Dict[str, float]


@router.get("/api/export.csv")
def export_csv(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    csv_content = generate_results_csv(db)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="judgeforge_results.csv"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/api/organizer/leaderboard")
def get_leaderboard_api(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    return compute_leaderboard(db)


@router.get("/api/organizer/progress")
def get_judging_progress(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    total_projects = db.query(Project).count()
    total_judges = db.query(Judge).count()
    total_scores = db.query(Score).count()

    judges = db.query(Judge).all()
    judge_progress = []
    for j in judges:
        score_count = db.query(Score).filter(Score.judge_id == j.id).count()
        track_names = [t.name for t in j.tracks]
        judge_progress.append({
            "id": j.id,
            "name": j.name,
            "email": j.email,
            "tracks": track_names,
            "scored_count": score_count,
        })

    tracks = db.query(Track).all()
    track_stats = []
    for trk in tracks:
        p_count = db.query(Project).filter(Project.track_id == trk.id).count()
        sc_count = (
            db.query(Score)
            .join(Project, Score.project_id == Project.id)
            .filter(Project.track_id == trk.id)
            .count()
        )
        track_stats.append({
            "id": trk.id,
            "name": trk.name,
            "project_count": p_count,
            "score_count": sc_count,
        })

    return {
        "total_projects": total_projects,
        "total_judges": total_judges,
        "total_scores": total_scores,
        "judges": judge_progress,
        "tracks": track_stats,
    }


@router.put("/api/organizer/rubric")
def update_rubric_weights(
    payload: RubricUpdateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    for name, weight in payload.weights.items():
        crit = db.query(RubricCriterion).filter(RubricCriterion.name == name).first()
        if crit:
            crit.weight = max(0.0, float(weight))

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE_RUBRIC",
            target_type="RubricCriterion",
            target_id="weights",
            details=str(payload.weights),
        )
    )
    db.commit()
    return {"message": "Rubric weights updated successfully"}


@router.get("/organizer", response_class=HTMLResponse)
def view_organizer_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user or user.role != "organizer":
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"user": user, "error": "Organizer access required."},
        )

    leaderboard = compute_leaderboard(db)
    tracks = db.query(Track).all()
    judges = db.query(Judge).all()
    criteria = db.query(RubricCriterion).all()
    audit_logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(20).all()
    event = db.query(Event).first()

    return templates.TemplateResponse(
        request=request,
        name="organizer_dashboard.html",
        context={
            "user": user,
            "event": event,
            "leaderboard": leaderboard,
            "tracks": tracks,
            "judges": judges,
            "criteria": criteria,
            "audit_logs": audit_logs,
        },
    )


class JudgeCreateSchema(BaseModel):
    name: str
    email: str
    track_ids: Optional[List[str]] = None


class TrackAssignSchema(BaseModel):
    track_ids: List[str]


class EventUpdateSchema(BaseModel):
    name: Optional[str] = None
    submissions_close: Optional[str] = None


@router.post("/api/judges", status_code=status.HTTP_201_CREATED)
def invite_or_create_judge(
    payload: JudgeCreateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    email = payload.email.strip().lower()
    existing_judge = db.query(Judge).filter(Judge.email == email).first()
    if existing_judge:
        raise HTTPException(status_code=400, detail="Judge with this email already exists")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user_id = "usr_" + secrets.token_hex(12)
        user = User(
            id=user_id,
            email=email,
            name=payload.name.strip(),
            role="judge",
        )
        db.add(user)
        db.flush()
    else:
        raise HTTPException(409, "Email already belongs to an account; cannot change its role")

    judge_count = db.query(Judge).count()
    judge_id = "jdg_" + secrets.token_hex(12)
    initial_password = secrets.token_urlsafe(20)
    db.add(PasswordCredential(user_id=user.id, password_hash=hash_password(initial_password)))

    judge = Judge(
        id=judge_id,
        user_id=user.id,
        name=payload.name.strip(),
        email=email,
    )
    db.add(judge)
    db.flush()

    if payload.track_ids:
        for tid in payload.track_ids:
            track = db.query(Track).filter(Track.id == tid).first()
            if track:
                judge.tracks.append(track)

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="CREATE_JUDGE",
            target_type="Judge",
            target_id=judge.id,
            details=f"Invited judge {judge.name} ({email})",
        )
    )

    db.commit()
    db.refresh(judge)

    return {
        "id": judge.id,
        "name": judge.name,
        "email": judge.email,
        "tracks": [t.id for t in judge.tracks],
        "initial_password": initial_password,
    }


@router.post("/api/judges/{judge_id}/tracks")
def assign_judge_tracks(
    judge_id: str,
    payload: TrackAssignSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    judge = db.query(Judge).filter(Judge.id == judge_id).first()
    if not judge:
        raise HTTPException(status_code=404, detail="Judge not found")

    judge.tracks.clear()
    for tid in payload.track_ids:
        track = db.query(Track).filter(Track.id == tid).first()
        if track:
            judge.tracks.append(track)

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="ASSIGN_TRACKS",
            target_type="Judge",
            target_id=judge.id,
            details=f"Assigned tracks: {payload.track_ids}",
        )
    )
    db.commit()

    return {"message": "Judge tracks updated successfully", "judge_id": judge_id, "tracks": [t.id for t in judge.tracks]}


@router.put("/api/events/{event_id}")
def update_event(
    event_id: str,
    payload: EventUpdateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    import datetime

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if payload.name:
        event.name = payload.name.strip()
    if payload.submissions_close:
        try:
            close_str = payload.submissions_close
            if close_str.endswith("Z"):
                close_str = close_str[:-1] + "+00:00"
            event.submissions_close = datetime.datetime.fromisoformat(close_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid ISO 8601 datetime format for submissions_close")

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE_EVENT",
            target_type="Event",
            target_id=event.id,
            details=f"Updated event details: close={event.submissions_close}",
        )
    )
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "name": event.name,
        "submissions_close": event.submissions_close.isoformat(),
    }

