from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import VerifiableJudgeRecord, Event
from app.services.verifiable_records import verify_chain

router = APIRouter(prefix="/api/v1/verifiable-records", tags=["Verifiable Records"])


@router.get("")
def list_verifiable_records(
    event_id: Optional[str] = None,
    project_id: Optional[str] = None,
    judge_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Returns signed tamper-evident judge score records including previous hash, record hash, and HMAC signature.
    """
    query = db.query(VerifiableJudgeRecord)
    if event_id:
        query = query.filter(VerifiableJudgeRecord.event_id == event_id)
    if project_id:
        query = query.filter(VerifiableJudgeRecord.project_id == project_id)
    if judge_id:
        query = query.filter(VerifiableJudgeRecord.judge_id == judge_id)

    records = query.order_by(VerifiableJudgeRecord.id.asc()).limit(limit).all()

    return [
        {
            "id": r.id,
            "score_id": r.score_id,
            "judge_id": r.judge_id,
            "project_id": r.project_id,
            "event_id": r.event_id,
            "record_hash": r.record_hash,
            "prev_hash": r.prev_hash,
            "signature": r.signature,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


@router.get("/verify")
def verify_hash_chain(
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Cryptographically verifies the judge records hash chain for an event:
    1. Checks SHA-256 link continuity across all records (prev_hash chain).
    2. Re-verifies HMAC-SHA256 signatures with server secret.
    3. Detects any retroactively altered score values or deleted reviews.
    """
    target_event_id = event_id or "evt_01"
    event = db.query(Event).filter(Event.id == target_event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    result = verify_chain(db, target_event_id)
    return {
        "event_id": target_event_id,
        "event_name": event.name,
        **result,
    }
