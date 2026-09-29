from fastapi.templating import Jinja2Templates
from app.database import SessionLocal
from app.models import Event

templates = Jinja2Templates(directory="app/templates")


def get_default_event_closed() -> bool:
    db = SessionLocal()
    try:
        ev = db.query(Event).order_by(Event.created_at.desc()).first()
        return ev.is_closed if ev else False
    except Exception:
        return False
    finally:
        db.close()


templates.env.globals["get_default_event_closed"] = get_default_event_closed
