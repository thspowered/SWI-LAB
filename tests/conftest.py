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


@pytest.fixture
def client(engine, session):
    """HTTP klient nad aplikaciou, ktory pise do testovacej session."""
    from fastapi.testclient import TestClient

    from swilab.db import get_session
    from swilab.main import app

    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- tovarne na testovacie data -------------------------------------------
# Sprava pristrojov a certifikatov nie je v rozsahu v0.1 (TBD-02), takze
# data zakladame priamo cez model, nie cez API.


@pytest.fixture
def make_instrument(session):
    from swilab.domain.states import InstrumentCategory
    from swilab.models import Instrument

    def _make(category=InstrumentCategory.MICROSCOPE, is_active=True, name="Zeiss Axio"):
        instrument = Instrument(
            name=name, category=category, location="Lab B2.14", is_active=is_active
        )
        session.add(instrument)
        session.commit()
        return instrument

    return _make


@pytest.fixture
def make_user(session):
    from swilab.domain.states import UserRole
    from swilab.models import User

    counter = iter(range(1, 1000))

    def _make(role=UserRole.STUDENT, name="Testovaci Student"):
        number = next(counter)
        user = User(
            full_name=f"{name} {number}",
            email=f"user{number}@swi-lab.local",
            role=role,
        )
        session.add(user)
        session.commit()
        return user

    return _make


@pytest.fixture
def make_certification(session):
    from swilab.models import Certification

    def _make(user, category, valid_until):
        certification = Certification(
            user_id=user.id, category=category, valid_until=valid_until
        )
        session.add(certification)
        session.commit()
        return certification

    return _make


@pytest.fixture
def make_reservation(session):
    from swilab.domain.states import ReservationState
    from swilab.models import Reservation

    def _make(instrument, user, starts_at, ends_at, state=ReservationState.DRAFT):
        reservation = Reservation(
            instrument_id=instrument.id,
            user_id=user.id,
            starts_at=starts_at,
            ends_at=ends_at,
            state=state,
        )
        session.add(reservation)
        session.commit()
        return reservation

    return _make
