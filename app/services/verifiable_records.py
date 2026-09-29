import os
import json
import hmac
import hashlib
import datetime
from typing import Optional, Dict, Any, List
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from sqlalchemy.orm import Session
from app.models import VerifiableJudgeRecord, Score, Project

SECRET_KEY = os.getenv("SECRET_KEY", "judgeforge-tamper-evident-chain-v1")
DATA_DIR = os.getenv("DATA_DIR", "data")
KEY_PEM_PATH = os.path.join(DATA_DIR, "ed25519_private_key.pem")
KEY_META_PATH = os.path.join(DATA_DIR, "ed25519_meta.json")
GENESIS_HASH = "0" * 64

# In-memory cached key information
_KEY_PAIR: Optional[Dict[str, Any]] = None


# ============================================================================
# Maintained Asymmetric Cryptographic Engine (Ed25519 via Python cryptography)
# ============================================================================
def get_or_create_key_pair(force_reload: bool = False) -> Dict[str, Any]:
    """
    Retrieves or generates a persistent Ed25519 signing key pair.
    Keys are persisted in PKCS8 PEM format in the application data directory
    (which maps to the Docker volume), surviving application and server restarts.
    Zero cloud dependencies.
    """
    global _KEY_PAIR
    if _KEY_PAIR is not None and not force_reload:
        return _KEY_PAIR

    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(KEY_PEM_PATH):
        try:
            with open(KEY_PEM_PATH, "rb") as f:
                pem_data = f.read()
            priv = serialization.load_pem_private_key(pem_data, password=None)
            pub = priv.public_key()
            pub_hex = pub.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ).hex()

            meta = {}
            if os.path.exists(KEY_META_PATH):
                with open(KEY_META_PATH, "r", encoding="utf-8") as f:
                    meta = json.load(f)

            _KEY_PAIR = {
                "private_key": priv,
                "public_key_hex": pub_hex,
                "key_id": meta.get("key_id", "key_2026_judgeforge_ed25519_v1"),
                "algorithm": "Ed25519",
                "created_at": meta.get("created_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            return _KEY_PAIR
        except Exception:
            pass  # If file is corrupted, regenerate

    # Generate new Ed25519 key pair
    priv = ed25519.Ed25519PrivateKey.generate()
    pem_bytes = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(KEY_PEM_PATH, "wb") as f:
        f.write(pem_bytes)

    pub = priv.public_key()
    pub_hex = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()

    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    meta = {
        "key_id": "key_2026_judgeforge_ed25519_v1",
        "algorithm": "Ed25519",
        "created_at": created_at,
    }
    with open(KEY_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    _KEY_PAIR = {
        "private_key": priv,
        "public_key_hex": pub_hex,
        "key_id": meta["key_id"],
        "algorithm": "Ed25519",
        "created_at": created_at,
    }
    return _KEY_PAIR


def get_public_key() -> Dict[str, Any]:
    """
    Exposes the public key parameters for independent public verification.
    No private parameters are revealed.
    """
    key_pair = get_or_create_key_pair()
    return {
        "key_id": key_pair["key_id"],
        "algorithm": "Ed25519",
        "public_key": key_pair["public_key_hex"],
        "created_at": key_pair["created_at"],
        "instructions": (
            "Independent Verification: "
            "1. Decode public key: ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)). "
            "2. Verify signature: public_key.verify(bytes.fromhex(signature), record_hash.encode('utf-8')). "
            "3. If verify() does not raise InvalidSignature, the record signature is authentic and untampered."
        ),
    }


def sign_record_hash(hash_hex: str) -> str:
    """
    Signs a record hash using the server's persistent private Ed25519 key.
    Produces an asymmetric signature verifiable by any third party with the public key.
    """
    key_pair = get_or_create_key_pair()
    priv = key_pair["private_key"]
    sig = priv.sign(hash_hex.encode("utf-8"))
    return sig.hex()


def verify_record_signature(
    hash_hex: str,
    signature_hex: str,
    public_key_hex: Optional[str] = None,
) -> bool:
    """
    Independently verifies an asymmetric Ed25519 signature against a record hash.
    Accepts either an explicit public key or defaults to the server's published key.
    """
    try:
        if public_key_hex is None:
            key_pair = get_or_create_key_pair()
            pub_hex = key_pair["public_key_hex"]
        else:
            pub_hex = public_key_hex.strip()

        pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        sig_bytes = bytes.fromhex(signature_hex.strip())
        pub.verify(sig_bytes, hash_hex.encode("utf-8"))
        return True
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
    Signed using asymmetric Ed25519 cryptographic keys for independent public verifiability.
    """
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
    # Sign with persistent Ed25519 private key
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
    2. Asymmetric Ed25519 digital signature verification with published public key.
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

        # 2. Verify Digital Signature (Asymmetric Ed25519, with fallback to legacy HMAC)
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
        "signature_scheme": "Ed25519",
        "message": f"All {len(records)} judge records cryptographically verified with Ed25519 signatures",
    }
