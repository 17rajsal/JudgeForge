import os
import tempfile
from pathlib import Path
import pytest

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["DEMO_MODE"] = "true"

@pytest.fixture(autouse=True)
def isolated_database():
    from app.database import Base, engine, SessionLocal
    from app.seed import seed_database
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_database(db)
    yield
    engine.dispose()
