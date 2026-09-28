import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import engine, Base, SessionLocal
from app.seed import seed_database, print_demo_credentials
from app.routers import health, auth, projects, judging, organizer
from app.auth import get_current_user_optional

STATIC_DIR = Path("app/static")
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR = Path("app/templates")
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize schema
    Base.metadata.create_all(bind=engine)

    # 2. Seed database idempotently
    try:
        db = SessionLocal()
        seed_database(db)
        db.close()
    except Exception as e:
        print(f"Warning during seed: {e}")

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

templates = Jinja2Templates(directory="app/templates")

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(judging.router)
app.include_router(organizer.router)


@app.get("/")
def root():
    return RedirectResponse(url="/projects", status_code=302)


@app.get("/login")
def login_page(request: Request):
    db = SessionLocal()
    try:
        user = get_current_user_optional(request, db)
        return templates.TemplateResponse(request=request, name="login.html", context={"user": user})
    finally:
        db.close()
