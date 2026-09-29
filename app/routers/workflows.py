import datetime as dt
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user, get_current_user_optional, require_organizer, require_participant_or_organizer
from app.models import Event, Track, Team, TeamMember, TeamInvite, AuditLog, RubricCriterion, Project
from app.templating import templates

router = APIRouter()

class EventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    submissions_close: dt.datetime
    tracks: list[str] = Field(min_length=1, max_length=30)

@router.post("/api/events", status_code=201)
def create_event(payload: EventCreate, db: Session = Depends(get_db), user=Depends(require_organizer)):
    if not payload.name.strip() or any(not t.strip() for t in payload.tracks):
        raise HTTPException(400, "Event and track names cannot be blank")
    close = payload.submissions_close
    if close.tzinfo is None:
        raise HTTPException(400, "Deadline must include a UTC offset")
    close = close.astimezone(dt.timezone.utc)
    event = Event(id="evt_" + secrets.token_hex(12), name=payload.name.strip(), submissions_close=close)
    db.add(event)
    db.flush()
    for name in dict.fromkeys(t.strip() for t in payload.tracks):
        db.add(Track(id="trk_" + secrets.token_hex(12), event_id=event.id, name=name))
    for name in ("functionality", "quality", "innovation"):
        db.add(RubricCriterion(id="crit_" + secrets.token_hex(12), event_id=event.id, name=name, label=name.title(), weight=1.0))
    db.add(AuditLog(user_id=user.id, action="CREATE_EVENT", target_type="Event", target_id=event.id, details=event.name))
    db.commit()
    return {"id": event.id, "name": event.name}

@router.get("/workspace")
def workspace(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user_optional)):
    if not user:
        from fastapi.responses import RedirectResponse
        invite_token = request.query_params.get("invite")
        redirect_url = f"/login?invite={invite_token}" if invite_token else "/login"
        return RedirectResponse(redirect_url, status_code=303)

    events = db.query(Event).order_by(Event.created_at.desc()).all()
    teams = db.query(Team).join(TeamMember).filter(TeamMember.user_id == user.id).all()
    team_ids = [t.id for t in teams]
    user_projects = (
        db.query(Project).filter(Project.team_id.in_(team_ids)).order_by(Project.submitted_at.desc()).all()
        if team_ids
        else []
    )
    has_draft = any(p.status == "draft" for p in user_projects)
    has_submitted = any(p.status == "submitted" for p in user_projects)
    target_event_id = request.query_params.get("event_id")
    if target_event_id:
        active_event = db.get(Event, target_event_id)
    else:
        active_event = db.get(Event, "evt_01") or (events[0] if events else None)
    active_event_closed = active_event.is_closed if active_event else False
    open_event = next((e for e in events if not e.is_closed), None)
    all_closed = all(e.is_closed for e in events) if events else True
    results_published = any(e.results_published for e in events)

    return templates.TemplateResponse(
        request=request,
        name="workspace.html",
        context={
            "user": user,
            "events": events,
            "open_event": open_event,
            "teams": teams,
            "user_projects": user_projects,
            "has_draft": has_draft,
            "has_submitted": has_submitted,
            "active_event": active_event,
            "active_event_closed": active_event_closed,
            "all_closed": all_closed,
            "results_published": results_published,
        },
    )

@router.post("/api/teams/{team_id}/invites", status_code=201)
def invite(team_id: str, db: Session = Depends(get_db), user=Depends(require_participant_or_organizer)):
    if not db.get(Team, team_id):
        raise HTTPException(404, "Team not found")
    if user.role != "organizer" and not db.query(TeamMember).filter_by(team_id=team_id,user_id=user.id).first():
        raise HTTPException(403, "Only team members may create invitations")
    token = secrets.token_urlsafe(32)
    db.add(TeamInvite(token=token,team_id=team_id,expires_at=dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=2)))
    db.commit()
    return {"invite_url":"/workspace?invite="+token}

@router.post("/api/team-invites/{token}/accept")
def accept(token: str, db: Session = Depends(get_db), user=Depends(require_participant_or_organizer)):
    invitation = db.get(TeamInvite, token)
    now=dt.datetime.now(dt.timezone.utc)
    if not invitation or invitation.used_by or invitation.expires_at.replace(tzinfo=dt.timezone.utc) <= now:
        raise HTTPException(400, "Invitation invalid, expired or already used")
    team = db.get(Team, invitation.team_id)
    if not team:
        raise HTTPException(404, "Team no longer exists")
    changed=db.query(TeamInvite).filter_by(token=token,used_by=None).update({"used_by":user.id})
    if not changed:
        raise HTTPException(409, "Invitation already used")
    if not db.query(TeamMember).filter_by(team_id=invitation.team_id,user_id=user.id).first():
        db.add(TeamMember(team_id=invitation.team_id,user_id=user.id,email=user.email))
    db.commit()
    return {"team_id": invitation.team_id, "team_name": team.name}
