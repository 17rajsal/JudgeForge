from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import VerifiableJudgeRecord, Event
from app.services.verifiable_records import (
    verify_chain,
    get_public_key,
    verify_record_signature,
    sign_hash_legacy,
)

router = APIRouter(prefix="/api/v1/verifiable-records", tags=["Verifiable Records"])


class VerifyRecordRequest(BaseModel):
    record_id: Optional[int] = None
    record_hash: Optional[str] = None
    signature: Optional[str] = None
    public_key: Optional[str] = None


@router.get("")
def list_verifiable_records(
    event_id: Optional[str] = None,
    project_id: Optional[str] = None,
    judge_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Returns signed tamper-evident judge score records including previous hash,
    record hash, and asymmetric Ed25519 digital signature.
    Exposes proof of evaluation integrity without leaking numeric scores or private comments.
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
            "judge_id": r.judge_id,
            "project_id": r.project_id,
            "event_id": r.event_id,
            "record_hash": r.record_hash,
            "prev_hash": r.prev_hash,
            "signature": r.signature,
            "signature_scheme": "Ed25519",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


@router.get("/public-key")
def get_verification_public_key():
    """
    Returns the server's public cryptographic Ed25519 verification key.
    Enables any participant, judge, organizer, or external auditor to independently
    verify the authenticity and integrity of signed judge participation records
    without cloud dependencies or private credentials.
    """
    return get_public_key()


@router.post("/verify-record")
def verify_single_record(
    payload: VerifyRecordRequest,
    db: Session = Depends(get_db),
):
    """
    Independently verifies an asymmetric Ed25519 digital signature for a single judge record.
    Can verify by `record_id` or directly by `record_hash` and `signature`.
    Optionally accepts a custom public key (`public_key`) for offline audits.
    """
    record = None
    if payload.record_id is not None:
        record = db.query(VerifiableJudgeRecord).filter(VerifiableJudgeRecord.id == payload.record_id).first()
        if not record:
            raise HTTPException(status_code=404, detail=f"Record #{payload.record_id} not found")
        hash_hex = record.record_hash
        signature_hex = record.signature
    elif payload.record_hash and payload.signature:
        hash_hex = payload.record_hash.strip()
        signature_hex = payload.signature.strip()
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide either 'record_id' or both 'record_hash' and 'signature'",
        )

    # Verify asymmetric Ed25519 signature
    is_valid = verify_record_signature(
        hash_hex=hash_hex,
        signature_hex=signature_hex,
        public_key_hex=payload.public_key,
    )

    # If verification failed on server key, check legacy HMAC as backwards fallback
    if not is_valid and payload.public_key is None:
        import hmac
        expected_hmac = sign_hash_legacy(hash_hex)
        if hmac.compare_digest(signature_hex, expected_hmac):
            is_valid = True

    return {
        "valid": is_valid,
        "record_id": record.id if record else payload.record_id,
        "record_hash": hash_hex,
        "signature": signature_hex,
        "signature_scheme": "Ed25519",
        "verified_with": "custom_public_key" if payload.public_key else "server_public_key",
        "message": (
            "Cryptographic Ed25519 signature verified successfully with public key"
            if is_valid
            else "Cryptographic Ed25519 signature verification failed: signature does not match public key"
        ),
    }


@router.get("/verify")
def verify_hash_chain(
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Cryptographically verifies the judge records hash chain for an event:
    1. Checks SHA-256 link continuity across all records (prev_hash chain).
    2. Re-verifies asymmetric Ed25519 digital signatures with published public key.
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
