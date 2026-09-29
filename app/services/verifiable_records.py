import os
import hmac
import hashlib
import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from app.models import VerifiableJudgeRecord, Score, Project

SECRET_KEY = os.getenv("SECRET_KEY", "judgeforge-tamper-evident-chain-v1")
GENESIS_HASH = "0" * 64


def build_canonical_record_string(
    prev_hash: str,
    score_id: int,
    judge_id: str,
    project_id: str,
    functionality: Optional[int],
    quality: Optional[int],
    innovation: Optional[int],
    submitted_at: datetime.datetime,
) -> str:
    """Builds a deterministic canonical string for hash calculation."""
    iso_time = submitted_at.isoformat() if submitted_at else ""
    return f"{prev_hash}|{score_id}|{judge_id}|{project_id}|{functionality}|{quality}|{innovation}|{iso_time}"


def sign_hash(hash_hex: str) -> str:
    """Signs a record hash with the server's cryptographic HMAC key."""
    return hmac.new(SECRET_KEY.encode("utf-8"), hash_hex.encode("utf-8"), hashlib.sha256).hexdigest()


def append_verifiable_record(db: Session, score: Score) -> VerifiableJudgeRecord:
    """
    Appends a new signed tamper-evident record for a judge's score into the event's hash chain.
    """
    # Determine event_id from project
    project = db.query(Project).filter(Project.id == score.project_id).first()
    event_id = project.event_id if project else "evt_01"

    # Get the latest record in this event's chain
    last_record = (
        db.query(VerifiableJudgeRecord)
        .filter(VerifiableJudgeRecord.event_id == event_id)
        .order_by(VerifiableJudgeRecord.id.desc())
        .first()
    )

    prev_hash = last_record.record_hash if last_record else GENESIS_HASH

    canonical_str = build_canonical_record_string(
        prev_hash=prev_hash,
        score_id=score.id,
        judge_id=score.judge_id,
        project_id=score.project_id,
        functionality=score.functionality,
        quality=score.quality,
        innovation=score.innovation,
        submitted_at=score.submitted_at,
    )

    record_hash = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
    signature = sign_hash(record_hash)

    record = VerifiableJudgeRecord(
        score_id=score.id,
        judge_id=score.judge_id,
        project_id=score.project_id,
        event_id=event_id,
        record_hash=record_hash,
        prev_hash=prev_hash,
        signature=signature,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def verify_chain(db: Session, event_id: str) -> Dict[str, Any]:
    """
    Verifies the integrity of the cryptographic hash chain for an event.
    Returns validation status, total records, and details on any tampering.
    """
    records = (
        db.query(VerifiableJudgeRecord)
        .filter(VerifiableJudgeRecord.event_id == event_id)
        .order_by(VerifiableJudgeRecord.id.asc())
        .all()
    )

    if not records:
        return {
            "valid": True,
            "total_records": 0,
            "verified_records": 0,
            "broken_at_id": None,
            "message": "No records in chain yet (clean state)",
        }

    expected_prev = GENESIS_HASH

    for idx, rec in enumerate(records):
        # 1. Verify prev_hash matches expected prior link
        if rec.prev_hash != expected_prev:
            return {
                "valid": False,
                "total_records": len(records),
                "verified_records": idx,
                "broken_at_id": rec.id,
                "message": f"Hash chain linkage broken at record #{rec.id}: expected prev_hash {expected_prev[:12]}..., got {rec.prev_hash[:12]}...",
            }

        # 2. Verify HMAC signature
        expected_sig = sign_hash(rec.record_hash)
        if rec.signature != expected_sig:
            return {
                "valid": False,
                "total_records": len(records),
                "verified_records": idx,
                "broken_at_id": rec.id,
                "message": f"HMAC signature mismatch at record #{rec.id}: record signature is invalid or tampered",
            }

        # 3. Verify record hash against underlying score
        score = db.query(Score).filter(Score.id == rec.score_id).first()
        if not score:
            return {
                "valid": False,
                "total_records": len(records),
                "verified_records": idx,
                "broken_at_id": rec.id,
                "message": f"Underlying score #{rec.score_id} for record #{rec.id} has been deleted or purged",
            }

        canonical_str = build_canonical_record_string(
            prev_hash=rec.prev_hash,
            score_id=score.id,
            judge_id=rec.judge_id,
            project_id=rec.project_id,
            functionality=score.functionality,
            quality=score.quality,
            innovation=score.innovation,
            submitted_at=score.submitted_at,
        )
        expected_hash = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

        if rec.record_hash != expected_hash:
            return {
                "valid": False,
                "total_records": len(records),
                "verified_records": idx,
                "broken_at_id": rec.id,
                "message": f"Score content mismatch at record #{rec.id}: underlying score data was modified after signing",
            }

        # Advance expected prev
        expected_prev = rec.record_hash

    return {
        "valid": True,
        "total_records": len(records),
        "verified_records": len(records),
        "broken_at_id": None,
        "latest_hash": expected_prev,
        "message": f"All {len(records)} judge records cryptographically verified and untampered",
    }
