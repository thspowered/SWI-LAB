"""Priklady overenia zo specifikacie - zakladne operacie OP-01 az OP-04.

Kazdy test nesie v nazve alebo v docstringu pravidlo alebo poziadavku,
ktoru overuje. Testy su zamerne pisane proti docs/specification.md, nie
proti implementacii - ked sa zmeni specifikacia, ma zlyhat test.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from swilab.domain.states import InstrumentCategory, ReservationState, UserRole
from swilab.errors import DomainError, ErrorCode
from swilab.services import reservations as service

HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)
SECOND = timedelta(seconds=1)


@pytest.fixture
def now():
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def tomorrow(now):
    """Interval [zajtra 10:00, zajtra 11:00) - referencny interval
    z prikladov overenia v specifikacii."""
    base = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0)
    return base, base + HOUR


def _certified(make_certification, user, instrument, until):
    return make_certification(user, instrument.category, until)


# =========================================================================
# OP-01 Create Reservation
# =========================================================================


def test_create_produces_draft(session, make_instrument, make_user, now, tomorrow):
    """REQ-01: platny vstup vytvori prave jednu rezervaciu v stave DRAFT."""
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = tomorrow

    reservation = service.create_reservation(
        session,
        instrument_id=instrument.id,
        user_id=user.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )

    assert reservation.state is ReservationState.DRAFT
    assert reservation.id is not None


def test_create_rejects_empty_interval(session, make_instrument, make_user, now, tomorrow):
    """BR-01, hranica: starts_at == ends_at je neplatny interval."""
    instrument, user = make_instrument(), make_user()
    starts_at, _ = tomorrow

    with pytest.raises(DomainError) as excinfo:
        service.create_reservation(
            session,
            instrument_id=instrument.id,
            user_id=user.id,
            starts_at=starts_at,
            ends_at=starts_at,
            now=now,
        )

    assert excinfo.value.code is ErrorCode.INVALID_INTERVAL


def test_create_rejects_inactive_instrument(
    session, make_instrument, make_user, now, tomorrow
):
    """BR-05: neaktivny pristroj neprijme ani novy zamer."""
    instrument = make_instrument(is_active=False)
    user = make_user()
    starts_at, ends_at = tomorrow

    with pytest.raises(DomainError) as excinfo:
        service.create_reservation(
            session,
            instrument_id=instrument.id,
            user_id=user.id,
            starts_at=starts_at,
            ends_at=ends_at,
            now=now,
        )

    assert excinfo.value.code is ErrorCode.INSTRUMENT_INACTIVE


def test_create_rejects_past_start(session, make_instrument, make_user, now):
    """OP-01: rezervovat spatne nie je mozne."""
    instrument, user = make_instrument(), make_user()

    with pytest.raises(DomainError) as excinfo:
        service.create_reservation(
            session,
            instrument_id=instrument.id,
            user_id=user.id,
            starts_at=now - HOUR,
            ends_at=now + HOUR,
            now=now,
        )

    assert excinfo.value.code is ErrorCode.START_IN_PAST


def test_create_ignores_overlap_and_certification(
    session, make_instrument, make_user, make_reservation, now, tomorrow
):
    """REQ-02: Create NEVYHODNOCUJE prekryv ani certifikat.

    Tento test je poistka proti tichemu presunu kontrol do Create. Ak ho
    niekto zmeni, musi najprv zmenit specifikaciu.
    """
    instrument = make_instrument()
    owner, second = make_user(), make_user()
    starts_at, ends_at = tomorrow
    make_reservation(
        instrument, owner, starts_at, ends_at, state=ReservationState.CONFIRMED
    )

    reservation = service.create_reservation(
        session,
        instrument_id=instrument.id,
        user_id=second.id,  # bez akehokolvek certifikatu
        starts_at=starts_at,
        ends_at=ends_at,  # uplny prekryv s potvrdenou rezervaciou
        now=now,
    )

    assert reservation.state is ReservationState.DRAFT


# =========================================================================
# OP-02 Check Availability
# =========================================================================


@pytest.mark.parametrize(
    ("offset_start", "offset_end", "expected_available", "case"),
    [
        (-HOUR, timedelta(0), True, "dotyk zlava [09:00,10:00)"),
        (HOUR, 2 * HOUR, True, "dotyk sprava [11:00,12:00)"),
        (timedelta(minutes=30), timedelta(minutes=90), False, "ciastocny prekryv"),
        (-HOUR, 2 * HOUR, False, "obsahuje celu rezervaciu"),
        (timedelta(minutes=15), timedelta(minutes=45), False, "lezi vnutri"),
    ],
)
def test_availability_interval_boundaries(
    session,
    make_instrument,
    make_user,
    make_reservation,
    now,
    tomorrow,
    offset_start,
    offset_end,
    expected_available,
    case,
):
    """REQ-03 + BR-01: susediace intervaly nekoliduju, prekryvajuce ano."""
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = tomorrow
    make_reservation(
        instrument, user, starts_at, ends_at, state=ReservationState.CONFIRMED
    )

    result = service.check_availability(
        session,
        instrument_id=instrument.id,
        starts_at=starts_at + offset_start,
        ends_at=starts_at + offset_end,
        now=now,
    )

    assert result.available is expected_available, case


@pytest.mark.parametrize(
    "non_blocking_state", [ReservationState.DRAFT, ReservationState.CANCELLED]
)
def test_draft_and_cancelled_do_not_block(
    session, make_instrument, make_user, make_reservation, now, tomorrow,
    non_blocking_state,
):
    """BR-02: DRAFT ani CANCELLED pristroj neblokuju.

    Blokujuce stavy su od v0.2 dva - PENDING_APPROVAL je overeny
    v tests/test_approval.py.
    """
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = tomorrow
    make_reservation(instrument, user, starts_at, ends_at, state=non_blocking_state)

    result = service.check_availability(
        session,
        instrument_id=instrument.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )

    assert result.available is True


def test_availability_reports_conflicting_reservation(
    session, make_instrument, make_user, make_reservation, now, tomorrow
):
    """OP-02: zoznam kolidujucich rezervacii je sucast vysledku."""
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = tomorrow
    blocking = make_reservation(
        instrument, user, starts_at, ends_at, state=ReservationState.CONFIRMED
    )

    result = service.check_availability(
        session,
        instrument_id=instrument.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )

    assert result.available is False
    assert result.conflicting_reservation_ids == [blocking.id]


def test_inactive_instrument_is_unavailable(session, make_instrument, now, tomorrow):
    """BR-05: neaktivny pristroj je nedostupny - ale je to ODPOVED, nie chyba."""
    instrument = make_instrument(is_active=False)
    starts_at, ends_at = tomorrow

    result = service.check_availability(
        session,
        instrument_id=instrument.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )

    assert result.available is False
    assert result.reason == ErrorCode.INSTRUMENT_INACTIVE.value


def test_availability_allows_past_interval(
    session, make_instrument, make_user, make_reservation, now
):
    """Nalez N-02: dopyt do minulosti je platny, lebo OP-02 iba cita."""
    instrument, user = make_instrument(), make_user()
    past_start = now - 3 * HOUR
    past_end = now - 2 * HOUR
    make_reservation(
        instrument, user, past_start, past_end, state=ReservationState.CONFIRMED
    )

    result = service.check_availability(
        session,
        instrument_id=instrument.id,
        starts_at=past_start,
        ends_at=past_end,
        now=now,
    )

    assert result.available is False


# =========================================================================
# OP-03 Confirm Reservation
# =========================================================================


def test_confirm_allocates_instrument(
    session,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
    now,
    tomorrow,
):
    """REQ-04: DRAFT + aktivny pristroj + platny certifikat + ziadny prekryv."""
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = tomorrow
    _certified(make_certification, user, instrument, starts_at + 365 * 24 * HOUR)
    reservation = make_reservation(instrument, user, starts_at, ends_at)

    confirmed = service.confirm_reservation(
        session, reservation_id=reservation.id, requested_by=user.id, now=now
    )

    assert confirmed.state is ReservationState.CONFIRMED

    availability = service.check_availability(
        session,
        instrument_id=instrument.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )
    assert availability.available is False


def test_confirm_rejects_overlap_and_keeps_draft(
    session,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
    now,
    tomorrow,
):
    """REQ-04: pri prekryve zamietnute a rezervacia ZOSTAVA v DRAFT."""
    instrument = make_instrument()
    first, second = make_user(), make_user()
    starts_at, ends_at = tomorrow
    _certified(make_certification, second, instrument, ends_at + HOUR)
    make_reservation(
        instrument, first, starts_at, ends_at, state=ReservationState.CONFIRMED
    )
    reservation = make_reservation(
        instrument, second, starts_at + timedelta(minutes=30), ends_at + timedelta(minutes=30)
    )

    with pytest.raises(DomainError) as excinfo:
        service.confirm_reservation(
            session, reservation_id=reservation.id, requested_by=second.id, now=now
        )

    assert excinfo.value.code is ErrorCode.OVERLAP
    session.refresh(reservation)
    assert reservation.state is ReservationState.DRAFT


def test_confirm_allows_adjacent_interval(
    session,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
    now,
    tomorrow,
):
    """BR-01: [11:00,12:00) susedi s [10:00,11:00) a nekoliduje."""
    instrument = make_instrument()
    first, second = make_user(), make_user()
    starts_at, ends_at = tomorrow
    _certified(make_certification, second, instrument, ends_at + 24 * HOUR)
    make_reservation(
        instrument, first, starts_at, ends_at, state=ReservationState.CONFIRMED
    )
    reservation = make_reservation(instrument, second, ends_at, ends_at + HOUR)

    confirmed = service.confirm_reservation(
        session, reservation_id=reservation.id, requested_by=second.id, now=now
    )

    assert confirmed.state is ReservationState.CONFIRMED


@pytest.mark.parametrize(
    ("offset", "expected_state", "case"),
    [
        (timedelta(0), None, "valid_until == starts_at je NEPLATNY (hranica BR-04)"),
        (SECOND, ReservationState.CONFIRMED, "valid_until o sekundu neskor uz plati"),
    ],
)
def test_certification_boundary(
    session,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
    now,
    tomorrow,
    offset,
    expected_state,
    case,
):
    """BR-04, hranica platnosti certifikatu."""
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = tomorrow
    _certified(make_certification, user, instrument, starts_at + offset)
    reservation = make_reservation(instrument, user, starts_at, ends_at)

    if expected_state is None:
        with pytest.raises(DomainError) as excinfo:
            service.confirm_reservation(
                session, reservation_id=reservation.id, requested_by=user.id, now=now
            )
        assert excinfo.value.code is ErrorCode.MISSING_CERTIFICATION, case
        session.refresh(reservation)
        assert reservation.state is ReservationState.DRAFT
    else:
        confirmed = service.confirm_reservation(
            session, reservation_id=reservation.id, requested_by=user.id, now=now
        )
        assert confirmed.state is expected_state, case


def test_confirm_rejects_wrong_category_certification(
    session,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
    now,
    tomorrow,
):
    """BR-04: certifikat plati na kategoriu, nie na cokolvek."""
    instrument = make_instrument(category=InstrumentCategory.MICROSCOPE)
    user = make_user()
    starts_at, ends_at = tomorrow
    make_certification(user, InstrumentCategory.CENTRIFUGE, ends_at + 24 * HOUR)
    reservation = make_reservation(instrument, user, starts_at, ends_at)

    with pytest.raises(DomainError) as excinfo:
        service.confirm_reservation(
            session, reservation_id=reservation.id, requested_by=user.id, now=now
        )

    assert excinfo.value.code is ErrorCode.MISSING_CERTIFICATION


def test_confirm_is_not_idempotent(
    session,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
    now,
    tomorrow,
):
    """OP-03: druhe potvrdenie je chyba (na rozdiel od zrusenia)."""
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = tomorrow
    _certified(make_certification, user, instrument, ends_at + 24 * HOUR)
    reservation = make_reservation(
        instrument, user, starts_at, ends_at, state=ReservationState.CONFIRMED
    )

    with pytest.raises(DomainError) as excinfo:
        service.confirm_reservation(
            session, reservation_id=reservation.id, requested_by=user.id, now=now
        )

    assert excinfo.value.code is ErrorCode.INVALID_STATE
    session.refresh(reservation)
    assert reservation.state is ReservationState.CONFIRMED


def test_confirm_by_foreign_student_is_forbidden(
    session, make_instrument, make_user, make_reservation, now, tomorrow
):
    """BR-06: cudziu rezervaciu student potvrdit nesmie."""
    instrument = make_instrument()
    owner, stranger = make_user(), make_user()
    starts_at, ends_at = tomorrow
    reservation = make_reservation(instrument, owner, starts_at, ends_at)

    with pytest.raises(DomainError) as excinfo:
        service.confirm_reservation(
            session, reservation_id=reservation.id, requested_by=stranger.id, now=now
        )

    assert excinfo.value.code is ErrorCode.FORBIDDEN


def test_supervisor_confirms_foreign_reservation(
    session,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
    now,
    tomorrow,
):
    """BR-06: veduci laboratoria smie operovat nad cudzou rezervaciou,
    ale certifikat sa overuje jej VLASTNIKOVI."""
    instrument = make_instrument()
    owner = make_user()
    supervisor = make_user(role=UserRole.SUPERVISOR, name="Veduci")
    starts_at, ends_at = tomorrow
    _certified(make_certification, owner, instrument, ends_at + 24 * HOUR)
    reservation = make_reservation(instrument, owner, starts_at, ends_at)

    confirmed = service.confirm_reservation(
        session, reservation_id=reservation.id, requested_by=supervisor.id, now=now
    )

    assert confirmed.state is ReservationState.CONFIRMED


# =========================================================================
# OP-04 Cancel Reservation
# =========================================================================


def test_cancel_draft_has_no_deadline(
    session, make_instrument, make_user, make_reservation, now
):
    """REQ-07 / nalez N-03: DRAFT sa da zrusit aj po zaciatku."""
    instrument, user = make_instrument(), make_user()
    reservation = make_reservation(instrument, user, now - HOUR, now + HOUR)

    result = service.cancel_reservation(
        session, reservation_id=reservation.id, requested_by=user.id, now=now
    )

    assert result.changed is True
    assert result.reservation.state is ReservationState.CANCELLED


def test_cancel_confirmed_frees_instrument(
    session, make_instrument, make_user, make_reservation, now
):
    """REQ-08: po zruseni prestava rezervacia blokovat pristroj."""
    instrument, user = make_instrument(), make_user()
    starts_at = now + 3 * HOUR
    ends_at = starts_at + HOUR
    reservation = make_reservation(
        instrument, user, starts_at, ends_at, state=ReservationState.CONFIRMED
    )

    service.cancel_reservation(
        session, reservation_id=reservation.id, requested_by=user.id, now=now
    )

    availability = service.check_availability(
        session,
        instrument_id=instrument.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )
    assert availability.available is True


@pytest.mark.parametrize(
    ("lead_time", "should_succeed", "case"),
    [
        (HOUR, False, "presne 60:00 min pred zaciatkom - hranica je ostra"),
        (HOUR + SECOND, True, "60:00:01 pred zaciatkom uz prejde"),
        (timedelta(minutes=30), False, "30 min pred zaciatkom"),
    ],
)
def test_cancel_confirmed_boundary(
    session,
    make_instrument,
    make_user,
    make_reservation,
    now,
    lead_time,
    should_succeed,
    case,
):
    """BR-03, hranica 60 minut pre potvrdenu rezervaciu."""
    instrument, user = make_instrument(), make_user()
    starts_at = now + lead_time
    reservation = make_reservation(
        instrument, user, starts_at, starts_at + HOUR, state=ReservationState.CONFIRMED
    )

    if should_succeed:
        result = service.cancel_reservation(
            session, reservation_id=reservation.id, requested_by=user.id, now=now
        )
        assert result.reservation.state is ReservationState.CANCELLED, case
    else:
        with pytest.raises(DomainError) as excinfo:
            service.cancel_reservation(
                session, reservation_id=reservation.id, requested_by=user.id, now=now
            )
        assert excinfo.value.code is ErrorCode.TOO_LATE, case
        session.refresh(reservation)
        assert reservation.state is ReservationState.CONFIRMED, case


def test_cancel_is_idempotent(
    session, make_instrument, make_user, make_reservation, now, tomorrow
):
    """REQ-09: druhe zrusenie je uspech bez zmeny stavu."""
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = tomorrow
    reservation = make_reservation(instrument, user, starts_at, ends_at)

    first = service.cancel_reservation(
        session, reservation_id=reservation.id, requested_by=user.id, now=now
    )
    second = service.cancel_reservation(
        session, reservation_id=reservation.id, requested_by=user.id, now=now
    )

    assert first.changed is True
    assert second.changed is False
    assert second.reservation.state is ReservationState.CANCELLED


def test_cancel_by_foreign_student_is_forbidden(
    session, make_instrument, make_user, make_reservation, now, tomorrow
):
    """BR-06 plati aj pre zrusenie - a aj pre jeho idempotentne opakovanie."""
    instrument = make_instrument()
    owner, stranger = make_user(), make_user()
    starts_at, ends_at = tomorrow
    reservation = make_reservation(instrument, owner, starts_at, ends_at)

    with pytest.raises(DomainError) as excinfo:
        service.cancel_reservation(
            session, reservation_id=reservation.id, requested_by=stranger.id, now=now
        )

    assert excinfo.value.code is ErrorCode.FORBIDDEN


# =========================================================================
# Chybove vysledky, ktore specifikacia uvadza pri viacerych operaciach
# =========================================================================


def test_confirm_after_start_is_rejected(
    session, make_instrument, make_user, make_certification, make_reservation, now
):
    """REQ-06: rezervaciu, ktorej zaciatok uz nastal, nemozno potvrdit."""
    instrument, user = make_instrument(), make_user()
    starts_at = now - MINUTE
    make_certification(user, instrument.category, starts_at + 24 * HOUR)
    reservation = make_reservation(instrument, user, starts_at, starts_at + HOUR)

    with pytest.raises(DomainError) as excinfo:
        service.confirm_reservation(
            session, reservation_id=reservation.id, requested_by=user.id, now=now
        )

    assert excinfo.value.code is ErrorCode.ALREADY_STARTED
    session.refresh(reservation)
    assert reservation.state is ReservationState.DRAFT


def test_confirm_rejects_inactive_instrument(
    session, make_instrument, make_user, make_certification, make_reservation,
    now, tomorrow,
):
    """BR-05 pri potvrdeni - doteraz overene len pri vytvoreni."""
    instrument = make_instrument(is_active=False)
    user = make_user()
    starts_at, ends_at = tomorrow
    make_certification(user, instrument.category, ends_at + 24 * HOUR)
    reservation = make_reservation(instrument, user, starts_at, ends_at)

    with pytest.raises(DomainError) as excinfo:
        service.confirm_reservation(
            session, reservation_id=reservation.id, requested_by=user.id, now=now
        )

    assert excinfo.value.code is ErrorCode.INSTRUMENT_INACTIVE
    session.refresh(reservation)
    assert reservation.state is ReservationState.DRAFT


@pytest.mark.parametrize(
    "operation", ["confirm", "cancel"], ids=["OP-03", "OP-04"]
)
def test_unknown_reservation_is_rejected(session, make_user, now, operation):
    """Neznama rezervacia -> NOT_FOUND vo vsetkych operaciach nad nou."""
    user = make_user()
    missing_id = uuid.uuid4()

    with pytest.raises(DomainError) as excinfo:
        if operation == "confirm":
            service.confirm_reservation(
                session, reservation_id=missing_id, requested_by=user.id, now=now
            )
        else:
            service.cancel_reservation(
                session, reservation_id=missing_id, requested_by=user.id, now=now
            )

    assert excinfo.value.code is ErrorCode.NOT_FOUND


def test_unknown_user_is_rejected(session, make_instrument, now, tomorrow):
    """BR-06: pouzivatel v poziadavke musi existovat."""
    instrument = make_instrument()
    starts_at, ends_at = tomorrow

    with pytest.raises(DomainError) as excinfo:
        service.create_reservation(
            session,
            instrument_id=instrument.id,
            user_id=uuid.uuid4(),
            starts_at=starts_at,
            ends_at=ends_at,
            now=now,
        )

    assert excinfo.value.code is ErrorCode.UNKNOWN_USER


def test_availability_rejects_unknown_instrument(session, now, tomorrow):
    """OP-02: neznamy pristroj -> zamietnute, NIE 'nedostupny'.

    Systém o takom pristroji nema co tvrdit.
    """
    starts_at, ends_at = tomorrow

    with pytest.raises(DomainError) as excinfo:
        service.check_availability(
            session,
            instrument_id=uuid.uuid4(),
            starts_at=starts_at,
            ends_at=ends_at,
            now=now,
        )

    assert excinfo.value.code is ErrorCode.UNKNOWN_INSTRUMENT


def test_availability_rejects_invalid_interval(
    session, make_instrument, now, tomorrow
):
    """OP-02 + BR-01: neplatny interval sa zamieta aj pri citacej operacii."""
    instrument = make_instrument()
    starts_at, _ = tomorrow

    with pytest.raises(DomainError) as excinfo:
        service.check_availability(
            session,
            instrument_id=instrument.id,
            starts_at=starts_at,
            ends_at=starts_at,
            now=now,
        )

    assert excinfo.value.code is ErrorCode.INVALID_INTERVAL
