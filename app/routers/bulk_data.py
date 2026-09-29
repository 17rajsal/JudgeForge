import json
import hashlib
import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (
    Event, Track, RubricCriterion, Prize, Project, Team, TeamMember,
    Score, Comment, VotingVoucher, AuditLog, User
)
from app.auth import require_organizer

router = APIRouter(prefix="/api/v1", tags=["Bulk Data"])


class BulkImportSchema(BaseModel):
    version: Optional[str] = "1.0"
    checksum: Optional[str] = None
    data: Dict[str, Any]


@router.get("/export/bulk")
def export_bulk_data(
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """
    Exports full event data package including tracks, criteria, prizes, projects, teams,
    scores, and comments, with a SHA-256 integrity checksum for offline portability.
    """
    target_event_id = event_id or "evt_01"
    event = db.query(Event).filter(Event.id == target_event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    tracks = db.query(Track).filter(Track.event_id == event.id).all()
    criteria = db.query(RubricCriterion).filter(RubricCriterion.event_id == event.id).all()
    prizes = db.query(Prize).filter(Prize.event_id == event.id).all()
    projects = db.query(Project).filter(Project.event_id == event.id).all()

    project_ids = [p.id for p in projects]
    scores = db.query(Score).filter(Score.project_id.in_(project_ids)).all() if project_ids else []
    comments = db.query(Comment).filter(Comment.project_id.in_(project_ids)).all() if project_ids else []
    vouchers = db.query(VotingVoucher).filter(VotingVoucher.event_id == event.id).all()

    export_dict = {
        "event": {
            "id": event.id,
            "name": event.name,
            "submissions_close": event.submissions_close.isoformat() if event.submissions_close else None,
            "results_published": bool(event.results_published),
            "voting_mode": event.voting_mode,
        },
        "tracks": [
            {"id": t.id, "name": t.name}
            for t in tracks
        ],
        "rubric_criteria": [
            {
                "id": c.id,
                "name": c.name,
                "label": c.label,
                "description": c.description,
                "weight": c.weight,
                "min_score": c.min_score,
                "max_score": c.max_score,
            }
            for c in criteria
        ],
        "prizes": [
            {
                "id": pr.id,
                "title": pr.title,
                "description": pr.description,
                "amount": pr.amount,
                "placement": pr.placement,
            }
            for pr in prizes
        ],
        "projects": [
            {
                "id": p.id,
                "title": p.title,
                "summary": p.summary,
                "track_id": p.track_id,
                "team_id": p.team_id,
                "status": p.status,
                "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
            }
            for p in projects
        ],
        "scores": [
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
        ],
        "comments": [
            {
                "id": c.id,
                "project_id": c.project_id,
                "author_name": c.author_name,
                "content": c.content,
                "is_flagged": bool(c.is_flagged),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in comments
        ],
        "vouchers": [
            {"token": v.token, "email": v.email, "is_used": bool(v.is_used)}
            for v in vouchers
        ],
    }

    canonical_bytes = json.dumps(export_dict, sort_keys=True).encode("utf-8")
    checksum = hashlib.sha256(canonical_bytes).hexdigest()

    return {
        "version": "1.0",
        "event_id": event.id,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "checksum": checksum,
        "data": export_dict,
    }


@router.post("/import/bulk", status_code=status.HTTP_200_OK)
def import_bulk_data(
    payload: BulkImportSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """
    Imports bulk event data atomically.
    Validates payload integrity and schema. Rolls back all changes if any record is invalid.
    """
    data = payload.data
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Import payload 'data' must be a valid dictionary")

    # If checksum provided, verify it
    if payload.checksum:
        canonical_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
        computed_checksum = hashlib.sha256(canonical_bytes).hexdigest()
        if payload.checksum.lower() != computed_checksum.lower():
            raise HTTPException(status_code=400, detail="Data integrity checksum mismatch: payload may be corrupted or altered")

    event_data = data.get("event")
    if not event_data or not isinstance(event_data, dict) or "id" not in event_data:
        raise HTTPException(status_code=400, detail="Missing or malformed 'event' object in import payload")

    imported_counts = {
        "tracks": 0,
        "criteria": 0,
        "prizes": 0,
        "projects": 0,
        "scores": 0,
    }

    try:
        # Atomic transaction execution
        # 1. Upsert or update event
        target_event_id = event_data["id"]
        event = db.query(Event).filter(Event.id == target_event_id).first()
        if not event:
            event = Event(
                id=target_event_id,
                name=event_data.get("name", "Imported Event"),
            )
            db.add(event)
            db.flush()
        else:
            if "name" in event_data:
                event.name = event_data["name"]

        # 2. Tracks
        for t_info in data.get("tracks", []):
            if "id" not in t_info or "name" not in t_info:
                raise ValueError("Track must have 'id' and 'name'")
            track = db.query(Track).filter(Track.id == t_info["id"]).first()
            if not track:
                track = Track(
                    id=t_info["id"],
                    event_id=event.id,
                    name=t_info["name"],
                )
                db.add(track)
                imported_counts["tracks"] += 1
            else:
                track.name = t_info["name"]

        # 3. Criteria
        for c_info in data.get("rubric_criteria", []):
            if "id" not in c_info or "name" not in c_info or "weight" not in c_info:
                raise ValueError("Rubric criterion must have 'id', 'name', and 'weight'")
            crit = db.query(RubricCriterion).filter(RubricCriterion.id == c_info["id"]).first()
            if not crit:
                crit = RubricCriterion(
                    id=c_info["id"],
                    event_id=event.id,
                    name=c_info["name"],
                    label=c_info.get("label", c_info["name"].capitalize()),
                    description=c_info.get("description", ""),
                    weight=float(c_info["weight"]),
                    min_score=int(c_info.get("min_score", 1)),
                    max_score=int(c_info.get("max_score", 5)),
                )
                db.add(crit)
                imported_counts["criteria"] += 1
            else:
                crit.weight = float(c_info["weight"])

        # 4. Prizes
        for p_info in data.get("prizes", []):
            if "id" not in p_info or "title" not in p_info or "amount" not in p_info:
                raise ValueError("Prize must have 'id', 'title', and 'amount'")
            prize = db.query(Prize).filter(Prize.id == p_info["id"]).first()
            if not prize:
                prize = Prize(
                    id=p_info["id"],
                    event_id=event.id,
                    title=p_info["title"],
                    description=p_info.get("description", ""),
                    amount=p_info["amount"],
                    placement=p_info.get("placement", "General"),
                )
                db.add(prize)
                imported_counts["prizes"] += 1

        # 5. Projects
        for proj_info in data.get("projects", []):
            if "id" not in proj_info or "title" not in proj_info:
                raise ValueError("Project must have 'id' and 'title'")
            proj = db.query(Project).filter(Project.id == proj_info["id"]).first()
            if not proj:
                track_id = proj_info.get("track_id")
                if not track_id:
                    t = db.query(Track).filter(Track.event_id == event.id).first()
                    track_id = t.id if t else "trk_01"
                team_id = proj_info.get("team_id")
                if not team_id:
                    tm = db.query(Team).filter(Team.event_id == event.id).first()
                    team_id = tm.id if tm else "tea_01"

                proj = Project(
                    id=proj_info["id"],
                    event_id=event.id,
                    title=proj_info["title"],
                    summary=proj_info.get("summary", ""),
                    track_id=track_id,
                    team_id=team_id,
                    status=proj_info.get("status", "submitted"),
                )
                db.add(proj)
                imported_counts["projects"] += 1
            else:
                proj.title = proj_info["title"]
                if "summary" in proj_info:
                    proj.summary = proj_info["summary"]

        # 6. Scores
        for s_info in data.get("scores", []):
            if "judge_id" not in s_info or "project_id" not in s_info:
                raise ValueError("Score must specify 'judge_id' and 'project_id'")
            existing_score = (
                db.query(Score)
                .filter(
                    Score.judge_id == s_info["judge_id"],
                    Score.project_id == s_info["project_id"],
                )
                .first()
            )
            if not existing_score:
                sc = Score(
                    judge_id=s_info["judge_id"],
                    project_id=s_info["project_id"],
                    functionality=s_info.get("functionality"),
                    quality=s_info.get("quality"),
                    innovation=s_info.get("innovation"),
                    comment=s_info.get("comment"),
                )
                db.add(sc)
                imported_counts["scores"] += 1

        db.add(
            AuditLog(
                user_id=current_user.id,
                action="IMPORT_BULK_DATA",
                target_type="Event",
                target_id=event.id,
                details=f"Imported bulk dataset: {imported_counts}",
            )
        )
        db.commit()
    except Exception as ex:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Import failed and was rolled back: {str(ex)}",
        )

    return {
        "message": "Bulk import completed successfully with transaction safety",
        "event_id": event.id,
        "imported_counts": imported_counts,
    }
