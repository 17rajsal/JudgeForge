import time
from sqlalchemy import select, delete, func
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models import RateLimitRecord


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
