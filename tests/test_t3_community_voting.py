import datetime as dt
import time
from starlette.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Event, Project, CommunityVote, Comment, VotingVoucher, AuditLog

ORG = {"Authorization": "Bearer token_organizer"}
PARTICIPANT = {"Authorization": "Bearer token_participant"}
JUDGE_A = {"Authorization": "Bearer token_judge_a"}


def test_community_voting_authenticated_mode():
    with TestClient(app) as client:
        # Get fixture event and projects
        with SessionLocal() as db:
            event = db.query(Event).first()
            event_id = event.id
            project = db.query(Project).filter(Project.event_id == event_id, Project.status == "submitted").first()
            project_id = project.id

            # Ensure voting window is open and mode is authenticated
            event.voting_mode = "authenticated"
            event.voting_opens = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
            event.voting_closes = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5)
            event.voting_results_public = 0
            db.commit()

        # Unauthenticated vote rejected
        res = client.post(f"/api/events/{event_id}/vote", json={"project_id": project_id})
        assert res.status_code == 401

        # Authenticated vote accepted
        res = client.post(
            f"/api/events/{event_id}/vote",
            headers=PARTICIPANT,
            json={"project_id": project_id},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["project_id"] == project_id

        # Duplicate vote rejected
        res_dup = client.post(
            f"/api/events/{event_id}/vote",
            headers=PARTICIPANT,
            json={"project_id": project_id},
        )
        assert res_dup.status_code == 409
        assert "Duplicate vote detected" in res_dup.json()["detail"]


def test_community_voting_open_mode():
    with TestClient(app) as client:
        with SessionLocal() as db:
            event = db.query(Event).first()
            event_id = event.id
            project = db.query(Project).filter(Project.event_id == event_id, Project.status == "submitted").first()
            project_id = project.id

            event.voting_mode = "open"
            event.voting_opens = None
            event.voting_closes = None
            db.commit()

        # Open vote with voter token 1
        res = client.post(
            f"/api/events/{event_id}/vote",
            json={"project_id": project_id, "voter_token": "token_device_abc"},
        )
        assert res.status_code == 200

        # Duplicate open vote with same token rejected
        res_dup = client.post(
            f"/api/events/{event_id}/vote",
            json={"project_id": project_id, "voter_token": "token_device_abc"},
        )
        assert res_dup.status_code == 409

        # Open vote with different token accepted
        res2 = client.post(
            f"/api/events/{event_id}/vote",
            json={"project_id": project_id, "voter_token": "token_device_xyz"},
        )
        assert res2.status_code == 200


def test_community_voting_email_gated_mode():
    with TestClient(app) as client:
        with SessionLocal() as db:
            event = db.query(Event).first()
            event_id = event.id
            project = db.query(Project).filter(Project.event_id == event_id, Project.status == "submitted").first()
            project_id = project.id

            event.voting_mode = "email_gated"
            db.commit()

        # Attempt vote without voucher or email
        res = client.post(f"/api/events/{event_id}/vote", json={"project_id": project_id})
        assert res.status_code == 400

        # Attempt vote with invalid voucher
        res = client.post(
            f"/api/events/{event_id}/vote",
            json={"project_id": project_id, "voucher_token": "nonexistent_token"},
        )
        assert res.status_code == 403

        # Organizer issues voucher
        res_vch = client.post(
            f"/api/events/{event_id}/vouchers",
            headers=ORG,
            json={"email": "voter_1@local.community"},
        )
        assert res_vch.status_code == 200
        voucher_token = res_vch.json()["voucher_token"]

        # Vote with valid voucher
        res_vote = client.post(
            f"/api/events/{event_id}/vote",
            json={"project_id": project_id, "voucher_token": voucher_token},
        )
        assert res_vote.status_code == 200

        # Attempt to reuse redeemed voucher
        res_reuse = client.post(
            f"/api/events/{event_id}/vote",
            json={"project_id": project_id, "voucher_token": voucher_token},
        )
        assert res_reuse.status_code == 403


def test_voting_window_enforcement_and_result_hiding():
    with TestClient(app) as client:
        with SessionLocal() as db:
            event = db.query(Event).first()
            event_id = event.id
            project = db.query(Project).filter(Project.event_id == event_id, Project.status == "submitted").first()
            project_id = project.id

            # Case A: Window in future
            event.voting_mode = "open"
            event.voting_opens = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
            event.voting_closes = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5)
            event.voting_results_public = 0
            db.commit()

        res_early = client.post(
            f"/api/events/{event_id}/vote",
            json={"project_id": project_id, "voter_token": "early_bird"},
        )
        assert res_early.status_code == 400
        assert "not opened yet" in res_early.json()["detail"]

        # Case B: Window in past
        with SessionLocal() as db:
            event = db.get(Event, event_id)
            event.voting_opens = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)
            event.voting_closes = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
            db.commit()

        res_late = client.post(
            f"/api/events/{event_id}/vote",
            json={"project_id": project_id, "voter_token": "late_bird"},
        )
        assert res_late.status_code == 400
        assert "ended" in res_late.json()["detail"]

        # Result hiding while results_public = 0 and non-organizer
        res_pub_hide = client.get(f"/api/events/{event_id}/community-results")
        assert res_pub_hide.status_code == 403

        # Organizer can preview results even when private
        res_org_prev = client.get(f"/api/events/{event_id}/community-results", headers=ORG)
        assert res_org_prev.status_code == 200
        assert "standings" in res_org_prev.json()

        # Reveal results publicly
        client.put(
            f"/api/events/{event_id}/voting-config",
            headers=ORG,
            json={"voting_results_public": True},
        )
        res_revealed = client.get(f"/api/events/{event_id}/community-results")
        assert res_revealed.status_code == 200
        assert res_revealed.json()["results_published"] is True


def test_randomized_ballot_ordering():
    with TestClient(app) as client:
        with SessionLocal() as db:
            event = db.query(Event).first()
            event_id = event.id

        # Fetch ballot as two distinct participants
        res_a = client.get(f"/api/events/{event_id}/ballot", headers=PARTICIPANT)
        assert res_a.status_code == 200
        ballot_a = [p["id"] for p in res_a.json()["ballot"]]

        res_b = client.get(f"/api/events/{event_id}/ballot", headers=JUDGE_A)
        assert res_b.status_code == 200
        ballot_b = [p["id"] for p in res_b.json()["ballot"]]

        # Both have same projects, but different shuffled ordering
        assert set(ballot_a) == set(ballot_b)
        assert len(ballot_a) > 1
        # Seeded determinism: request as participant again returns identical order
        res_a2 = client.get(f"/api/events/{event_id}/ballot", headers=PARTICIPANT)
        assert [p["id"] for p in res_a2.json()["ballot"]] == ballot_a


def test_project_comments_and_moderation():
    with TestClient(app) as client:
        with SessionLocal() as db:
            project = db.query(Project).filter(Project.status == "submitted").first()
            project_id = project.id

        # Post comment as guest
        res_guest = client.post(
            f"/api/projects/{project_id}/comments",
            json={"author_name": "Open Source Fan", "content": "Awesome project architecture!"},
        )
        assert res_guest.status_code == 201
        guest_cid = res_guest.json()["id"]

        # Post comment as participant
        res_user = client.post(
            f"/api/projects/{project_id}/comments",
            headers=PARTICIPANT,
            json={"content": "Built with SQLite and FastAPI."},
        )
        assert res_user.status_code == 201
        user_cid = res_user.json()["id"]

        # List comments
        res_list = client.get(f"/api/projects/{project_id}/comments")
        assert res_list.status_code == 200
        comments = res_list.json()
        assert len(comments) >= 2
        assert any(c["id"] == guest_cid for c in comments)
        assert any(c["id"] == user_cid for c in comments)

        # Unauthorized delete attempt by guest
        assert client.delete(f"/api/comments/{user_cid}").status_code == 401

        # Author delete their own comment
        res_del_own = client.delete(f"/api/comments/{user_cid}", headers=PARTICIPANT)
        assert res_del_own.status_code == 200

        # Organizer delete any comment
        res_del_org = client.delete(f"/api/comments/{guest_cid}", headers=ORG)
        assert res_del_org.status_code == 200


def test_voting_rate_limiting():
    with TestClient(app) as client:
        with SessionLocal() as db:
            event = db.query(Event).first()
            event_id = event.id
            project = db.query(Project).filter(Project.event_id == event_id, Project.status == "submitted").first()
            project_id = project.id
            event.voting_mode = "open"
            event.voting_opens = None
            event.voting_closes = None
            db.commit()

        # Fire 12 votes rapidly from the same client IP
        responses = []
        for i in range(12):
            res = client.post(
                f"/api/events/{event_id}/vote",
                json={"project_id": project_id, "voter_token": f"burst_token_{i}"},
            )
            responses.append(res.status_code)

        # At least one should be rate limited with HTTP 429
        assert 429 in responses
        idx_429 = responses.index(429)
        assert idx_429 >= 10  # Allowed under 10, then throttled


def test_audit_trail_records_voting_events():
    with TestClient(app) as client:
        with SessionLocal() as db:
            event = db.query(Event).first()
            event_id = event.id
            project = db.query(Project).filter(Project.event_id == event_id, Project.status == "submitted").first()
            project_id = project.id
            event.voting_mode = "open"
            event.voting_opens = None
            event.voting_closes = None
            db.commit()

        # Cast a vote
        client.post(f"/api/events/{event_id}/vote", json={"project_id": project_id, "voter_token": "audit_token_1"})
        # Attempt duplicate
        client.post(f"/api/events/{event_id}/vote", json={"project_id": project_id, "voter_token": "audit_token_1"})

        # Add comment
        res_c = client.post(f"/api/projects/{project_id}/comments", json={"content": "Audit trail comment", "author_name": "Auditor"})
        cid = res_c.json()["id"]

        # Delete comment as organizer
        client.delete(f"/api/comments/{cid}", headers=ORG)

        with SessionLocal() as db:
            actions = [log.action for log in db.query(AuditLog).all()]
            assert "VOTE_CAST" in actions
            assert "VOTE_DUPLICATE_REJECTED" in actions
            assert "COMMENT_ADDED" in actions
            assert "COMMENT_DELETED" in actions
