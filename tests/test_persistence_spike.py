"""C01 engineering spike - varianta A (Persistence).

Otazka: prejde rezervacia cez realny Postgres tam a spat s neporusenymi
casmi? Nie "funguje SQLAlchemy", ale konkretne: prezije timezone a
mikrosekundova presnost zapis a nacitanie?
"""

from datetime import UTC, datetime, timedelta, timezone

from swilab.domain.states import InstrumentCategory, ReservationState, UserRole
from swilab.models import Instrument, Reservation, User

BRATISLAVA = timezone(timedelta(hours=2))


def test_reservation_round_trips_through_postgres(session, new_session):
    instrument = Instrument(
        name="Zeiss Axio Observer",
        category=InstrumentCategory.MICROSCOPE,
        location="Lab B2.14",
    )
    user = User(
        full_name="Testovaci Pouzivatel",
        email="test@swi-lab.local",
        role=UserRole.STUDENT,
    )
    session.add_all([instrument, user])
    session.flush()

    # Zamerne nekrúhle cislo mikrosekund - prave to odhali tiche
    # orezanie presnosti.
    starts_at = datetime(2026, 10, 5, 9, 30, 0, 123456, tzinfo=BRATISLAVA)
    ends_at = datetime(2026, 10, 5, 11, 0, 0, 654321, tzinfo=BRATISLAVA)

    reservation = Reservation(
        instrument_id=instrument.id,
        user_id=user.id,
        starts_at=starts_at,
        ends_at=ends_at,
        state=ReservationState.DRAFT,
        created_at=datetime.now(UTC),
    )
    session.add(reservation)
    session.commit()
    reservation_id = reservation.id

    # Nova session - nesmieme dostat objekt z identity map tej povodnej,
    # inak by sme netestovali databazu ale pamat.
    with new_session() as fresh:
        loaded = fresh.get(Reservation, reservation_id)

        assert loaded is not None, "rezervacia sa vobec nenacitala"

        assert loaded.starts_at.tzinfo is not None, "cas sa vratil ako naive"
        assert loaded.ends_at.tzinfo is not None, "cas sa vratil ako naive"

        assert loaded.starts_at == starts_at
        assert loaded.ends_at == ends_at

        assert loaded.starts_at.microsecond == 123456
        assert loaded.ends_at.microsecond == 654321

        assert loaded.state is ReservationState.DRAFT
        assert loaded.instrument_id == instrument.id
        assert loaded.user_id == user.id
