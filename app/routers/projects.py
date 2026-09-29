import datetime
import secrets
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Project, Event, Track, Team, TeamMember, User, AuditLog
from app.auth import (
    get_current_user_optional,
    get_current_user,
    require_participant_or_organizer,
)

router = APIRouter(tags=["Projects"])
from app.templating import templates


class ProjectCreateSchema(BaseModel):
    title: str
    summary: Optional[str] = None
    repo_url: Optional[str] = None
    track_id: Optional[str] = None
    team_id: Optional[str] = None
    event_id: Optional[str] = None
    status: Optional[str] = "submitted"


class ProjectUpdateSchema(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    repo_url: Optional[str] = None
    track_id: Optional[str] = None
    status: Optional[str] = None


@router.get("/submit", response_class=HTMLResponse)
def view_submit_form(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    event = db.get(Event, request.query_params.get("event_id", "evt_01"))
    if not event:
        raise HTTPException(404, "Event not found")
    tracks = db.query(Track).filter_by(event_id=event.id).all()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    close_time = event.submissions_close if event else None
    if close_time and close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=datetime.timezone.utc)
    is_closed = now_utc > close_time if close_time else False

    return templates.TemplateResponse(
        request=request,
        name="submit.html",
        context={
            "event": event,
            "tracks": tracks,
            "user": user,
            "is_closed": is_closed,
        },
    )


@router.get("/projects", response_class=HTMLResponse)
def view_gallery(
    request: Request,
    track: Optional[str] = Query(None, description="Filter by track ID"),
    q: Optional[str] = Query(None, description="Search by title or summary"),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    query = db.query(Project).filter(Project.status == "submitted")
    if track:
        query = query.filter(Project.track_id == track)
    if q:
        search_filter = f"%{q.strip()}%"
        query = query.filter(
            (Project.title.ilike(search_filter)) | (Project.summary.ilike(search_filter))
        )

    projects = query.order_by(Project.submitted_at.desc()).all()
    tracks = db.query(Track).all()
    event = db.query(Event).first()

    return templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={
            "projects": projects,
            "tracks": tracks,
            "current_track": track,
            "search_query": q or "",
            "event": event,
            "user": user,
        },
    )


@router.get("/api/projects")
def list_projects_api(
    track: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Project).filter(Project.status == "submitted")
    if track:
        query = query.filter(Project.track_id == track)
    if q:
        search_filter = f"%{q.strip()}%"
        query = query.filter(
            (Project.title.ilike(search_filter)) | (Project.summary.ilike(search_filter))
        )
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


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def view_project_detail(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    is_team_member = False
    if user:
        if user.role in ["organizer", "admin"]:
            is_team_member = True
        else:
            team_membership = (
                db.query(TeamMember)
                .filter(TeamMember.team_id == project.team_id, TeamMember.user_id == user.id)
                .first()
            )
            if team_membership:
                is_team_member = True

    if project.status == "draft" and not is_team_member:
        raise HTTPException(status_code=404, detail="Project not found")

    event = project.event or db.query(Event).first()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    close_time = event.submissions_close if event else None
    if close_time and close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=datetime.timezone.utc)
    is_closed = now_utc > close_time if close_time else False

    return templates.TemplateResponse(
        request=request,
        name="project_detail.html",
        context={
            "project": project,
            "user": user,
            "is_team_member": is_team_member,
            "is_closed": is_closed,
            "event": event,
        },
    )


@router.post("/api/projects", status_code=status.HTTP_201_CREATED)
def submit_project(
    payload: ProjectCreateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    # 1. Enforce Event Deadline Server-Side
    event_id = payload.event_id or "evt_01"
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=400, detail="No active hackathon event found")

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    close_time = event.submissions_close
    if close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=datetime.timezone.utc)

    if now_utc > close_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Submissions for {event.name} closed on {close_time.strftime('%Y-%m-%d %H:%M:%S UTC')}. New submissions are refused.",
        )

    # 2. Determine Team
    team_id = payload.team_id
    if not team_id:
        membership = (
            db.query(TeamMember).join(Team).filter(TeamMember.user_id == current_user.id, Team.event_id == event.id).first()
        )
        if membership:
            team_id = membership.team_id
        else:
            # Create a default team for this participant
            team_count = db.query(Team).count()
            team_id = "tm_" + secrets.token_hex(12)
            new_team = Team(
                id=team_id,
                event_id=event.id,
                name=f"Team {current_user.name}",
            )
            db.add(new_team)
            db.flush()
            db.add(TeamMember(team_id=team_id, email=current_user.email, user_id=current_user.id))
            db.flush()

    team = db.get(Team, team_id)
    if not team or team.event_id != event.id:
        raise HTTPException(400, "Team must belong to this event")
    if current_user.role != "organizer" and not db.query(TeamMember).filter_by(team_id=team_id, user_id=current_user.id).first():
        raise HTTPException(403, "Only team members can submit for this team")

    # 3. Determine Track
    track_id = payload.track_id
    if not track_id:
        first_track = db.query(Track).filter(Track.event_id == event.id).first()
        if not first_track:
            raise HTTPException(status_code=400, detail="No tracks configured for this event")
        track_id = first_track.id

    track = db.get(Track, track_id)
    if not track or track.event_id != event.id:
        raise HTTPException(400, "Track must belong to this event")
    if not payload.title.strip():
        raise HTTPException(400, "Project title is required")

    # 4. Generate project ID
    prj_count = db.query(Project).count()
    project_id = "prj_" + secrets.token_hex(12)

    proj_status = "submitted"
    if payload.status:
        s = payload.status.strip().lower()
        if s in ("draft", "submitted"):
            proj_status = s

    project = Project(
        id=project_id,
        event_id=event.id,
        team_id=team_id,
        track_id=track_id,
        title=payload.title.strip(),
        summary=payload.summary.strip() if payload.summary else "",
        repo_url=payload.repo_url.strip() if payload.repo_url else "",
        status=proj_status,
        submitted_at=now_utc,
    )
    db.add(project)

    # Audit log
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="CREATE_PROJECT",
            target_type="Project",
            target_id=project.id,
            details=f"Title: {project.title}, Team: {team_id}, Track: {track_id}, Status: {proj_status}",
        )
    )

    db.commit()
    db.refresh(project)

    if proj_status == "submitted":
        try:
            from app.services.webhooks import dispatch_webhook
            dispatch_webhook(
                db=db,
                event_id=event.id,
                event_type="project.submitted",
                payload={
                    "project_id": project.id,
                    "title": project.title,
                    "team_id": project.team_id,
                    "track_id": project.track_id,
                    "status": project.status,
                },
            )
        except Exception:
            pass

    return {
        "id": project.id,
        "title": project.title,
        "summary": project.summary,
        "repo_url": project.repo_url,
        "team_id": project.team_id,
        "track_id": project.track_id,
        "status": project.status,
        "submitted_at": project.submitted_at.isoformat(),
        "message": "Project submitted successfully" if proj_status == "submitted" else "Project draft saved successfully",
    }


@router.put("/api/projects/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectUpdateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 1. Enforce Deadline Server-Side (except for organizers and admins)
    if current_user.role not in ["organizer", "admin"]:
        event = project.event or db.query(Event).first()
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        close_time = event.submissions_close if event else None
        if close_time and close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=datetime.timezone.utc)
        if close_time and now_utc > close_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Submissions are closed. Projects cannot be edited after the deadline.",
            )

        # 2. Check team authorization
        membership = (
            db.query(TeamMember)
            .filter(TeamMember.team_id == project.team_id, TeamMember.user_id == current_user.id)
            .first()
        )
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only team members can edit this project",
            )

    if payload.title is not None:
        project.title = payload.title.strip()
    if payload.summary is not None:
        project.summary = payload.summary.strip()
    if payload.repo_url is not None:
        project.repo_url = payload.repo_url.strip()
    if payload.track_id is not None:
        track = db.get(Track, payload.track_id)
        if not track or track.event_id != project.event_id:
            raise HTTPException(400, "Track must belong to this event")
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
            details=f"Updated by {current_user.email}",
        )
    )
    db.commit()
    db.refresh(project)

    return {"message": "Project updated successfully", "id": project.id, "status": project.status}


@router.post("/api/projects/{project_id}/submit")
def finalize_project_submission(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    event = project.event or db.query(Event).first()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    close_time = event.submissions_close if event else None
    if close_time and close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=datetime.timezone.utc)

    # 1. Enforce deadline server-side
    if close_time and now_utc > close_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submissions are closed. Drafts cannot be submitted after the deadline.",
        )

    # 2. Check team authorization
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
            details=f"Draft finalized and submitted by {current_user.email}",
        )
    )
    db.commit()
    db.refresh(project)

    try:
        from app.services.webhooks import dispatch_webhook
        dispatch_webhook(
            db=db,
            event_id=event.id if event else "evt_01",
            event_type="project.submitted",
            payload={
                "project_id": project.id,
                "title": project.title,
                "team_id": project.team_id,
                "track_id": project.track_id,
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


class TeamCreateSchema(BaseModel):
    name: str
    event_id: Optional[str] = None


class TeamMemberAddSchema(BaseModel):
    email: str


@router.get("/api/teams")
def list_teams(db: Session = Depends(get_db)):
    teams = db.query(Team).all()
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


@router.post("/api/teams", status_code=status.HTTP_201_CREATED)
def create_team(
    payload: TeamCreateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_participant_or_organizer),
):
    event = (
        db.query(Event).filter(Event.id == (payload.event_id or "evt_01")).first()
    )
    if not event or not payload.name.strip():
        raise HTTPException(400, "Valid event and team name required")
    team_count = db.query(Team).count()
    team_id = "tm_" + secrets.token_hex(12)
    team = Team(
        id=team_id,
        event_id=event.id if event else "evt_01",
        name=payload.name.strip(),
    )
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, email=current_user.email, user_id=current_user.id))
    db.commit()
    db.refresh(team)
    return {"id": team.id, "name": team.name, "members": [current_user.email]}


@router.post("/api/teams/{team_id}/members")
def add_team_member(
    team_id: str,
    payload: TeamMemberAddSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Only existing team members or organizer can add members
    if current_user.role != "organizer":
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

