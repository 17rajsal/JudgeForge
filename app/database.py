import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import declarative_base, sessionmaker

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR}/judgeforge.db")

engine = create_engine(
    DATABASE_URL,
    **({"poolclass": StaticPool} if DATABASE_URL == "sqlite://" else {}),
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def ensure_schema_migrations(eng):
    with eng.connect() as conn:
        try:
            res = conn.exec_driver_sql("PRAGMA table_info(events)").fetchall()
            cols = {row[1] for row in res}
            if cols and "results_published" not in cols:
                conn.exec_driver_sql("ALTER TABLE events ADD COLUMN results_published INTEGER NOT NULL DEFAULT 0")
            if cols and "results_published_at" not in cols:
                conn.exec_driver_sql("ALTER TABLE events ADD COLUMN results_published_at TIMESTAMP NULL")
            if cols and "voting_mode" not in cols:
                conn.exec_driver_sql("ALTER TABLE events ADD COLUMN voting_mode VARCHAR NOT NULL DEFAULT 'authenticated'")
            if cols and "voting_opens" not in cols:
                conn.exec_driver_sql("ALTER TABLE events ADD COLUMN voting_opens TIMESTAMP NULL")
            if cols and "voting_closes" not in cols:
                conn.exec_driver_sql("ALTER TABLE events ADD COLUMN voting_closes TIMESTAMP NULL")
            if cols and "voting_results_public" not in cols:
                conn.exec_driver_sql("ALTER TABLE events ADD COLUMN voting_results_public INTEGER NOT NULL DEFAULT 0")

            res = conn.exec_driver_sql("PRAGMA table_info(projects)").fetchall()
            cols = {row[1] for row in res}
            if cols and "status" not in cols:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN status VARCHAR NOT NULL DEFAULT 'submitted'")
            if cols and "tagline" not in cols:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN tagline VARCHAR NULL")
            if cols and "description" not in cols:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN description TEXT NULL")
            if cols and "demo_url" not in cols:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN demo_url VARCHAR NULL")
            if cols and "video_url" not in cols:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN video_url VARCHAR NULL")
            if cols and "pitch_deck_url" not in cols:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN pitch_deck_url VARCHAR NULL")
            if cols and "tech_stack" not in cols:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN tech_stack VARCHAR NULL")
            if cols and "thumbnail_url" not in cols:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN thumbnail_url VARCHAR NULL")

            conn.commit()
        except Exception:
            pass


try:
    ensure_schema_migrations(engine)
except Exception:
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
