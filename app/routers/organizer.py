from typing import Optional, List, Dict
import secrets
from app.models import PasswordCredential
from app.passwords import hash_password
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Query
from fastapi.responses import HTMLResponse
from app.templating import templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Judge, Project, Score, Track, RubricCriterion, AuditLog, Event, Prize
from app.auth import get_current_user, get_current_user_optional, require_organizer, require_admin
from app.services.scoring import compute_leaderboard, generate_results_csv

router = APIRouter(tags=["Organizer"])


class RubricUpdateSchema(BaseModel):
    weights: Dict[str, float]


@router.get("/api/export.csv")
def export_csv(
    event_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    csv_content = generate_results_csv(db, event_id=event_id)
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
    event_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    p_query = db.query(Project).filter(Project.status == "submitted")
    if event_id:
        p_query = p_query.filter(Project.event_id == event_id)
    projects = p_query.all()
    total_projects = len(projects)

    judges = db.query(Judge).all()
    total_judges = len(judges)

    s_query = db.query(Score)
    if event_id:
        s_query = s_query.join(Project).filter(Project.event_id == event_id)
    total_scores = s_query.count()

    judge_progress = []
    inactive_judges = []
    for j in judges:
        sc_q = db.query(Score).filter(Score.judge_id == j.id)
        if event_id:
            sc_q = sc_q.join(Project).filter(Project.event_id == event_id)
        score_count = sc_q.count()
        track_names = [t.name for t in j.tracks]
        j_info = {
            "id": j.id,
            "name": j.name,
            "email": j.email,
            "tracks": track_names,
            "scored_count": score_count,
        }
        judge_progress.append(j_info)
        if score_count == 0:
            inactive_judges.append(j_info)

    target_reviews = min(2, total_judges) if total_judges > 0 else 1

    zero_review_projects = []
    below_target_projects = []
    fully_reviewed_projects = []

    for p in projects:
        sc_cnt = len(p.scores)
        p_data = {"id": p.id, "title": p.title, "track_name": p.track.name if p.track else "", "review_count": sc_cnt}
        if sc_cnt == 0:
            zero_review_projects.append(p_data)
        elif sc_cnt < target_reviews:
            below_target_projects.append(p_data)
        else:
            fully_reviewed_projects.append(p_data)

    judges_not_started = [j for j in judge_progress if j["scored_count"] == 0]
    judges_in_progress = [j for j in judge_progress if 0 < j["scored_count"] < total_projects]
    judges_complete = [j for j in judge_progress if total_projects > 0 and j["scored_count"] >= total_projects]

    tracks = db.query(Track).filter(Track.event_id == event_id).all() if event_id else db.query(Track).all()
    track_stats = []
    for trk in tracks:
        p_count = db.query(Project).filter(Project.track_id == trk.id, Project.status == "submitted").count()
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

    expected_reviews = total_projects * (total_judges if total_judges > 0 else 1)
    completion_pct = round((total_scores / max(1, expected_reviews)) * 100) if expected_reviews > 0 else 0

    return {
        "total_projects": total_projects,
        "total_judges": total_judges,
        "total_scores": total_scores,
        "expected_reviews": expected_reviews,
        "completion_pct": min(100, completion_pct),
        "target_reviews_per_project": target_reviews,
        "zero_review_count": len(zero_review_projects),
        "zero_review_projects": zero_review_projects,
        "below_target_count": len(below_target_projects),
        "below_target_projects": below_target_projects,
        "fully_reviewed_count": len(fully_reviewed_projects),
        "fully_reviewed_projects": fully_reviewed_projects,
        "judges_not_started": judges_not_started,
        "judges_in_progress": judges_in_progress,
        "judges_complete": judges_complete,
        "inactive_judges_count": len(inactive_judges),
        "inactive_judges": inactive_judges,
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
    event_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user or user.role not in ["organizer", "admin"]:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"user": user, "error": "Organizer or Admin access required."},
        )

    events = db.query(Event).order_by(Event.created_at.desc()).all()
    target_event_id = event_id or request.query_params.get("event_id")
    event = None
    if target_event_id:
        event = db.get(Event, target_event_id)
    if not event:
        event = db.get(Event, "evt_01") or (events[0] if events else None)

    leaderboard = compute_leaderboard(db, event_id=event.id if event else None)
    tracks = db.query(Track).filter(Track.event_id == event.id).all() if event else db.query(Track).all()
    judges = db.query(Judge).all()
    criteria = db.query(RubricCriterion).filter(RubricCriterion.event_id == event.id).all() if event else db.query(RubricCriterion).all()
    audit_logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(20).all()
    prizes = db.query(Prize).filter(Prize.event_id == event.id).all() if event else []
    all_users = db.query(User).order_by(User.role.asc(), User.email.asc()).all() if user.role == "admin" else []

    # Detailed judging progress analytics for the selected event
    event_projects = db.query(Project).filter(Project.event_id == event.id, Project.status == "submitted").all() if event else []
    total_event_submissions = len(event_projects)

    event_track_ids = {t.id for t in tracks}
    event_judges = [j for j in judges if any(t.id in event_track_ids for t in j.tracks)] if event_track_ids else judges
    total_assigned_judges = len(event_judges)

    expected_reviews = total_event_submissions * max(1, total_assigned_judges)
    completed_reviews = db.query(Score).join(Project).filter(Project.event_id == event.id).count() if event else 0
    completion_pct = round((completed_reviews / max(1, expected_reviews)) * 100) if expected_reviews > 0 else 0

    target_reviews = min(2, total_assigned_judges) if total_assigned_judges > 0 else 1

    zero_review_projects = []
    below_target_projects = []
    fully_reviewed_projects = []

    for p in event_projects:
        sc_cnt = len(p.scores)
        p_data = {"id": p.id, "title": p.title, "track_name": p.track.name if p.track else "", "review_count": sc_cnt}
        if sc_cnt == 0:
            zero_review_projects.append(p_data)
        elif sc_cnt < target_reviews:
            below_target_projects.append(p_data)
        else:
            fully_reviewed_projects.append(p_data)

    judges_not_started = []
    judges_in_progress = []
    judges_complete = []

    for j in event_judges:
        j_count = db.query(Score).join(Project).filter(Project.event_id == event.id, Score.judge_id == j.id).count() if event else 0
        j_info = {"id": j.id, "name": j.name, "email": j.email, "reviewed_count": j_count}
        if j_count == 0:
            judges_not_started.append(j_info)
        elif total_event_submissions > 0 and j_count >= total_event_submissions:
            judges_complete.append(j_info)
        else:
            judges_in_progress.append(j_info)

    total_rubric_weight = round(sum(c.weight for c in criteria), 2) if criteria else 3.0

    judging_progress = {
        "total_submissions": total_event_submissions,
        "total_judges": total_assigned_judges,
        "target_reviews_per_project": target_reviews,
        "expected_reviews": expected_reviews,
        "completed_reviews": completed_reviews,
        "completion_pct": min(100, completion_pct),
        "zero_review_projects": zero_review_projects,
        "zero_review_count": len(zero_review_projects),
        "below_target_projects": below_target_projects,
        "below_target_count": len(below_target_projects),
        "fully_reviewed_projects": fully_reviewed_projects,
        "fully_reviewed_count": len(fully_reviewed_projects),
        "judges_not_started": judges_not_started,
        "judges_in_progress": judges_in_progress,
        "judges_complete": judges_complete,
        "inactive_judges": judges_not_started,
        "inactive_judge_count": len(judges_not_started),
        "total_rubric_weight": total_rubric_weight,
    }

    return templates.TemplateResponse(
        request=request,
        name="organizer_dashboard.html",
        context={
            "user": user,
            "event": event,
            "events": events,
            "leaderboard": leaderboard,
            "tracks": tracks,
            "judges": judges,
            "criteria": criteria,
            "total_rubric_weight": total_rubric_weight,
            "prizes": prizes,
            "all_users": all_users,
            "audit_logs": audit_logs,
            "judging_progress": judging_progress,
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


# ============================================================================
# Event Prizes
# ============================================================================
class PrizeCreateSchema(BaseModel):
    title: str
    description: Optional[str] = None
    amount: str
    placement: str


class PrizeUpdateSchema(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[str] = None
    placement: Optional[str] = None


@router.get("/api/events/{event_id}/prizes")
def list_event_prizes(
    event_id: str,
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
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


@router.post("/api/events/{event_id}/prizes", status_code=status.HTTP_201_CREATED)
def create_prize(
    event_id: str,
    payload: PrizeCreateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
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
            details=f"Created prize {prize.title} ({prize.amount}) for event {event.id}",
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


@router.put("/api/prizes/{prize_id}")
def update_prize(
    prize_id: str,
    payload: PrizeUpdateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    prize = db.query(Prize).filter(Prize.id == prize_id).first()
    if not prize:
        raise HTTPException(status_code=404, detail="Prize not found")

    if payload.title is not None:
        if not payload.title.strip():
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        prize.title = payload.title.strip()
    if payload.description is not None:
        prize.description = payload.description.strip()
    if payload.amount is not None:
        if not payload.amount.strip():
            raise HTTPException(status_code=400, detail="Amount cannot be empty")
        prize.amount = payload.amount.strip()
    if payload.placement is not None:
        if not payload.placement.strip():
            raise HTTPException(status_code=400, detail="Placement cannot be empty")
        prize.placement = payload.placement.strip()

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE_PRIZE",
            target_type="Prize",
            target_id=prize.id,
            details=f"Updated prize {prize.title}",
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


@router.delete("/api/prizes/{prize_id}")
def delete_prize(
    prize_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
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
            details=f"Deleted prize {prize_id} from event {event_id}",
        )
    )
    db.commit()
    return {"message": "Prize deleted successfully", "id": prize_id}


# ============================================================================
# Results Publication
# ============================================================================
@router.post("/api/events/{event_id}/publish")
def publish_results(
    event_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    import datetime
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
            details=f"Results published by {current_user.email}",
        )
    )
    db.commit()

    try:
        from app.services.webhooks import dispatch_webhook
        dispatch_webhook(
            db=db,
            event_id=event.id,
            event_type="results.published",
            payload={
                "event_id": event.id,
                "event_name": event.name,
                "published_at": now.isoformat(),
            },
        )
    except Exception:
        pass

    return {
        "message": "Results published successfully",
        "event_id": event.id,
        "results_published": True,
        "results_published_at": now.isoformat(),
    }


@router.post("/api/events/{event_id}/unpublish")
def unpublish_results(
    event_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
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
            details=f"Results unpublished by {current_user.email}",
        )
    )
    db.commit()
    return {
        "message": "Results unpublished successfully",
        "event_id": event.id,
        "results_published": False,
    }


@router.get("/api/results")
def get_public_results(
    request: Request,
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    user = get_current_user_optional(request, db)
    target_event_id = event_id or "evt_01"
    event = db.query(Event).filter(Event.id == target_event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # If unpublished, only organizer and admin can preview
    if not event.results_published:
        if not user or user.role not in ["organizer", "admin"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Results have not been published yet",
            )
        is_preview = True
    else:
        is_preview = False

    leaderboard = compute_leaderboard(db, event.id)
    prizes = db.query(Prize).filter(Prize.event_id == event.id).order_by(Prize.created_at.asc()).all()

    # NOTE: leaderboard computes aggregated scores (raw_score, normalized_score, rank)
    # Individual judge scores and judge names are NEVER included here!
    return {
        "event_id": event.id,
        "event_name": event.name,
        "published": bool(event.results_published),
        "published_at": event.results_published_at.isoformat() if event.results_published_at else None,
        "preview": is_preview,
        "prizes": [
            {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "amount": p.amount,
                "placement": p.placement,
            }
            for p in prizes
        ],
        "leaderboard": [
            {
                "rank": row["rank"],
                "project_id": row["project_id"],
                "title": row["title"],
                "team_name": row["team_name"],
                "track_name": row["track_name"],
                "raw_score": row["raw_score"],
                "normalized_score": row["normalized_score"],
                "review_count": row["review_count"],
            }
            for row in leaderboard
        ],
    }


@router.get("/results", response_class=HTMLResponse)
def view_public_results(
    request: Request,
    event_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    events = db.query(Event).order_by(Event.created_at.desc()).all()
    target_event_id = event_id or request.query_params.get("event_id")
    event = None
    if target_event_id:
        event = db.query(Event).filter(Event.id == target_event_id).first()
    if not event:
        event = db.get(Event, "evt_01") or (events[0] if events else None)

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    is_organizer_or_admin = bool(user and user.role in ["organizer", "admin"])
    leaderboard = []
    if event.results_published or is_organizer_or_admin:
        leaderboard = compute_leaderboard(db, event.id)

    prizes = db.query(Prize).filter(Prize.event_id == event.id).order_by(Prize.created_at.asc()).all()

    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "user": user,
            "event": event,
            "events": events,
            "prizes": prizes,
            "leaderboard": leaderboard,
            "is_published": bool(event.results_published),
            "is_organizer_or_admin": is_organizer_or_admin,
        },
    )


# ============================================================================
# Admin User Role Management
# ============================================================================
class UserRoleUpdateSchema(BaseModel):
    role: str


@router.get("/api/admin/users")
def list_users_for_admin(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    users = db.query(User).order_by(User.role.asc(), User.email.asc()).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,
        }
        for u in users
    ]


@router.put("/api/admin/users/{user_id}/role")
def update_user_role(
    user_id: str,
    payload: UserRoleUpdateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    valid_roles = {"participant", "judge", "organizer", "admin"}
    new_role = payload.role.strip().lower()
    if new_role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed roles: {', '.join(sorted(valid_roles))}")

    old_role = target_user.role
    target_user.role = new_role
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE_USER_ROLE",
            target_type="User",
            target_id=target_user.id,
            details=f"Changed role of {target_user.email} from {old_role} to {new_role}",
        )
    )
    db.commit()
    db.refresh(target_user)
    return {
        "id": target_user.id,
        "email": target_user.email,
        "name": target_user.name,
        "role": target_user.role,
        "message": f"User role updated to {target_user.role}",
    }

