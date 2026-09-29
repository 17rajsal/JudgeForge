import secrets
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import WebhookSubscription, WebhookDeliveryLog, Event, User, AuditLog
from app.auth import require_organizer
from app.services.webhooks import send_test_ping

router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])


class WebhookCreateSchema(BaseModel):
    event_id: str
    target_url: str
    secret: Optional[str] = None
    events_subscribed: Optional[str] = "*"


class WebhookResponseSchema(BaseModel):
    id: str
    event_id: str
    target_url: str
    events_subscribed: str
    is_active: bool
    created_at: str


@router.get("", response_model=List[dict])
def list_webhooks(
    event_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Lists webhook subscriptions for an event (organizer/admin only)."""
    query = db.query(WebhookSubscription)
    if event_id:
        query = query.filter(WebhookSubscription.event_id == event_id)
    subs = query.all()
    return [
        {
            "id": s.id,
            "event_id": s.event_id,
            "target_url": s.target_url,
            "events_subscribed": s.events_subscribed,
            "is_active": bool(s.is_active),
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in subs
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_webhook(
    payload: WebhookCreateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Registers a new outbound webhook subscription with an HMAC signing secret."""
    event = db.query(Event).filter(Event.id == payload.event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    target_url = payload.target_url.strip()
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Target URL must start with http:// or https://")

    secret = payload.secret.strip() if payload.secret else secrets.token_hex(24)
    sub_id = "wh_" + secrets.token_hex(8)

    sub = WebhookSubscription(
        id=sub_id,
        event_id=event.id,
        target_url=target_url,
        secret=secret,
        events_subscribed=payload.events_subscribed or "*",
        is_active=1,
    )
    db.add(sub)
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="CREATE_WEBHOOK",
            target_type="WebhookSubscription",
            target_id=sub.id,
            details=f"Target: {target_url}, Events: {sub.events_subscribed}",
        )
    )
    db.commit()
    db.refresh(sub)

    return {
        "id": sub.id,
        "event_id": sub.event_id,
        "target_url": sub.target_url,
        "secret": sub.secret,
        "events_subscribed": sub.events_subscribed,
        "is_active": True,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


@router.delete("/{webhook_id}")
def delete_webhook(
    webhook_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Deletes an outbound webhook subscription."""
    sub = db.query(WebhookSubscription).filter(WebhookSubscription.id == webhook_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook subscription not found")

    db.delete(sub)
    db.add(
        AuditLog(
            user_id=current_user.id,
            action="DELETE_WEBHOOK",
            target_type="WebhookSubscription",
            target_id=webhook_id,
            details=f"Deleted webhook subscription {webhook_id}",
        )
    )
    db.commit()
    return {"message": "Webhook subscription deleted successfully", "id": webhook_id}


@router.post("/{webhook_id}/test")
def test_webhook(
    webhook_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Sends a synchronous test ping event to the subscribed webhook endpoint."""
    sub = db.query(WebhookSubscription).filter(WebhookSubscription.id == webhook_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook subscription not found")

    result = send_test_ping(db, sub)
    return {
        "webhook_id": sub.id,
        "test_result": result,
    }


@router.get("/{webhook_id}/logs")
def get_webhook_logs(
    webhook_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_organizer),
):
    """Returns recent delivery logs for a webhook subscription."""
    sub = db.query(WebhookSubscription).filter(WebhookSubscription.id == webhook_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Webhook subscription not found")

    logs = (
        db.query(WebhookDeliveryLog)
        .filter(WebhookDeliveryLog.subscription_id == webhook_id)
        .order_by(WebhookDeliveryLog.attempted_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": log.id,
            "event_type": log.event_type,
            "payload_json": log.payload_json,
            "status_code": log.status_code,
            "response_body": log.response_body,
            "success": bool(log.success),
            "attempted_at": log.attempted_at.isoformat() if log.attempted_at else None,
        }
        for log in logs
    ]
