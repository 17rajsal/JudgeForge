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
    results_published = Column(Integer, nullable=False, default=0)
    results_published_at = Column(DateTime, nullable=True)
    voting_mode = Column(String, nullable=False, default="authenticated")  # "authenticated", "email_gated", "open"
    voting_opens = Column(DateTime, nullable=True)
    voting_closes = Column(DateTime, nullable=True)
    voting_results_public = Column(Integer, nullable=False, default=0)

    tracks = relationship("Track", back_populates="event", cascade="all, delete-orphan")
    teams = relationship("Team", back_populates="event", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="event", cascade="all, delete-orphan")
    rubric_criteria = relationship("RubricCriterion", back_populates="event", cascade="all, delete-orphan")
    prizes = relationship("Prize", back_populates="event", cascade="all, delete-orphan")
    votes = relationship("CommunityVote", back_populates="event", cascade="all, delete-orphan")
    vouchers = relationship("VotingVoucher", back_populates="event", cascade="all, delete-orphan")

    @property
    def is_closed(self) -> bool:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        close_time = self.submissions_close
        if close_time and close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=datetime.timezone.utc)
        return now_utc > close_time if close_time else False

    @property
    def status_label(self) -> str:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        if self.results_published:
            return "Results published"

        v_opens = self.voting_opens
        if v_opens and v_opens.tzinfo is None:
            v_opens = v_opens.replace(tzinfo=datetime.timezone.utc)
        v_closes = self.voting_closes
        if v_closes and v_closes.tzinfo is None:
            v_closes = v_closes.replace(tzinfo=datetime.timezone.utc)

        if v_opens and v_closes and v_opens <= now_utc <= v_closes:
            return "Voting open"
        elif v_opens and not v_closes and now_utc >= v_opens:
            return "Voting open"

        if self.is_closed:
            return "Submissions closed"
        return "Submissions open"

    @property
    def status_class(self) -> str:
        if self.results_published or self.status_label == "Voting open":
            return "stat-open"
        if self.is_closed:
            return "stat-closed"
        return "stat-open"

    @property
    def demo_status_badge(self) -> str:
        if self.id == "evt_01":
            return "Official Fixture · Closed"
        if self.results_published:
            return "Completed · Results Published"
        if self.is_closed:
            return "Judging In Progress"
        return "Submissions Open"


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
    tagline = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    repo_url = Column(String, nullable=True)
    demo_url = Column(String, nullable=True)
    video_url = Column(String, nullable=True)
    pitch_deck_url = Column(String, nullable=True)
    tech_stack = Column(String, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    status = Column(String, nullable=False, default="submitted")  # "draft" or "submitted"
    submitted_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    event = relationship("Event", back_populates="projects")
    team = relationship("Team", back_populates="projects")
    track = relationship("Track", back_populates="projects")
    scores = relationship("Score", back_populates="project", cascade="all, delete-orphan")
    votes = relationship("CommunityVote", back_populates="project", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="project", cascade="all, delete-orphan")

    @property
    def short_description(self) -> str:
        return self.tagline or self.summary or ""

    @property
    def full_description(self) -> str:
        return self.description or self.summary or ""

    @property
    def tech_tags(self) -> list:
        if not self.tech_stack:
            return []
        return [t.strip() for t in self.tech_stack.split(",") if t.strip()]


class Prize(Base):
    __tablename__ = "prizes"

    id = Column(String, primary_key=True, index=True)
    event_id = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    amount = Column(String, nullable=False)  # value / amount, e.g. "$5,000"
    placement = Column(String, nullable=False)  # placement / category, e.g. "1st Place", "Grand Prize"
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    event = relationship("Event", back_populates="prizes")


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


class PasswordCredential(Base):
    __tablename__ = "password_credentials"
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    password_hash = Column(String, nullable=False)
    demo = Column(Integer, nullable=False, default=0)


class TeamInvite(Base):
    __tablename__ = "team_invites"
    token = Column(String, primary_key=True)
    team_id = Column(String, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_by = Column(String, nullable=True)


class CommunityVote(Base):
    __tablename__ = "community_votes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    voter_identifier = Column(String, nullable=False, index=True)
    voter_type = Column(String, nullable=False)  # "authenticated", "email_gated", "open"
    voter_email = Column(String, nullable=True, index=True)
    voter_ip = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    event = relationship("Event", back_populates="votes")
    project = relationship("Project", back_populates="votes")


class VotingVoucher(Base):
    __tablename__ = "voting_vouchers"

    token = Column(String, primary_key=True, index=True)
    event_id = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String, nullable=False, index=True)
    is_used = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    event = relationship("Event", back_populates="vouchers")


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_name = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    is_flagged = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    project = relationship("Project", back_populates="comments")
    user = relationship("User")


class RateLimitRecord(Base):
    __tablename__ = "rate_limit_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, nullable=False, index=True)  # e.g. "vote:127.0.0.1" or "comment:127.0.0.1"
    timestamp = Column(Float, nullable=False, index=True)


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id = Column(String, primary_key=True, index=True)
    event_id = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    target_url = Column(String, nullable=False)
    secret = Column(String, nullable=False)
    events_subscribed = Column(String, nullable=False, default="*")  # comma-separated or "*"
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    deliveries = relationship("WebhookDeliveryLog", back_populates="subscription", cascade="all, delete-orphan")


class WebhookDeliveryLog(Base):
    __tablename__ = "webhook_delivery_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscription_id = Column(String, ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False)
    status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    success = Column(Integer, nullable=False, default=0)
    attempted_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    subscription = relationship("WebhookSubscription", back_populates="deliveries")


class VerifiableJudgeRecord(Base):
    __tablename__ = "verifiable_judge_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    score_id = Column(Integer, ForeignKey("scores.id", ondelete="CASCADE"), nullable=False, index=True)
    judge_id = Column(String, nullable=False, index=True)
    project_id = Column(String, nullable=False, index=True)
    event_id = Column(String, nullable=False, index=True)
    record_hash = Column(String, nullable=False, unique=True, index=True)
    prev_hash = Column(String, nullable=False)
    signature = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
