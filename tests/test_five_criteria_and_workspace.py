import json
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Event, Track, Project, Score, RubricCriterion, Judge, User
from app.services.scoring import calculate_raw_score, compute_leaderboard, DEFAULT_FIVE_CRITERIA, CRITERIA_GUIDANCE


@pytest.fixture
def client():
    return TestClient(app)


def test_1_generic_rubric_criteria_parsing():
    """Score.criteria_json is safely parsed and matched against criteria weights."""
    score = Score(
        judge_id="jdg_01",
        project_id="prj_test_01",
        criteria_json=json.dumps({
            "functionality": 4,
            "innovation": 5,
            "quality": 4,
            "live_demo": 4,
            "presentation": 3,
        }),
    )
    weights = {
        "functionality": 0.25,
        "innovation": 0.20,
        "quality": 0.25,
        "live_demo": 0.20,
        "presentation": 0.10,
    }
    raw = calculate_raw_score(score, weights)
    assert raw == 4.10


def test_2_weighted_raw_score_calculation_generic_and_ignores_unknown():
    """Weighted raw score calculation uses all configured criteria and ignores unknown keys."""
    score = Score(
        judge_id="jdg_01",
        project_id="prj_test_02",
        criteria_json=json.dumps({
            "functionality": 5,
            "quality": 5,
            "unknown_metric": 1,
            "random_extra": 2,
        }),
    )
    weights = {
        "functionality": 1.0,
        "quality": 1.0,
    }
    # Weighted average: (5*1 + 5*1) / (1 + 1) = 5.00
    raw = calculate_raw_score(score, weights)
    assert raw == 5.0


def test_3_exact_mathematical_example():
    """Functionality=4 (25%), Innovation=5 (20%), Quality=4 (25%), Live Demo=4 (20%), Pitch=3 (10%) = 4.10 / 5.00."""
    score = Score(
        judge_id="jdg_01",
        project_id="prj_test_03",
        criteria_json=json.dumps({
            "functionality": 4,
            "innovation": 5,
            "quality": 4,
            "live_demo": 4,
            "presentation": 3,
        }),
    )
    weights = {
        "functionality": 0.25,
        "innovation": 0.20,
        "quality": 0.25,
        "live_demo": 0.20,
        "presentation": 0.10,
    }
    raw = calculate_raw_score(score, weights)
    expected = (4 * 0.25 + 5 * 0.20 + 4 * 0.25 + 4 * 0.20 + 3 * 0.10) / (0.25 + 0.20 + 0.25 + 0.20 + 0.10)
    assert round(raw, 2) == 4.10
    assert abs(raw - expected) < 1e-4


def test_4_dynamic_criteria_submission_enforces_all_criteria(client):
    """Dynamic criteria submission must reject missing criteria with 400 Bad Request (no default 3)."""
    # BuildForge event has 5 criteria
    payload = {
        "project_id": "prj_bf_01",
        "criteria": {
            "functionality": 4,
            "innovation": 5,
            "quality": 4,
            # missing live_demo and presentation!
        },
        "comment": "Incomplete submission test",
    }
    res = client.post(
        "/api/judge/scores",
        json=payload,
        cookies={"session": "jdg_a_91bc"},
    )
    assert res.status_code == 400
    assert "Missing score for criterion" in res.json()["detail"]


def test_5_score_range_validation_1_to_5(client):
    """Scores outside 1-5 must be rejected with 400 Bad Request."""
    payload_too_high = {
        "project_id": "prj_bf_01",
        "criteria": {
            "functionality": 6,
            "innovation": 5,
            "quality": 4,
            "live_demo": 4,
            "presentation": 3,
        },
    }
    res = client.post(
        "/api/judge/scores",
        json=payload_too_high,
        cookies={"session": "jdg_a_91bc"},
    )
    assert res.status_code == 400

    payload_too_low = {
        "project_id": "prj_bf_01",
        "criteria": {
            "functionality": 0,
            "innovation": 5,
            "quality": 4,
            "live_demo": 4,
            "presentation": 3,
        },
    }
    res = client.post(
        "/api/judge/scores",
        json=payload_too_low,
        cookies={"session": "jdg_a_91bc"},
    )
    assert res.status_code == 400


def test_6_legacy_3_criteria_submission_compatibility(client):
    """Legacy 3-criteria submission (functionality, quality, innovation) succeeds without error."""
    payload = {
        "project_id": "prj_bf_01",
        "functionality": 4,
        "quality": 4,
        "innovation": 5,
        "comment": "Legacy test evaluation",
    }
    res = client.post(
        "/api/judge/scores",
        json=payload,
        cookies={"session": "jdg_a_91bc"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["message"] == "Score saved successfully"
    assert data["criteria"]["functionality"] == 4
    assert data["criteria"]["quality"] == 4
    assert data["criteria"]["innovation"] == 5


def test_7_legacy_scores_with_null_criteria_json():
    """calculate_raw_score works correctly when criteria_json is None, reading legacy columns."""
    score = Score(
        judge_id="jdg_01",
        project_id="prj_legacy_01",
        functionality=4,
        quality=5,
        innovation=3,
        criteria_json=None,
    )
    weights = {"functionality": 1.0, "quality": 1.0, "innovation": 1.0}
    raw = calculate_raw_score(score, weights)
    assert raw == 4.0


def test_8_judge_review_workspace_access_control(client):
    """Dedicated review page access control: 403 for unauthorized roles/tracks, 404 for missing project."""
    # 1. Anonymous -> Redirect or login
    anon_res = client.get("/judge/projects/prj_bf_01", follow_redirects=False)
    assert anon_res.status_code in (303, 307)
    assert "/login" in anon_res.headers.get("location", "")

    # 2. Participant -> 403 Forbidden
    part_res = client.get("/judge/projects/prj_bf_01", cookies={"session": "prt_2e88"})
    assert part_res.status_code == 403

    # 3. Judge assigned to track -> 200 OK
    judge_res = client.get("/judge/projects/prj_bf_01", cookies={"session": "jdg_a_91bc"})
    assert judge_res.status_code == 200
    assert "NovaStack" in judge_res.text

    # 4. Non-existent project -> 404
    nf_res = client.get("/judge/projects/prj_nonexistent_999", cookies={"session": "jdg_a_91bc"})
    assert nf_res.status_code == 404


def test_9_judge_review_workspace_content_and_materials(client):
    """Review workspace renders 2-column layout with evidence materials on left and sticky ballot on right."""
    res = client.get("/judge/projects/prj_bf_01", cookies={"session": "jdg_a_91bc"})
    assert res.status_code == 200
    html = res.text

    # Left Column: Evidence & Materials
    assert "NovaStack" in html
    assert "Open Live Demo" in html
    assert "View GitHub Code" in html
    assert "Code Inspection Checklist" in html
    assert "README &amp; setup instructions" in html
    assert "Real commit history vs template" in html
    assert "Pitch Deck" in html

    # Right Column: Evaluation Ballot
    assert "Judge Ballot" in html
    assert "Calculated Raw Score" in html
    assert "Functionality &amp; Completeness" in html
    assert "Innovation &amp; Problem Solving" in html
    assert "GitHub Code &amp; Engineering Quality" in html
    assert "Live Demo &amp; Product Experience" in html
    assert "Pitch Deck &amp; Presentation" in html
    assert "Private Evaluation Notes &amp; Rationale" in html
    assert "Submit Review" in html


def test_10_judge_review_workspace_prepopulates_existing_scores(client):
    """When a project already has a score, the workspace pre-populates existing scores and comment."""
    res = client.get("/judge/projects/prj_bf_01", cookies={"session": "jdg_a_91bc"})
    assert res.status_code == 200
    html = res.text
    # Judge 1 scored NovaStack f=4, i=4, q=4, ld=5, p=4
    assert "Previously Scored" in html
    assert "Exceptional system design" in html


def test_11_judge_review_next_project_resolution(client):
    """Next project resolution links to the next unreviewed project in the judge's queue."""
    # Judge 1 in AI Systems: prj_ai_04 has 0 reviews
    res = client.get("/judge/projects/prj_ai_01", cookies={"session": "jdg_a_91bc"})
    assert res.status_code == 200
    html = res.text
    assert "Review Next Project" in html


def test_12_judge_queue_dashboard_improvements(client):
    """Judge Console (/judge) displays filter tabs, status badges with raw scores, and direct review links."""
    res = client.get("/judge", cookies={"session": "jdg_a_91bc"})
    assert res.status_code == 200
    html = res.text

    # Filter tabs
    assert "filterQueue('all'" in html
    assert "filterQueue('pending'" in html
    assert "filterQueue('completed'" in html

    # Status badges with raw scores
    assert "Scored (" in html or "Pending Review" in html

    # Direct links to review workspace
    assert "/judge/projects/" in html
    assert "Review Submission" in html


def test_13_organizer_rubric_ui_displays_5_criteria_and_updates(client):
    """Organizer UI displays 5 criteria with percentage weights and supports weight updates."""
    res = client.get("/organizer?event_id=evt_buildforge", cookies={"session": "org_7f2a"})
    assert res.status_code == 200
    html = res.text

    # Rubric section displays 5 criteria
    assert "Functionality &amp; Completeness" in html
    assert "Innovation &amp; Problem Solving" in html
    assert "GitHub Code &amp; Engineering Quality" in html
    assert "Live Demo &amp; Product Experience" in html
    assert "Pitch Deck &amp; Presentation" in html

    # Percentage weights
    assert "25%" in html
    assert "20%" in html
    assert "10%" in html

    # Update rubric weights
    update_res = client.put(
        "/api/organizer/rubric?event_id=evt_buildforge",
        json={"weights": {"functionality": 0.30, "innovation": 0.20, "quality": 0.20, "live_demo": 0.20, "presentation": 0.10}},
        cookies={"session": "org_7f2a"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["message"] == "Rubric weights updated successfully"

    # Reset back to original
    client.put(
        "/api/organizer/rubric?event_id=evt_buildforge",
        json={"weights": {"functionality": 0.25, "innovation": 0.20, "quality": 0.25, "live_demo": 0.20, "presentation": 0.10}},
        cookies={"session": "org_7f2a"},
    )


def test_14_demo_events_have_5_criteria_and_evt01_has_3():
    """BuildForge, AI Systems, and Open Live have 5 criteria; evt_01 strictly has 3 criteria."""
    with SessionLocal() as db:
        bf_crits = db.query(RubricCriterion).filter(RubricCriterion.event_id == "evt_buildforge").all()
        assert len(bf_crits) == 5

        ai_crits = db.query(RubricCriterion).filter(RubricCriterion.event_id == "evt_aisystems").all()
        assert len(ai_crits) == 5

        live_crits = db.query(RubricCriterion).filter(RubricCriterion.event_id == "evt_open_live").all()
        assert len(live_crits) == 5

        evt01_crits = db.query(RubricCriterion).filter(RubricCriterion.event_id == "evt_01").all()
        assert len(evt01_crits) == 3
        evt01_names = {c.name for c in evt01_crits}
        assert evt01_names == {"functionality", "quality", "innovation"}


def test_15_evt_buildforge_podium_preserved_with_5_criteria():
    """BuildForge leaderboard ranks NovaStack #1, ByteForge #2, Neural Nomads #3."""
    with SessionLocal() as db:
        leaderboard = compute_leaderboard(db, event_id="evt_buildforge")
        assert len(leaderboard) >= 5
        top_titles = [entry["title"] for entry in leaderboard[:5]]
        assert top_titles[0] == "NovaStack"
        assert top_titles[1] == "ByteForge"
        assert top_titles[2] == "Neural Nomads"
        assert top_titles[3] == "Runtime Rebels"
        assert top_titles[4] == "ZeroDay Studio"
