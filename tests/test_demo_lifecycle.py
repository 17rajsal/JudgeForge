import datetime as dt
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Event, Track, Team, TeamMember, Project, Score, Judge, RubricCriterion

ORG_HEADERS = {"Authorization": "Bearer org_7f2a"}
PARTICIPANT_HEADERS = {"Authorization": "Bearer prt_2e88"}


def test_fixture_event_closed_deadline_rejection():
    """Ensure evt_01 is closed and continues rejecting submissions."""
    client = TestClient(app)
    res = client.post(
        "/api/projects",
        json={
            "title": "Late Entry Test",
            "summary": "Should be rejected because evt_01 is closed",
            "event_id": "evt_01",
        },
        headers=PARTICIPANT_HEADERS,
    )
    assert res.status_code == 400
    assert "closed" in res.json().get("detail", "").lower()


def test_complete_hackathon_demo_lifecycle():
    """
    Complete end-to-end hackathon lifecycle test:
    create event -> team -> invite acceptance -> draft -> submit ->
    judge assignment -> scoring -> peer isolation -> publish -> public results.
    """
    client = TestClient(app)

    # -------------------------------------------------------------------------
    # 1. Organizer creates future open event
    # -------------------------------------------------------------------------
    future_close = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)).isoformat()
    event_payload = {
        "name": "Global Open Demo Hack 2026",
        "submissions_close": future_close,
        "tracks": ["AI & Machine Learning", "Developer Infrastructure"],
    }
    create_event_res = client.post("/api/events", json=event_payload, headers=ORG_HEADERS)
    assert create_event_res.status_code == 201, create_event_res.text
    event_data = create_event_res.json()
    event_id = event_data["id"]

    # Verify event tracks and rubric were created
    with SessionLocal() as db:
        ev = db.get(Event, event_id)
        assert ev is not None
        assert not ev.is_closed
        assert ev.status_label == "Submissions open"
        tracks = db.query(Track).filter_by(event_id=event_id).all()
        assert len(tracks) == 2
        ai_track = next(t for t in tracks if t.name == "AI & Machine Learning")
        ai_track_id = ai_track.id

    # -------------------------------------------------------------------------
    # 2. Participant A registers & creates a team for the demo event
    # -------------------------------------------------------------------------
    part_a_creds = {
        "email": "part_a_demo@example.org",
        "password": "Password-Demo-1234!",
        "name": "Participant Alice",
    }
    reg_a = client.post("/api/auth/register", json=part_a_creds)
    assert reg_a.status_code == 201, reg_a.text
    user_a_id = reg_a.json()["id"]

    login_a = client.post(
        "/api/auth/login",
        json={"email": part_a_creds["email"], "password": part_a_creds["password"]},
    )
    assert login_a.status_code == 200
    token_a = login_a.json()["token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Participant A creates a team
    create_team_res = client.post(
        "/api/teams",
        json={"name": "Neural Pioneers", "event_id": event_id},
        headers=headers_a,
    )
    assert create_team_res.status_code == 201, create_team_res.text
    team_id = create_team_res.json()["id"]

    # -------------------------------------------------------------------------
    # 3. Participant A generates a team invite link
    # -------------------------------------------------------------------------
    invite_res = client.post(f"/api/teams/{team_id}/invites", headers=headers_a)
    assert invite_res.status_code == 201
    invite_url = invite_res.json()["invite_url"]
    assert "invite=" in invite_url
    invite_token = invite_url.split("invite=")[1]

    # -------------------------------------------------------------------------
    # 4. Participant B registers and accepts the invite
    # -------------------------------------------------------------------------
    # Unauthenticated workspace check with invite parameter redirects to login preserving token
    client_b = TestClient(app)
    unauth_workspace = client_b.get(f"/workspace?invite={invite_token}", follow_redirects=False)
    assert unauth_workspace.status_code in (302, 303, 307)
    assert f"invite={invite_token}" in unauth_workspace.headers.get("location", "")

    # Participant B registers in browser
    part_b_creds = {
        "email": "part_b_demo@example.org",
        "password": "Password-Demo-1234!",
        "name": "Participant Bob",
    }
    reg_b = client_b.post("/api/auth/register", json=part_b_creds)
    assert reg_b.status_code == 201
    user_b_id = reg_b.json()["id"]

    login_b = client_b.post(
        "/api/auth/login",
        json={"email": part_b_creds["email"], "password": part_b_creds["password"]},
    )
    token_b = login_b.json()["token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Participant B accepts the invite
    accept_res = client_b.post(f"/api/team-invites/{invite_token}/accept", headers=headers_b)
    assert accept_res.status_code == 200, accept_res.text
    assert accept_res.json().get("team_id") == team_id
    assert accept_res.json().get("team_name") == "Neural Pioneers"

    # Re-accepting should fail (already used)
    re_accept = client_b.post(f"/api/team-invites/{invite_token}/accept", headers=headers_b)
    assert re_accept.status_code in (400, 409)

    # Verify both members exist in team
    with SessionLocal() as db:
        members = db.query(TeamMember).filter_by(team_id=team_id).all()
        member_ids = {m.user_id for m in members}
        assert user_a_id in member_ids
        assert user_b_id in member_ids

    # -------------------------------------------------------------------------
    # 5. Team saves a draft project for the open event
    # -------------------------------------------------------------------------
    draft_payload = {
        "event_id": event_id,
        "team_id": team_id,
        "track_id": ai_track_id,
        "title": "Quantum Agent Optimizer",
        "summary": "Autonomous AI optimization framework",
        "repo_url": "https://github.com/neural-pioneers/optimizer",
        "status": "draft",
    }
    create_draft_res = client.post("/api/projects", json=draft_payload, headers=headers_a)
    assert create_draft_res.status_code == 201, create_draft_res.text
    project_id = create_draft_res.json()["id"]

    # Verify draft is in database with status='draft'
    with SessionLocal() as db:
        proj = db.get(Project, project_id)
        assert proj.status == "draft"
        assert proj.event_id == event_id

    # Public gallery does not show drafts
    client_pub = TestClient(app)
    gallery_res = client_pub.get(f"/projects?event_id={event_id}")
    assert gallery_res.status_code == 200
    assert "Quantum Agent Optimizer" not in gallery_res.text

    # -------------------------------------------------------------------------
    # 6. Team finalizes submission
    # -------------------------------------------------------------------------
    finalize_res = client_b.post(f"/api/projects/{project_id}/submit", headers=headers_b)
    assert finalize_res.status_code == 200

    # Project is now submitted and visible in event gallery
    gallery_after = client_pub.get(f"/projects?event_id={event_id}")
    assert gallery_after.status_code == 200
    assert "Quantum Agent Optimizer" in gallery_after.text

    # -------------------------------------------------------------------------
    # 7. Organizer creates & assigns Judge X to AI track
    # -------------------------------------------------------------------------
    judge_payload = {
        "name": "Dr. Sarah Connor",
        "email": "sarah.connor.judge@example.org",
        "track_ids": [ai_track_id],
    }
    judge_res = client.post("/api/judges", json=judge_payload, headers=ORG_HEADERS)
    assert judge_res.status_code == 201
    judge_data = judge_res.json()
    judge_id = judge_data["id"]
    judge_initial_pw = judge_data["initial_password"]

    # Judge X logs in with own client
    client_judge = TestClient(app)
    judge_login = client_judge.post(
        "/api/auth/login",
        json={"email": judge_payload["email"], "password": judge_initial_pw},
    )
    assert judge_login.status_code == 200
    judge_token = judge_login.json()["token"]
    headers_judge_x = {"Authorization": f"Bearer {judge_token}"}

    # Judge dashboard displays the project under assigned track
    dashboard_res = client_judge.get("/judge", headers=headers_judge_x)
    assert dashboard_res.status_code == 200
    assert "Quantum Agent Optimizer" in dashboard_res.text

    # -------------------------------------------------------------------------
    # 8. Judge X scores the project
    # -------------------------------------------------------------------------
    score_payload = {
        "project_id": project_id,
        "functionality": 5,
        "quality": 4,
        "innovation": 5,
        "comment": "Exceptional architecture and test coverage.",
    }
    score_res = client_judge.post("/api/judge/scores", json=score_payload, headers=headers_judge_x)
    assert score_res.status_code == 200, score_res.text

    # Judge X sees own score
    own_scores = client_judge.get("/api/judge/scores", headers=headers_judge_x)
    assert own_scores.status_code == 200
    assert any(s["project_id"] == project_id for s in own_scores.json())

    # -------------------------------------------------------------------------
    # 9. Peer Isolation enforced: Judge Y cannot see Judge X's score
    # -------------------------------------------------------------------------
    peer_probe = client.get(
        f"/api/judge/scores?judge={judge_id}",
        headers={"Authorization": "Bearer jdg_b_44de"},  # Judge B token
    )
    assert peer_probe.status_code == 403

    # -------------------------------------------------------------------------
    # 10. Check results BEFORE publication (Deliberation active / private)
    # -------------------------------------------------------------------------
    unpub_api = client_pub.get(f"/api/results?event_id={event_id}")
    assert unpub_api.status_code == 403

    unpub_html = client_pub.get(f"/results?event_id={event_id}")
    assert unpub_html.status_code == 200
    assert "Deliberation Active" in unpub_html.text

    # -------------------------------------------------------------------------
    # 11. Organizer publishes results for the demo event
    # -------------------------------------------------------------------------
    publish_res = client.post(f"/api/events/{event_id}/publish", headers=ORG_HEADERS)
    assert publish_res.status_code == 200
    assert publish_res.json()["results_published"] is True

    # -------------------------------------------------------------------------
    # 12. Check results AFTER publication (Publicly available)
    # -------------------------------------------------------------------------
    pub_api = client_pub.get(f"/api/results?event_id={event_id}")
    assert pub_api.status_code == 200
    res_data = pub_api.json()
    assert res_data["published"] is True
    assert len(res_data["leaderboard"]) >= 1
    top_entry = res_data["leaderboard"][0]
    assert top_entry["title"] == "Quantum Agent Optimizer"
    assert top_entry["team_name"] == "Neural Pioneers"
    assert top_entry["rank"] == 1
    assert top_entry["raw_score"] > 0

    pub_html = client_pub.get(f"/results?event_id={event_id}")
    assert pub_html.status_code == 200
    assert "Published" in pub_html.text
    assert "Quantum Agent Optimizer" in pub_html.text

    # Organizer CSV export for this event contains project and scores
    csv_res = client.get(f"/api/export.csv?event_id={event_id}", headers=ORG_HEADERS)
    assert csv_res.status_code == 200
    csv_text = csv_res.text
    assert "Quantum Agent Optimizer" in csv_text
    assert "Neural Pioneers" in csv_text

    # -------------------------------------------------------------------------
    # 13. Evt_01 remains closed throughout
    # -------------------------------------------------------------------------
    late_evt01_res = client.post(
        "/api/projects",
        json={"title": "Should Fail Evt01", "event_id": "evt_01"},
        headers=headers_a,
    )
    assert late_evt01_res.status_code == 400
