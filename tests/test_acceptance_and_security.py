import datetime
import json
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Event, Track, Project, Team, TeamMember, Score, Judge, User, SessionToken
from app.seed import seed_database
from app.services.scoring import compute_leaderboard, generate_results_csv


@pytest.fixture(scope="session", autouse=True)
def init_db():
    db = SessionLocal()
    seed_database(db)
    db.close()


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health & Basics
# ---------------------------------------------------------------------------
def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# T1: Gallery & Submissions
# ---------------------------------------------------------------------------
def test_t1_gallery_is_public(client):
    # No auth header
    response = client.get("/projects")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_t1_project_from_fixtures_shown(client):
    response = client.get("/projects")
    assert response.status_code == 200
    body = response.text.lower()
    # At least one of the first fixture projects must appear
    fixture_titles = ["glass signal", "fallen timber", "salt road"]
    assert any(title in body for title in fixture_titles)


def test_t1_closed_event_refuses_submissions(client):
    # Participant tries to submit to a closed event
    headers = {"Authorization": "Bearer token_participant"}
    response = client.post(
        "/api/projects",
        headers=headers,
        json={"title": "dogfood-late-submission-probe", "summary": "probe"},
    )
    assert 400 <= response.status_code < 500
    data = response.json()
    assert "closed" in data.get("detail", "").lower()


def test_submission_allowed_when_event_is_open(client):
    # Temporarily update event deadline to the future
    db = SessionLocal()
    event = db.query(Event).filter(Event.id == "evt_01").first()
    original_deadline = event.submissions_close
    created_id = None
    try:
        future_deadline = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
        event.submissions_close = future_deadline
        db.commit()

        headers = {"Authorization": "Bearer token_participant"}
        response = client.post(
            "/api/projects",
            headers=headers,
            json={
                "title": "Future Open Project",
                "summary": "Built before deadline",
                "repo_url": "https://example.org/open",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Future Open Project"
        created_id = data["id"]
    finally:
        event.submissions_close = original_deadline
        if created_id:
            db.query(Project).filter(Project.id == created_id).delete()
        db.commit()
        db.close()


def test_project_edit_rejected_after_deadline(client):
    headers = {"Authorization": "Bearer token_participant"}
    response = client.put(
        "/api/projects/prj_01",
        headers=headers,
        json={"title": "Attempted Late Edit"},
    )
    assert response.status_code == 400
    assert "closed" in response.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# T2: Judging & RBAC Isolation
# ---------------------------------------------------------------------------
def test_t2_judge_sees_own_scores(client):
    headers = {"Authorization": "Bearer token_judge_a"}
    response = client.get("/api/judge/scores", headers=headers)
    assert response.status_code == 200
    scores = response.json()
    assert isinstance(scores, list)
    # Judge A (jdg_01) should only have their own scores returned
    for s in scores:
        assert s["judge_id"] == "jdg_01"


def test_t2_judge_cannot_see_peer_scores(client):
    # Judge B (jdg_02) attempts to probe Judge A's (jdg_01) scores
    headers = {"Authorization": "Bearer token_judge_b"}
    response = client.get("/api/judge/scores?judge=jdg_01", headers=headers)
    assert response.status_code in (401, 403)


def test_t2_participant_blocked_from_judging(client):
    headers = {"Authorization": "Bearer token_participant"}
    response = client.get("/api/judge/scores", headers=headers)
    assert response.status_code in (401, 403)


def test_anonymous_blocked_from_judging(client):
    response = client.get("/api/judge/scores")
    assert response.status_code == 401


def test_t2_csv_export_works_for_organizer(client):
    headers = {"Authorization": "Bearer token_organizer"}
    response = client.get("/api/export.csv", headers=headers)
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    lines = response.text.splitlines()
    assert len(lines) > 1
    assert "," in lines[0]
    assert "rank" in lines[0]
    assert "normalized_score" in lines[0]


def test_unauthorized_users_blocked_from_csv_export(client):
    # Participant blocked
    p_res = client.get("/api/export.csv", headers={"Authorization": "Bearer token_participant"})
    assert p_res.status_code in (401, 403)

    # Judge blocked
    j_res = client.get("/api/export.csv", headers={"Authorization": "Bearer token_judge_a"})
    assert j_res.status_code in (401, 403)

    # Anonymous blocked
    a_res = client.get("/api/export.csv")
    assert a_res.status_code == 401


# ---------------------------------------------------------------------------
# Auth Flexibility: Cookie Session Testing
# ---------------------------------------------------------------------------
def test_cookie_session_auth(client):
    # Test organizer via cookie
    client.cookies.set("session", "org_7f2a")
    res = client.get("/api/export.csv")
    assert res.status_code == 200

    # Test judge via cookie
    client.cookies.set("session", "jdg_a_91bc")
    res2 = client.get("/api/judge/scores")
    assert res2.status_code == 200


def test_invalid_token_rejected(client):
    headers = {"Authorization": "Bearer non_existent_token_xyz"}
    res = client.get("/api/judge/scores", headers=headers)
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Judging Workflow & Scoring
# ---------------------------------------------------------------------------
def test_judge_submit_score(client):
    headers = {"Authorization": "Bearer token_judge_a"}
    payload = {
        "project_id": "prj_02",
        "functionality": 5,
        "quality": 4,
        "innovation": 5,
        "comment": "Outstanding architecture and complete test coverage.",
    }
    response = client.post("/api/judge/scores", headers=headers, json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == "prj_02"
    assert data["judge_id"] == "jdg_01"


# ---------------------------------------------------------------------------
# Normalization & Edge Cases
# ---------------------------------------------------------------------------
def test_normalization_computation():
    db = SessionLocal()
    leaderboard = compute_leaderboard(db)
    assert len(leaderboard) == 41
    # Check that leaderboard is sorted descending by normalized_score
    scores = [item["normalized_score"] for item in leaderboard]
    assert scores == sorted(scores, reverse=True)
    # Check rank numbers are 1 to N
    assert [item["rank"] for item in leaderboard] == list(range(1, 42))
    db.close()


def test_seed_idempotency():
    db = SessionLocal()
    initial_event_count = db.query(Event).count()
    initial_project_count = db.query(Project).count()
    initial_judge_count = db.query(Judge).count()
    initial_score_count = db.query(Score).count()

    # Re-run seed
    seed_database(db)

    assert db.query(Event).count() == initial_event_count
    assert db.query(Project).count() == initial_project_count
    assert db.query(Judge).count() == initial_judge_count
    assert db.query(Score).count() == initial_score_count
    db.close()
