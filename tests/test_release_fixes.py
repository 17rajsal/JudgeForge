import datetime as dt
from starlette.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import PasswordCredential

ORG = {"Authorization": "Bearer token_organizer"}
PARTICIPANT = {"Authorization": "Bearer token_participant"}

def test_login_requires_password_and_correct_password_works():
    with TestClient(app) as client:
        email = "organizer@judgeforge.local"
        assert client.post('/api/auth/login', json={"email":email}).status_code == 422
        assert client.post('/api/auth/login', json={"email":email,"password":"wrong"}).status_code == 401
        response=client.post('/api/auth/login',json={"email":email,"password":"JudgeForge-Demo-2026!"})
        assert response.status_code == 200
        assert client.get('/api/export.csv').status_code == 200

def test_production_mode_rejects_demo_credentials(monkeypatch):
    with TestClient(app) as client:
        response=client.post('/api/auth/login',json={"email":"organizer@judgeforge.local","password":"JudgeForge-Demo-2026!"})
        assert response.status_code == 200
        monkeypatch.setenv('DEMO_MODE','false')
        assert client.get('/api/export.csv').status_code == 401
        assert client.get('/api/export.csv',headers=ORG).status_code == 401
        assert client.post('/api/auth/login',json={"email":"organizer@judgeforge.local","password":"JudgeForge-Demo-2026!"}).status_code == 401

def test_new_participant_event_team_invite_submission_workflow():
    with TestClient(app) as client:
        body={"email":"new@example.org","name":"New Participant","password":"a-long-new-password"}
        assert client.post('/api/auth/register',json={**body,"role":"organizer"}).status_code == 422
        response=client.post('/api/auth/register',json=body)
        assert response.status_code == 201
        assert response.json()['role']=='participant'
        with SessionLocal() as db:
            assert db.get(PasswordCredential,response.json()['id']).password_hash != body['password']
        assert client.post('/api/auth/register',json=body).status_code==409
        event_data={"name":"New Hack", "submissions_close":(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat(),"tracks":["Tools"]}
        assert client.post('/api/events',json=event_data,headers=PARTICIPANT).status_code==403
        event=client.post('/api/events',json=event_data,headers=ORG).json()
        team=client.post('/api/teams',json={"name":"Fresh team","event_id":event['id']},headers=PARTICIPANT).json()
        response=client.post('/api/auth/login',json={"email":body['email'],"password":body['password']})
        assert response.status_code==200
        assert client.get('/workspace').status_code==200
        assert client.get('/submit?event_id='+event['id']).status_code==200
        project={"title":"Fresh submission","event_id":event['id'],"team_id":team['id']}
        assert client.post('/api/projects',json=project).status_code==403
        assert client.post('/api/teams/'+team['id']+'/invites').status_code==403
        invite=client.post('/api/teams/'+team['id']+'/invites',headers=PARTICIPANT).json()['invite_url'].split('invite=')[1]
        assert client.post('/api/team-invites/'+invite+'/accept').status_code==200
        assert client.post('/api/team-invites/'+invite+'/accept').status_code==400
        assert client.post('/api/projects',json={**project,"track_id":"trk_01"}).status_code==400
        response=client.post('/api/projects',json=project)
        assert response.status_code==201
        assert client.put('/api/projects/'+response.json()['id'],json={"title":"Edited"}).status_code==200
        assert client.put('/api/projects/'+response.json()['id'],json={"track_id":"trk_01"}).status_code==400

def test_seed_failure_stops_startup(monkeypatch):
    def fail(db):
        raise RuntimeError('Seed failed')
    monkeypatch.setattr('app.main.seed_database',fail)
    import pytest
    with pytest.raises(RuntimeError,match='Seed failed'):
        with TestClient(app):
            pass


ADMIN = {"Authorization": "Bearer token_admin"}
JUDGE_A = {"Authorization": "Bearer token_judge_a"}


def test_distinct_admin_role_and_rbac():
    with TestClient(app) as client:
        # 1. Admin can access admin-only endpoints
        res = client.get("/api/admin/users", headers=ADMIN)
        assert res.status_code == 200
        users = res.json()
        assert any(u["role"] == "admin" for u in users)

        # 2. Organizer, Judge, Participant CANNOT access admin endpoints
        assert client.get("/api/admin/users", headers=ORG).status_code == 403
        assert client.get("/api/admin/users", headers=JUDGE_A).status_code == 403
        assert client.get("/api/admin/users", headers=PARTICIPANT).status_code == 403
        assert client.get("/api/admin/users").status_code == 401

        # 3. Admin can update a user's role
        target_user = next(u for u in users if u["role"] == "participant")
        original_role = target_user["role"]
        res = client.put(f"/api/admin/users/{target_user['id']}/role", headers=ADMIN, json={"role": "judge"})
        assert res.status_code == 200
        assert res.json()["role"] == "judge"

        # Organizer cannot change roles
        assert client.put(f"/api/admin/users/{target_user['id']}/role", headers=ORG, json={"role": "participant"}).status_code == 403

        # Reset role back
        res = client.put(f"/api/admin/users/{target_user['id']}/role", headers=ADMIN, json={"role": "participant"})
        assert res.status_code == 200
        assert res.json()["role"] == "participant"

        # 4. Admin can also access organizer endpoints
        assert client.get("/api/export.csv", headers=ADMIN).status_code == 200
        assert client.get("/api/organizer/leaderboard", headers=ADMIN).status_code == 200


def test_event_prize_crud_and_permissions():
    with TestClient(app) as client:
        # Default seeded prizes exist for evt_01
        res = client.get("/api/events/evt_01/prizes")
        assert res.status_code == 200
        prizes = res.json()
        assert len(prizes) >= 3
        assert any(p["placement"] == "1st" for p in prizes)

        # Non-organizers cannot create prizes
        prize_payload = {
            "title": "Community Choice",
            "placement": "Special",
            "amount": "$500",
            "description": "Voted by attendees",
        }
        assert client.post("/api/events/evt_01/prizes", json=prize_payload, headers=PARTICIPANT).status_code == 403
        assert client.post("/api/events/evt_01/prizes", json=prize_payload, headers=JUDGE_A).status_code == 403

        # Organizer can create a prize
        res = client.post("/api/events/evt_01/prizes", json=prize_payload, headers=ORG)
        assert res.status_code == 201
        created_prize = res.json()
        assert created_prize["title"] == "Community Choice"

        # Organizer can update prize
        res = client.put(f"/api/prizes/{created_prize['id']}", json={"amount": "$750"}, headers=ORG)
        assert res.status_code == 200
        assert res.json()["amount"] == "$750"

        # Participant cannot update or delete prize
        assert client.put(f"/api/prizes/{created_prize['id']}", json={"amount": "$1000"}, headers=PARTICIPANT).status_code == 403
        assert client.delete(f"/api/prizes/{created_prize['id']}", headers=PARTICIPANT).status_code == 403

        # Organizer can delete prize
        res = client.delete(f"/api/prizes/{created_prize['id']}", headers=ORG)
        assert res.status_code == 200
        assert res.json()["message"] == "Prize deleted successfully"


def test_project_draft_workflow_and_deadline_enforcement():
    with TestClient(app) as client:
        # 1. Create open event with future deadline
        future_time = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat()
        ev = client.post("/api/events", json={"name": "Draft Hack", "submissions_close": future_time, "tracks": ["General"]}, headers=ORG).json()
        team = client.post("/api/teams", json={"name": "Draft Team", "event_id": ev["id"]}, headers=PARTICIPANT).json()

        # 2. Participant saves a draft project before deadline
        draft_payload = {
            "event_id": ev["id"],
            "team_id": team["id"],
            "title": "My Draft Project",
            "summary": "Work in progress",
            "repo_url": "https://github.com/draft/repo",
            "status": "draft",
        }
        res = client.post("/api/projects", json=draft_payload, headers=PARTICIPANT)
        assert res.status_code == 201
        proj = res.json()
        assert proj["status"] == "draft"
        proj_id = proj["id"]

        # 3. Draft project must NOT appear in public gallery or public API
        gallery_res = client.get("/projects")
        assert "my draft project" not in gallery_res.text.lower()
        api_res = client.get("/api/projects").json()
        assert not any(p["id"] == proj_id for p in api_res)

        # 4. Anonymous or other participants cannot view draft detail (404)
        assert client.get(f"/projects/{proj_id}").status_code == 404

        # 5. Team member can edit draft before deadline
        edit_res = client.put(f"/api/projects/{proj_id}", json={"summary": "Updated WIP summary"}, headers=PARTICIPANT)
        assert edit_res.status_code == 200

        # 6. Participant explicitly finalizes / submits draft before deadline
        submit_res = client.post(f"/api/projects/{proj_id}/submit", headers=PARTICIPANT)
        assert submit_res.status_code == 200
        assert submit_res.json()["status"] == "submitted"

        # 7. Now project is visible in public gallery and API
        gallery_res = client.get("/projects")
        assert "my draft project" in gallery_res.text.lower()
        api_res = client.get("/api/projects").json()
        assert any(p["id"] == proj_id for p in api_res)

        # 8. Attempting mutations after deadline is blocked
        # Set event deadline to past
        client.put(f"/api/events/{ev['id']}", json={"submissions_close": "2020-01-01T00:00:00Z"}, headers=ORG)
        # Edit after deadline rejected with 400
        assert client.put(f"/api/projects/{proj_id}", json={"title": "Too late edit"}, headers=PARTICIPANT).status_code == 400
        # New draft after deadline rejected with 400
        assert client.post("/api/projects", json=draft_payload, headers=PARTICIPANT).status_code == 400
        # Finalize submit after deadline rejected with 400
        assert client.post(f"/api/projects/{proj_id}/submit", headers=PARTICIPANT).status_code == 400


def test_results_publication_workflow_and_privacy():
    with TestClient(app) as client:
        # By default, results are unpublished for evt_01
        # Anonymous, participant, and judge are forbidden from accessing results API
        assert client.get("/api/results").status_code == 403
        assert client.get("/api/results", headers=PARTICIPANT).status_code == 403
        assert client.get("/api/results", headers=JUDGE_A).status_code == 403

        # Organizer and Admin CAN access preview of unpublished results
        res_org = client.get("/api/results", headers=ORG)
        assert res_org.status_code == 200
        assert res_org.json()["published"] is False
        assert res_org.json()["preview"] is True

        res_admin = client.get("/api/results", headers=ADMIN)
        assert res_admin.status_code == 200
        assert res_admin.json()["preview"] is True

        # Non-organizers cannot publish
        assert client.post("/api/events/evt_01/publish", headers=PARTICIPANT).status_code == 403
        assert client.post("/api/events/evt_01/publish", headers=JUDGE_A).status_code == 403

        # Organizer publishes results
        pub_res = client.post("/api/events/evt_01/publish", headers=ORG)
        assert pub_res.status_code == 200
        assert pub_res.json()["results_published"] is True

        # Now public / anonymous can view results
        public_res = client.get("/api/results")
        assert public_res.status_code == 200
        data = public_res.json()
        assert data["published"] is True
        assert len(data["leaderboard"]) > 0

        # CRITICAL PRIVACY CHECK: individual judge scores and judge names are NEVER exposed
        for item in data["leaderboard"]:
            assert "judge_id" not in item
            assert "judge_name" not in item
            assert "scores" not in item
            assert "rank" in item
            assert "normalized_score" in item

        # Public HTML results page also returns 200
        assert client.get("/results").status_code == 200

        # Organizer can unpublish
        unpub_res = client.post("/api/events/evt_01/unpublish", headers=ORG)
        assert unpub_res.status_code == 200
        assert unpub_res.json()["results_published"] is False

        # Public is blocked again
        assert client.get("/api/results").status_code == 403
