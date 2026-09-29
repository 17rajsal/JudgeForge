import datetime
import hashlib
import random
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event, Project, CommunityVote, VotingVoucher, AuditLog, User
from app.auth import get_current_user_optional, require_organizer, require_admin
from app.rate_limiter import check_rate_limit

router = APIRouter(prefix="/api/events/{event_id}", tags=["community-voting"])


class VoteRequest(BaseModel):
    project_id: str
    voter_token: Optional[str] = None  # for open mode client fingerprint
    voucher_token: Optional[str] = None  # for email-gated voucher mode
    email: Optional[str] = None  # for email allowlist mode


class VotingConfigUpdate(BaseModel):
    voting_mode: Optional[str] = None  # "authenticated", "email_gated", "open"
    voting_opens: Optional[datetime.datetime] = None
    voting_closes: Optional[datetime.datetime] = None
    voting_results_public: Optional[bool] = None


class VoucherCreateRequest(BaseModel):
    email: str


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


@router.get("/ballot")
def get_randomized_ballot(
    event_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Return projects in randomized ballot order to prevent position bias."""
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Only show submitted projects in the ballot
    projects = db.scalars(
        select(Project).where(Project.event_id == event_id, Project.status == "submitted")
    ).all()

    # Determine voter seed for deterministic session-based randomization
    client_ip = get_client_ip(request)
    seed_str = f"{user.id if user else client_ip}-{event_id}"
    seed_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16) % (2**32)

    shuffled = list(projects)
    rng = random.Random(seed_val)
    rng.shuffle(shuffled)

    return {
        "event_id": event.id,
        "event_name": event.name,
        "voting_mode": event.voting_mode,
        "voting_opens": event.voting_opens.isoformat() if event.voting_opens else None,
        "voting_closes": event.voting_closes.isoformat() if event.voting_closes else None,
        "ballot": [
            {
                "id": p.id,
                "title": p.title,
                "summary": p.summary,
                "track": p.track.name if p.track else "General",
                "team": p.team.name if p.team else "Independent",
                "repo_url": p.repo_url,
            }
            for p in shuffled
        ],
    }


@router.post("/vote")
def cast_community_vote(
    event_id: str,
    payload: VoteRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Cast a community vote with rate limiting, duplicate detection, and window enforcement."""
    client_ip = get_client_ip(request)

    # 1. Rate Limiting: max 10 vote requests per minute per IP
    check_rate_limit(db, f"vote:{client_ip}", max_requests=10, window_seconds=60)

    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # 2. Check voting window
    now = datetime.datetime.now(datetime.timezone.utc)
    if event.voting_opens:
        voting_opens_utc = event.voting_opens if event.voting_opens.tzinfo else event.voting_opens.replace(tzinfo=datetime.timezone.utc)
        if now < voting_opens_utc:
            db.add(AuditLog(
                user_id=user.id if user else None,
                action="VOTE_REJECTED_WINDOW_EARLY",
                target_type="event",
                target_id=event_id,
                details=f"IP: {client_ip}. Voting has not opened yet.",
            ))
            db.commit()
            raise HTTPException(status_code=400, detail="Voting has not opened yet for this event.")

    if event.voting_closes:
        voting_closes_utc = event.voting_closes if event.voting_closes.tzinfo else event.voting_closes.replace(tzinfo=datetime.timezone.utc)
        if now > voting_closes_utc:
            db.add(AuditLog(
                user_id=user.id if user else None,
                action="VOTE_REJECTED_WINDOW_CLOSED",
                target_type="event",
                target_id=event_id,
                details=f"IP: {client_ip}. Voting window has ended.",
            ))
            db.commit()
            raise HTTPException(status_code=400, detail="Voting has ended for this event.")

    # 3. Check project validity
    project = db.get(Project, payload.project_id)
    if not project or project.event_id != event_id or project.status != "submitted":
        raise HTTPException(status_code=404, detail="Submitted project not found for this event")

    # 4. Resolve voter identifier & enforce access mode
    mode = event.voting_mode or "authenticated"
    voter_type = mode
    voter_email = None

    if mode == "authenticated":
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required to cast a community vote.")
        voter_identifier = f"user:{user.id}"
        voter_email = user.email

    elif mode == "email_gated":
        # Email-gated mode: requires a locally-issued voucher token or allowlisted email
        voucher = None
        if payload.voucher_token:
            voucher = db.get(VotingVoucher, payload.voucher_token)
            if not voucher or voucher.event_id != event_id or voucher.is_used:
                raise HTTPException(status_code=403, detail="Invalid or already redeemed voting voucher.")
            voter_identifier = f"email:{voucher.email.lower()}"
            voter_email = voucher.email
        elif payload.email:
            email_clean = payload.email.strip().lower()
            # Check if email is in voucher allowlist
            voucher = db.scalar(
                select(VotingVoucher).where(
                    VotingVoucher.event_id == event_id,
                    VotingVoucher.email == email_clean,
                    VotingVoucher.is_used == 0,
                )
            )
            if not voucher:
                raise HTTPException(status_code=403, detail="Email is not registered on the voting allowlist or voucher was already used.")
            voter_identifier = f"email:{email_clean}"
            voter_email = email_clean
        else:
            raise HTTPException(status_code=400, detail="email_gated voting requires a valid voucher_token or allowlisted email.")

    elif mode == "open":
        # Open mode: voter token/fingerprint + IP
        token = payload.voter_token or "anon"
        raw_id = f"{client_ip}:{token}"
        voter_identifier = f"open:{hashlib.sha256(raw_id.encode()).hexdigest()[:24]}"
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported voting mode: {mode}")

    # 5. Duplicate Vote Detection
    existing_vote = db.scalar(
        select(CommunityVote).where(
            CommunityVote.event_id == event_id,
            CommunityVote.project_id == payload.project_id,
            CommunityVote.voter_identifier == voter_identifier,
        )
    )
    if existing_vote:
        db.add(AuditLog(
            user_id=user.id if user else None,
            action="VOTE_DUPLICATE_REJECTED",
            target_type="project",
            target_id=payload.project_id,
            details=f"Voter {voter_identifier} attempted duplicate vote. IP: {client_ip}.",
        ))
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="Duplicate vote detected: you have already voted for this project.",
        )

    # 6. Record Vote
    vote = CommunityVote(
        event_id=event_id,
        project_id=payload.project_id,
        voter_identifier=voter_identifier,
        voter_type=voter_type,
        voter_email=voter_email,
        voter_ip=client_ip,
    )
    db.add(vote)

    # Mark voucher as used if email_gated
    if mode == "email_gated" and voucher:
        voucher.is_used = 1

    # 7. Readable Audit Log
    db.add(AuditLog(
        user_id=user.id if user else None,
        action="VOTE_CAST",
        target_type="project",
        target_id=payload.project_id,
        details=f"Community vote recorded. Voter: {voter_identifier}, Mode: {mode}, IP: {client_ip}.",
    ))
    db.commit()
    db.refresh(vote)

    try:
        from app.services.webhooks import dispatch_webhook
        dispatch_webhook(
            db=db,
            event_id=event_id,
            event_type="vote.cast",
            payload={
                "vote_id": vote.id,
                "project_id": vote.project_id,
                "voter_type": vote.voter_type,
            },
        )
    except Exception:
        pass

    return {
        "status": "success",
        "vote_id": vote.id,
        "project_id": vote.project_id,
        "message": "Community vote successfully recorded!",
    }


@router.get("/community-results")
def get_community_results(
    event_id: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Retrieve community voting results.
    Enforces voting-window result hiding: hidden from participants until closed/revealed.
    """
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    is_privileged = user is not None and user.role in ["organizer", "admin"]

    # Check if results are public or if voting has concluded and organizer revealed
    now = datetime.datetime.now(datetime.timezone.utc)
    is_window_active = False
    if event.voting_closes:
        voting_closes_utc = event.voting_closes if event.voting_closes.tzinfo else event.voting_closes.replace(tzinfo=datetime.timezone.utc)
        if now < voting_closes_utc:
            is_window_active = True

    # If results are not published or window is active, hide results from non-privileged users
    if (is_window_active or not event.voting_results_public) and not is_privileged:
        raise HTTPException(
            status_code=403,
            detail="Community voting results are hidden while the voting window is open.",
        )

    # Compute community tallies
    tallies = db.execute(
        select(
            Project.id,
            Project.title,
            func.count(CommunityVote.id).label("vote_count"),
        )
        .join(CommunityVote, CommunityVote.project_id == Project.id, isouter=True)
        .where(Project.event_id == event_id, Project.status == "submitted")
        .group_by(Project.id, Project.title)
        .order_by(func.count(CommunityVote.id).desc())
    ).all()

    ranked = []
    for rank, row in enumerate(tallies, start=1):
        ranked.append({
            "rank": rank,
            "project_id": row[0],
            "title": row[1],
            "votes": row[2],
        })

    peoples_choice = ranked[0] if ranked and ranked[0]["votes"] > 0 else None

    return {
        "event_id": event.id,
        "results_published": bool(event.voting_results_public),
        "is_preview": not event.voting_results_public and is_privileged,
        "total_votes": sum(r["votes"] for r in ranked),
        "peoples_choice": peoples_choice,
        "standings": ranked,
    }


@router.put("/voting-config")
def update_voting_config(
    event_id: str,
    payload: VotingConfigUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_organizer),
):
    """Configure community voting parameters (Organizer/Admin only)."""
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if payload.voting_mode is not None:
        if payload.voting_mode not in ["authenticated", "email_gated", "open"]:
            raise HTTPException(status_code=400, detail="Invalid voting mode")
        event.voting_mode = payload.voting_mode

    if payload.voting_opens is not None:
        event.voting_opens = payload.voting_opens
    if payload.voting_closes is not None:
        event.voting_closes = payload.voting_closes

    if payload.voting_results_public is not None:
        event.voting_results_public = 1 if payload.voting_results_public else 0

    db.add(AuditLog(
        user_id=user.id,
        action="VOTING_CONFIG_UPDATED",
        target_type="event",
        target_id=event_id,
        details=f"Mode: {event.voting_mode}, ResultsPublic: {event.voting_results_public}",
    ))
    db.commit()

    return {
        "status": "updated",
        "voting_mode": event.voting_mode,
        "voting_opens": event.voting_opens.isoformat() if event.voting_opens else None,
        "voting_closes": event.voting_closes.isoformat() if event.voting_closes else None,
        "voting_results_public": bool(event.voting_results_public),
    }


@router.post("/vouchers")
def create_voting_voucher(
    event_id: str,
    payload: VoucherCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_organizer),
):
    """Generate a local voting voucher token for an email-gated allowlist (Organizer/Admin only)."""
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    email_clean = payload.email.strip().lower()
    token = f"vch_{hashlib.sha256(f'{event_id}:{email_clean}:{datetime.datetime.now(datetime.timezone.utc)}'.encode()).hexdigest()[:16]}"

    voucher = VotingVoucher(
        token=token,
        event_id=event_id,
        email=email_clean,
        is_used=0,
    )
    db.add(voucher)
    db.add(AuditLog(
        user_id=user.id,
        action="VOUCHER_ISSUED",
        target_type="voucher",
        target_id=token,
        details=f"Issued for {email_clean} on event {event_id}",
    ))
    db.commit()

    return {
        "voucher_token": token,
        "event_id": event_id,
        "email": email_clean,
    }


@router.get("/vouchers")
def list_voting_vouchers(
    event_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_organizer),
):
    """List local voting vouchers for organizer review (Organizer/Admin only)."""
    vouchers = db.scalars(
        select(VotingVoucher).where(VotingVoucher.event_id == event_id)
    ).all()
    return [
        {
            "token": v.token,
            "email": v.email,
            "is_used": bool(v.is_used),
            "created_at": v.created_at.isoformat(),
        }
        for v in vouchers
    ]
