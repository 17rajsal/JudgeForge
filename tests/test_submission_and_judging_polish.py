import io
import datetime as dt
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Event, Track, Team, TeamMember, Project, Score, Judge, RubricCriterion

ORG_HEADERS = {"Authorization": "Bearer org_7f2a"}
PARTICIPANT_HEADERS = {"Authorization": "Bearer prt_2e88"}
JUDGE_HEADERS = {"Authorization": "Bearer jdg_a_91bc"}


def test_file_upload_and_serving_security():
    """Verify local asset upload, mime validation, and traversal protection."""
    client = TestClient(app)

    # 1. Unauthenticated upload fails
    res_unauth = client.post("/api/upload", files={"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")})
    assert res_unauth.status_code in (401, 403)

    # 2. Valid PDF upload succeeds
    pdf_content = b"%PDF-1.5 \n%Fake PDF content for pitch deck verification\n%%EOF"
    res_upload = client.post(
        "/api/upload",
        files={"file": ("my_pitch_deck.pdf", pdf_content, "application/pdf")},
        headers=PARTICIPANT_HEADERS,
    )
    assert res_upload.status_code == 200, res_upload.text
    data = res_upload.json()
    assert "url" in data
    assert data["url"].startswith("/uploads/")
    assert "filename" in data
    stored_url = data["url"]

    # 3. Serve uploaded file via GET
    res_serve = client.get(stored_url)
    assert res_serve.status_code == 200
    assert res_serve.content == pdf_content

    # 4. Reject disallowed extensions
    res_bad = client.post(
        "/api/upload",
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/x-msdownload")},
        headers=PARTICIPANT_HEADERS,
    )
    assert res_bad.status_code == 400
    assert "Unsupported file type" in res_bad.json()["detail"]

    # 5. Path traversal protection on /uploads/{filename}
    res_traversal1 = client.get("/uploads/..%2Fmain.py")
    assert res_traversal1.status_code in (400, 404)

    res_traversal2 = client.get("/uploads/....//....//app/main.py")
    assert res_traversal2.status_code in (400, 404)


def test_project_submission_with_rich_metadata():
    """Verify project creation with tagline, description, demo, video, pitch deck, and tech stack."""
    client = TestClient(app)

    # Create open event
    future_close = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=5)).isoformat()
    ev_res = client.post(
        "/api/events",
        json={
            "name": "Rich Submission Hackathon",
            "submissions_close": future_close,
            "tracks": ["Cloud Native", "Security & AI"],
        },
        headers=ORG_HEADERS,
    )
    assert ev_res.status_code == 201
    event_id = ev_res.json()["id"]

    # Find track
    with SessionLocal() as db:
        track = db.query(Track).filter_by(event_id=event_id).first()
        track_id = track.id

    # Submit project with all rich fields
    payload = {
        "event_id": event_id,
        "track_id": track_id,
        "title": "Quantum Shield Guard",
        "tagline": "Post-quantum cryptographic mesh for autonomous agents",
        "summary": "High-throughput deterministic lattice-based verification proxy.",
        "description": "Comprehensive design details including zero-knowledge proofs and safe local SQLite caching.",
        "tech_stack": "Python, FastAPI, SQLite, Cryptography, Ed25519, Docker",
        "repo_url": "https://github.com/judgeforge/quantum-shield",
        "demo_url": "https://demo.quantumshield.io",
        "video_url": "https://youtube.com/watch?v=demo12345",
        "pitch_deck_url": "/uploads/deck_sample.pdf",
        "status": "submitted",
    }

    sub_res = client.post("/api/projects", json=payload, headers=PARTICIPANT_HEADERS)
    assert sub_res.status_code == 201, sub_res.text
    project_data = sub_res.json()
    project_id = project_data["id"]

    assert project_data["title"] == "Quantum Shield Guard"
    assert project_data["tagline"] == "Post-quantum cryptographic mesh for autonomous agents"
    assert project_data["demo_url"] == "https://demo.quantumshield.io"
    assert project_data["video_url"] == "https://youtube.com/watch?v=demo12345"
    assert project_data["pitch_deck_url"] == "/uploads/deck_sample.pdf"
    assert project_data["tech_stack"] == "Python, FastAPI, SQLite, Cryptography, Ed25519, Docker"

    # Verify model helper properties in DB
    with SessionLocal() as db:
        p = db.get(Project, project_id)
        assert p is not None
        assert p.short_description == "Post-quantum cryptographic mesh for autonomous agents"
        assert "zero-knowledge proofs" in p.full_description
        assert "FastAPI" in p.tech_tags
        assert "Ed25519" in p.tech_tags

    # Verify Project Detail HTML page renders all rich elements
    detail_res = client.get(f"/projects/{project_id}")
    assert detail_res.status_code == 200
    html = detail_res.text
    assert "Quantum Shield Guard" in html
    assert "Post-quantum cryptographic mesh for autonomous agents" in html
    assert "https://github.com/judgeforge/quantum-shield" in html
    assert "https://demo.quantumshield.io" in html
    assert "https://youtube.com/watch?v=demo12345" in html
    assert "/uploads/deck_sample.pdf" in html
    assert "Cryptography" in html

    # Update project with new tagline
    update_res = client.put(
        f"/api/projects/{project_id}",
        json={"tagline": "Updated cutting-edge cryptographic shield"},
        headers=PARTICIPANT_HEADERS,
    )
    assert update_res.status_code == 200
    with SessionLocal() as db:
        p = db.get(Project, project_id)
        assert p.tagline == "Updated cutting-edge cryptographic shield"


def test_organizer_progress_and_rubric_weights():
    """Verify organizer progress metrics (reviews expected vs completed, inactive judges, unreviewed projects)."""
    client = TestClient(app)

    # Create event
    future_close = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).isoformat()
    ev_res = client.post(
        "/api/events",
        json={"name": "Progress Analytics Test Hack", "submissions_close": future_close, "tracks": ["Main Track"]},
        headers=ORG_HEADERS,
    )
    assert ev_res.status_code == 201
    event_id = ev_res.json()["id"]

    with SessionLocal() as db:
        track = db.query(Track).filter_by(event_id=event_id).first()
        track_id = track.id

    # Create 2 submitted projects
    p1_res = client.post(
        "/api/projects",
        json={"event_id": event_id, "track_id": track_id, "title": "Project Alpha", "summary": "Test Alpha"},
        headers=PARTICIPANT_HEADERS,
    )
    assert p1_res.status_code == 201
    p1_id = p1_res.json()["id"]

    p2_res = client.post(
        "/api/projects",
        json={"event_id": event_id, "track_id": track_id, "title": "Project Beta", "summary": "Test Beta"},
        headers=PARTICIPANT_HEADERS,
    )
    assert p2_res.status_code == 201
    p2_id = p2_res.json()["id"]

    # Check progress via API before any score
    prog_res1 = client.get(f"/api/organizer/progress?event_id={event_id}", headers=ORG_HEADERS)
    assert prog_res1.status_code == 200
    prog1 = prog_res1.json()
    assert prog1["total_projects"] == 2
    assert prog1["total_scores"] == 0
    assert prog1["zero_review_count"] == 2
    assert prog1["completion_pct"] == 0

    # Judge submits score for Project Alpha
    score_res = client.post(
        "/api/judge/scores",
        json={
            "project_id": p1_id,
            "functionality": 5,
            "quality": 4,
            "innovation": 5,
            "comment": "Exceptional functionality and architecture",
        },
        headers=JUDGE_HEADERS,
    )
    assert score_res.status_code == 200, score_res.text

    # Check progress after scoring
    prog_res2 = client.get(f"/api/organizer/progress?event_id={event_id}", headers=ORG_HEADERS)
    assert prog_res2.status_code == 200
    prog2 = prog_res2.json()
    assert prog2["total_scores"] >= 1
    assert prog2["zero_review_count"] == 1
    assert any(zp["id"] == p2_id for zp in prog2["zero_review_projects"])

    # Organizer HTML view includes progress card
    org_html_res = client.get(f"/organizer?event_id={event_id}", headers=ORG_HEADERS)
    assert org_html_res.status_code == 200
    assert "Judging Progress &amp; Deliberation Quota" in org_html_res.text
    assert "Reviews Completed" in org_html_res.text
    assert "Unreviewed Projects" in org_html_res.text


def test_public_results_and_scoring_methodology_card():
    """Verify published results include asset links and methodology card explaining 1-5 normalization."""
    client = TestClient(app)

    # Publish results for the Sample Hack or active event
    pub_res = client.post("/api/events/evt_01/publish", headers=ORG_HEADERS)
    assert pub_res.status_code == 200

    results_html = client.get("/results?event_id=evt_01")
    assert results_html.status_code == 200
    html = results_html.text

    # Leaderboard and methodology checks
    assert "Fair Scoring &amp; Normalization Methodology" in html
    assert "Calibrated Rubric (1–5)" in html
    assert "Z-Score Standardization" in html
    assert "Zero-Variance Proofing" in html
    assert "Rescaled to 1–5 Scale" in html
