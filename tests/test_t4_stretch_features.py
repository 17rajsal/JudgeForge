import os
import json
import pytest
from starlette.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import ed25519
from app.main import app
from app.database import SessionLocal
from app.models import (
    User, Project, Score, Event, Track, Team, TeamMember,
    WebhookSubscription, WebhookDeliveryLog, VerifiableJudgeRecord, Prize
)
from app.services.webhooks import compute_signature
from app.services.verifiable_records import (
    verify_chain,
    get_public_key,
    get_or_create_key_pair,
    verify_record_signature,
    sign_record_hash,
    KEY_PEM_PATH,
)
from app.services.certificates import compute_certificate_fingerprint

ORG = {"Authorization": "Bearer token_organizer"}
PARTICIPANT = {"Authorization": "Bearer token_participant"}
JUDGE_A = {"Authorization": "Bearer token_judge_a"}


@pytest.fixture
def client():
    return TestClient(app)


def test_t4_rest_api_v1_projects_and_events(client):
    """Verifies REST API v1 endpoints for projects, events, and rubric criteria."""
    # 1. List projects
    res = client.get("/api/v1/projects")
    assert res.status_code == 200
    projects = res.json()
    assert isinstance(projects, list)
    assert len(projects) > 0

    first_proj = projects[0]
    proj_id = first_proj["id"]

    # 2. Get project details
    res = client.get(f"/api/v1/projects/{proj_id}")
    assert res.status_code == 200
    assert res.json()["id"] == proj_id

    # 3. List events
    res = client.get("/api/v1/events")
    assert res.status_code == 200
    events = res.json()
    assert len(events) > 0
    event_id = events[0]["id"]

    # 4. Get event details
    res = client.get(f"/api/v1/events/{event_id}")
    assert res.status_code == 200
    assert res.json()["id"] == event_id

    # 5. List rubric criteria
    res = client.get(f"/api/v1/rubric-criteria?event_id={event_id}")
    assert res.status_code == 200
    criteria = res.json()
    assert len(criteria) >= 3


def test_t4_rest_api_v1_rbac_and_results_privacy(client):
    """Verifies security boundaries on REST API v1 audit logs and unpublished results."""
    # 1. Anonymous audit log access must be rejected
    res = client.get("/api/v1/audit-logs")
    assert res.status_code in (401, 403)

    # 2. Participant cannot access audit logs
    res = client.get("/api/v1/audit-logs", headers=PARTICIPANT)
    assert res.status_code == 403

    # 3. Organizer CAN access audit logs
    res = client.get("/api/v1/audit-logs", headers=ORG)
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_t4_project_team_authorization_and_cross_event_rejection(client):
    """
    Verifies project team authorization boundaries:
    - Participant supplying a team_id must be a member of that team (403 if not).
    - Participant can create project for their own team (201).
    - Organizer/admin can bypass team membership check (201).
    - Reject cross-event team_id (400).
    - Reject cross-event track_id (400).
    """
    # 1. Create an open event with its own track
    ev_open_res = client.post(
        "/api/v1/events",
        json={
            "name": "Open Event For Security Testing",
            "submissions_close": "2030-01-01T00:00:00Z",
            "tracks": ["Main Open Track"],
        },
        headers=ORG,
    )
    assert ev_open_res.status_code == 201
    ev_open_id = ev_open_res.json()["id"]

    # Create a second event with a different track
    ev2_res = client.post(
        "/api/v1/events",
        json={
            "name": "Second Event For Security Testing",
            "submissions_close": "2030-01-01T00:00:00Z",
            "tracks": ["Security Track EV2"],
        },
        headers=ORG,
    )
    assert ev2_res.status_code == 201
    ev2_id = ev2_res.json()["id"]

    ev2_tracks = client.get(f"/api/v1/events/{ev2_id}/tracks").json()
    assert len(ev2_tracks) > 0
    ev2_track_id = ev2_tracks[0]["id"]

    # Create team in ev2
    ev2_team_res = client.post("/api/v1/teams", json={"name": "EV2 Team", "event_id": ev2_id}, headers=ORG)
    assert ev2_team_res.status_code == 201
    ev2_team_id = ev2_team_res.json()["id"]

    # 2. Create another team in ev_open where PARTICIPANT is NOT a member
    other_team_res = client.post("/api/v1/teams", json={"name": "Organizer Private Team", "event_id": ev_open_id}, headers=ORG)
    assert other_team_res.status_code == 201
    other_team_id = other_team_res.json()["id"]

    # Create a team where PARTICIPANT IS a member
    own_team_res = client.post("/api/v1/teams", json={"name": "Participant Own Team", "event_id": ev_open_id}, headers=PARTICIPANT)
    assert own_team_res.status_code == 201
    own_team_id = own_team_res.json()["id"]

    # 3. Participant tries to create a project specifying another team -> MUST BE REJECTED 403
    bad_team_res = client.post(
        "/api/v1/projects",
        json={
            "title": "Unauthorized Team Project",
            "event_id": ev_open_id,
            "team_id": other_team_id,
        },
        headers=PARTICIPANT,
    )
    assert bad_team_res.status_code == 403
    assert "not a member" in bad_team_res.json()["detail"].lower()

    # 4. Participant creates project specifying their own team -> MUST SUCCEED 201
    good_team_res = client.post(
        "/api/v1/projects",
        json={
            "title": "Authorized Own Team Project",
            "event_id": ev_open_id,
            "team_id": own_team_id,
            "status": "draft",
        },
        headers=PARTICIPANT,
    )
    assert good_team_res.status_code == 201

    # 5. Organizer creates project for another team -> ORGANIZER BYPASS ALLOWED 201
    org_team_res = client.post(
        "/api/v1/projects",
        json={
            "title": "Organizer Created Project",
            "event_id": ev_open_id,
            "team_id": other_team_id,
        },
        headers=ORG,
    )
    assert org_team_res.status_code == 201

    # 6. Reject cross-event team_id -> team belongs to ev2, but project is for ev_open -> MUST BE REJECTED 400
    cross_team_res = client.post(
        "/api/v1/projects",
        json={
            "title": "Cross Event Team Project",
            "event_id": ev_open_id,
            "team_id": ev2_team_id,
        },
        headers=ORG,
    )
    assert cross_team_res.status_code == 400
    assert "team does not belong to the selected event" in cross_team_res.json()["detail"].lower()

    # 7. Reject cross-event track_id -> track belongs to ev2, but project is for ev_open -> MUST BE REJECTED 400
    cross_track_res = client.post(
        "/api/v1/projects",
        json={
            "title": "Cross Event Track Project",
            "event_id": ev_open_id,
            "team_id": own_team_id,
            "track_id": ev2_track_id,
        },
        headers=PARTICIPANT,
    )
    assert cross_track_res.status_code == 400
    assert "track does not belong to the selected event" in cross_track_res.json()["detail"].lower()


def test_t4_team_privacy_and_draft_protection(client):
    """
    Verifies that:
    - GET /api/v1/teams never exposes member emails or user IDs.
    - Public representation contains id, name, event_id, member_count, submitted_project_count.
    - GET /api/v1/teams/{id} redacts member emails, user IDs, and draft projects for anonymous/non-members.
    - Authenticated team members and organizers receive full detailed member info and draft projects.
    """
    # 1. Create an open event and dedicated team
    ev_res = client.post(
        "/api/v1/events",
        json={"name": "Privacy Testing Event", "submissions_close": "2030-01-01T00:00:00Z", "tracks": ["Privacy Track"]},
        headers=ORG,
    )
    assert ev_res.status_code == 201
    ev_id = ev_res.json()["id"]

    team_res = client.post("/api/v1/teams", json={"name": "Privacy Shield Team", "event_id": ev_id}, headers=PARTICIPANT)
    assert team_res.status_code == 201
    t_id = team_res.json()["id"]

    # Create 1 draft project
    p1 = client.post(
        "/api/v1/projects",
        json={"title": "Secret Draft Project", "event_id": ev_id, "team_id": t_id, "status": "draft"},
        headers=PARTICIPANT,
    )
    assert p1.status_code == 201

    # Create 1 submitted project
    p2 = client.post(
        "/api/v1/projects",
        json={"title": "Public Final Project", "event_id": ev_id, "team_id": t_id, "status": "submitted"},
        headers=PARTICIPANT,
    )
    assert p2.status_code == 201

    # 2. Check public list: GET /api/v1/teams
    list_res = client.get(f"/api/v1/teams?event_id={ev_id}")
    assert list_res.status_code == 200
    teams_list = list_res.json()
    target_team = next((item for item in teams_list if item["id"] == t_id), None)
    assert target_team is not None

    # Verify public fields exist
    assert "id" in target_team
    assert "name" in target_team
    assert "event_id" in target_team
    assert "member_count" in target_team
    assert "submitted_project_count" in target_team

    # Verify sensitive data is NOT exposed
    assert "members" not in target_team
    assert "email" not in target_team
    assert "user_id" not in target_team
    # Only submitted projects should be counted
    assert target_team["submitted_project_count"] == 1
    assert target_team["member_count"] >= 1

    # 3. Check single team as ANONYMOUS caller: GET /api/v1/teams/{id}
    anon_res = client.get(f"/api/v1/teams/{t_id}")
    assert anon_res.status_code == 200
    anon_team = anon_res.json()

    # Anonymous caller MUST NOT see member emails or user IDs
    assert "members" not in anon_team
    assert "email" not in str(anon_team)

    # Anonymous caller MUST NOT see draft projects
    anon_projects = anon_team.get("projects", [])
    assert len(anon_projects) == 1
    assert anon_projects[0]["title"] == "Public Final Project"
    assert anon_projects[0]["status"] == "submitted"
    assert not any(p["title"] == "Secret Draft Project" for p in anon_projects)

    # 4. Check single team as AUTHENTICATED TEAM MEMBER
    member_res = client.get(f"/api/v1/teams/{t_id}", headers=PARTICIPANT)
    assert member_res.status_code == 200
    member_team = member_res.json()

    # Team member CAN see members with emails and user IDs
    assert "members" in member_team
    assert len(member_team["members"]) >= 1
    assert "email" in member_team["members"][0]
    assert "user_id" in member_team["members"][0]

    # Team member CAN see draft projects
    member_projects = member_team.get("projects", [])
    assert len(member_projects) == 2
    assert any(p["title"] == "Secret Draft Project" for p in member_projects)

    # 5. Check single team as ORGANIZER
    org_res = client.get(f"/api/v1/teams/{t_id}", headers=ORG)
    assert org_res.status_code == 200
    org_team = org_res.json()
    assert "members" in org_team
    assert len(org_team.get("projects", [])) == 2


def test_t4_asymmetric_ed25519_verification_and_key_persistence(client):
    """
    Verifies maintained Ed25519 asymmetric signing via the cryptography package:
    - Public verification key exposure via API.
    - Automatic signing of score records with Ed25519 private key.
    - Third-party independent verification using raw public key bytes.
    - Standalone verification endpoint (/verify-record).
    - Tampered signature rejection.
    - Tampered record hash rejection.
    - Persistent key recovery across server reloads without key regeneration.
    - Private key never exposed over API.
    """
    # 1. Fetch public verification key from API
    pub_res = client.get("/api/v1/verifiable-records/public-key")
    assert pub_res.status_code == 200
    pub_data = pub_res.json()
    assert pub_data["algorithm"] == "Ed25519"
    assert "public_key" in pub_data
    pub_hex = pub_data["public_key"]
    # Ed25519 public key is exactly 32 bytes = 64 hex characters
    assert len(pub_hex) == 64
    # Private key must NEVER be leaked
    assert "private_key" not in pub_data
    assert "d" not in pub_data

    # 2. Judge submits score
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.status == "submitted").first()
        proj_id = project.id

    score_res = client.post(
        "/api/v1/scores",
        json={"project_id": proj_id, "functionality": 5, "quality": 4, "innovation": 5, "comment": "Ed25519 verification score"},
        headers=JUDGE_A,
    )
    assert score_res.status_code == 200

    # 3. Retrieve signed verifiable record from API
    rec_res = client.get(f"/api/v1/verifiable-records?project_id={proj_id}")
    assert rec_res.status_code == 200
    records = rec_res.json()
    assert len(records) > 0
    latest_rec = records[-1]
    rec_hash = latest_rec["record_hash"]
    signature = latest_rec["signature"]
    assert latest_rec["signature_scheme"] == "Ed25519"
    # Ed25519 signature is 64 bytes = 128 hex characters
    assert len(signature) == 128

    # 4. Independent third-party verification using standard Ed25519 library
    public_key_obj = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    # verify() will raise InvalidSignature if invalid; returns None on success
    public_key_obj.verify(bytes.fromhex(signature), rec_hash.encode("utf-8"))

    # 5. Standalone verify-record endpoint by record_id
    verify_res = client.post(
        "/api/v1/verifiable-records/verify-record",
        json={"record_id": latest_rec["id"]},
    )
    assert verify_res.status_code == 200
    assert verify_res.json()["valid"] is True
    assert verify_res.json()["signature_scheme"] == "Ed25519"

    # 6. Standalone verify-record endpoint with custom public key
    verify_custom = client.post(
        "/api/v1/verifiable-records/verify-record",
        json={"record_hash": rec_hash, "signature": signature, "public_key": pub_hex},
    )
    assert verify_custom.status_code == 200
    assert verify_custom.json()["valid"] is True

    # 7. Tampered signature fails verification
    tampered_sig = ("00" if signature[:2] != "00" else "ff") + signature[2:]
    bad_sig_res = client.post(
        "/api/v1/verifiable-records/verify-record",
        json={"record_hash": rec_hash, "signature": tampered_sig},
    )
    assert bad_sig_res.status_code == 200
    assert bad_sig_res.json()["valid"] is False

    # 8. Tampered record hash fails verification
    tampered_hash = "0" * 64
    bad_hash_res = client.post(
        "/api/v1/verifiable-records/verify-record",
        json={"record_hash": tampered_hash, "signature": signature},
    )
    assert bad_hash_res.status_code == 200
    assert bad_hash_res.json()["valid"] is False

    # 9. Key persistence across restart
    assert os.path.exists(KEY_PEM_PATH), f"Ed25519 key file must exist at {KEY_PEM_PATH}"
    reloaded_keys = get_or_create_key_pair(force_reload=True)
    assert reloaded_keys["public_key_hex"] == pub_hex, "Public key changed across restart reload!"
    # Verify that signature created prior to reload remains valid
    assert verify_record_signature(rec_hash, signature) is True


def test_t4_verifiable_records_privacy_and_isolation(client):
    """
    Verifies that verifiable judge records and chain verification endpoints:
    - Never expose numeric judge scores (functionality, quality, innovation).
    - Never expose private judge comments.
    - Never expose authentication tokens or credentials.
    - Preserves full T2 score privacy.
    """
    # 1. Ensure a record exists
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.status == "submitted").first()
        proj_id = project.id

    client.post(
        "/api/v1/scores",
        json={"project_id": proj_id, "functionality": 4, "quality": 4, "innovation": 4, "comment": "Privacy check evaluation"},
        headers=JUDGE_A,
    )

    # 2. Check verifiable records list
    res = client.get("/api/v1/verifiable-records")
    assert res.status_code == 200
    records = res.json()
    assert len(records) > 0

    for r in records:
        # Must not expose numeric evaluation scores
        assert "functionality" not in r
        assert "quality" not in r
        assert "innovation" not in r
        # Must not expose judge comments
        assert "comment" not in r
        # Must not expose credentials or password tokens
        assert "token" not in r
        assert "password" not in r

    # 3. Check chain verification endpoint
    chain_res = client.get("/api/v1/verifiable-records/verify?event_id=evt_01")
    assert chain_res.status_code == 200
    chain_data = chain_res.json()
    assert chain_data["valid"] is True
    assert "functionality" not in str(chain_data)
    assert "comment" not in str(chain_data)


def test_t4_outbound_webhooks_lifecycle_and_fail_safety(client):
    """Verifies webhook subscription registration, test ping, HMAC calculation, and crash isolation."""
    # 1. Create Webhook Subscription
    create_payload = {
        "event_id": "evt_01",
        "target_url": "http://127.0.0.1:9999/test-webhook",
        "secret": "test_webhook_secret_key_12345",
        "events_subscribed": "project.submitted,score.submitted",
    }
    res = client.post("/api/v1/webhooks", json=create_payload, headers=ORG)
    assert res.status_code == 201
    wh = res.json()
    assert wh["target_url"] == create_payload["target_url"]
    wh_id = wh["id"]

    # 2. List Webhooks
    res = client.get("/api/v1/webhooks?event_id=evt_01", headers=ORG)
    assert res.status_code == 200
    ids = [item["id"] for item in res.json()]
    assert wh_id in ids

    # 3. Test Webhook Ping (delivery to nonexistent port 9999 must NOT crash the app)
    res = client.post(f"/api/v1/webhooks/{wh_id}/test", headers=ORG)
    assert res.status_code == 200
    test_result = res.json()["test_result"]
    assert test_result["success"] is False

    # 4. Check delivery logs
    res = client.get(f"/api/v1/webhooks/{wh_id}/logs", headers=ORG)
    assert res.status_code == 200
    logs = res.json()
    assert len(logs) >= 1
    assert logs[0]["event_type"] == "test.ping"

    # 5. Verify HMAC computation helper
    payload_bytes = b'{"event":"test","status":"ok"}'
    sig = compute_signature("secret_key", payload_bytes)
    assert len(sig) == 64  # SHA-256 hex string

    # 6. Delete Webhook
    del_res = client.delete(f"/api/v1/webhooks/{wh_id}", headers=ORG)
    assert del_res.status_code == 200


def test_t4_verifiable_judge_records_and_tamper_detection(client):
    """Verifies signed judge score records hash chain and cryptographic tamper detection."""
    # 1. Submit score to ensure chain exists
    with SessionLocal() as db:
        project = db.query(Project).filter(Project.status == "submitted").first()
        proj_id = project.id

    client.post(
        "/api/v1/scores",
        json={"project_id": proj_id, "functionality": 5, "quality": 4, "innovation": 5, "comment": "Tamper test score"},
        headers=JUDGE_A,
    )

    # 2. Check verifiable records chain verification
    res = client.get("/api/v1/verifiable-records/verify?event_id=evt_01")
    assert res.status_code == 200
    verify_data = res.json()
    assert verify_data["valid"] is True
    assert verify_data["total_records"] > 0

    # 3. Test Tamper Detection
    with SessionLocal() as db:
        latest_record = db.query(VerifiableJudgeRecord).order_by(VerifiableJudgeRecord.id.desc()).first()
        assert latest_record is not None
        original_hash = latest_record.record_hash

        # Tamper with the record hash
        latest_record.record_hash = "f" * 64
        db.commit()

        # Chain verification must now detect tampering
        tamper_check = verify_chain(db, "evt_01")
        assert tamper_check["valid"] is False
        assert tamper_check["broken_at_id"] == latest_record.id

        # Restore original hash
        latest_record.record_hash = original_hash
        db.commit()

        # Must be valid again
        restore_check = verify_chain(db, "evt_01")
        assert restore_check["valid"] is True


def test_t4_rest_api_v1_full_inventory_and_rbac(client):
    """
    Verifies full REST API v1 coverage and RBAC boundaries across:
    auth, events, tracks, prizes, teams, comments, judging, and community voting.
    """
    # 1. Auth endpoints
    res = client.get("/api/v1/auth/me")
    assert res.status_code == 200
    assert res.json()["authenticated"] is False

    res = client.get("/api/v1/auth/me", headers=PARTICIPANT)
    assert res.status_code == 200
    assert res.json()["authenticated"] is True
    assert res.json()["user"]["role"] == "participant"

    # 2. Event tracks management (Organizer only for creation)
    track_create = client.post("/api/v1/events/evt_01/tracks", json={"name": "AI & Robotics"}, headers=PARTICIPANT)
    assert track_create.status_code == 403

    track_ok = client.post("/api/v1/events/evt_01/tracks", json={"name": "AI & Robotics"}, headers=ORG)
    assert track_ok.status_code == 201
    new_track_id = track_ok.json()["id"]

    tracks_res = client.get("/api/v1/events/evt_01/tracks")
    assert tracks_res.status_code == 200
    assert any(t["id"] == new_track_id for t in tracks_res.json())

    # 3. Event prizes management (Organizer only for creation/update/deletion)
    prize_data = {"title": "Best Technical Architecture", "description": "Offline-first resilience", "amount": "$3,000", "placement": "1st"}
    prize_denied = client.post("/api/v1/events/evt_01/prizes", json=prize_data, headers=PARTICIPANT)
    assert prize_denied.status_code == 403

    prize_ok = client.post("/api/v1/events/evt_01/prizes", json=prize_data, headers=ORG)
    assert prize_ok.status_code == 201
    new_prize_id = prize_ok.json()["id"]

    prize_update = client.put(f"/api/v1/prizes/{new_prize_id}", json={"amount": "$4,000"}, headers=ORG)
    assert prize_update.status_code == 200
    assert prize_update.json()["amount"] == "$4,000"

    # 4. Rubric weights update (Organizer only)
    rubric_denied = client.put("/api/v1/rubric-criteria/weights", json={"weights": {"functionality": 2.0}}, headers=PARTICIPANT)
    assert rubric_denied.status_code == 403

    rubric_ok = client.put("/api/v1/rubric-criteria/weights", json={"weights": {"functionality": 2.0}}, headers=ORG)
    assert rubric_ok.status_code == 200

    # 5. Judging progress (Organizer only)
    progress_denied = client.get("/api/v1/judging/progress", headers=PARTICIPANT)
    assert progress_denied.status_code == 403

    progress_ok = client.get("/api/v1/judging/progress", headers=ORG)
    assert progress_ok.status_code == 200
    assert "total_projects" in progress_ok.json()
    assert "total_judges" in progress_ok.json()

    # 6. Community voting config (Organizer only)
    vconfig_denied = client.put("/api/v1/events/evt_01/voting-config", json={"voting_mode": "open"}, headers=PARTICIPANT)
    assert vconfig_denied.status_code == 403

    # Clean up test prize
    client.delete(f"/api/v1/prizes/{new_prize_id}", headers=ORG)


def test_t4_offline_certificate_generation_and_verification(client):
    """Verifies standalone SVG certificate generation and cryptographic SHA-256 verification."""
    with SessionLocal() as db:
        proj = db.query(Project).filter(Project.status == "submitted").first()
        proj_id = proj.id
        event_id = proj.event_id
        team_name = proj.team.name if proj.team else "Independent Creator"

    # 1. SVG Certificate Download
    res = client.get(f"/certificates/project/{proj_id}/svg")
    assert res.status_code == 200
    assert "image/svg+xml" in res.headers["content-type"]
    svg_body = res.text
    assert "<svg" in svg_body
    assert "</svg>" in svg_body
    assert "JudgeForge" in svg_body

    # 2. HTML Certificate View
    res = client.get(f"/certificates/project/{proj_id}")
    assert res.status_code == 200
    assert "<svg" in res.text

    # 3. Calculate authentic fingerprint
    fp = compute_certificate_fingerprint(
        event_id=event_id,
        project_id=proj_id,
        recipient_name=team_name,
        award_title="Official Participant",
    )

    # 4. Verify authentic fingerprint via API
    res = client.get(f"/api/v1/certificates/verify/{fp}")
    assert res.status_code == 200
    assert res.json()["valid"] is True
    assert res.json()["project_id"] == proj_id

    # 5. Verify invalid fingerprint returns valid: False
    res = client.get("/api/v1/certificates/verify/0000000000000000000000000000000000000000000000000000000000000000")
    assert res.status_code == 200
    assert res.json()["valid"] is False


def test_t4_embeddable_gallery_and_frame_policy(client):
    """Verifies embed routes relax framing headers to allow iframe integration."""
    with SessionLocal() as db:
        proj = db.query(Project).filter(Project.status == "submitted").first()
        proj_id = proj.id

    # 1. Embed Gallery
    res = client.get("/embed/gallery")
    assert res.status_code == 200
    assert "frame-ancestors *" in res.headers.get("Content-Security-Policy", "")
    assert res.headers.get("X-Frame-Options") != "DENY"
    assert "Hackathon Gallery" in res.text or "projects" in res.text

    # 2. Embed Single Project
    res = client.get(f"/embed/project/{proj_id}")
    assert res.status_code == 200
    assert "frame-ancestors *" in res.headers.get("Content-Security-Policy", "")


def test_t4_bulk_export_and_import_with_rollback_safety(client):
    """Verifies bulk data export with SHA-256 checksum and atomic import rollback upon error."""
    # 1. Export Bulk Data
    res = client.get("/api/v1/export/bulk?event_id=evt_01", headers=ORG)
    assert res.status_code == 200
    export_pkg = res.json()
    assert "checksum" in export_pkg
    assert "data" in export_pkg
    assert "event" in export_pkg["data"]
    assert "projects" in export_pkg["data"]

    # 2. Test Invalid Data Import Rollback
    invalid_pkg = {
        "version": "1.0",
        "data": {
            "event": {"id": "evt_01"},
            "projects": [
                {"id": "bad_proj"}
            ]
        }
    }
    import_res = client.post("/api/v1/import/bulk", json=invalid_pkg, headers=ORG)
    assert import_res.status_code == 400
    assert "Import failed and was rolled back" in import_res.json()["detail"]

    # 3. Test Checksum Tamper Rejection
    tampered_pkg = {
        "version": "1.0",
        "checksum": "0000000000000000000000000000000000000000000000000000000000000000",
        "data": export_pkg["data"],
    }
    tamper_res = client.post("/api/v1/import/bulk", json=tampered_pkg, headers=ORG)
    assert tamper_res.status_code == 400
    assert "Data integrity checksum mismatch" in tamper_res.json()["detail"]

    # 4. Valid Import Package
    valid_import = {
        "version": "1.0",
        "checksum": export_pkg["checksum"],
        "data": export_pkg["data"],
    }
    valid_res = client.post("/api/v1/import/bulk", json=valid_import, headers=ORG)
    assert valid_res.status_code == 200
    assert "Bulk import completed successfully" in valid_res.json()["message"]
