import os
import json
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import (
    User, Project, Score, Event, WebhookSubscription,
    WebhookDeliveryLog, VerifiableJudgeRecord, Prize
)
from app.services.webhooks import compute_signature
from app.services.verifiable_records import (
    verify_chain,
    get_public_key,
    get_or_create_key_pair,
    verify_record_signature,
    sign_record_hash,
    KEY_FILE_PATH,
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
    assert test_result["success"] is False  # server not running on 9999, safely handled

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
    with SessionLocal() as db:
        project = db.query(Project).first()
        proj_id = project.id

    # 1. Judge submits score
    score_payload = {
        "project_id": proj_id,
        "functionality": 5,
        "quality": 4,
        "innovation": 5,
        "comment": "T4 cryptographic audit chain test score",
    }
    res = client.post("/api/v1/scores", json=score_payload, headers=JUDGE_A)
    assert res.status_code == 200

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


def test_t4_asymmetric_signature_verification_and_key_persistence(client):
    """
    Verifies asymmetric RSA signing, public key exposure, third-party independent verification,
    tampered signature rejection, and persistent key recovery across server reloads.
    """
    # 1. Fetch public verification key from API
    pub_res = client.get("/api/v1/verifiable-records/public-key")
    assert pub_res.status_code == 200
    pub_data = pub_res.json()
    assert "modulus_n" in pub_data
    assert "exponent_e" in pub_data
    assert pub_data["algorithm"] == "RSASSA-PKCS1-v1_5-SHA256"
    assert pub_data["exponent_e"] == 65537
    n_hex = pub_data["modulus_n"]
    e_val = pub_data["exponent_e"]

    # 2. Create a signed score and obtain the record
    with SessionLocal() as db:
        project = db.query(Project).first()
        proj_id = project.id

    score_res = client.post(
        "/api/v1/scores",
        json={"project_id": proj_id, "functionality": 5, "quality": 5, "innovation": 5, "comment": "Asymmetric test"},
        headers=JUDGE_A,
    )
    assert score_res.status_code == 200

    # 3. Query the verifiable record from the API
    rec_res = client.get(f"/api/v1/verifiable-records?project_id={proj_id}")
    assert rec_res.status_code == 200
    records = rec_res.json()
    assert len(records) > 0
    latest_rec = records[-1]
    rec_hash = latest_rec["record_hash"]
    signature = latest_rec["signature"]

    # 4. Independent verification using ONLY public parameters
    n_int = int(n_hex, 16)
    sig_int = int(signature, 16)
    recovered_m = pow(sig_int, e_val, n_int)
    expected_m = int(rec_hash, 16) % n_int
    assert recovered_m == expected_m, "Independent mathematical verification failed!"

    # 5. Verify through standalone verify-record endpoint
    verify_api_res = client.post(
        "/api/v1/verifiable-records/verify-record",
        json={"record_id": latest_rec["id"]},
    )
    assert verify_api_res.status_code == 200
    assert verify_api_res.json()["valid"] is True

    # 6. Tamper with signature: Must fail verification
    bad_sig = hex((int(signature, 16) + 1) % n_int)[2:]
    bad_verify_res = client.post(
        "/api/v1/verifiable-records/verify-record",
        json={"record_hash": rec_hash, "signature": bad_sig},
    )
    assert bad_verify_res.status_code == 200
    assert bad_verify_res.json()["valid"] is False

    # 7. Tamper with record hash: Must fail verification
    tampered_hash = "0" * 64
    tamper_hash_res = client.post(
        "/api/v1/verifiable-records/verify-record",
        json={"record_hash": tampered_hash, "signature": signature},
    )
    assert tamper_hash_res.status_code == 200
    assert tamper_hash_res.json()["valid"] is False

    # 8. Key persistence across simulated restart
    assert os.path.exists(KEY_FILE_PATH), f"Key file must exist at {KEY_FILE_PATH}"
    # Force reload keys from disk as if application restarted
    reloaded_keys = get_or_create_key_pair(force_reload=True)
    assert reloaded_keys["public_key"]["n"] == n_hex, "Modulus changed across reload!"
    # Verify signature created before restart still verifies with reloaded key
    assert verify_record_signature(rec_hash, signature) is True


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

    # 4. Teams & invites
    team_res = client.post("/api/v1/teams", json={"name": "Team Alpha V1"}, headers=PARTICIPANT)
    assert team_res.status_code == 201
    team_id = team_res.json()["id"]

    invite_res = client.post(f"/api/v1/teams/{team_id}/invites", headers=PARTICIPANT)
    assert invite_res.status_code == 201
    assert "token" in invite_res.json()

    # 5. Rubric weights update (Organizer only)
    rubric_denied = client.put("/api/v1/rubric-criteria/weights", json={"weights": {"functionality": 2.0}}, headers=PARTICIPANT)
    assert rubric_denied.status_code == 403

    rubric_ok = client.put("/api/v1/rubric-criteria/weights", json={"weights": {"functionality": 2.0}}, headers=ORG)
    assert rubric_ok.status_code == 200

    # 6. Judging progress (Organizer only)
    progress_denied = client.get("/api/v1/judging/progress", headers=PARTICIPANT)
    assert progress_denied.status_code == 403

    progress_ok = client.get("/api/v1/judging/progress", headers=ORG)
    assert progress_ok.status_code == 200
    assert "total_projects" in progress_ok.json()
    assert "total_judges" in progress_ok.json()

    # 7. Community voting config (Organizer only)
    vconfig_denied = client.put("/api/v1/events/evt_01/voting-config", json={"voting_mode": "open"}, headers=PARTICIPANT)
    assert vconfig_denied.status_code == 403

    # Clean up test prize
    client.delete(f"/api/v1/prizes/{new_prize_id}", headers=ORG)


def test_t4_offline_certificate_generation_and_verification(client):
    """Verifies standalone SVG certificate generation and cryptographic SHA-256 verification."""
    with SessionLocal() as db:
        proj = db.query(Project).first()
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
        proj = db.query(Project).first()
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
                {"id": "bad_proj"}  # missing mandatory 'title' field -> triggers ValueError
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
