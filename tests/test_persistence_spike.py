"""C01 engineering spike - varianta A (Persistence).

Otazka: prejde rezervacia cez realny Postgres tam a spat s neporusenymi
casmi? Nie "funguje SQLAlchemy", ale konkretne: prezije timezone a
mikrosekundova presnost zapis a nacitanie?
"""

from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import text

from swilab.db import create_db_engine
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

        # Nalez zo spiku, pribity ako assert: timestamptz neuklada pasmo.
        # Asserty vyssie to nezachytia - "==" na aware datetime porovnava
        # okamih, takze presli by rovnako pri +02:00 aj pri +00:00.
        # UTC tu nevracia "kazdy Postgres", ale nase spojenie: pasmo session
        # fixuje create_db_engine() (F-01 z C06, testy nizsie).
        assert loaded.starts_at.utcoffset() == timedelta(0), (
            "ocakavame UTC, nie povodny offset +02:00"
        )
        assert loaded.starts_at.hour == 7, "9:30+02:00 ma v UTC hodinu 7, nie 9"

        assert loaded.state is ReservationState.DRAFT
        assert loaded.instrument_id == instrument.id
        assert loaded.user_id == user.id


# --- F-01 z C06: pasmo session nezavisi od servera ani od klienta ----------
#
# Postgres vracia timestamptz v pasme SESSION (parameter TimeZone), nie vzdy
# v UTC. Session ho dedi z postgresql.conf, z ALTER DATABASE alebo z PGTZ
# klienta. Povodny spike to nevidel, lebo obraz postgres:16 ma predvolene
# UTC - nezavisle review na to narazilo pri prvom behu mimo docker-compose.


def test_session_timezone_is_utc_whatever_the_server_says(engine):
    """F-01: spojenie, ktore by inak dostalo Europe/Prague (tak, ako ho
    dostane od servera s inym postgresql.conf), musi aj tak bezat v UTC."""
    prague = create_db_engine(
        engine.url, connect_args={"options": "-c timezone=Europe/Prague"}
    )
    try:
        with prague.connect() as connection:
            zone = connection.execute(text("SHOW TimeZone")).scalar_one()
            loaded = connection.execute(
                text("SELECT TIMESTAMPTZ '2026-10-05 09:30:00+02'")
            ).scalar_one()
    finally:
        prague.dispose()

    assert zone == "UTC"
    assert loaded.utcoffset() == timedelta(0), "ocakavame UTC, nie offset session"
    assert loaded.hour == 7


def test_engine_aplikacie_aj_testov_bezi_v_utc(engine):
    """F-01: aplikacia aj testy dostavaju engine z tej istej tovarne.
    Rovnaky tvar ako kontrola READ COMMITTED v test_concurrency_req05."""
    from swilab.db import engine as app_engine

    for candidate in (app_engine, engine):
        with candidate.connect() as connection:
            assert connection.execute(text("SHOW TimeZone")).scalar_one() == "UTC"
