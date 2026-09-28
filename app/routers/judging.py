import datetime
import json
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Score, Judge, Project, RubricCriterion, User, AuditLog, Track
from app.auth import get_current_user, get_current_user_optional

router = APIRouter(tags=["Judging"])
templates = Jinja2Templates(directory="app/templates")


class ScoreSubmissionSchema(BaseModel):
    project_id: str
    functionality: Optional[int] = Field(None, ge=1, le=5)
    quality: Optional[int] = Field(None, ge=1, le=5)
    innovation: Optional[int] = Field(None, ge=1, le=5)
    criteria: Optional[Dict[str, int]] = None
    comment: Optional[str] = ""


@router.get("/api/judge/scores")
def list_judge_scores(
    judge: Optional[str] = Query(None, description="Optional judge ID to filter scores"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 1. Enforce RBAC: participant or visitor is blocked
    if current_user.role not in ("judge", "organizer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only judges and organizers can access judging scores",
        )

    # 2. Enforce Judge Peer Isolation
    target_judge_id: Optional[str] = None

    if current_user.role == "judge":
        judge_profile = current_user.judge_profile
        if not judge_profile:
            # Fallback check if user ID matches a judge record
            judge_record = db.query(Judge).filter(Judge.email == current_user.email).first()
            if judge_record:
                judge_profile = judge_record
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No associated judge profile found for this account",
                )

        # Critical security check: A judge can ONLY see their own scores
        if judge and judge != judge_profile.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Judges cannot inspect peer scores (attempted access to {judge})",
            )

        target_judge_id = judge_profile.id
    else:
        # Organizer can inspect any specific judge's scores or all
        target_judge_id = judge

    query = db.query(Score)
    if target_judge_id:
        query = query.filter(Score.judge_id == target_judge_id)

    scores = query.all()
    results = []
    for sc in scores:
        crit_dict = {}
        if sc.criteria_json:
            try:
                crit_dict = json.loads(sc.criteria_json)
            except Exception:
                crit_dict = {}
        if not crit_dict:
            crit_dict = {
                "functionality": sc.functionality,
                "quality": sc.quality,
                "innovation": sc.innovation,
            }

        results.append({
            "id": sc.id,
            "judge_id": sc.judge_id,
            "project_id": sc.project_id,
            "project_title": sc.project.title if sc.project else "",
            "criteria": crit_dict,
            "functionality": sc.functionality,
            "quality": sc.quality,
            "innovation": sc.innovation,
            "comment": sc.comment or "",
            "submitted_at": sc.submitted_at.isoformat() if sc.submitted_at else None,
        })

    return results


@router.post("/api/judge/scores")
def submit_or_update_score(
    payload: ScoreSubmissionSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ("judge", "organizer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only judges and organizers can submit scores",
        )

    # Resolve judge ID
    if current_user.role == "judge":
        judge_profile = current_user.judge_profile or db.query(Judge).filter(Judge.email == current_user.email).first()
        if not judge_profile:
            raise HTTPException(status_code=403, detail="No judge profile associated with account")
        judge_id = judge_profile.id
    else:
        first_judge = db.query(Judge).first()
        judge_id = first_judge.id if first_judge else "jdg_01"

    project = db.query(Project).filter(Project.id == payload.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Criteria parsing
    crit = payload.criteria or {}
    func_score = payload.functionality if payload.functionality is not None else crit.get("functionality", 3)
    qual_score = payload.quality if payload.quality is not None else crit.get("quality", 3)
    innov_score = payload.innovation if payload.innovation is not None else crit.get("innovation", 3)

    score_dict = {
        "functionality": func_score,
        "quality": qual_score,
        "innovation": innov_score,
    }

    # Upsert score
    score = (
        db.query(Score)
        .filter(Score.judge_id == judge_id, Score.project_id == payload.project_id)
        .first()
    )
    is_update = score is not None
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    if not score:
        score = Score(
            judge_id=judge_id,
            project_id=payload.project_id,
            functionality=func_score,
            quality=qual_score,
            innovation=innov_score,
            criteria_json=json.dumps(score_dict),
            comment=payload.comment,
            submitted_at=now_utc,
        )
        db.add(score)
    else:
        score.functionality = func_score
        score.quality = qual_score
        score.innovation = innov_score
        score.criteria_json = json.dumps(score_dict)
        score.comment = payload.comment
        score.submitted_at = now_utc

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE_SCORE" if is_update else "CREATE_SCORE",
            target_type="Score",
            target_id=f"{judge_id}:{payload.project_id}",
            details=f"Project: {payload.project_id}, Judge: {judge_id}, Criteria: {score_dict}",
        )
    )

    db.commit()
    db.refresh(score)

    return {
        "message": "Score saved successfully",
        "id": score.id,
        "project_id": score.project_id,
        "judge_id": score.judge_id,
        "criteria": score_dict,
        "comment": score.comment,
    }


@router.get("/judge", response_class=HTMLResponse)
def view_judge_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user or user.role not in ("judge", "organizer"):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"user": user, "error": "Please log in as a judge or organizer to view this page."},
        )

    judge = user.judge_profile or db.query(Judge).filter(Judge.email == user.email).first()
    assigned_tracks = judge.tracks if judge else []
    assigned_track_ids = [t.id for t in assigned_tracks]

    # Find projects in assigned tracks
    if assigned_track_ids:
        projects = db.query(Project).filter(Project.track_id.in_(assigned_track_ids)).all()
    else:
        projects = db.query(Project).all()

    # Scores by this judge
    judge_id = judge.id if judge else "jdg_01"
    my_scores = {s.project_id: s for s in db.query(Score).filter(Score.judge_id == judge_id).all()}

    criteria = db.query(RubricCriterion).all()

    return templates.TemplateResponse(
        request=request,
        name="judge_dashboard.html",
        context={
            "user": user,
            "judge": judge,
            "assigned_tracks": assigned_tracks,
            "projects": projects,
            "my_scores": my_scores,
            "criteria": criteria,
        },
    )
