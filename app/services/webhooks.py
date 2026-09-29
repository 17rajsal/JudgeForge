import json
import hmac
import hashlib
import datetime
import urllib.request
import urllib.error
import secrets
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from app.models import WebhookSubscription, WebhookDeliveryLog


def compute_signature(secret: str, payload_bytes: bytes) -> str:
    """Computes HMAC-SHA256 signature for payload verification."""
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def dispatch_webhook(
    db: Session,
    event_id: str,
    event_type: str,
    payload: Dict[str, Any],
) -> int:
    """
    Dispatches outbound webhook notifications to all matching active subscribers for an event.
    Fail-safe: Catches all network/HTTP exceptions and records attempts in WebhookDeliveryLog.
    Never raises an exception or crashes the calling request.
    Returns the number of matching subscribers attempted.
    """
    try:
        subscriptions = (
            db.query(WebhookSubscription)
            .filter(
                WebhookSubscription.event_id == event_id,
                WebhookSubscription.is_active == 1,
            )
            .all()
        )
    except Exception:
        return 0

    attempted_count = 0
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for sub in subscriptions:
        # Check event filtering
        subscribed_events = [e.strip() for e in sub.events_subscribed.split(",")]
        if "*" not in subscribed_events and event_type not in subscribed_events:
            continue

        attempted_count += 1
        message_dict = {
            "event": event_type,
            "event_id": event_id,
            "timestamp": now_utc.isoformat(),
            "data": payload,
        }
        body_str = json.dumps(message_dict, sort_keys=True)
        body_bytes = body_str.encode("utf-8")
        sig = compute_signature(sub.secret, body_bytes)

        headers = {
            "Content-Type": "application/json",
            "X-JudgeForge-Event": event_type,
            "X-JudgeForge-Signature": f"sha256={sig}",
            "X-JudgeForge-Timestamp": now_utc.isoformat(),
            "User-Agent": "JudgeForge-Webhook-Dispatcher/1.0",
        }

        status_code: Optional[int] = None
        response_body: Optional[str] = None
        success = 0

        try:
            req = urllib.request.Request(
                sub.target_url,
                data=body_bytes,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                status_code = resp.getcode()
                raw_data = resp.read(1024)
                response_body = raw_data.decode("utf-8", errors="replace")
                if 200 <= status_code < 300:
                    success = 1
        except urllib.error.HTTPError as he:
            status_code = he.code
            try:
                response_body = he.read(1024).decode("utf-8", errors="replace")
            except Exception:
                response_body = str(he)
        except Exception as ex:
            status_code = 0
            response_body = f"Connection error: {str(ex)}"

        # Log delivery result
        try:
            log_entry = WebhookDeliveryLog(
                subscription_id=sub.id,
                event_type=event_type,
                payload_json=body_str,
                status_code=status_code,
                response_body=response_body,
                success=success,
                attempted_at=now_utc,
            )
            db.add(log_entry)
            db.commit()
        except Exception:
            db.rollback()

    return attempted_count


def send_test_ping(db: Session, sub: WebhookSubscription) -> Dict[str, Any]:
    """Sends a synchronous test ping event to verify a webhook subscription."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    message_dict = {
        "event": "test.ping",
        "event_id": sub.event_id,
        "subscription_id": sub.id,
        "timestamp": now_utc.isoformat(),
        "data": {
            "message": "JudgeForge webhook test ping",
            "status": "ready",
        },
    }
    body_str = json.dumps(message_dict, sort_keys=True)
    body_bytes = body_str.encode("utf-8")
    sig = compute_signature(sub.secret, body_bytes)

    headers = {
        "Content-Type": "application/json",
        "X-JudgeForge-Event": "test.ping",
        "X-JudgeForge-Signature": f"sha256={sig}",
        "X-JudgeForge-Timestamp": now_utc.isoformat(),
        "User-Agent": "JudgeForge-Webhook-Dispatcher/1.0",
    }

    status_code: Optional[int] = None
    response_body: Optional[str] = None
    success = 0

    try:
        req = urllib.request.Request(
            sub.target_url,
            data=body_bytes,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            status_code = resp.getcode()
            response_body = resp.read(1024).decode("utf-8", errors="replace")
            if 200 <= status_code < 300:
                success = 1
    except urllib.error.HTTPError as he:
        status_code = he.code
        try:
            response_body = he.read(1024).decode("utf-8", errors="replace")
        except Exception:
            response_body = str(he)
    except Exception as ex:
        status_code = 0
        response_body = f"Connection error: {str(ex)}"

    try:
        log_entry = WebhookDeliveryLog(
            subscription_id=sub.id,
            event_type="test.ping",
            payload_json=body_str,
            status_code=status_code,
            response_body=response_body,
            success=success,
            attempted_at=now_utc,
        )
        db.add(log_entry)
        db.commit()
    except Exception:
        db.rollback()

    return {
        "success": bool(success),
        "status_code": status_code,
        "response_body": response_body,
    }
