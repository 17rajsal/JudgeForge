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
