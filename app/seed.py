import datetime
import json
import os
from pathlib import Path
from sqlalchemy.orm import Session
from app.database import Base, SessionLocal, engine
from app.models import (
    Event,
    Track,
    Judge,
    judge_tracks,
    Team,
    TeamMember,
    Project,
    RubricCriterion,
    Score,
    User,
    SessionToken,
)

FIXTURE_CANDIDATES = [
    os.getenv("FIXTURES_PATH"),
    "fixtures.json",
    "./fixtures.json",
    "../fixtures.json",
    "/app/fixtures.json",
]


def find_fixtures_path() -> Path:
    for c in FIXTURE_CANDIDATES:
        if c and Path(c).is_file():
            return Path(c)
    raise FileNotFoundError("fixtures.json not found in candidate paths")


def parse_datetime(dt_str: str) -> datetime.datetime:
    if not dt_str:
        return datetime.datetime.now(datetime.timezone.utc)
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(dt_str)


def seed_database(db: Session | None = None) -> dict:
    should_close = False
    if db is None:
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        should_close = True

    try:
        fix_path = find_fixtures_path()
        with open(fix_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 1. Event
        evt_data = data.get("event", {})
        evt_id = evt_data.get("id", "evt_01")
        existing_event = db.query(Event).filter(Event.id == evt_id).first()
        if not existing_event:
            event = Event(
                id=evt_id,
                name=evt_data.get("name", "Sample Hack 2026"),
                submissions_close=parse_datetime(evt_data.get("submissions_close", "2026-03-01T18:00:00Z")),
            )
            db.add(event)
            db.flush()
        else:
            event = existing_event

        # 2. Rubric Criteria
        default_criteria = [
            ("crit_func", "functionality", "Functionality", 1.0),
            ("crit_qual", "quality", "Code & Design Quality", 1.0),
            ("crit_innov", "innovation", "Innovation & Creativity", 1.0),
        ]
        for crit_id, name, label, weight in default_criteria:
            if not db.query(RubricCriterion).filter(RubricCriterion.id == crit_id).first():
                db.add(
                    RubricCriterion(
                        id=crit_id,
                        event_id=event.id,
                        name=name,
                        label=label,
                        weight=weight,
                        min_score=1,
                        max_score=5,
                    )
                )
        db.flush()

        # 3. Tracks
        for trk_data in data.get("tracks", []):
            if not db.query(Track).filter(Track.id == trk_data["id"]).first():
                db.add(
                    Track(
                        id=trk_data["id"],
                        event_id=event.id,
                        name=trk_data["name"],
                    )
                )
        db.flush()

        # 4. Judges
        for jdg_data in data.get("judges", []):
            judge_id = jdg_data["id"]
            existing_judge = db.query(Judge).filter(Judge.id == judge_id).first()
            user_id = f"usr_{judge_id}"

            # Create or get user
            user = db.query(User).filter(User.email == jdg_data["email"]).first()
            if not user:
                user = User(
                    id=user_id,
                    email=jdg_data["email"],
                    name=jdg_data["name"],
                    role="judge",
                )
                db.add(user)
                db.flush()

            if not existing_judge:
                judge = Judge(
                    id=judge_id,
                    user_id=user.id,
                    name=jdg_data["name"],
                    email=jdg_data["email"],
                )
                db.add(judge)
                db.flush()

                # Add tracks
                for trk_id in jdg_data.get("tracks", []):
                    trk = db.query(Track).filter(Track.id == trk_id).first()
                    if trk:
                        judge.tracks.append(trk)
                db.flush()

        # 5. Teams & Members
        for tm_data in data.get("teams", []):
            team_id = tm_data["id"]
            existing_team = db.query(Team).filter(Team.id == team_id).first()
            if not existing_team:
                team = Team(
                    id=team_id,
                    event_id=event.id,
                    name=tm_data["name"],
                )
                db.add(team)
                db.flush()
            else:
                team = existing_team

            for member_email in tm_data.get("members", []):
                existing_member = (
                    db.query(TeamMember)
                    .filter(TeamMember.team_id == team.id, TeamMember.email == member_email)
                    .first()
                )
                if not existing_member:
                    user = db.query(User).filter(User.email == member_email).first()
                    if not user:
                        user_id = f"usr_{member_email.split('@')[0]}"
                        user = User(
                            id=user_id,
                            email=member_email,
                            name=member_email.split("@")[0].capitalize(),
                            role="participant",
                        )
                        db.add(user)
                        db.flush()

                    db.add(
                        TeamMember(
                            team_id=team.id,
                            email=member_email,
                            user_id=user.id,
                        )
                    )
        db.flush()

        # 6. Projects
        for prj_data in data.get("projects", []):
            prj_id = prj_data["id"]
            if not db.query(Project).filter(Project.id == prj_id).first():
                db.add(
                    Project(
                        id=prj_id,
                        event_id=event.id,
                        team_id=prj_data["team"],
                        track_id=prj_data["track"],
                        title=prj_data["title"],
                        summary=prj_data.get("summary", ""),
                        repo_url=prj_data.get("repo_url", ""),
                        submitted_at=parse_datetime(prj_data.get("submitted_at")),
                    )
                )
        db.flush()

        # 7. Scores
        for sc_data in data.get("scores", []):
            criteria = sc_data.get("criteria", {})
            existing_score = (
                db.query(Score)
                .filter(
                    Score.judge_id == sc_data["judge"],
                    Score.project_id == sc_data["project"],
                )
                .first()
            )
            if not existing_score:
                db.add(
                    Score(
                        judge_id=sc_data["judge"],
                        project_id=sc_data["project"],
                        functionality=criteria.get("functionality"),
                        quality=criteria.get("quality"),
                        innovation=criteria.get("innovation"),
                        criteria_json=json.dumps(criteria),
                        comment=sc_data.get("comment", ""),
                    )
                )
        db.flush()

        # 8. Organizer and Demo User Accounts & Tokens
        # Organizer
        org_user = db.query(User).filter(User.email == "organizer@judgeforge.local").first()
        if not org_user:
            org_user = User(
                id="usr_organizer",
                email="organizer@judgeforge.local",
                name="Head Organizer",
                role="organizer",
            )
            db.add(org_user)
            db.flush()

        # Admin
        admin_user = db.query(User).filter(User.email == "admin@judgeforge.local").first()
        if not admin_user:
            admin_user = User(
                id="usr_admin",
                email="admin@judgeforge.local",
                name="System Administrator",
                role="admin",
            )
            db.add(admin_user)
            db.flush()

        # Deterministic session tokens for testing
        demo_tokens = [
            ("token_admin", admin_user.id),
            ("adm_9a11", admin_user.id),
            ("token_organizer", org_user.id),
            ("org_7f2a", org_user.id),
        ]

        # Judge A (jdg_01)
        judge_a = db.query(Judge).filter(Judge.id == "jdg_01").first()
        if judge_a and judge_a.user_id:
            demo_tokens.extend([
                ("token_judge_a", judge_a.user_id),
                ("jdg_a_91bc", judge_a.user_id),
            ])

        # Judge B (jdg_02)
        judge_b = db.query(Judge).filter(Judge.id == "jdg_02").first()
        if judge_b and judge_b.user_id:
            demo_tokens.extend([
                ("token_judge_b", judge_b.user_id),
                ("jdg_b_44de", judge_b.user_id),
            ])

        # Participant (priya1@example.org)
        prt_user = db.query(User).filter(User.email == "priya1@example.org").first()
        if prt_user:
            demo_tokens.extend([
                ("token_participant", prt_user.id),
                ("prt_2e88", prt_user.id),
            ])

        for tok, uid in demo_tokens:
            if not db.query(SessionToken).filter(SessionToken.token == tok).first():
                db.add(SessionToken(token=tok, user_id=uid))

        # 9. Default Prizes
        from app.models import Prize
        if not db.query(Prize).filter(Prize.event_id == event.id).first():
            db.add(
                Prize(
                    id="prz_01",
                    event_id=event.id,
                    title="1st Place Overall",
                    description="Grand prize for the top scoring project",
                    amount="$5,000",
                    placement="1st",
                )
            )
            db.add(
                Prize(
                    id="prz_02",
                    event_id=event.id,
                    title="Runner Up",
                    description="Second place overall across all tracks",
                    amount="$2,500",
                    placement="2nd",
                )
            )
            db.add(
                Prize(
                    id="prz_03",
                    event_id=event.id,
                    title="Best Technical Architecture",
                    description="Excellence in system design, offline resilience and code quality",
                    amount="$1,000",
                    placement="Special",
                )
            )
            db.flush()

        from app.models import PasswordCredential
        from app.passwords import hash_password, demo_enabled
        if demo_enabled():
            for uid in {uid for _, uid in demo_tokens}:
                if not db.get(PasswordCredential, uid):
                    db.add(PasswordCredential(user_id=uid, password_hash=hash_password("JudgeForge-Demo-2026!"), demo=1))
        elif os.getenv("ORGANIZER_PASSWORD") and not db.get(PasswordCredential, org_user.id):
            password = os.environ["ORGANIZER_PASSWORD"]
            if len(password) < 12:
                raise ValueError("ORGANIZER_PASSWORD must have at least 12 characters")
            db.add(PasswordCredential(user_id=org_user.id, password_hash=hash_password(password)))
        db.commit()

        auth_info = {
            "organizer": "token_organizer",
            "judge_a": "token_judge_a",
            "judge_b": "token_judge_b",
            "participant": "token_participant",
            "cookie_organizer": "org_7f2a",
            "cookie_judge_a": "jdg_a_91bc",
            "cookie_judge_b": "jdg_b_44de",
            "cookie_participant": "prt_2e88",
        }
        return auth_info
    except Exception as e:
        db.rollback()
        raise e
    finally:
        if should_close:
            db.close()


def print_demo_credentials():
    from app.passwords import demo_enabled
    if not demo_enabled():
        print("Demo authentication disabled.")
        return
    print("Local evaluation only. Demo password: JudgeForge-Demo-2026!")
    print("""
=================================
JUDGEFORGE DEMO AUTH
=================================
Organizer:
  Authorization: Bearer token_organizer
  Cookie: session=org_7f2a

Participant:
  Authorization: Bearer token_participant
  Cookie: session=prt_2e88

Judge A (Tomas Varga - jdg_01):
  Authorization: Bearer token_judge_a
  Cookie: session=jdg_a_91bc

Judge B (Wei Lindqvist - jdg_02):
  Authorization: Bearer token_judge_b
  Cookie: session=jdg_b_44de
=================================
""")


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    seed_database()
    print_demo_credentials()
