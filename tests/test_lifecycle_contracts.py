"""Component testy kontraktov jednotky Reservation Lifecycle (C05).

Odvodene z navrhu v docs/architecture-and-decisions.md, C05.4 a C05.8.

Priklady overenia zo specifikacie su v test_operations.py
a test_approval.py - tie hovoria, CO system urobi. Tieto testy hovoria,
AKO si to jednotka vo vnutri rozdelila: kto rozhoduje, kto zapisuje, kto
commituje, kto drzi zamok a v akom poradi sa zamieta.

UROVEN: component - sluzba proti skutocnemu Postgresu. Nizsie to nejde:
kazde tvrdenie tu je o transakcii, o zamku alebo o tom, co vidi INE
spojenie. Samotna rozhodovacia tabulka je overena na urovni unit
v test_transitions.py.

Stav sa tu cita cez `new_session`, nie cez `session.refresh()`. Refresh
bezi v tej istej transakcii a videl by aj zapis, ktory nikto necommitol.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from swilab.domain.states import ReservationState, UserRole
from swilab.domain.transitions import (
    cancel_decision,
    confirm_decision,
    expiry_decision,
)
from swilab.errors import DomainError, ErrorCode
from swilab.models import Reservation
from swilab.services import reservations as service

S = ReservationState

HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)
TICK = timedelta(microseconds=1)


@pytest.fixture
def now():
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def tomorrow(now):
    base = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0)
    return base, base + HOUR


def _persisted_state(new_session, reservation_id) -> ReservationState:
    """Stav, ako ho vidi INE spojenie - teda iba to, co je commitnute."""
    with new_session() as other:
        return other.get(Reservation, reservation_id).state


def _outcome(operation) -> ErrorCode | None:
    """Kod zamietnutia, alebo None pri uspechu."""
    try:
        operation()
    except DomainError as exc:
        return exc.code
    return None


def _instrument_lock_is_free(engine, instrument_id) -> bool:
    """Skusi vziat zamok straznika invariantu z INEHO spojenia, bez cakania."""
    with engine.connect() as other:
        try:
            other.execute(
                text(
                    "SELECT id FROM instruments WHERE id = :id "
                    "FOR NO KEY UPDATE NOWAIT"
                ),
                {"id": instrument_id},
            )
        except OperationalError as exc:
            # 55P03 = lock_not_available. Ine zlyhanie nie je odpoved na
            # otazku, ci zamok niekto drzi.
            assert exc.orig.sqlstate == "55P03", exc
            return False
        return True


# =========================================================================
# DD-1: operacia vykona presne to, co rozhodla politika
# =========================================================================


@pytest.mark.parametrize(
    ("state", "lead"),
    [
        (S.DRAFT, HOUR),
        (S.PENDING_APPROVAL, HOUR),
        (S.PENDING_APPROVAL, -HOUR),
        (S.CONFIRMED, 2 * HOUR),
        (S.CONFIRMED, 30 * MINUTE),
        (S.CANCELLED, HOUR),
        (S.REJECTED, HOUR),
        (S.EXPIRED, -HOUR),
    ],
)
def test_cancel_carries_out_exactly_the_policy_decision(
    session, new_session, make_instrument, make_user, make_reservation, now, state, lead
):
    """Kazdy riadok tabulky zrusenia: co politika rozhodne, to operacia
    vykona - cielovy stav je commitnuty, zamietnutie vyhodene, `changed`
    hovori, ci sa stav zmenil. Ziadne druhe rozhodovanie v sluzbe."""
    owner = make_user()
    starts_at = now + lead
    reservation = make_reservation(
        make_instrument(), owner, starts_at, starts_at + HOUR, state=state
    )
    decision = cancel_decision(state, starts_at, now)

    result = None

    def cancel():
        nonlocal result
        result = service.cancel_reservation(
            session, reservation_id=reservation.id, requested_by=owner.id, now=now
        )

    assert _outcome(cancel) == decision.rejection
    assert _persisted_state(new_session, reservation.id) is (decision.target or state)
    if result is not None:
        assert result.changed is (decision.target is not None)


@pytest.mark.parametrize(
    ("state", "lead", "requires_approval"),
    [
        (S.DRAFT, HOUR, False),
        (S.DRAFT, HOUR, True),
        (S.DRAFT, -HOUR, False),
        (S.CONFIRMED, HOUR, False),
        (S.PENDING_APPROVAL, HOUR, True),
        (S.CANCELLED, HOUR, False),
    ],
)
def test_confirm_enters_exactly_the_state_the_policy_chose(
    session,
    new_session,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
    now,
    state,
    lead,
    requires_approval,
):
    """Predpoklady alokacie su splnene a prekryv nie je, takze o vysledku
    rozhoduje iba politika - vratane volby cieloveho stavu (REQ-10)."""
    instrument = make_instrument(requires_approval=requires_approval)
    owner = make_user()
    starts_at = now + lead
    make_certification(owner, instrument.category, starts_at + 24 * HOUR)
    reservation = make_reservation(
        instrument, owner, starts_at, starts_at + HOUR, state=state
    )
    decision = confirm_decision(
        state, starts_at, now, requires_approval=requires_approval
    )

    outcome = _outcome(
        lambda: service.confirm_reservation(
            session, reservation_id=reservation.id, requested_by=owner.id, now=now
        )
    )

    assert outcome == decision.rejection
    assert _persisted_state(new_session, reservation.id) is (decision.target or state)


# =========================================================================
# DD-4: EXPIRED je commitnuty skor, nez operacia skonci zamietnutim
# =========================================================================


@pytest.mark.parametrize("operation", ["cancel", "approve", "reject"])
def test_expiry_is_committed_before_the_operation_is_rejected(
    session, new_session, make_instrument, make_user, make_reservation, now, operation
):
    """N-04, REQ-15: jediny pripad, ked operacia zmeni stav a aj tak skonci
    zamietnutim. Keby zapis nebol commitnuty PRED vynimkou, rollback pri
    zatvoreni session by ho zahodil a ziadost by ostala visiet."""
    owner, supervisor = make_user(), make_user(role=UserRole.SUPERVISOR)
    starts_at = now - HOUR
    request = make_reservation(
        make_instrument(requires_approval=True),
        owner,
        starts_at,
        starts_at + 2 * HOUR,
        state=S.PENDING_APPROVAL,
    )

    def run():
        if operation == "cancel":
            service.cancel_reservation(
                session, reservation_id=request.id, requested_by=owner.id, now=now
            )
        else:
            service.decide_reservation(
                session,
                reservation_id=request.id,
                requested_by=supervisor.id,
                approve=operation == "approve",
                now=now,
            )

    assert _outcome(run) is ErrorCode.EXPIRED
    # Povodna session je stale otvorena - pyta sa ine spojenie.
    assert _persisted_state(new_session, request.id) is S.EXPIRED


# =========================================================================
# Kontrakt straznika invariantu - enter_blocking_state() (C05.4, DD-3, DD-6)
# =========================================================================


@pytest.fixture
def draft(make_instrument, make_user, make_reservation, tomorrow):
    """DRAFT, ktory splna predpoklady straznika az na zamok rezervacie."""
    starts_at, ends_at = tomorrow
    return make_reservation(make_instrument(), make_user(), starts_at, ends_at)


def test_guard_writes_the_target_but_leaves_the_commit_to_the_caller(
    session, new_session, draft, now
):
    """DD-6: hranica transakcie patri operacii. Po navrate zo straznika je
    stav zapisany v session, NIE v databaze."""
    reservation = service._lock_reservation(session, draft.id)

    service.enter_blocking_state(
        session, reservation=reservation, target_state=S.CONFIRMED, now=now
    )

    assert reservation.state is S.CONFIRMED
    assert _persisted_state(new_session, draft.id) is S.DRAFT
    session.commit()
    assert _persisted_state(new_session, draft.id) is S.CONFIRMED


def test_guard_holds_the_instrument_lock_until_the_callers_commit(
    session, engine, draft, now
):
    """Kontrakt: kriticka sekcia okolo BR-02 trva od vstupu do straznika
    po commit VOLAJUCEHO. Testy subehu to dokladaju pravdepodobnostne
    (8 kol); tento test deterministicky."""
    reservation = service._lock_reservation(session, draft.id)
    assert _instrument_lock_is_free(engine, draft.instrument_id)

    service.enter_blocking_state(
        session, reservation=reservation, target_state=S.CONFIRMED, now=now
    )

    assert not _instrument_lock_is_free(engine, draft.instrument_id)
    session.commit()
    assert _instrument_lock_is_free(engine, draft.instrument_id)


def test_guard_changes_nothing_when_it_rejects_overlap(
    session, new_session, make_user, make_reservation, draft, now
):
    """Kontrakt, vetva OVERLAP: stav nezmeneny, nic sa nezapisalo."""
    make_reservation(
        draft.instrument, make_user(), draft.starts_at, draft.ends_at, state=S.CONFIRMED
    )
    reservation = service._lock_reservation(session, draft.id)

    with pytest.raises(DomainError) as excinfo:
        service.enter_blocking_state(
            session, reservation=reservation, target_state=S.CONFIRMED, now=now
        )

    assert excinfo.value.code is ErrorCode.OVERLAP
    assert reservation.state is S.DRAFT
    session.commit()
    assert _persisted_state(new_session, draft.id) is S.DRAFT


def test_guard_refuses_a_non_blocking_target_before_touching_the_database(
    session, engine, draft, now
):
    """Kontrakt: neblokujuci ciel je chyba programu, nie business
    zamietnutie - a straznik ju vyhodi skor, nez si vezme zamok."""
    reservation = service._lock_reservation(session, draft.id)

    with pytest.raises(ValueError):
        service.enter_blocking_state(
            session, reservation=reservation, target_state=S.CANCELLED, now=now
        )

    assert reservation.state is S.DRAFT
    assert _instrument_lock_is_free(engine, draft.instrument_id)


# =========================================================================
# DD-5: predpoklady alokacie - jedna implementacia, jedno poradie
# =========================================================================


@pytest.mark.parametrize("path", ["confirm", "approve"])
def test_allocation_preconditions_keep_one_order_on_both_paths(
    session,
    new_session,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
    tomorrow,
    now,
    path,
):
    """BR-05 pred BR-04, obe pred BR-02 - rovnako pri potvrdeni (OP-03)
    aj pri schvaleni (OP-05). Prekazky sa odstranuju po jednej; kazdy
    pokus musi pomenovat tu, ktora je v poradi prva."""
    owner, supervisor = make_user(), make_user(role=UserRole.SUPERVISOR)
    instrument = make_instrument(requires_approval=path == "approve", is_active=False)
    starts_at, ends_at = tomorrow
    source = S.DRAFT if path == "confirm" else S.PENDING_APPROVAL
    reservation = make_reservation(instrument, owner, starts_at, ends_at, state=source)
    # Termin medzitym potvrdeny niekomu inemu - BR-02 by zamietlo tiez.
    make_reservation(instrument, make_user(), starts_at, ends_at, state=S.CONFIRMED)

    def attempt() -> ErrorCode | None:
        if path == "confirm":
            return _outcome(
                lambda: service.confirm_reservation(
                    session, reservation_id=reservation.id,
                    requested_by=owner.id, now=now,
                )
            )
        return _outcome(
            lambda: service.decide_reservation(
                session, reservation_id=reservation.id,
                requested_by=supervisor.id, approve=True, now=now,
            )
        )

    # Neplati nic: pristroj v servise, ziadny certifikat, prekryv.
    assert attempt() is ErrorCode.INSTRUMENT_INACTIVE

    instrument.is_active = True
    session.commit()
    assert attempt() is ErrorCode.MISSING_CERTIFICATION

    make_certification(owner, instrument.category, ends_at + HOUR)
    assert attempt() is ErrorCode.OVERLAP

    session.commit()
    assert _persisted_state(new_session, reservation.id) is source


# =========================================================================
# DD-7: poradie zamietnuti
# =========================================================================


@pytest.mark.parametrize("operation", ["confirm_reservation", "cancel_reservation"])
def test_unknown_requester_is_rejected_and_nothing_changes(
    session, new_session, draft, now, operation
):
    """BR-06: pouzivatel uvedeny v poziadavke musi existovat. Vetvu
    pomenovalo C03 cast A (A3) ako spravanie bez testu."""
    outcome = _outcome(
        lambda: getattr(service, operation)(
            session, reservation_id=draft.id, requested_by=uuid.uuid4(), now=now
        )
    )

    assert outcome is ErrorCode.UNKNOWN_USER
    session.commit()
    assert _persisted_state(new_session, draft.id) is S.DRAFT


@pytest.mark.parametrize(
    ("operation", "state"),
    [
        ("confirm_reservation", S.CONFIRMED),
        ("cancel_reservation", S.CANCELLED),
        ("cancel_reservation", S.REJECTED),
    ],
)
def test_authorization_is_judged_before_the_state(
    session, make_instrument, make_user, make_reservation, tomorrow, now, operation, state
):
    """RV-6: cudzi student sa z kodu chyby nesmie dozvediet stav cudzej
    rezervacie. Plati to aj pre idempotentne zrusenie - REQ-09 neznamena,
    ze uspech dostane ktokolvek."""
    starts_at, ends_at = tomorrow
    reservation = make_reservation(
        make_instrument(), make_user(), starts_at, ends_at, state=state
    )
    stranger = make_user()

    outcome = _outcome(
        lambda: getattr(service, operation)(
            session, reservation_id=reservation.id, requested_by=stranger.id, now=now
        )
    )

    assert outcome is ErrorCode.FORBIDDEN


@pytest.mark.parametrize(
    ("state", "lead", "expected"),
    [
        (S.CANCELLED, HOUR, ErrorCode.INVALID_STATE),
        (S.DRAFT, -HOUR, ErrorCode.ALREADY_STARTED),
    ],
)
def test_policy_rejection_comes_before_allocation_preconditions(
    session, make_instrument, make_user, make_reservation, now, state, lead, expected
):
    """Pristroj je v servise a vlastnik nema certifikat, ale volajuci sa ma
    dozvediet to, co je v poradi prve: ze rezervaciu uz potvrdit nemozno."""
    owner = make_user()
    starts_at = now + lead
    reservation = make_reservation(
        make_instrument(is_active=False), owner, starts_at, starts_at + HOUR, state=state
    )

    outcome = _outcome(
        lambda: service.confirm_reservation(
            session, reservation_id=reservation.id, requested_by=owner.id, now=now
        )
    )

    assert outcome is expected


# =========================================================================
# BR-08 ma dve formy - v politike a v SQL dotaze na prekryv
# =========================================================================


@pytest.mark.parametrize("lead", [TICK, timedelta(0)])
def test_liveness_in_the_overlap_query_agrees_with_the_policy(
    session, make_instrument, make_user, make_reservation, lead
):
    """Podmienka zivosti ziadosti existuje dvakrat: ako predikat, ktorym
    rozhoduje politika, a ako SQL v dotaze na prekryv (ADR-01, dosledok 4).
    Na hranici musia odpovedat rovnako - inak by ziadost, ktoru politika
    povazuje za vyprsanu, dalej blokovala pristroj, alebo naopak."""
    now = datetime.now(UTC)
    starts_at = now + lead
    instrument = make_instrument(requires_approval=True)
    make_reservation(
        instrument, make_user(), starts_at, starts_at + HOUR, state=S.PENDING_APPROVAL
    )

    expired = expiry_decision(S.PENDING_APPROVAL, starts_at, now).target is S.EXPIRED
    availability = service.check_availability(
        session,
        instrument_id=instrument.id,
        starts_at=starts_at,
        ends_at=starts_at + HOUR,
        now=now,
    )

    assert availability.available is expired
