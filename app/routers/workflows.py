import datetime as dt
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user, require_organizer, require_participant_or_organizer
from app.models import Event, Track, Team, TeamMember, TeamInvite, AuditLog, RubricCriterion

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

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
def workspace(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    events = db.query(Event).order_by(Event.created_at.desc()).all()
    teams = db.query(Team).join(TeamMember).filter(TeamMember.user_id == user.id).all()
    return templates.TemplateResponse(request=request, name="workspace.html", context={"user":user,"events":events,"teams":teams})

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
    if not db.get(Team, invitation.team_id):
        raise HTTPException(404, "Team no longer exists")
    changed=db.query(TeamInvite).filter_by(token=token,used_by=None).update({"used_by":user.id})
    if not changed:
        raise HTTPException(409, "Invitation already used")
    if not db.query(TeamMember).filter_by(team_id=invitation.team_id,user_id=user.id).first():
        db.add(TeamMember(team_id=invitation.team_id,user_id=user.id,email=user.email))
    db.commit()
    return {"team_id":invitation.team_id}
