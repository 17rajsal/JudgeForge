import datetime
import secrets
import json
import re
import hashlib
import random
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Response
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    User, Project, Event, Track, Score, RubricCriterion, Prize,
    Team, TeamMember, TeamInvite, AuditLog, PasswordCredential,
    SessionToken, Judge, Comment, CommunityVote, VotingVoucher
)
from app.auth import (
    get_current_user, get_current_user_optional,
    require_organizer, require_admin, require_participant_or_organizer, require_judge_or_organizer,
    create_session
)
from app.passwords import hash_password, verify_password, demo_enabled
from app.services.scoring import compute_leaderboard, generate_results_csv
from app.services.verifiable_records import append_verifiable_record
from app.services.webhooks import dispatch_webhook
from app.rate_limiter import check_rate_limit

router = APIRouter(prefix="/api/v1", tags=["REST API v1"])


# ============================================================================
# Helpers
# ============================================================================
def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


# ============================================================================
# Schemas
# ============================================================================
class LoginV1(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)


class RegisterV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    email: str
    password: str = Field(min_length=12, max_length=256)


class ProjectCreateV1(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: Optional[str] = None
    repo_url: Optional[str] = None
    track_id: Optional[str] = None
    event_id: Optional[str] = None
    team_id: Optional[str] = None
    status: Optional[str] = "submitted"


class ProjectUpdateV1(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    repo_url: Optional[str] = None
    track_id: Optional[str] = None
    status: Optional[str] = None


class ScoreSubmitV1(BaseModel):
    project_id: str
    functionality: Optional[int] = Field(None, ge=1, le=5)
    quality: Optional[int] = Field(None, ge=1, le=5)
    innovation: Optional[int] = Field(None, ge=1, le=5)
    comment: Optional[str] = None


class EventCreateV1(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    submissions_close: datetime.datetime
    tracks: List[str] = Field(min_length=1, max_length=30)


class EventUpdateV1(BaseModel):
    name: Optional[str] = None
    submissions_close: Optional[str] = None


class TrackCreateV1(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PrizeCreateV1(BaseModel):
    title: str
    description: Optional[str] = None
    amount: str
    placement: str


class PrizeUpdateV1(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[str] = None
    placement: Optional[str] = None


class TeamCreateV1(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    event_id: Optional[str] = None


class TeamMemberAddV1(BaseModel):
    email: str


class RubricWeightsV1(BaseModel):
    weights: Dict[str, float]


class JudgeCreateV1(BaseModel):
    name: str
    email: str
    track_ids: Optional[List[str]] = None


class JudgeTrackAssignV1(BaseModel):
    track_ids: List[str]


class CommentCreateV1(BaseModel):
    author_name: Optional[str] = Field(None, max_length=100)
    content: str = Field(min_length=1, max_length=2000)


class VoteCastV1(BaseModel):
    project_id: str
    voter_token: Optional[str] = None
    voucher_token: Optional[str] = None
    email: Optional[str] = None


class VotingConfigV1(BaseModel):
    voting_mode: Optional[str] = None
    voting_opens: Optional[datetime.datetime] = None
    voting_closes: Optional[datetime.datetime] = None
    voting_results_public: Optional[bool] = None


class VoucherCreateV1(BaseModel):
    email: str


# ============================================================================
# Auth Endpoints
# ============================================================================
@router.post("/auth/login", summary="Login to obtain session token")
def login_v1(payload: LoginV1, response: Response, db: Session = Depends(get_db)):
    """Authenticates user credentials and returns a session token and user profile."""
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email.ilike(email)).first()
    credential = db.get(PasswordCredential, user.id) if user else None
    if (
        not credential
        or (credential.demo and not demo_enabled())
        or not verify_password(payload.password, credential.password_hash)
    ):
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


@router.post("/auth/register", status_code=status.HTTP_201_CREATED, summary="Register participant account")
def register_v1(payload: RegisterV1, db: Session = Depends(get_db)):
    """Registers a new participant account with secure password hashing."""
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not payload.name.strip():
        raise HTTPException(400, "Valid email and name required")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "Account already exists")

    user = User(
        id="usr_" + secrets.token_hex(12),
        email=email,
        name=payload.name.strip(),
        role="participant",
    )
    db.add(user)
    db.flush()
    db.add(PasswordCredential(user_id=user.id, password_hash=hash_password(payload.password)))
    db.commit()
    return {"id": user.id, "email": user.email, "role": user.role}


@router.post("/auth/logout", summary="Logout and invalidate session")
def logout_v1(request: Request, response: Response, db: Session = Depends(get_db)):
    """Invalidates the active session token and clears session cookie."""
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


@router.get("/auth/me", summary="Get current session identity")
def me_v1(user: Optional[User] = Depends(get_current_user_optional)):
    """Returns the authenticated identity of the current caller, or unauthenticated status."""
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

    if event_id:
        query = query.filter(Project.event_id == event_id)

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


@router.post("/projects", status_code=status.HTTP_201_CREATED, summary="Create project draft or submission")
def create_project_v1(
    payload: ProjectCreateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    """Creates a new project draft or finalized submission."""
    event = db.query(Event).filter(Event.id == payload.event_id).first() if payload.event_id else db.query(Event).first()
    if not event:
        raise HTTPException(status_code=404, detail="No active event found")

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if current_user.role not in ["organizer", "admin"]:
        close_time = event.submissions_close
        if close_time and close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=datetime.timezone.utc)
        if close_time and now_utc > close_time:
            raise HTTPException(status_code=400, detail="Event submissions are closed")

    track_id = payload.track_id
    if not track_id:
        t = db.query(Track).filter(Track.event_id == event.id).first()
        track_id = t.id if t else "trk_01"

    if payload.team_id:
        team = db.query(Team).filter(Team.id == payload.team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="Specified team not found")
        team_id = team.id
    else:
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
        "submitted_at": project.submitted_at.isoformat() if project.submitted_at else None,
    }


@router.put("/projects/{project_id}", summary="Update project")
def update_project_v1(
    project_id: str,
    payload: ProjectUpdateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    """Updates an existing project draft or submission."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

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

    if payload.title is not None:
        project.title = payload.title.strip()
    if payload.summary is not None:
        project.summary = payload.summary.strip()
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


@router.post("/projects/{project_id}/submit", summary="Finalize project draft submission")
def finalize_project_v1(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    """Explicitly submits/finalizes a draft project before the deadline."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    event = project.event or db.query(Event).first()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    close_time = event.submissions_close if event else None
    if close_time and close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=datetime.timezone.utc)

    if close_time and now_utc > close_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submissions are closed. Drafts cannot be submitted after the deadline.",
        )

    if current_user.role not in ["organizer", "admin"]:
        membership = (
            db.query(TeamMember)
            .filter(TeamMember.team_id == project.team_id, TeamMember.user_id == current_user.id)
            .first()
        )
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only team members can submit this project",
            )

    project.status = "submitted"
    project.submitted_at = now_utc

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="SUBMIT_PROJECT",
            target_type="Project",
            target_id=project.id,
            details=f"REST v1: finalized by {current_user.email}",
        )
    )
    db.commit()
    db.refresh(project)

    try:
        dispatch_webhook(
            db=db,
            event_id=event.id if event else "evt_01",
            event_type="project.submitted",
            payload={
                "project_id": project.id,
                "title": project.title,
                "team_id": project.team_id,
                "status": project.status,
            },
        )
    except Exception:
        pass

    return {
        "message": "Project submitted successfully",
        "id": project.id,
        "title": project.title,
        "status": project.status,
        "submitted_at": project.submitted_at.isoformat(),
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
# Teams & Invitations Endpoints
# ============================================================================
@router.get("/teams", summary="List teams")
def list_teams_v1(
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Lists teams with members and project counts."""
    query = db.query(Team)
    if event_id:
        query = query.filter(Team.event_id == event_id)
    teams = query.all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "event_id": t.event_id,
            "members": [m.email for m in t.members],
            "project_count": len(t.projects),
        }
        for t in teams
    ]


@router.get("/teams/{team_id}", summary="Get team details")
def get_team_v1(team_id: str, db: Session = Depends(get_db)):
    """Retrieves single team details."""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return {
        "id": team.id,
        "name": team.name,
        "event_id": team.event_id,
        "members": [{"email": m.email, "user_id": m.user_id} for m in team.members],
        "projects": [{"id": p.id, "title": p.title, "status": p.status} for p in team.projects],
    }


@router.post("/teams", status_code=status.HTTP_201_CREATED, summary="Create team")
def create_team_v1(
    payload: TeamCreateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    """Creates a new team with creator as first member."""
    target_event_id = payload.event_id or "evt_01"
    event = db.query(Event).filter(Event.id == target_event_id).first()
    if not event or not payload.name.strip():
        raise HTTPException(400, "Valid event and team name required")

    team_id = "tm_" + secrets.token_hex(12)
    team = Team(
        id=team_id,
        event_id=event.id,
        name=payload.name.strip(),
    )
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, email=current_user.email, user_id=current_user.id))
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="CREATE_TEAM",
            target_type="Team",
            target_id=team.id,
            details=f"Created team {team.name}",
        )
    )
    db.commit()
    db.refresh(team)
    return {"id": team.id, "name": team.name, "members": [current_user.email]}


@router.post("/teams/{team_id}/members", summary="Add member to team")
def add_team_member_v1(
    team_id: str,
    payload: TeamMemberAddV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Adds a member to a team (team member or organizer/admin only)."""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if current_user.role not in ["organizer", "admin"]:
        is_member = (
            db.query(TeamMember)
            .filter(TeamMember.team_id == team_id, TeamMember.user_id == current_user.id)
            .first()
        )
        if not is_member:
            raise HTTPException(status_code=403, detail="Forbidden: Only team members can invite new members")

    email = payload.email.strip().lower()
    existing = (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team_id, TeamMember.email == email)
        .first()
    )
    if existing:
        return {"message": "User is already a member of this team", "team_id": team_id}

    user = db.query(User).filter(User.email == email).first()
    db.add(TeamMember(team_id=team_id, email=email, user_id=user.id if user else None))
    db.commit()
    return {"message": f"Added {email} to team {team.name}", "team_id": team_id}


@router.delete("/teams/{team_id}/members/{user_id}", summary="Remove member from team")
def remove_team_member_v1(
    team_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    """Removes a member from a team."""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if current_user.role not in ["organizer", "admin"]:
        if current_user.id != user_id:
            is_member = (
                db.query(TeamMember)
                .filter(TeamMember.team_id == team_id, TeamMember.user_id == current_user.id)
                .first()
            )
            if not is_member:
                raise HTTPException(status_code=403, detail="Forbidden")

    member = db.query(TeamMember).filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in this team")

    db.delete(member)
    db.commit()
    return {"message": "Member removed from team", "team_id": team_id, "user_id": user_id}


@router.post("/teams/{team_id}/invites", status_code=status.HTTP_201_CREATED, summary="Create team invite link")
def create_team_invite_v1(
    team_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    """Generates a secure invite token for joining a team."""
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    if current_user.role not in ["organizer", "admin"]:
        if not db.query(TeamMember).filter_by(team_id=team_id, user_id=current_user.id).first():
            raise HTTPException(403, "Only team members may create invitations")

    token = secrets.token_urlsafe(32)
    db.add(TeamInvite(token=token, team_id=team_id, expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)))
    db.commit()
    return {"token": token, "invite_url": f"/workspace?invite={token}", "team_id": team_id}


@router.post("/team-invites/{token}/accept", summary="Accept team invite")
def accept_team_invite_v1(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    """Claims and accepts a team invite token."""
    invitation = db.get(TeamInvite, token)
    now = datetime.datetime.now(datetime.timezone.utc)
    if not invitation or invitation.used_by or invitation.expires_at.replace(tzinfo=datetime.timezone.utc) <= now:
        raise HTTPException(400, "Invitation invalid, expired or already used")
    if not db.get(Team, invitation.team_id):
        raise HTTPException(404, "Team no longer exists")

    changed = db.query(TeamInvite).filter_by(token=token, used_by=None).update({"used_by": current_user.id})
    if not changed:
        raise HTTPException(409, "Invitation already used")

    if not db.query(TeamMember).filter_by(team_id=invitation.team_id, user_id=current_user.id).first():
        db.add(TeamMember(team_id=invitation.team_id, user_id=current_user.id, email=current_user.email))

    db.commit()
    return {"status": "accepted", "team_id": invitation.team_id}


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
    appends to the tamper-evident hash chain with an asymmetric RSA signature,
    and triggers outbound webhooks.
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

    # 1. Cryptographic hash chain append with asymmetric RSA signature
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


@router.put("/rubric-criteria/weights", summary="Update rubric weights")
def update_rubric_weights_v1(
    payload: RubricWeightsV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Updates rubric criteria scoring weights (organizer/admin only)."""
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
            details=f"REST v1: {payload.weights}",
        )
    )
    db.commit()
    return {"message": "Rubric weights updated successfully", "weights": payload.weights}


# ============================================================================
# Judges Management Endpoints
# ============================================================================
@router.get("/judges", summary="List judges")
def list_judges_v1(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Lists all judges, their assigned tracks, and score counts (organizer/admin only)."""
    judges = db.query(Judge).all()
    return [
        {
            "id": j.id,
            "name": j.name,
            "email": j.email,
            "tracks": [{"id": t.id, "name": t.name} for t in j.tracks],
            "scores_count": len(j.scores),
        }
        for j in judges
    ]


@router.post("/judges", status_code=status.HTTP_201_CREATED, summary="Invite or create judge")
def create_judge_v1(
    payload: JudgeCreateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Invites a new judge, generates login credentials, and assigns tracks (organizer/admin only)."""
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
            details=f"REST v1: invited judge {judge.name} ({email})",
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


@router.post("/judges/{judge_id}/tracks", summary="Assign tracks to judge")
def assign_judge_tracks_v1(
    judge_id: str,
    payload: JudgeTrackAssignV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Assigns specific track IDs to a judge (organizer/admin only)."""
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
            details=f"REST v1: assigned tracks {payload.track_ids}",
        )
    )
    db.commit()
    return {"message": "Judge tracks updated successfully", "judge_id": judge_id, "tracks": [t.id for t in judge.tracks]}


@router.get("/judging/progress", summary="Get judging progress metrics")
def get_judging_progress_v1(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Returns judging progress metrics across tracks and judges (organizer/admin only)."""
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


@router.post("/events", status_code=status.HTTP_201_CREATED, summary="Create event")
def create_event_v1(
    payload: EventCreateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Creates a new event with initial tracks and default rubric criteria (organizer/admin only)."""
    if not payload.name.strip() or any(not t.strip() for t in payload.tracks):
        raise HTTPException(400, "Event and track names cannot be blank")

    close = payload.submissions_close
    if close.tzinfo is None:
        raise HTTPException(400, "Deadline must include a UTC offset")
    close = close.astimezone(datetime.timezone.utc)

    event_id = "evt_" + secrets.token_hex(12)
    event = Event(id=event_id, name=payload.name.strip(), submissions_close=close)
    db.add(event)
    db.flush()

    for name in dict.fromkeys(t.strip() for t in payload.tracks):
        db.add(Track(id="trk_" + secrets.token_hex(12), event_id=event.id, name=name))

    for name in ("functionality", "quality", "innovation"):
        db.add(RubricCriterion(id="crit_" + secrets.token_hex(12), event_id=event.id, name=name, label=name.title(), weight=1.0))

    db.add(AuditLog(user_id=current_user.id, action="CREATE_EVENT", target_type="Event", target_id=event.id, details=event.name))
    db.commit()
    db.refresh(event)
    return {"id": event.id, "name": event.name, "submissions_close": event.submissions_close.isoformat()}


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
        "voting_opens": event.voting_opens.isoformat() if event.voting_opens else None,
        "voting_closes": event.voting_closes.isoformat() if event.voting_closes else None,
        "voting_results_public": bool(event.voting_results_public),
    }


@router.put("/events/{event_id}", summary="Update event details")
def update_event_v1(
    event_id: str,
    payload: EventUpdateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Updates event name or submission deadline (organizer/admin only)."""
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
            details=f"REST v1: updated event details, close={event.submissions_close}",
        )
    )
    db.commit()
    db.refresh(event)

    return {
        "id": event.id,
        "name": event.name,
        "submissions_close": event.submissions_close.isoformat(),
    }


@router.post("/events/{event_id}/publish", summary="Publish event results")
def publish_results_v1(
    event_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Publishes official event results and dispatches webhook (organizer/admin only)."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    now = datetime.datetime.now(datetime.timezone.utc)
    event.results_published = 1
    event.results_published_at = now
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="PUBLISH_RESULTS",
            target_type="Event",
            target_id=event.id,
            details=f"REST v1: results published by {current_user.email}",
        )
    )
    db.commit()

    try:
        dispatch_webhook(
            db=db,
            event_id=event.id,
            event_type="results.published",
            payload={"event_id": event.id, "event_name": event.name, "published_at": now.isoformat()},
        )
    except Exception:
        pass

    return {
        "message": "Results published successfully",
        "event_id": event.id,
        "results_published": True,
        "results_published_at": now.isoformat(),
    }


@router.post("/events/{event_id}/unpublish", summary="Unpublish event results")
def unpublish_results_v1(
    event_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Unpublishes event results (organizer/admin only)."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    event.results_published = 0
    event.results_published_at = None
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UNPUBLISH_RESULTS",
            target_type="Event",
            target_id=event.id,
            details=f"REST v1: results unpublished by {current_user.email}",
        )
    )
    db.commit()
    return {
        "message": "Results unpublished successfully",
        "event_id": event.id,
        "results_published": False,
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
        "published_at": event.results_published_at.isoformat() if event.results_published_at else None,
        "prizes": [
            {"id": p.id, "title": p.title, "amount": p.amount, "placement": p.placement}
            for p in prizes
        ],
        "leaderboard": leaderboard,
    }


# ============================================================================
# Tracks Endpoints
# ============================================================================
@router.get("/tracks", summary="List all tracks")
def list_tracks_v1(db: Session = Depends(get_db)):
    """Lists all tracks across events."""
    tracks = db.query(Track).all()
    return [{"id": t.id, "event_id": t.event_id, "name": t.name} for t in tracks]


@router.get("/events/{event_id}/tracks", summary="List tracks for event")
def list_event_tracks_v1(event_id: str, db: Session = Depends(get_db)):
    """Lists tracks belonging to a specific event."""
    tracks = db.query(Track).filter(Track.event_id == event_id).all()
    return [{"id": t.id, "event_id": t.event_id, "name": t.name} for t in tracks]


@router.post("/events/{event_id}/tracks", status_code=status.HTTP_201_CREATED, summary="Create track for event")
def create_track_v1(
    event_id: str,
    payload: TrackCreateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Creates a new track for an event (organizer/admin only)."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    track_id = "trk_" + secrets.token_hex(6)
    track = Track(id=track_id, event_id=event.id, name=payload.name.strip())
    db.add(track)
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="CREATE_TRACK",
            target_type="Track",
            target_id=track.id,
            details=f"REST v1: track {track.name} in event {event.id}",
        )
    )
    db.commit()
    db.refresh(track)
    return {"id": track.id, "event_id": track.event_id, "name": track.name}


# ============================================================================
# Prizes Endpoints
# ============================================================================
@router.get("/events/{event_id}/prizes", summary="List prizes for event")
def list_prizes_v1(event_id: str, db: Session = Depends(get_db)):
    """Lists all configured prizes for an event."""
    prizes = db.query(Prize).filter(Prize.event_id == event_id).order_by(Prize.created_at.asc()).all()
    return [
        {
            "id": p.id,
            "event_id": p.event_id,
            "title": p.title,
            "description": p.description,
            "amount": p.amount,
            "placement": p.placement,
        }
        for p in prizes
    ]


@router.post("/events/{event_id}/prizes", status_code=status.HTTP_201_CREATED, summary="Create prize")
def create_prize_v1(
    event_id: str,
    payload: PrizeCreateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Creates a prize for an event (organizer/admin only)."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not payload.title.strip() or not payload.amount.strip() or not payload.placement.strip():
        raise HTTPException(status_code=400, detail="Title, amount, and placement are required")

    prize_id = "prz_" + secrets.token_hex(6)
    prize = Prize(
        id=prize_id,
        event_id=event.id,
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else "",
        amount=payload.amount.strip(),
        placement=payload.placement.strip(),
    )
    db.add(prize)
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="CREATE_PRIZE",
            target_type="Prize",
            target_id=prize.id,
            details=f"REST v1: prize {prize.title} for event {event.id}",
        )
    )
    db.commit()
    db.refresh(prize)
    return {
        "id": prize.id,
        "event_id": prize.event_id,
        "title": prize.title,
        "description": prize.description,
        "amount": prize.amount,
        "placement": prize.placement,
    }


@router.get("/prizes/{prize_id}", summary="Get prize details")
def get_prize_v1(prize_id: str, db: Session = Depends(get_db)):
    """Retrieves single prize details."""
    prize = db.query(Prize).filter(Prize.id == prize_id).first()
    if not prize:
        raise HTTPException(status_code=404, detail="Prize not found")
    return {
        "id": prize.id,
        "event_id": prize.event_id,
        "title": prize.title,
        "description": prize.description,
        "amount": prize.amount,
        "placement": prize.placement,
    }


@router.put("/prizes/{prize_id}", summary="Update prize")
def update_prize_v1(
    prize_id: str,
    payload: PrizeUpdateV1,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Updates prize details (organizer/admin only)."""
    prize = db.query(Prize).filter(Prize.id == prize_id).first()
    if not prize:
        raise HTTPException(status_code=404, detail="Prize not found")

    if payload.title is not None:
        prize.title = payload.title.strip()
    if payload.description is not None:
        prize.description = payload.description.strip()
    if payload.amount is not None:
        prize.amount = payload.amount.strip()
    if payload.placement is not None:
        prize.placement = payload.placement.strip()

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE_PRIZE",
            target_type="Prize",
            target_id=prize.id,
            details=f"REST v1: updated prize {prize.title}",
        )
    )
    db.commit()
    db.refresh(prize)
    return {
        "id": prize.id,
        "event_id": prize.event_id,
        "title": prize.title,
        "description": prize.description,
        "amount": prize.amount,
        "placement": prize.placement,
    }


@router.delete("/prizes/{prize_id}", summary="Delete prize")
def delete_prize_v1(
    prize_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Deletes a prize (organizer/admin only)."""
    prize = db.query(Prize).filter(Prize.id == prize_id).first()
    if not prize:
        raise HTTPException(status_code=404, detail="Prize not found")

    event_id = prize.event_id
    db.delete(prize)
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="DELETE_PRIZE",
            target_type="Prize",
            target_id=prize_id,
            details=f"REST v1: deleted prize {prize_id} from event {event_id}",
        )
    )
    db.commit()
    return {"message": "Prize deleted successfully", "id": prize_id}


# ============================================================================
# Comments Endpoints
# ============================================================================
@router.get("/projects/{project_id}/comments", summary="List comments for project")
def list_comments_v1(project_id: str, db: Session = Depends(get_db)):
    """Lists unflagged public comments for a project."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    comments = db.scalars(
        select(Comment)
        .where(Comment.project_id == project_id, Comment.is_flagged == 0)
        .order_by(Comment.created_at.asc())
    ).all()

    return [
        {
            "id": c.id,
            "project_id": c.project_id,
            "user_id": c.user_id,
            "author_name": c.author_name,
            "content": c.content,
            "created_at": c.created_at.isoformat(),
        }
        for c in comments
    ]


@router.post("/projects/{project_id}/comments", status_code=status.HTTP_201_CREATED, summary="Add comment to project")
def add_comment_v1(
    project_id: str,
    payload: CommentCreateV1,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Adds a comment to a submitted project with rate limiting and audit logging."""
    client_ip = get_client_ip(request)
    check_rate_limit(db, f"comment:{client_ip}", max_requests=10, window_seconds=300)

    project = db.get(Project, project_id)
    if not project or project.status != "submitted":
        raise HTTPException(status_code=404, detail="Submitted project not found")

    content_clean = payload.content.strip()
    if not content_clean:
        raise HTTPException(status_code=422, detail="Comment content cannot be blank")

    if user:
        author_name = user.name
        user_id = user.id
    else:
        author_name = (payload.author_name or "").strip() or "Community Contributor"
        user_id = None

    comment = Comment(
        project_id=project_id,
        user_id=user_id,
        author_name=author_name,
        content=content_clean,
        is_flagged=0,
    )
    db.add(comment)
    db.add(
        AuditLog(
            user_id=user_id,
            action="COMMENT_ADDED",
            target_type="project",
            target_id=project_id,
            details=f"REST v1: {author_name}, IP={client_ip}",
        )
    )
    db.commit()
    db.refresh(comment)

    return {
        "id": comment.id,
        "project_id": comment.project_id,
        "author_name": comment.author_name,
        "content": comment.content,
        "created_at": comment.created_at.isoformat(),
    }


@router.delete("/comments/{comment_id}", summary="Delete comment")
def delete_comment_v1(
    comment_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Deletes a comment (author or organizer/admin only)."""
    comment = db.get(Comment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    is_author = comment.user_id and comment.user_id == user.id
    is_mod = user.role in ["organizer", "admin"]
    if not (is_author or is_mod):
        raise HTTPException(status_code=403, detail="Forbidden")

    db.delete(comment)
    db.add(
        AuditLog(
            user_id=user.id,
            action="COMMENT_DELETED",
            target_type="comment",
            target_id=str(comment_id),
            details=f"REST v1: deleted by {user.role} {user.email}",
        )
    )
    db.commit()
    return {"status": "deleted", "comment_id": comment_id}


# ============================================================================
# Community Voting Endpoints
# ============================================================================
@router.get("/events/{event_id}/ballot", summary="Get randomized voting ballot")
def get_voting_ballot_v1(
    event_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Returns submitted projects in randomized order to prevent ballot position bias."""
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    projects = db.scalars(
        select(Project).where(Project.event_id == event_id, Project.status == "submitted")
    ).all()

    client_ip = get_client_ip(request)
    seed_str = f"{user.id if user else client_ip}-{event_id}"
    seed_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16) % (2**32)

    shuffled = list(projects)
    rng = random.Random(seed_val)
    rng.shuffle(shuffled)

    return {
        "event_id": event.id,
        "event_name": event.name,
        "voting_mode": event.voting_mode,
        "voting_opens": event.voting_opens.isoformat() if event.voting_opens else None,
        "voting_closes": event.voting_closes.isoformat() if event.voting_closes else None,
        "ballot": [
            {
                "id": p.id,
                "title": p.title,
                "summary": p.summary,
                "track": p.track.name if p.track else "General",
                "team": p.team.name if p.team else "Independent",
                "repo_url": p.repo_url,
            }
            for p in shuffled
        ],
    }


@router.post("/events/{event_id}/vote", summary="Cast community vote")
def cast_community_vote_v1(
    event_id: str,
    payload: VoteCastV1,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Casts a community vote with rate limiting, duplicate detection, and window enforcement."""
    client_ip = get_client_ip(request)
    check_rate_limit(db, f"vote:{client_ip}", max_requests=10, window_seconds=60)

    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    now = datetime.datetime.now(datetime.timezone.utc)
    if event.voting_opens:
        vo = event.voting_opens if event.voting_opens.tzinfo else event.voting_opens.replace(tzinfo=datetime.timezone.utc)
        if now < vo:
            raise HTTPException(status_code=400, detail="Voting has not opened yet for this event.")
    if event.voting_closes:
        vc = event.voting_closes if event.voting_closes.tzinfo else event.voting_closes.replace(tzinfo=datetime.timezone.utc)
        if now > vc:
            raise HTTPException(status_code=400, detail="Voting has ended for this event.")

    project = db.get(Project, payload.project_id)
    if not project or project.event_id != event_id or project.status != "submitted":
        raise HTTPException(status_code=404, detail="Submitted project not found for this event")

    mode = event.voting_mode or "authenticated"
    voter_type = mode
    voter_email = None

    if mode == "authenticated":
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required to cast a community vote.")
        voter_identifier = f"user:{user.id}"
        voter_email = user.email
    elif mode == "email_gated":
        voucher = None
        if payload.voucher_token:
            voucher = db.get(VotingVoucher, payload.voucher_token)
            if not voucher or voucher.event_id != event_id or voucher.is_used:
                raise HTTPException(status_code=403, detail="Invalid or already redeemed voting voucher.")
            voter_identifier = f"email:{voucher.email.lower()}"
            voter_email = voucher.email
        elif payload.email:
            email_clean = payload.email.strip().lower()
            voucher = db.scalar(
                select(VotingVoucher).where(
                    VotingVoucher.event_id == event_id,
                    VotingVoucher.email == email_clean,
                    VotingVoucher.is_used == 0,
                )
            )
            if not voucher:
                raise HTTPException(status_code=403, detail="Email is not registered on the voting allowlist or voucher was already used.")
            voter_identifier = f"email:{email_clean}"
            voter_email = email_clean
        else:
            raise HTTPException(status_code=400, detail="email_gated voting requires a voucher_token or registered email.")
    elif mode == "open":
        token = payload.voter_token or "anon"
        raw_id = f"{client_ip}:{token}"
        voter_identifier = f"open:{hashlib.sha256(raw_id.encode()).hexdigest()[:24]}"
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported voting mode: {mode}")

    # Duplicate check
    existing_vote = db.scalar(
        select(CommunityVote).where(
            CommunityVote.event_id == event_id,
            CommunityVote.project_id == payload.project_id,
            CommunityVote.voter_identifier == voter_identifier,
        )
    )
    if existing_vote:
        raise HTTPException(status_code=409, detail="Duplicate vote detected: you have already voted for this project.")

    vote = CommunityVote(
        event_id=event_id,
        project_id=payload.project_id,
        voter_identifier=voter_identifier,
        voter_type=voter_type,
        voter_email=voter_email,
        voter_ip=client_ip,
    )
    db.add(vote)
    if mode == "email_gated" and voucher:
        voucher.is_used = 1

    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action="VOTE_CAST",
            target_type="project",
            target_id=payload.project_id,
            details=f"REST v1: community vote by {voter_identifier}",
        )
    )
    db.commit()
    db.refresh(vote)

    return {"status": "success", "vote_id": vote.id, "project_id": vote.project_id}


@router.get("/events/{event_id}/community-results", summary="Get community voting results")
def get_community_results_v1(
    event_id: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Retrieves community standings. Hidden from non-privileged users while voting window is open."""
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    is_privileged = user is not None and user.role in ["organizer", "admin"]
    now = datetime.datetime.now(datetime.timezone.utc)
    is_window_active = False
    if event.voting_closes:
        vc = event.voting_closes if event.voting_closes.tzinfo else event.voting_closes.replace(tzinfo=datetime.timezone.utc)
        if now < vc:
            is_window_active = True

    if (is_window_active or not event.voting_results_public) and not is_privileged:
        raise HTTPException(status_code=403, detail="Community voting results are hidden while the voting window is open.")

    tallies = db.execute(
        select(
            Project.id,
            Project.title,
            func.count(CommunityVote.id).label("vote_count"),
        )
        .join(CommunityVote, CommunityVote.project_id == Project.id, isouter=True)
        .where(Project.event_id == event_id, Project.status == "submitted")
        .group_by(Project.id, Project.title)
        .order_by(func.count(CommunityVote.id).desc())
    ).all()

    ranked = [
        {"rank": rank, "project_id": row[0], "title": row[1], "votes": row[2]}
        for rank, row in enumerate(tallies, start=1)
    ]

    return {
        "event_id": event.id,
        "results_published": bool(event.voting_results_public),
        "total_votes": sum(r["votes"] for r in ranked),
        "standings": ranked,
    }


@router.put("/events/{event_id}/voting-config", summary="Update community voting settings")
def update_voting_config_v1(
    event_id: str,
    payload: VotingConfigV1,
    db: Session = Depends(get_db),
    user: User = Depends(require_organizer),
):
    """Configures voting mode, window dates, and publication status (organizer/admin only)."""
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if payload.voting_mode is not None:
        if payload.voting_mode not in ["authenticated", "email_gated", "open"]:
            raise HTTPException(status_code=400, detail="Invalid voting mode")
        event.voting_mode = payload.voting_mode

    if payload.voting_opens is not None:
        event.voting_opens = payload.voting_opens
    if payload.voting_closes is not None:
        event.voting_closes = payload.voting_closes
    if payload.voting_results_public is not None:
        event.voting_results_public = 1 if payload.voting_results_public else 0

    db.add(
        AuditLog(
            user_id=user.id,
            action="VOTING_CONFIG_UPDATED",
            target_type="event",
            target_id=event_id,
            details=f"REST v1: mode={event.voting_mode}, public={event.voting_results_public}",
        )
    )
    db.commit()
    return {
        "status": "updated",
        "voting_mode": event.voting_mode,
        "voting_results_public": bool(event.voting_results_public),
    }


@router.post("/events/{event_id}/vouchers", summary="Create voting voucher token")
def create_voting_voucher_v1(
    event_id: str,
    payload: VoucherCreateV1,
    db: Session = Depends(get_db),
    user: User = Depends(require_organizer),
):
    """Issues a voting voucher for email-gated allowlists (organizer/admin only)."""
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    email_clean = payload.email.strip().lower()
    token = f"vch_{hashlib.sha256(f'{event_id}:{email_clean}:{datetime.datetime.now(datetime.timezone.utc)}'.encode()).hexdigest()[:16]}"

    voucher = VotingVoucher(token=token, event_id=event_id, email=email_clean, is_used=0)
    db.add(voucher)
    db.commit()
    return {"voucher_token": token, "event_id": event_id, "email": email_clean}


@router.get("/events/{event_id}/vouchers", summary="List voting vouchers")
def list_voting_vouchers_v1(
    event_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_organizer),
):
    """Lists issued vouchers for organizer review (organizer/admin only)."""
    vouchers = db.scalars(select(VotingVoucher).where(VotingVoucher.event_id == event_id)).all()
    return [
        {"token": v.token, "email": v.email, "is_used": bool(v.is_used), "created_at": v.created_at.isoformat()}
        for v in vouchers
    ]


# ============================================================================
# Results Export & Audit Logs
# ============================================================================
@router.get("/export/csv", summary="Export results CSV")
def export_results_csv_v1(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Exports hackathon results as a CSV spreadsheet (organizer/admin only)."""
    csv_content = generate_results_csv(db)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="judgeforge_results.csv"'},
    )


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
