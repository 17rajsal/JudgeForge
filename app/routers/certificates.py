import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import HTMLResponse
from app.templating import templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Project, Event, Prize
from app.services.certificates import (
    generate_certificate_svg,
    compute_certificate_fingerprint,
    verify_certificate_digest,
)

router = APIRouter(tags=["Certificates"])


def _get_project_certificate_meta(project: Project, db: Session):
    event = db.query(Event).filter(Event.id == project.event_id).first()
    event_name = event.name if event else "Hackathon Event"
    team_name = project.team.name if project.team else "Independent Creator"

    # Determine award: Check if event prizes match or default to Official Participant
    prizes = db.query(Prize).filter(Prize.event_id == project.event_id).all()
    award_title = "Official Participant"
    # If project won a prize or has placement, we can check or default
    date_str = datetime.datetime.now().strftime("%B %d, %Y")

    fingerprint = compute_certificate_fingerprint(
        event_id=project.event_id,
        project_id=project.id,
        recipient_name=team_name,
        award_title=award_title,
    )

    return {
        "event_id": project.event_id,
        "event_name": event_name,
        "project_id": project.id,
        "project_title": project.title,
        "recipient_name": team_name,
        "award_title": award_title,
        "date_str": date_str,
        "fingerprint": fingerprint,
    }


@router.get("/certificates/project/{project_id}/svg")
def get_certificate_svg(
    project_id: str,
    award: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Generates and serves a standalone, printable vector SVG certificate."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    meta = _get_project_certificate_meta(project, db)
    if award:
        meta["award_title"] = award
        meta["fingerprint"] = compute_certificate_fingerprint(
            event_id=meta["event_id"],
            project_id=meta["project_id"],
            recipient_name=meta["recipient_name"],
            award_title=meta["award_title"],
        )

    svg_content = generate_certificate_svg(
        event_name=meta["event_name"],
        project_title=meta["project_title"],
        recipient_name=meta["recipient_name"],
        award_title=meta["award_title"],
        date_str=meta["date_str"],
        fingerprint=meta["fingerprint"],
    )

    return Response(
        content=svg_content,
        media_type="image/svg+xml",
        headers={
            "Content-Disposition": f'inline; filename="certificate_{project.id}.svg"',
        },
    )


@router.get("/certificates/project/{project_id}", response_class=HTMLResponse)
def view_certificate_html(
    request: Request,
    project_id: str,
    award: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Web preview page for certificate with print and SVG export."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    meta = _get_project_certificate_meta(project, db)
    if award:
        meta["award_title"] = award
        meta["fingerprint"] = compute_certificate_fingerprint(
            event_id=meta["event_id"],
            project_id=meta["project_id"],
            recipient_name=meta["recipient_name"],
            award_title=meta["award_title"],
        )

    svg_content = generate_certificate_svg(
        event_name=meta["event_name"],
        project_title=meta["project_title"],
        recipient_name=meta["recipient_name"],
        award_title=meta["award_title"],
        date_str=meta["date_str"],
        fingerprint=meta["fingerprint"],
    )

    return templates.TemplateResponse(
        request=request,
        name="certificate_view.html",
        context={
            "meta": meta,
            "svg_content": svg_content,
        },
    )


@router.get("/certificates/verify/{fingerprint}", response_class=HTMLResponse)
def view_certificate_verification_html(
    request: Request,
    fingerprint: str,
    db: Session = Depends(get_db),
):
    """Public certificate verification page."""
    result = verify_certificate_digest(db, fingerprint)
    return templates.TemplateResponse(
        request=request,
        name="certificate_verify.html",
        context={"result": result, "fingerprint": fingerprint},
    )


@router.get("/api/v1/certificates/verify/{fingerprint}")
def api_verify_certificate(
    fingerprint: str,
    db: Session = Depends(get_db),
):
    """API endpoint to verify the cryptographic fingerprint of any issued certificate."""
    return verify_certificate_digest(db, fingerprint)
