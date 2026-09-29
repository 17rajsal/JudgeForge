import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import engine, Base, SessionLocal
from app.seed import seed_database, print_demo_credentials
from app.routers import (
    health, auth, projects, judging, organizer, workflows, voting, comments,
    webhooks, verifiable_records, certificates, embed, bulk_data, api_v1,
)
from app.auth import get_current_user_optional
from app.passwords import demo_enabled

STATIC_DIR = Path("app/static")
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR = Path("app/templates")
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


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

            res = conn.exec_driver_sql("PRAGMA table_info(scores)").fetchall()
            cols = {row[1] for row in res}
            if cols and "criteria_json" not in cols:
                conn.exec_driver_sql("ALTER TABLE scores ADD COLUMN criteria_json TEXT NULL")

            conn.commit()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize schema and apply any pending column additions to existing tables
    Base.metadata.create_all(bind=engine)
    ensure_schema_migrations(engine)

    # 2. Seed database idempotently
    with SessionLocal() as db:
        seed_database(db)

    # 3. Print demo credentials banner
    print_demo_credentials()

    yield


app = FastAPI(
    title="JudgeForge",
    description="Self-hosted hackathon submissions and fair judging, with zero cloud dependencies.",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

from app.templating import templates

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(judging.router)
app.include_router(organizer.router)
app.include_router(workflows.router)
app.include_router(voting.router)
app.include_router(comments.router)
app.include_router(comments.comments_admin_router)
app.include_router(webhooks.router)
app.include_router(verifiable_records.router)
app.include_router(certificates.router)
app.include_router(embed.router)
app.include_router(bulk_data.router)
app.include_router(api_v1.router)


@app.get("/")
def root():
    return RedirectResponse(url="/projects", status_code=302)


@app.get("/login")
def login_page(request: Request):
    db = SessionLocal()
    try:
        user = get_current_user_optional(request, db)
        return templates.TemplateResponse(request=request, name="login.html", context={"user": user, "demo_mode": demo_enabled()})
    finally:
        db.close()
