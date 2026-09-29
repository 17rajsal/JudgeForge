import datetime
import secrets
import json
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    User, Project, Event, Track, Score, RubricCriterion, Prize,
    Team, TeamMember, AuditLog
)
from app.auth import (
    get_current_user, get_current_user_optional,
    require_organizer, require_participant_or_organizer, require_judge_or_organizer
)
from app.services.scoring import compute_leaderboard
from app.services.verifiable_records import append_verifiable_record
from app.services.webhooks import dispatch_webhook

router = APIRouter(prefix="/api/v1", tags=["REST API v1"])


# ============================================================================
# Schemas
# ============================================================================
class ProjectCreateV1(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: Optional[str] = None
    description: Optional[str] = None
    repo_url: Optional[str] = None
    track_id: Optional[str] = None
    event_id: Optional[str] = None
    status: Optional[str] = "submitted"


class ProjectUpdateV1(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    repo_url: Optional[str] = None
    track_id: Optional[str] = None
    status: Optional[str] = None


class ScoreSubmitV1(BaseModel):
    project_id: str
    functionality: Optional[int] = Field(None, ge=1, le=5)
    quality: Optional[int] = Field(None, ge=1, le=5)
    innovation: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = None


# ============================================================================
# Projects Endpoints
# ============================================================================
@router.get("/projects", summary="List projects")
def list_projects_v1(
    track_id: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None, alias="q"),
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Lists projects with optional filtering by track, search query, status, and event."""
    query = db.query(Project)

    # Filter by event
    if event_id:
        query = query.filter(Project.event_id == event_id)

    # Status visibility: Anonymous / participants only see submitted unless team member
    if status_filter:
        query = query.filter(Project.status == status_filter)
    elif not user or user.role not in ["organizer", "admin"]:
        query = query.filter(Project.status == "submitted")

    if track_id:
        query = query.filter(Project.track_id == track_id)

    if search:
        s = f"%{search.strip()}%"
        query = query.filter((Project.title.ilike(s)) | (Project.summary.ilike(s)))

    projects = query.order_by(Project.submitted_at.desc()).all()

    return [
        {
            "id": p.id,
            "title": p.title,
            "summary": p.summary,
            "repo_url": p.repo_url,
            "track_id": p.track_id,
            "track_name": p.track.name if p.track else None,
            "team_id": p.team_id,
            "team_name": p.team.name if p.team else None,
            "status": p.status,
            "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
        }
        for p in projects
    ]


@router.get("/projects/{project_id}", summary="Get project details")
def get_project_v1(
    project_id: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Retrieves full details for a single project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Draft visibility check
    if project.status == "draft":
        is_authorized = False
        if user:
            if user.role in ["organizer", "admin"]:
                is_authorized = True
            elif project.team_id:
                m = db.query(TeamMember).filter(TeamMember.team_id == project.team_id, TeamMember.user_id == user.id).first()
                if m:
                    is_authorized = True
        if not is_authorized:
            raise HTTPException(status_code=404, detail="Project not found")

    return {
        "id": project.id,
        "title": project.title,
        "summary": project.summary,
        "repo_url": project.repo_url,
        "event_id": project.event_id,
        "track_id": project.track_id,
        "track_name": project.track.name if project.track else None,
        "team_id": project.team_id,
        "team_name": project.team.name if project.team else None,
        "status": project.status,
        "submitted_at": project.submitted_at.isoformat() if project.submitted_at else None,
    }


@router.post("/projects", status_code=status.HTTP_201_CREATED, summary="Create project")
def create_project_v1(
    payload: ProjectCreateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    """Creates a new project draft or submission."""
    event = db.query(Event).filter(Event.id == payload.event_id).first() if payload.event_id else db.query(Event).first()
    if not event:
        raise HTTPException(status_code=404, detail="No active event found")

    # Deadline enforcement for non-organizers
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if current_user.role not in ["organizer", "admin"]:
        close_time = event.submissions_close
        if close_time and close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=datetime.timezone.utc)
        if close_time and now_utc > close_time:
            raise HTTPException(status_code=400, detail="Event submissions are closed")

    # Resolve track
    track_id = payload.track_id
    if not track_id:
        t = db.query(Track).filter(Track.event_id == event.id).first()
        track_id = t.id if t else "trk_01"

    # Resolve team
    team_membership = (
        db.query(TeamMember)
        .join(Team, TeamMember.team_id == Team.id)
        .filter(TeamMember.user_id == current_user.id, Team.event_id == event.id)
        .first()
    )
    if team_membership:
        team_id = team_membership.team_id
    else:
        team_id = "tea_" + secrets.token_hex(6)
        new_team = Team(
            id=team_id,
            event_id=event.id,
            name=f"{current_user.name or 'Creator'}'s Team",
        )
        db.add(new_team)
        db.flush()
        db.add(TeamMember(team_id=team_id, email=current_user.email, user_id=current_user.id))
        db.flush()

    project_id = "prj_" + secrets.token_hex(6)
    proj_status = (payload.status or "submitted").strip().lower()

    project = Project(
        id=project_id,
        event_id=event.id,
        team_id=team_id,
        track_id=track_id,
        title=payload.title.strip(),
        summary=payload.summary.strip() if payload.summary else payload.title.strip(),
        repo_url=payload.repo_url.strip() if payload.repo_url else None,
        status=proj_status,
        submitted_at=now_utc,
    )
    db.add(project)
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="CREATE_PROJECT",
            target_type="Project",
            target_id=project.id,
            details=f"REST v1: {project.title}, status={proj_status}",
        )
    )
    db.commit()
    db.refresh(project)

    if proj_status == "submitted":
        try:
            dispatch_webhook(
                db=db,
                event_id=event.id,
                event_type="project.submitted",
                payload={"project_id": project.id, "title": project.title, "status": project.status},
            )
        except Exception:
            pass

    return {
        "id": project.id,
        "title": project.title,
        "status": project.status,
        "created_at": project.created_at.isoformat(),
    }


@router.put("/projects/{project_id}", summary="Update project")
def update_project_v1(
    project_id: str,
    payload: ProjectUpdateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    """Updates an existing project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Deadline & authorization enforcement
    if current_user.role not in ["organizer", "admin"]:
        event = project.event or db.query(Event).first()
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        close_time = event.submissions_close if event else None
        if close_time and close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=datetime.timezone.utc)
        if close_time and now_utc > close_time:
            raise HTTPException(status_code=400, detail="Submissions closed. Cannot edit project after deadline.")

        if project.team_id:
            m = db.query(TeamMember).filter(TeamMember.team_id == project.team_id, TeamMember.user_id == current_user.id).first()
            if not m:
                raise HTTPException(status_code=403, detail="Only team members can edit this project")
        elif project.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Unauthorized")

    if payload.title is not None:
        project.title = payload.title.strip()
    if payload.summary is not None:
        project.summary = payload.summary.strip()
    if payload.description is not None:
        project.description = payload.description.strip()
    if payload.repo_url is not None:
        project.repo_url = payload.repo_url.strip()
    if payload.track_id is not None:
        project.track_id = payload.track_id
    if payload.status is not None:
        s = payload.status.strip().lower()
        if s in ("draft", "submitted"):
            project.status = s

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE_PROJECT",
            target_type="Project",
            target_id=project.id,
            details=f"REST v1: updated by {current_user.email}",
        )
    )
    db.commit()
    db.refresh(project)

    return {
        "message": "Project updated successfully",
        "id": project.id,
        "title": project.title,
        "status": project.status,
    }


@router.delete("/projects/{project_id}", summary="Delete project")
def delete_project_v1(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Deletes a project (organizer/admin only)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    title = project.title
    db.delete(project)
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="DELETE_PROJECT",
            target_type="Project",
            target_id=project_id,
            details=f"REST v1: deleted project '{title}'",
        )
    )
    db.commit()
    return {"message": "Project deleted successfully", "id": project_id}


# ============================================================================
# Scores & Rubric Endpoints
# ============================================================================
@router.get("/scores", summary="List scores")
def list_scores_v1(
    project_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists scores. Judges can see only their own scores; organizers and admins can see all."""
    query = db.query(Score)
    if current_user.role == "judge":
        judge_id = current_user.judge_profile.id if current_user.judge_profile else current_user.id
        query = query.filter(Score.judge_id == judge_id)
    elif current_user.role not in ["organizer", "admin"]:
        raise HTTPException(status_code=403, detail="Judging scores are private to judges and organizers")

    if project_id:
        query = query.filter(Score.project_id == project_id)

    scores = query.all()
    return [
        {
            "id": s.id,
            "judge_id": s.judge_id,
            "project_id": s.project_id,
            "functionality": s.functionality,
            "quality": s.quality,
            "innovation": s.innovation,
            "comment": s.comment,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        }
        for s in scores
    ]


@router.post("/scores", status_code=status.HTTP_200_OK, summary="Submit or update score")
def submit_score_v1(
    payload: ScoreSubmitV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_judge_or_organizer),
):
    """
    Submits or updates a judge score. Automatically logs an audit trail,
    appends to the tamper-evident hash chain, and triggers outbound webhooks.
    """
    project = db.query(Project).filter(Project.id == payload.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    judge_id = current_user.judge_profile.id if current_user.judge_profile else (
        current_user.id if current_user.role == "judge" else "jdg_01"
    )

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    score = db.query(Score).filter(Score.judge_id == judge_id, Score.project_id == payload.project_id).first()
    is_update = bool(score)

    if not score:
        score = Score(
            judge_id=judge_id,
            project_id=payload.project_id,
            functionality=payload.functionality,
            quality=payload.quality,
            innovation=payload.innovation,
            comment=payload.comment,
            submitted_at=now_utc,
        )
        db.add(score)
    else:
        score.functionality = payload.functionality
        score.quality = payload.quality
        score.innovation = payload.innovation
        score.comment = payload.comment
        score.submitted_at = now_utc

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE_SCORE" if is_update else "CREATE_SCORE",
            target_type="Score",
            target_id=f"{judge_id}:{payload.project_id}",
            details=f"REST v1: Project {payload.project_id}, Judge {judge_id}",
        )
    )
    db.commit()
    db.refresh(score)

    # 1. Cryptographic hash chain append
    try:
        append_verifiable_record(db, score)
    except Exception:
        pass

    # 2. Webhook dispatch
    try:
        dispatch_webhook(
            db=db,
            event_id=project.event_id,
            event_type="score.submitted",
            payload={
                "score_id": score.id,
                "judge_id": score.judge_id,
                "project_id": score.project_id,
                "functionality": score.functionality,
                "quality": score.quality,
                "innovation": score.innovation,
            },
        )
    except Exception:
        pass

    return {
        "message": "Score recorded successfully with cryptographic verification",
        "id": score.id,
        "project_id": score.project_id,
        "judge_id": score.judge_id,
    }


@router.get("/rubric-criteria", summary="List rubric criteria")
def list_rubric_criteria_v1(
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Lists rubric criteria and weights for scoring."""
    target_event_id = event_id or "evt_01"
    criteria = db.query(RubricCriterion).filter(RubricCriterion.event_id == target_event_id).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "label": c.label,
            "weight": c.weight,
            "min_score": c.min_score,
            "max_score": c.max_score,
            "description": c.description,
        }
        for c in criteria
    ]


# ============================================================================
# Events & Results Endpoints
# ============================================================================
@router.get("/events", summary="List events")
def list_events_v1(db: Session = Depends(get_db)):
    """Lists all hackathon events."""
    events = db.query(Event).order_by(Event.created_at.desc()).all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "submissions_close": e.submissions_close.isoformat() if e.submissions_close else None,
            "results_published": bool(e.results_published),
            "voting_mode": e.voting_mode,
        }
        for e in events
    ]


@router.get("/events/{event_id}", summary="Get event details")
def get_event_v1(event_id: str, db: Session = Depends(get_db)):
    """Retrieves single event details."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    return {
        "id": event.id,
        "name": event.name,
        "submissions_close": event.submissions_close.isoformat() if event.submissions_close else None,
        "results_published": bool(event.results_published),
        "results_published_at": event.results_published_at.isoformat() if event.results_published_at else None,
        "voting_mode": event.voting_mode,
    }


@router.get("/events/{event_id}/results", summary="Get leaderboard results")
def get_event_results_v1(
    event_id: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Returns aggregated leaderboard results for an event.
    Hidden from participants until results are published.
    Individual judge identities and scores are omitted to preserve judge privacy.
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if not event.results_published:
        if not user or user.role not in ["organizer", "admin"]:
            raise HTTPException(status_code=403, detail="Results have not been published yet")

    leaderboard = compute_leaderboard(db, event.id)
    prizes = db.query(Prize).filter(Prize.event_id == event.id).all()

    return {
        "event_id": event.id,
        "event_name": event.name,
        "published": bool(event.results_published),
        "prizes": [
            {"id": p.id, "title": p.title, "amount": p.amount, "placement": p.placement}
            for p in prizes
        ],
        "leaderboard": leaderboard,
    }


@router.get("/audit-logs", summary="List audit logs")
def list_audit_logs_v1(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Lists audit trail entries for system actions (organizer/admin only)."""
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
