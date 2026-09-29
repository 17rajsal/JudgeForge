from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from app.templating import templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Project, Event, Track

router = APIRouter(prefix="/embed", tags=["Embed"])


def _relax_frame_headers(response: Response):
    """Allows embedding this endpoint in third-party or parent frame contexts."""
    response.headers["Content-Security-Policy"] = "frame-ancestors *"
    if "X-Frame-Options" in response.headers:
        del response.headers["X-Frame-Options"]


@router.get("/gallery", response_class=HTMLResponse)
def embed_gallery(
    request: Request,
    event_id: Optional[str] = None,
    track_id: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Renders an embeddable, responsive gallery widget of submitted projects.
    Designed for embedding in any external website via <iframe>.
    """
    target_event_id = event_id or "evt_01"
    event = db.query(Event).filter(Event.id == target_event_id).first()
    tracks = db.query(Track).filter(Track.event_id == target_event_id).all() if event else []

    query = db.query(Project).filter(Project.status == "submitted")
    if event:
        query = query.filter(Project.event_id == event.id)
    if track_id:
        query = query.filter(Project.track_id == track_id)
    if search:
        s = f"%{search.strip()}%"
        query = query.filter(Project.title.ilike(s) | Project.summary.ilike(s))

    projects = query.order_by(Project.submitted_at.desc()).all()

    response = templates.TemplateResponse(
        request=request,
        name="embed_gallery.html",
        context={
            "event": event,
            "tracks": tracks,
            "projects": projects,
            "selected_track": track_id,
            "search_query": search or "",
        },
    )
    _relax_frame_headers(response)
    return response


@router.get("/project/{project_id}", response_class=HTMLResponse)
def embed_single_project(
    request: Request,
    project_id: str,
    db: Session = Depends(get_db),
):
    """Renders a compact, embeddable project card widget."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    response = templates.TemplateResponse(
        request=request,
        name="embed_project.html",
        context={"project": project},
    )
    _relax_frame_headers(response)
    return response
