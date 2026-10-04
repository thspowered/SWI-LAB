"""Demo styroch zakladnych operacii proti BEZIACEJ aplikacii.

Pre kazdu operaciu jeden uspesny a jeden negativny / hranicny priklad,
presne podla prikladov overenia v docs/specification.md.

Spustenie:
    docker compose up -d --wait db
    PYTHONPATH=src .venv/bin/uvicorn swilab.main:app &
    PYTHONPATH=src .venv/bin/python scripts/demo.py
"""

import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from swilab.domain.states import InstrumentCategory, ReservationState, UserRole
from swilab.models import Base, Certification, Instrument, Reservation, User

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://swilab:swilab@localhost:5433/swilab"
)
HOUR = timedelta(hours=1)

engine = create_engine(DATABASE_URL, future=True)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def seed():
    """Pristroje, pouzivatelia a certifikaty sa zakladaju priamo v databaze -
    ich sprava nie je v rozsahu baseline v0.1 (TBD-02)."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE reservations, certifications, instruments, users "
                "RESTART IDENTITY CASCADE"
            )
        )

    starts_at = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )

    with SessionFactory() as session:
        microscope = Instrument(
            name="Zeiss Axio Observer",
            category=InstrumentCategory.MICROSCOPE,
            location="Lab B2.14",
        )
        broken = Instrument(
            name="Centrifuga Hettich (v servise)",
            category=InstrumentCategory.CENTRIFUGE,
            location="Lab B2.09",
            is_active=False,
        )
        student = User(
            full_name="Jana Certifikovana",
            email="jana@swi-lab.local",
            role=UserRole.STUDENT,
        )
        novice = User(
            full_name="Peter Bez Skolenia",
            email="peter@swi-lab.local",
            role=UserRole.STUDENT,
        )
        session.add_all([microscope, broken, student, novice])
        session.flush()
        session.add(
            Certification(
                user_id=student.id,
                category=InstrumentCategory.MICROSCOPE,
                valid_until=starts_at + 365 * 24 * HOUR,
            )
        )
        # Potvrdena rezervacia o 30 minut - na hranicnom priklade pre BR-03.
        soon = datetime.now(UTC) + timedelta(minutes=30)
        locked = Reservation(
            instrument_id=microscope.id,
            user_id=student.id,
            starts_at=soon,
            ends_at=soon + HOUR,
            state=ReservationState.CONFIRMED,
        )
        session.add(locked)
        session.commit()
        return {
            "microscope": microscope.id,
            "broken": broken.id,
            "student": student.id,
            "novice": novice.id,
            "locked": locked.id,
            "starts_at": starts_at,
        }


def show(label, response):
    print(f"\n### {label}")
    print(f"--> {response.request.method} {response.request.url}")
    print(f"<-- HTTP {response.status_code}  {response.text}")
    return response


def main() -> int:
    data = seed()
    starts_at = data["starts_at"]
    ends_at = starts_at + HOUR
    client = httpx.Client(base_url=BASE_URL, timeout=10)

    print("=" * 74)
    print("DEMO baseline v0.1 - styri operacie proti beziacej aplikacii")
    print("=" * 74)

    # ---- OP-01 Create ----------------------------------------------------
    created = show(
        "OP-01 uspech: vytvorenie navrhu rezervacie",
        client.post(
            "/reservations",
            json={
                "instrument_id": str(data["microscope"]),
                "user_id": str(data["student"]),
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
            },
        ),
    )
    reservation_id = created.json()["id"]

    show(
        "OP-01 hranica: starts_at == ends_at (BR-01)",
        client.post(
            "/reservations",
            json={
                "instrument_id": str(data["microscope"]),
                "user_id": str(data["student"]),
                "starts_at": starts_at.isoformat(),
                "ends_at": starts_at.isoformat(),
            },
        ),
    )

    # ---- OP-02 Availability ---------------------------------------------
    show(
        "OP-02 uspech: volny interval",
        client.get(
            f"/instruments/{data['microscope']}/availability",
            params={"starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat()},
        ),
    )

    locked_start = datetime.now(UTC) + timedelta(minutes=30)
    show(
        "OP-02 negativny: interval prekryva potvrdenu rezervaciu (BR-02)",
        client.get(
            f"/instruments/{data['microscope']}/availability",
            params={
                "starts_at": (locked_start + timedelta(minutes=15)).isoformat(),
                "ends_at": (locked_start + timedelta(minutes=45)).isoformat(),
            },
        ),
    )

    # ---- OP-03 Confirm ---------------------------------------------------
    show(
        "OP-03 uspech: potvrdenie s platnym certifikatom",
        client.post(
            f"/reservations/{reservation_id}/confirm",
            json={"requested_by": str(data["student"])},
        ),
    )

    novice_reservation = client.post(
        "/reservations",
        json={
            "instrument_id": str(data["microscope"]),
            "user_id": str(data["novice"]),
            "starts_at": (starts_at + 3 * HOUR).isoformat(),
            "ends_at": (ends_at + 3 * HOUR).isoformat(),
        },
    ).json()["id"]
    show(
        "OP-03 negativny: pouzivatel bez certifikatu (BR-04)",
        client.post(
            f"/reservations/{novice_reservation}/confirm",
            json={"requested_by": str(data["novice"])},
        ),
    )

    # ---- OP-04 Cancel ----------------------------------------------------
    show(
        "OP-04 uspech: zrusenie potvrdenej rezervacie viac ako 60 min pred zaciatkom",
        client.post(
            f"/reservations/{reservation_id}/cancel",
            json={"requested_by": str(data["student"])},
        ),
    )
    show(
        "OP-04 idempotencia: to iste zrusenie druhykrat (REQ-09)",
        client.post(
            f"/reservations/{reservation_id}/cancel",
            json={"requested_by": str(data["student"])},
        ),
    )
    show(
        "OP-04 negativny: potvrdena rezervacia 30 min pred zaciatkom (BR-03)",
        client.post(
            f"/reservations/{data['locked']}/cancel",
            json={"requested_by": str(data["student"])},
        ),
    )

    # ---- dolezity dosledok ----------------------------------------------
    show(
        "Kontrola: po zruseni je povodny interval opat dostupny",
        client.get(
            f"/instruments/{data['microscope']}/availability",
            params={"starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat()},
        ),
    )

    print("\nDemo dokoncene.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
