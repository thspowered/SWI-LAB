"""Demo piatich operacii proti BEZIACEJ aplikacii (baseline v0.2).

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
    # drop_all + create_all: v0.2 pridala hodnoty do enum typu a stlpec
    # requires_approval. Bez migracii je cista schema jediny sposob, ako
    # demo spolahlivo rozbehnut (migracie su driver pre C03).
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

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
        # v0.2: pristroj, ktory vyzaduje schvalenie veducim
        guarded = Instrument(
            name="Elektronovy mikroskop JEOL",
            category=InstrumentCategory.SPECTROMETER,
            location="Lab A1.02",
            requires_approval=True,
        )
        boss = User(
            full_name="Martina Veduca",
            email="veduca@swi-lab.local",
            role=UserRole.SUPERVISOR,
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
        session.add_all([microscope, broken, guarded, student, novice, boss])
        session.flush()
        session.add_all(
            [
                Certification(
                    user_id=student.id,
                    category=InstrumentCategory.MICROSCOPE,
                    valid_until=starts_at + 365 * 24 * HOUR,
                ),
                Certification(
                    user_id=student.id,
                    category=InstrumentCategory.SPECTROMETER,
                    valid_until=starts_at + 365 * 24 * HOUR,
                ),
            ]
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
        # v0.2: ziadost, ktorej termin uz zacal - na ukazku expiracie
        expired_request = Reservation(
            instrument_id=guarded.id,
            user_id=student.id,
            starts_at=datetime.now(UTC) - HOUR,
            ends_at=datetime.now(UTC) + HOUR,
            state=ReservationState.PENDING_APPROVAL,
        )
        session.add(expired_request)
        session.commit()
        return {
            "microscope": microscope.id,
            "broken": broken.id,
            "guarded": guarded.id,
            "student": student.id,
            "novice": novice.id,
            "boss": boss.id,
            "locked": locked.id,
            "expired_request": expired_request.id,
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

    # ---- v0.2: schvalovaci proces ---------------------------------------
    print("\n" + "=" * 74)
    print("ZMENA v0.2 - pristroj, ktory vyzaduje schvalenie veducim")
    print("=" * 74)

    guarded_start = starts_at + 6 * HOUR
    guarded_end = guarded_start + HOUR

    request_id = show(
        "OP-01: navrh na pristroj so schvalovanim",
        client.post(
            "/reservations",
            json={
                "instrument_id": str(data["guarded"]),
                "user_id": str(data["student"]),
                "starts_at": guarded_start.isoformat(),
                "ends_at": guarded_end.isoformat(),
            },
        ),
    ).json()["id"]
    show(
        "OP-03 uspech: ten isty pokyn, iny vysledny stav -> PENDING_APPROVAL (REQ-10)",
        client.post(
            f"/reservations/{request_id}/confirm",
            json={"requested_by": str(data["student"])},
        ),
    )
    show(
        "OP-02: cakajuca ziadost BLOKUJE pristroj (BR-02 v0.2, dovod PENDING_APPROVAL)",
        client.get(
            f"/instruments/{data['guarded']}/availability",
            params={
                "starts_at": guarded_start.isoformat(),
                "ends_at": guarded_end.isoformat(),
            },
        ),
    )
    show(
        "OP-05 negativny: o ziadosti sa pokusa rozhodnut student (BR-07)",
        client.post(
            f"/reservations/{request_id}/approve",
            json={"requested_by": str(data["student"])},
        ),
    )
    show(
        "OP-05 uspech: schvalenie veducim -> CONFIRMED (REQ-11)",
        client.post(
            f"/reservations/{request_id}/approve",
            json={"requested_by": str(data["boss"])},
        ),
    )
    show(
        "OP-02: po schvaleni sa dovod meni na CONFIRMED (REQ-13)",
        client.get(
            f"/instruments/{data['guarded']}/availability",
            params={
                "starts_at": guarded_start.isoformat(),
                "ends_at": guarded_end.isoformat(),
            },
        ),
    )

    second_request = client.post(
        "/reservations",
        json={
            "instrument_id": str(data["guarded"]),
            "user_id": str(data["student"]),
            "starts_at": (guarded_start + 3 * HOUR).isoformat(),
            "ends_at": (guarded_end + 3 * HOUR).isoformat(),
        },
    ).json()["id"]
    client.post(
        f"/reservations/{second_request}/confirm",
        json={"requested_by": str(data["student"])},
    )
    show(
        "OP-05 druhy vysledok: zamietnutie -> REJECTED, pristroj sa uvolni (REQ-12)",
        client.post(
            f"/reservations/{second_request}/reject",
            json={"requested_by": str(data["boss"])},
        ),
    )
    show(
        "OP-04 negativny: zamietnutu ziadost uz nemozno zrusit (REQ-14)",
        client.post(
            f"/reservations/{second_request}/cancel",
            json={"requested_by": str(data["student"])},
        ),
    )
    show(
        "OP-05 hranica: rozhodnutie o ziadosti, ktorej termin uz zacal (BR-08, REQ-15)",
        client.post(
            f"/reservations/{data['expired_request']}/approve",
            json={"requested_by": str(data["boss"])},
        ),
    )
    show(
        "Kontrola: vyprsana ziadost uz neblokuje pristroj",
        client.get(
            f"/instruments/{data['guarded']}/availability",
            params={
                "starts_at": (datetime.now(UTC) - HOUR).isoformat(),
                "ends_at": (datetime.now(UTC) + HOUR).isoformat(),
            },
        ),
    )

    print("\nDemo dokoncene.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
