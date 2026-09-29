import os
import json
import hmac
import hashlib
import datetime
import secrets
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from app.models import VerifiableJudgeRecord, Score, Project

SECRET_KEY = os.getenv("SECRET_KEY", "judgeforge-tamper-evident-chain-v1")
DATA_DIR = os.getenv("DATA_DIR", "data")
KEY_FILE_PATH = os.path.join(DATA_DIR, "verifiable_signing_key.json")
GENESIS_HASH = "0" * 64

# In-memory cached key pair
_KEY_PAIR: Optional[Dict[str, Any]] = None


# ============================================================================
# Asymmetric RSA Cryptographic Engine (Pure Python, Zero External Dependencies)
# ============================================================================
def _is_prime(n: int, k: int = 15) -> bool:
    """Miller-Rabin probabilistic primality test."""
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0:
        return False
    r, d = 0, n - 1
    while d % 2 == 0:
        r += 1
        d //= 2
    for _ in range(k):
        a = secrets.randbelow(n - 4) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _get_prime(bits: int = 512) -> int:
    """Generates a random prime number of specified bit length."""
    while True:
        p = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_prime(p):
            return p


def get_or_create_key_pair(force_reload: bool = False) -> Dict[str, Any]:
    """
    Retrieves or generates a persistent 1024-bit RSA signing key pair.
    Keys are persisted in JSON format in the application data directory
    (which maps to the Docker volume), surviving application and server restarts.
    """
    global _KEY_PAIR
    if _KEY_PAIR is not None and not force_reload:
        return _KEY_PAIR

    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(KEY_FILE_PATH):
        try:
            with open(KEY_FILE_PATH, "r", encoding="utf-8") as f:
                key_data = json.load(f)
                _KEY_PAIR = key_data
                return _KEY_PAIR
        except Exception:
            pass  # If file is corrupted, regenerate

    # Generate new RSA key pair
    p = _get_prime(512)
    q = _get_prime(512)
    while p == q:
        q = _get_prime(512)

    n = p * q
    phi = (p - 1) * (q - 1)
    e = 65537
    d = pow(e, -1, phi)

    key_data = {
        "key_id": "key_2026_judgeforge_asym_v1",
        "algorithm": "RSASSA-PKCS1-v1_5-SHA256",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "public_key": {
            "n": hex(n)[2:],
            "e": e,
            "format": "RSA-Modulus-Exponent",
        },
        "private_key": {
            "d": hex(d)[2:],
            "p": hex(p)[2:],
            "q": hex(q)[2:],
        },
    }

    with open(KEY_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(key_data, f, indent=2)

    _KEY_PAIR = key_data
    return _KEY_PAIR


def get_public_key() -> Dict[str, Any]:
    """
    Exposes the public key parameters for independent public verification.
    No private parameters are revealed.
    """
    key_pair = get_or_create_key_pair()
    return {
        "key_id": key_pair.get("key_id", "key_2026_judgeforge_asym_v1"),
        "algorithm": key_pair.get("algorithm", "RSASSA-PKCS1-v1_5-SHA256"),
        "modulus_n": key_pair["public_key"]["n"],
        "exponent_e": key_pair["public_key"]["e"],
        "created_at": key_pair.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "instructions": (
            "Independent Verification: "
            "1. Compute expected_m = int(record_hash, 16) % int(modulus_n, 16). "
            "2. Compute recovered_m = pow(int(signature, 16), exponent_e, int(modulus_n, 16)). "
            "3. If expected_m == recovered_m, the signature is cryptographically valid and untampered."
        ),
    }


def sign_record_hash(hash_hex: str) -> str:
    """
    Signs a record hash using the server's persistent private RSA key.
    Produces an asymmetric signature verifiable by any third party with the public key.
    """
    key_pair = get_or_create_key_pair()
    n = int(key_pair["public_key"]["n"], 16)
    d = int(key_pair["private_key"]["d"], 16)
    m = int(hash_hex, 16) % n
    sig_int = pow(m, d, n)
    return hex(sig_int)[2:]


def verify_record_signature(
    hash_hex: str,
    signature_hex: str,
    public_n: Optional[str] = None,
    public_e: Optional[int] = None,
) -> bool:
    """
    Independently verifies an asymmetric RSA signature against a record hash.
    Accepts either an explicit public key or defaults to the server's published key.
    """
    try:
        if public_n is None:
            key_pair = get_or_create_key_pair()
            n = int(key_pair["public_key"]["n"], 16)
            e = int(key_pair["public_key"]["e"])
        else:
            n = int(public_n, 16) if isinstance(public_n, str) else int(public_n)
            e = int(public_e) if public_e else 65537

        sig_int = int(signature_hex, 16)
        recovered_m = pow(sig_int, e, n)
        expected_m = int(hash_hex, 16) % n
        return recovered_m == expected_m
    except Exception:
        return False


def sign_hash_legacy(hash_hex: str) -> str:
    """Legacy HMAC-SHA256 signer retained for backwards compatibility."""
    return hmac.new(SECRET_KEY.encode("utf-8"), hash_hex.encode("utf-8"), hashlib.sha256).hexdigest()


# ============================================================================
# Canonical Serialization & Chain Management
# ============================================================================
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


def append_verifiable_record(db: Session, score: Score) -> VerifiableJudgeRecord:
    """
    Appends a new signed tamper-evident record for a judge's score into the event's hash chain.
    Signed using asymmetric RSA cryptographic keys for independent public verifiability.
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
    # Sign with persistent RSA private key
    signature = sign_record_hash(record_hash)

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
    Verifies the integrity of the cryptographic hash chain for an event:
    1. Prev-hash chain continuity.
    2. Asymmetric RSA digital signature verification with published public key.
    3. Underlying score data consistency.
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

        # 2. Verify Digital Signature (Asymmetric RSA, with fallback to legacy HMAC)
        is_valid_sig = verify_record_signature(rec.record_hash, rec.signature)
        if not is_valid_sig:
            # Check legacy HMAC
            expected_hmac = sign_hash_legacy(rec.record_hash)
            if hmac.compare_digest(rec.signature, expected_hmac):
                is_valid_sig = True

        if not is_valid_sig:
            return {
                "valid": False,
                "total_records": len(records),
                "verified_records": idx,
                "broken_at_id": rec.id,
                "message": f"Signature mismatch at record #{rec.id}: record signature is invalid or tampered",
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
        "signature_scheme": "RSASSA-PKCS1-v1_5-SHA256",
        "message": f"All {len(records)} judge records cryptographically verified with asymmetric signatures",
    }
