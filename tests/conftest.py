import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from swilab.models import Base

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://swilab:swilab@localhost:5433/swilab"
)


@pytest.fixture(scope="session")
def engine():
    """Realny Postgres. Ziadne SQLite, ziadny in-memory fake -
    spike ma overit skutocnu databazu."""
    eng = create_engine(DATABASE_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Session:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE reservations, certifications, instruments, users "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def new_session(engine):
    """Tovaren na CISTU session - potrebujeme nacitat zaznam mimo
    identity map tej session, ktora ho zapisala."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory
