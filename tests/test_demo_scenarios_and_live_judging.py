import datetime
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal, Base, engine
from app.models import Event, Project, Score, Prize, User
from app.demo_scenarios import ensure_demo_scenarios
from app.services.scoring import compute_leaderboard


@pytest.fixture(scope="module", autouse=True)
def init_db():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        ensure_demo_scenarios(db)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_buildforge_scenario_published_with_matched_prizes(client):
    """Scenario 1: BuildForge 2026 is published, closed, has 7 projects, 21 reviews,
    and 5 placement prizes dynamically matched to ranks 1 to 5.
    """
    with SessionLocal() as db:
        ev = db.query(Event).filter(Event.id == "evt_buildforge").first()
        assert ev is not None
        assert ev.results_published == 1
        assert ev.is_closed is True
        assert ev.demo_status_badge == "Completed · Results Published"

        projects = db.query(Project).filter(Project.event_id == ev.id).all()
        assert len(projects) == 7

        scores = db.query(Score).join(Project).filter(Project.event_id == ev.id).all()
        assert len(scores) == 21

        prizes = db.query(Prize).filter(Prize.event_id == ev.id).all()
        assert len(prizes) == 5

        # Verify leaderboard order
        leaderboard = compute_leaderboard(db, ev.id)
        assert len(leaderboard) == 7
        assert leaderboard[0]["title"] == "NovaStack"
        assert leaderboard[0]["rank"] == 1
        assert leaderboard[1]["title"] == "ByteForge"
        assert leaderboard[1]["rank"] == 2
        assert leaderboard[2]["title"] == "Neural Nomads"
        assert leaderboard[2]["rank"] == 3
        assert leaderboard[3]["title"] == "Runtime Rebels"
        assert leaderboard[3]["rank"] == 4
        assert leaderboard[4]["title"] == "ZeroDay Studio"
        assert leaderboard[4]["rank"] == 5

    # Verify public /results renders BuildForge podium and prize awards
    res = client.get("/results?event_id=evt_buildforge")
    assert res.status_code == 200
    html = res.text
    assert "NovaStack" in html
    assert "ByteForge" in html
    assert "Neural Nomads" in html
    assert "$10,000" in html
    assert "$5,000" in html
    assert "$2,500" in html
    assert "Prize / Award" in html
    assert "Grand Prize" in html
    assert "Runner Up" in html
    assert "Publicly Published" in html or "Published" in html


def test_aisystems_scenario_active_judging_privacy_and_coverage(client):
    """Scenario 2: AI Systems Challenge 2026 is unpublished with mixed review coverage.
    - Public visitors receive 403 on /api/results and 'Judging In Progress' on /results.
    - Organizer sees review coverage alerts (DeepTrace has 0 reviews).
    """
    with SessionLocal() as db:
        ev = db.query(Event).filter(Event.id == "evt_aisystems").first()
        assert ev is not None
        assert ev.results_published == 0
        assert ev.is_closed is True
        assert ev.demo_status_badge == "Judging In Progress"

        projects = db.query(Project).filter(Project.event_id == ev.id).all()
        assert len(projects) == 6

        # DeepTrace must have 0 reviews
        deeptrace = db.query(Project).filter(Project.id == "prj_ai_04").first()
        assert deeptrace is not None
        assert deeptrace.title == "DeepTrace"
        assert len(deeptrace.scores) == 0

    # Public /api/results returns 403 Forbidden
    res_api = client.get("/api/results?event_id=evt_aisystems")
    assert res_api.status_code == 403

    # Public /results displays "Judging In Progress" banner with no leaderboard leaks
    res_html = client.get("/results?event_id=evt_aisystems")
    assert res_html.status_code == 200
    assert "Judging In Progress" in res_html.text
    assert "Deliberation Active" in res_html.text
    assert "Complete Leaderboard &amp; Rankings" not in res_html.text

    # Organizer dashboard preview
    org_res = client.get("/organizer?event_id=evt_aisystems", cookies={"session": "org_7f2a"})
    assert org_res.status_code == 200
    org_html = org_res.text
    assert "Judging Progress &amp; Deliberation Quota" in org_html
    assert "Review Coverage Alert" in org_html
    assert "have no judge reviews yet" in org_html
    assert "Normalization Lab &amp; Fairness Analysis" in org_html


def test_open_systems_hack_live_submission_flows_to_judge(client):
    """Scenario 3: Open Systems Hack 2026 allows live submission that immediately
    appears in assigned judge console.
    """
    with SessionLocal() as db:
        ev = db.query(Event).filter(Event.id == "evt_open_live").first()
        assert ev is not None
        assert ev.is_closed is False
        assert ev.demo_status_badge == "Submissions Open"

        # Submit page renders "Fill Demo Details" button
        submit_res = client.get(f"/submit?event_id={ev.id}", cookies={"session": "prt_2e88"})
        assert submit_res.status_code == 200
        assert "Fill Demo Details" in submit_res.text
        assert "fillDemoProjectDetails()" in submit_res.text

        # Create live project submission
        create_payload = {
            "event_id": ev.id,
            "title": "PulseGrid Live",
            "tagline": "Real-time edge telemetry protocol",
            "summary": "Demonstrating live submission to judge review workflow.",
            "description": "Full end-to-end pipeline verification without cloud dependencies.",
            "tech_stack": "Python, FastAPI, SQLite WAL, Docker",
            "track_id": "trk_live_main",
            "repo_url": "https://github.com/judgeforge-demo/pulsegrid",
            "demo_url": "https://pulsegrid.local.mesh",
            "status": "submitted",
        }

    post_res = client.post(
        "/api/projects",
        json=create_payload,
        cookies={"session": "prt_2e88"},
    )
    assert post_res.status_code in [200, 201]
    created_id = post_res.json()["id"]

    # Judge console displays the newly submitted project with "Review Submission" CTA
    judge_res = client.get("/judge", cookies={"session": "jdg_a_91bc"})
    assert judge_res.status_code == 200
    judge_html = judge_res.text
    assert "PulseGrid Live" in judge_html
    assert "Review Submission" in judge_html
    assert "Stack:" in judge_html
    assert "Architecture &amp; Summary" in judge_html


def test_official_evt01_remains_closed_and_intact():
    """Verify that evt_01 (Sample Hack 2026) remains untouched and closed."""
    with SessionLocal() as db:
        evt_01 = db.query(Event).filter(Event.id == "evt_01").first()
        assert evt_01 is not None
        assert evt_01.name == "Sample Hack 2026"
        assert evt_01.is_closed is True
        assert evt_01.demo_status_badge == "Official Fixture · Closed"
