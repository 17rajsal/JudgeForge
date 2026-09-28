from typing import Optional, List, Dict
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
