import datetime
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    Text,
    Table,
)
from sqlalchemy.orm import relationship
from app.database import Base

judge_tracks = Table(
    "judge_tracks",
    Base.metadata,
    Column("judge_id", String, ForeignKey("judges.id", ondelete="CASCADE"), primary_key=True),
    Column("track_id", String, ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="participant")  # organizer, judge, participant, visitor
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    sessions = relationship("SessionToken", back_populates="user", cascade="all, delete-orphan")
    judge_profile = relationship("Judge", back_populates="user", uselist=False)
    team_memberships = relationship("TeamMember", back_populates="user")


class SessionToken(Base):
    __tablename__ = "session_tokens"

    token = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", back_populates="sessions")


class Event(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    submissions_close = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    tracks = relationship("Track", back_populates="event", cascade="all, delete-orphan")
    teams = relationship("Team", back_populates="event", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="event", cascade="all, delete-orphan")
    rubric_criteria = relationship("RubricCriterion", back_populates="event", cascade="all, delete-orphan")


class Track(Base):
    __tablename__ = "tracks"

    id = Column(String, primary_key=True, index=True)
    event_id = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)

    event = relationship("Event", back_populates="tracks")
    projects = relationship("Project", back_populates="track")
    judges = relationship("Judge", secondary=judge_tracks, back_populates="tracks")


class Judge(Base):
    __tablename__ = "judges"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)

    user = relationship("User", back_populates="judge_profile")
    tracks = relationship("Track", secondary=judge_tracks, back_populates="judges")
    scores = relationship("Score", back_populates="judge", cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"

    id = Column(String, primary_key=True, index=True)
    event_id = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    event = relationship("Event", back_populates="teams")
    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="team", cascade="all, delete-orphan")


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(String, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String, nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    team = relationship("Team", back_populates="members")
    user = relationship("User", back_populates="team_memberships")


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, index=True)
    event_id = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id = Column(String, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id = Column(String, ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    repo_url = Column(String, nullable=True)
    submitted_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    event = relationship("Event", back_populates="projects")
    team = relationship("Team", back_populates="projects")
    track = relationship("Track", back_populates="projects")
    scores = relationship("Score", back_populates="project", cascade="all, delete-orphan")


class RubricCriterion(Base):
    __tablename__ = "rubric_criteria"

    id = Column(String, primary_key=True, index=True)
    event_id = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)  # functionality, quality, innovation
    label = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    weight = Column(Float, nullable=False, default=1.0)
    min_score = Column(Integer, nullable=False, default=1)
    max_score = Column(Integer, nullable=False, default=5)

    event = relationship("Event", back_populates="rubric_criteria")


class Score(Base):
    __tablename__ = "scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    judge_id = Column(String, ForeignKey("judges.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    functionality = Column(Integer, nullable=True)
    quality = Column(Integer, nullable=True)
    innovation = Column(Integer, nullable=True)
    criteria_json = Column(Text, nullable=True)  # JSON dictionary for dynamic/extensible criteria
    comment = Column(Text, nullable=True)
    submitted_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    judge = relationship("Judge", back_populates="scores")
    project = relationship("Project", back_populates="scores")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False)
    target_type = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
