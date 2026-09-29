import time
import os
from sqlalchemy import select, delete, func
from sqlalchemy.orm import Session
from fastapi import HTTPException, Request
from app.models import RateLimitRecord


def get_client_ip(request: Request) -> str:
    """Extract client IP securely.
    Default self-hosted deployment uses request.client.host directly to prevent
    arbitrary clients from forging X-Forwarded-For to bypass rate limits or spoof votes.

    If the TRUSTED_PROXIES environment variable is configured (comma-separated list of IP addresses),
    X-Forwarded-For is only parsed when the immediate peer is in TRUSTED_PROXIES.
    """
    peer_ip = request.client.host if request.client else "127.0.0.1"
    trusted_env = os.environ.get("TRUSTED_PROXIES", "").strip()
    if trusted_env:
        trusted_proxies = {ip.strip() for ip in trusted_env.split(",") if ip.strip()}
        if peer_ip in trusted_proxies:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
    return peer_ip


def check_rate_limit(db: Session, key: str, max_requests: int, window_seconds: float) -> None:
    """Enforce rate limits per key (e.g. action:ip) backed by SQLite.
    Raises HTTPException 429 if the request limit is exceeded.
    """
    now = time.time()
    cutoff = now - window_seconds

    # 1. Clean up old records periodically
    db.execute(delete(RateLimitRecord).where(RateLimitRecord.timestamp < cutoff))

    # 2. Count requests in the current window
    count = db.scalar(
        select(func.count(RateLimitRecord.id)).where(
            RateLimitRecord.key == key,
            RateLimitRecord.timestamp >= cutoff,
        )
    ) or 0

    if count >= max_requests:
        db.commit()
        retry_after = int(window_seconds)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {max_requests} requests per {int(window_seconds)}s.",
            headers={"Retry-After": str(retry_after)},
        )

    # 3. Record new hit
    db.add(RateLimitRecord(key=key, timestamp=now))
    db.commit()
