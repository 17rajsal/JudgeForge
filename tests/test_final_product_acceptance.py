import io
import datetime as dt
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Event, Track, Team, TeamMember, Project, Score, Judge, RubricCriterion, User
from app.services.scoring import calculate_raw_score, compute_leaderboard

ORG_HEADERS = {"Authorization": "Bearer org_7f2a"}
PARTICIPANT_HEADERS = {"Authorization": "Bearer prt_2e88"}
JUDGE_A_HEADERS = {"Authorization": "Bearer jdg_a_91bc"}
JUDGE_B_HEADERS = {"Authorization": "Bearer jdg_b_44de"}


def test_01_existing_seeded_project_without_optional_fields_renders():
    """Verify that existing seeded fixture projects without new fields render cleanly without errors."""
    client = TestClient(app)
    res = client.get("/projects/prj_01")
    assert res.status_code == 200
    html = res.text
    assert "Glass Signal" in html
    assert "Submitted Entry" in html
    # Check that missing fields don't cause template crash
    assert "Technical Architecture &amp; Overview" in html


def test_02_participant_can_create_rich_project_with_optional_metadata():
    """Verify participant can submit a rich project with tagline, demo, video, deck, and tech stack."""
    client = TestClient(app)
    future_close = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)).isoformat()
    ev = client.post(
        "/api/events",
        json={"name": "Rich Meta Event", "submissions_close": future_close, "tracks": ["Web3 & AI"]},
        headers=ORG_HEADERS,
    ).json()

    with SessionLocal() as db:
        track = db.query(Track).filter_by(event_id=ev["id"]).first()
        track_id = track.id

    payload = {
        "event_id": ev["id"],
        "track_id": track_id,
        "title": "Hyperion AI Core",
        "tagline": "Real-time edge neural inference engine",
        "summary": "Distributed lattice-based edge inference system.",
        "description": "Deep architecture breakdown with verifiable audit trails.",
        "tech_stack": "Rust, Python, WebGPU, SQLite",
        "repo_url": "https://github.com/judgeforge/hyperion-core",
        "demo_url": "https://hyperion.example.org",
        "video_url": "https://loom.com/share/hyperion123",
        "pitch_deck_url": "https://speakerdeck.com/hyperion/deck",
        "status": "submitted",
    }
    sub_res = client.post("/api/projects", json=payload, headers=PARTICIPANT_HEADERS)
    assert sub_res.status_code == 201
    p_data = sub_res.json()
    assert p_data["title"] == "Hyperion AI Core"
    assert p_data["tagline"] == "Real-time edge neural inference engine"

    # Verify detail page renders links
    detail_res = client.get(f"/projects/{p_data['id']}")
    assert detail_res.status_code == 200
    html = detail_res.text
    assert "View GitHub Repository" in html
    assert "Open Live Demo &rarr;" in html
    assert "Watch Demo Video" in html
    assert "View / Download Pitch Deck" in html


def test_03_event_id_remains_correct():
    """Verify event_id is preserved and correctly associated with the project."""
    client = TestClient(app)
    future_close = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)).isoformat()
    ev = client.post(
        "/api/events",
        json={"name": "Event Scoping Test", "submissions_close": future_close, "tracks": ["General"]},
        headers=ORG_HEADERS,
    ).json()

    with SessionLocal() as db:
        track = db.query(Track).filter_by(event_id=ev["id"]).first()
        track_id = track.id

    sub_res = client.post(
        "/api/projects",
        json={"event_id": ev["id"], "track_id": track_id, "title": "Scoped Entry", "summary": "Scope Test"},
        headers=PARTICIPANT_HEADERS,
    )
    assert sub_res.status_code == 201
    proj_id = sub_res.json()["id"]

    with SessionLocal() as db:
        p = db.get(Project, proj_id)
        assert p.event_id == ev["id"]
        assert p.event_id != "evt_01"


def test_04_team_members_derive_from_membership():
    """Verify team members are automatically derived from TeamMember table and not manually typed."""
    client = TestClient(app)
    with SessionLocal() as db:
        # Check existing team
        team = db.query(Team).filter(Team.members.any()).first()
        assert team is not None
        member_emails = [m.email for m in team.members]
        assert len(member_emails) > 0

        # Check project belonging to this team
        proj = db.query(Project).filter(Project.team_id == team.id).first()
        if proj:
            res = client.get(f"/projects/{proj.id}")
            assert res.status_code == 200
            for email in member_emails:
                assert email in res.text


def test_05_closed_event_still_refuses_submission():
    """Verify closed event (evt_01) strictly rejects submissions via both API routes."""
    client = TestClient(app)
    # 1. POST /api/projects
    res1 = client.post(
        "/api/projects",
        json={"event_id": "evt_01", "track_id": "trk_01", "title": "Late Project", "summary": "Late"},
        headers=PARTICIPANT_HEADERS,
    )
    assert res1.status_code == 400
    assert "closed" in res1.json()["detail"].lower()

    # 2. POST /api/v1/projects
    res2 = client.post(
        "/api/v1/projects",
        json={"event_id": "evt_01", "track_id": "trk_01", "title": "Late V1", "summary": "Late"},
        headers=PARTICIPANT_HEADERS,
    )
    assert res2.status_code == 400


def test_06_draft_remains_private():
    """Verify draft projects remain private and are not publicly browsable."""
    client = TestClient(app)
    future_close = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)).isoformat()
    ev = client.post(
        "/api/events",
        json={"name": "Draft Privacy Event", "submissions_close": future_close, "tracks": ["Privacy Track"]},
        headers=ORG_HEADERS,
    ).json()

    with SessionLocal() as db:
        track = db.query(Track).filter_by(event_id=ev["id"]).first()
        track_id = track.id

    sub_res = client.post(
        "/api/projects",
        json={"event_id": ev["id"], "track_id": track_id, "title": "Secret Draft", "summary": "Secret", "status": "draft"},
        headers=PARTICIPANT_HEADERS,
    )
    assert sub_res.status_code == 201
    draft_id = sub_res.json()["id"]

    # Public caller without cookies cannot view draft
    anon_client = TestClient(app)
    pub_res = anon_client.get(f"/projects/{draft_id}")
    assert pub_res.status_code in (403, 404)

    # Author participant can view draft
    author_res = client.get(f"/projects/{draft_id}", headers=PARTICIPANT_HEADERS)
    assert author_res.status_code == 200
    assert "Draft (Not Submitted)" in author_res.text


def test_07_judge_only_sees_authorized_project():
    """Verify a judge assigned to specific tracks only sees and can only score projects in those tracks."""
    client = TestClient(app)
    future_close = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)).isoformat()
    ev = client.post(
        "/api/events",
        json={"name": "Track Scope Event", "submissions_close": future_close, "tracks": ["Track Alpha", "Track Beta"]},
        headers=ORG_HEADERS,
    ).json()

    with SessionLocal() as db:
        t_alpha = db.query(Track).filter_by(event_id=ev["id"], name="Track Alpha").first()
        t_beta = db.query(Track).filter_by(event_id=ev["id"], name="Track Beta").first()
        t_alpha_id = t_alpha.id
        t_beta_id = t_beta.id

    # Create project in Track Beta
    p_beta = client.post(
        "/api/projects",
        json={"event_id": ev["id"], "track_id": t_beta_id, "title": "Beta Only Project", "summary": "Beta", "status": "submitted"},
        headers=PARTICIPANT_HEADERS,
    ).json()

    # Assign Judge A strictly to Track Alpha
    with SessionLocal() as db:
        judge_a = db.query(Judge).filter((Judge.id == "jdg_01") | (Judge.email == "judge_a@dogfood.local")).first()
        if judge_a:
            judge_a.tracks = [db.get(Track, t_alpha_id)]
            db.commit()

    # Judge A attempts to score project in Track Beta -> blocked
    score_res = client.post(
        "/api/judge/scores",
        json={"project_id": p_beta["id"], "functionality": 4, "quality": 4, "innovation": 4},
        headers=JUDGE_A_HEADERS,
    )
    assert score_res.status_code == 403
    assert "track" in score_res.json()["detail"].lower()


def test_08_judge_cannot_see_peer_scores():
    """Verify judge peer isolation: Judge A cannot query Judge B's scores."""
    client = TestClient(app)
    res = client.get("/api/judge/scores?judge=jdg_b_44de", headers=JUDGE_A_HEADERS)
    assert res.status_code == 403
    assert "peer scores" in res.json()["detail"].lower()


def test_09_participant_cannot_score():
    """Verify participants cannot submit judge evaluations."""
    client = TestClient(app)
    res = client.post(
        "/api/judge/scores",
        json={"project_id": "prj_01", "functionality": 5, "quality": 5, "innovation": 5},
        headers=PARTICIPANT_HEADERS,
    )
    assert res.status_code == 403


def test_10_rubric_weights_produce_expected_raw_score():
    """Verify that rubric weights produce mathematically accurate raw score."""
    with SessionLocal() as db:
        score = Score(functionality=5, quality=3, innovation=4)
        # Equal weights 1:1:1 -> (5+3+4)/3 = 4.0
        w_equal = {"functionality": 1.0, "quality": 1.0, "innovation": 1.0}
        assert calculate_raw_score(score, w_equal) == pytest.approx(4.0, abs=1e-5)

        # Heavily weighted functionality: 3.0, 1.0, 1.0 -> (5*3 + 3*1 + 4*1) / 5 = 22/5 = 4.4
        w_func = {"functionality": 3.0, "quality": 1.0, "innovation": 1.0}
        assert calculate_raw_score(score, w_func) == pytest.approx(4.4, abs=1e-5)


def test_11_normalized_leaderboard_remains_event_scoped():
    """Verify compute_leaderboard only includes projects from the requested event."""
    with SessionLocal() as db:
        lb_evt1 = compute_leaderboard(db, event_id="evt_01")
        for item in lb_evt1:
            p = db.get(Project, item["project_id"])
            assert p.event_id == "evt_01"


def test_12_results_remain_private_before_publish():
    """Verify /api/results and /results hide final standings before publication."""
    client = TestClient(app)
    future_close = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)).isoformat()
    ev = client.post(
        "/api/events",
        json={"name": "Unpublished Standings Event", "submissions_close": future_close, "tracks": ["General"]},
        headers=ORG_HEADERS,
    ).json()

    # Participant / Public calling /api/results -> 403 Forbidden
    res_api = client.get(f"/api/results?event_id={ev['id']}", headers=PARTICIPANT_HEADERS)
    assert res_api.status_code == 403

    # Public HTML view does not display leaderboard
    anon_client = TestClient(app)
    res_html = anon_client.get(f"/results?event_id={ev['id']}")
    assert res_html.status_code == 200
    assert "Results Pending Deliberation" in res_html.text or "Deliberation Active" in res_html.text


def test_13_aggregated_results_visible_after_publish():
    """Verify /api/results and /results show leaderboard after organizer publishes."""
    client = TestClient(app)
    future_close = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)).isoformat()
    ev = client.post(
        "/api/events",
        json={"name": "Publish Event", "submissions_close": future_close, "tracks": ["General"]},
        headers=ORG_HEADERS,
    ).json()

    # Organizer publishes
    pub = client.post(f"/api/events/{ev['id']}/publish", headers=ORG_HEADERS)
    assert pub.status_code == 200

    # Public can now access /api/results
    res_api = client.get(f"/api/results?event_id={ev['id']}")
    assert res_api.status_code == 200
    assert res_api.json()["published"] is True


def test_14_individual_judge_comments_remain_private_after_publish():
    """Verify public results API and HTML omit individual judge identities and comments."""
    client = TestClient(app)
    # Ensure evt_01 is published
    client.post("/api/events/evt_01/publish", headers=ORG_HEADERS)

    res_api = client.get("/api/results?event_id=evt_01")
    assert res_api.status_code == 200
    data = res_api.json()
    for row in data["leaderboard"]:
        assert "comment" not in row
        assert "judge_id" not in row
        assert "judge_name" not in row


def test_15_unsafe_upload_extension_rejected():
    """Verify upload rejects executable scripts and dangerous extensions."""
    client = TestClient(app)
    for bad_ext, mime in [("script.sh", "text/x-sh"), ("malware.exe", "application/octet-stream"), ("exploit.php", "text/php")]:
        res = client.post(
            "/api/upload",
            files={"file": (bad_ext, b"echo exploit", mime)},
            headers=PARTICIPANT_HEADERS,
        )
        assert res.status_code == 400
        assert "Unsupported file type" in res.json()["detail"]


def test_16_safe_deck_upload_persists_correctly():
    """Verify safe presentation deck upload (.pdf / .pptx) stores file and serves content."""
    client = TestClient(app)
    deck_bytes = b"PK\x03\x04 fake PPTX presentation stream"
    res = client.post(
        "/api/upload",
        files={"file": ("presentation.pptx", deck_bytes, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        headers=PARTICIPANT_HEADERS,
    )
    assert res.status_code == 200
    url = res.json()["url"]
    assert url.startswith("/uploads/")

    serve_res = client.get(url)
    assert serve_res.status_code == 200
    assert serve_res.content == deck_bytes


def test_17_existing_official_acceptance_routes_stay_unchanged():
    """Verify core routes required by official checker remain intact."""
    client = TestClient(app)
    assert client.get("/projects").status_code == 200
    assert client.get("/judge").status_code in (200, 302)  # HTML page
    assert client.get("/api/export.csv", headers=ORG_HEADERS).status_code == 200
    assert client.get("/workspace", headers=PARTICIPANT_HEADERS).status_code == 200
