"""Priklady overenia pre zmenu v0.2 - schvalovaci proces.

Kazdy test odkazuje na poziadavku alebo pravidlo, ktore overuje. Casove
hranice sa testuju posunutim starts_at, nie cakanim - zdroj casu je vstup
operacie (BR-03, BR-08).
"""

from datetime import UTC, datetime, timedelta

import pytest

from swilab.domain.states import InstrumentCategory, ReservationState, UserRole
from swilab.errors import DomainError, ErrorCode
from swilab.services import reservations as service

HOUR = timedelta(hours=1)
SECOND = timedelta(seconds=1)


@pytest.fixture
def now():
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def tomorrow(now):
    base = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0)
    return base, base + HOUR


@pytest.fixture
def supervisor(make_user):
    return make_user(role=UserRole.SUPERVISOR, name="Veduci Laboratoria")


@pytest.fixture
def guarded_instrument(make_instrument):
    """Pristroj, ktory vyzaduje schvalenie (R-1: priznak na pristroji)."""
    return make_instrument(requires_approval=True, name="Elektronovy mikroskop")


# =========================================================================
# OP-03 — potvrdenie na pristroji so schvalovanim (REQ-10)
# =========================================================================


def test_confirm_on_guarded_instrument_creates_request(
    session, guarded_instrument, make_user, make_certification, make_reservation,
    now, tomorrow,
):
    """REQ-10: ten isty pokyn konci inym stavom podla pristroja."""
    user = make_user()
    starts_at, ends_at = tomorrow
    make_certification(user, guarded_instrument.category, ends_at + 24 * HOUR)
    reservation = make_reservation(guarded_instrument, user, starts_at, ends_at)

    result = service.confirm_reservation(
        session, reservation_id=reservation.id, requested_by=user.id, now=now
    )

    assert result.state is ReservationState.PENDING_APPROVAL


def test_confirm_on_normal_instrument_still_confirms(
    session, make_instrument, make_user, make_certification, make_reservation,
    now, tomorrow,
):
    """Nezasiahnuta cesta: bez priznaku sa nic nezmenilo."""
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = tomorrow
    make_certification(user, instrument.category, ends_at + 24 * HOUR)
    reservation = make_reservation(instrument, user, starts_at, ends_at)

    result = service.confirm_reservation(
        session, reservation_id=reservation.id, requested_by=user.id, now=now
    )

    assert result.state is ReservationState.CONFIRMED


# =========================================================================
# OP-02 — blokujuce stavy a expiracia (REQ-03, REQ-13, BR-08)
# =========================================================================


def test_pending_request_blocks_instrument(
    session, guarded_instrument, make_user, make_reservation, now, tomorrow
):
    """BR-02 v0.2: ziadost blokuje rovnako ako potvrdena rezervacia."""
    user = make_user()
    starts_at, ends_at = tomorrow
    make_reservation(
        guarded_instrument, user, starts_at, ends_at,
        state=ReservationState.PENDING_APPROVAL,
    )

    result = service.check_availability(
        session,
        instrument_id=guarded_instrument.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )

    assert result.available is False
    assert result.reason == ReservationState.PENDING_APPROVAL.value


def test_expired_request_does_not_block(
    session, guarded_instrument, make_user, make_reservation, now
):
    """BR-08: po starts_at ziadost neblokuje."""
    user = make_user()
    starts_at = now - HOUR
    make_reservation(
        guarded_instrument, user, starts_at, starts_at + 2 * HOUR,
        state=ReservationState.PENDING_APPROVAL,
    )

    result = service.check_availability(
        session,
        instrument_id=guarded_instrument.id,
        starts_at=starts_at,
        ends_at=starts_at + 2 * HOUR,
        now=now,
    )

    assert result.available is True


@pytest.mark.parametrize(
    "dead_state", [ReservationState.REJECTED, ReservationState.EXPIRED]
)
def test_terminal_states_do_not_block(
    session, guarded_instrument, make_user, make_reservation, now, tomorrow, dead_state
):
    user = make_user()
    starts_at, ends_at = tomorrow
    make_reservation(guarded_instrument, user, starts_at, ends_at, state=dead_state)

    result = service.check_availability(
        session,
        instrument_id=guarded_instrument.id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=now,
    )

    assert result.available is True


def test_confirmed_reason_wins_over_pending(
    session, guarded_instrument, make_user, make_reservation, now, tomorrow
):
    """REQ-13: ak koliduju oba stavy, dovodom je CONFIRMED - je definitivny."""
    first, second = make_user(), make_user()
    starts_at, ends_at = tomorrow
    make_reservation(
        guarded_instrument, first, starts_at, ends_at,
        state=ReservationState.CONFIRMED,
    )
    make_reservation(
        guarded_instrument, second, ends_at + HOUR, ends_at + 2 * HOUR,
        state=ReservationState.PENDING_APPROVAL,
    )

    result = service.check_availability(
        session,
        instrument_id=guarded_instrument.id,
        starts_at=starts_at,
        ends_at=ends_at + 2 * HOUR,
        now=now,
    )

    assert result.available is False
    assert result.reason == ReservationState.CONFIRMED.value
    assert len(result.conflicting_reservation_ids) == 2


# =========================================================================
# OP-05 — rozhodnutie o ziadosti (REQ-11, REQ-12, REQ-15, BR-07)
# =========================================================================


def _pending(make_reservation, instrument, user, starts_at, ends_at):
    return make_reservation(
        instrument, user, starts_at, ends_at,
        state=ReservationState.PENDING_APPROVAL,
    )


def test_supervisor_approves_request(
    session, guarded_instrument, make_user, make_certification, make_reservation,
    supervisor, now, tomorrow,
):
    """REQ-11: schvalena ziadost sa stava definitivnou alokaciou."""
    owner = make_user()
    starts_at, ends_at = tomorrow
    make_certification(owner, guarded_instrument.category, ends_at + 24 * HOUR)
    request = _pending(make_reservation, guarded_instrument, owner, starts_at, ends_at)

    result = service.decide_reservation(
        session, reservation_id=request.id, requested_by=supervisor.id,
        approve=True, now=now,
    )

    assert result.state is ReservationState.CONFIRMED

    availability = service.check_availability(
        session, instrument_id=guarded_instrument.id,
        starts_at=starts_at, ends_at=ends_at, now=now,
    )
    assert availability.reason == ReservationState.CONFIRMED.value


def test_supervisor_rejects_request_and_frees_instrument(
    session, guarded_instrument, make_user, make_reservation, supervisor, now, tomorrow
):
    """REQ-12: po zamietnuti je pristroj opat volny."""
    owner = make_user()
    starts_at, ends_at = tomorrow
    request = _pending(make_reservation, guarded_instrument, owner, starts_at, ends_at)

    result = service.decide_reservation(
        session, reservation_id=request.id, requested_by=supervisor.id,
        approve=False, now=now,
    )

    assert result.state is ReservationState.REJECTED

    availability = service.check_availability(
        session, instrument_id=guarded_instrument.id,
        starts_at=starts_at, ends_at=ends_at, now=now,
    )
    assert availability.available is True


def test_rejection_works_even_for_inactive_instrument(
    session, make_instrument, make_user, make_reservation, supervisor, now, tomorrow
):
    """REQ-12: zamietnutie nevyzaduje podmienky pre schvalenie.

    Inak by ziadost na pristroj, ktory medzitym isiel do servisu, visela
    az do vyprsania a veduci by s nou nemohol nic urobit.
    """
    instrument = make_instrument(requires_approval=True, is_active=False)
    owner = make_user()
    starts_at, ends_at = tomorrow
    request = _pending(make_reservation, instrument, owner, starts_at, ends_at)

    result = service.decide_reservation(
        session, reservation_id=request.id, requested_by=supervisor.id,
        approve=False, now=now,
    )

    assert result.state is ReservationState.REJECTED


def test_student_cannot_decide(
    session, guarded_instrument, make_user, make_reservation, now, tomorrow
):
    """BR-07: o ziadosti rozhoduje iba SUPERVISOR."""
    owner = make_user()
    starts_at, ends_at = tomorrow
    request = _pending(make_reservation, guarded_instrument, owner, starts_at, ends_at)

    with pytest.raises(DomainError) as excinfo:
        service.decide_reservation(
            session, reservation_id=request.id, requested_by=owner.id,
            approve=True, now=now,
        )

    assert excinfo.value.code is ErrorCode.FORBIDDEN
    session.refresh(request)
    assert request.state is ReservationState.PENDING_APPROVAL


def test_supervisor_cannot_decide_own_request(
    session, guarded_instrument, make_certification, make_reservation,
    supervisor, now, tomorrow,
):
    """BR-07: veduci nesmie rozhodnut o vlastnej ziadosti.

    Bez tejto podmienky by schvalovanie pre veduceho neexistovalo
    a pravidlo by platilo len pre studentov.
    """
    starts_at, ends_at = tomorrow
    make_certification(supervisor, guarded_instrument.category, ends_at + 24 * HOUR)
    request = _pending(
        make_reservation, guarded_instrument, supervisor, starts_at, ends_at
    )

    with pytest.raises(DomainError) as excinfo:
        service.decide_reservation(
            session, reservation_id=request.id, requested_by=supervisor.id,
            approve=True, now=now,
        )

    assert excinfo.value.code is ErrorCode.FORBIDDEN


def test_another_supervisor_may_decide_supervisors_request(
    session, guarded_instrument, make_user, make_certification, make_reservation,
    supervisor, now, tomorrow,
):
    """Druhy veduci rozhodnut smie - obmedzenie je na vlastnu ziadost."""
    other = make_user(role=UserRole.SUPERVISOR, name="Druhy Veduci")
    starts_at, ends_at = tomorrow
    make_certification(supervisor, guarded_instrument.category, ends_at + 24 * HOUR)
    request = _pending(
        make_reservation, guarded_instrument, supervisor, starts_at, ends_at
    )

    result = service.decide_reservation(
        session, reservation_id=request.id, requested_by=other.id,
        approve=True, now=now,
    )

    assert result.state is ReservationState.CONFIRMED


@pytest.mark.parametrize(
    ("time_to_start", "expect_expired", "case"),
    [
        (timedelta(0), True, "rozhodnutie presne v okamihu starts_at - uz neskoro"),
        (SECOND, False, "sekundu pred zaciatkom ziadost este plati"),
    ],
)
def test_expiry_boundary(
    session, guarded_instrument, make_user, make_certification, make_reservation,
    supervisor, now, time_to_start, expect_expired, case,
):
    """BR-08 + REQ-15: hranica expiracie je ostra.

    Rozhoduje sa vzdy v case `now`; posuva sa zaciatok rezervacie.
    """
    starts_at = now + time_to_start
    owner = make_user()
    make_certification(owner, guarded_instrument.category, starts_at + 24 * HOUR)
    request = _pending(
        make_reservation, guarded_instrument, owner, starts_at, starts_at + HOUR
    )

    if expect_expired:
        with pytest.raises(DomainError) as excinfo:
            service.decide_reservation(
                session, reservation_id=request.id, requested_by=supervisor.id,
                approve=True, now=now,
            )
        assert excinfo.value.code is ErrorCode.EXPIRED, case
        session.refresh(request)
        # REQ-15: stav sa ZAPISE, inak by ziadost visela navzdy
        assert request.state is ReservationState.EXPIRED, case
    else:
        result = service.decide_reservation(
            session, reservation_id=request.id, requested_by=supervisor.id,
            approve=True, now=now,
        )
        assert result.state is ReservationState.CONFIRMED, case


def test_approval_rechecks_certification(
    session, guarded_instrument, make_user, make_certification, make_reservation,
    supervisor, now, tomorrow,
):
    """REQ-11: medzi ziadostou a rozhodnutim mohol certifikat vyprsat."""
    owner = make_user()
    starts_at, ends_at = tomorrow
    # certifikat plati len do zaciatku rezervacie => podla BR-04 neplatny
    make_certification(owner, guarded_instrument.category, starts_at)
    request = _pending(make_reservation, guarded_instrument, owner, starts_at, ends_at)

    with pytest.raises(DomainError) as excinfo:
        service.decide_reservation(
            session, reservation_id=request.id, requested_by=supervisor.id,
            approve=True, now=now,
        )

    assert excinfo.value.code is ErrorCode.MISSING_CERTIFICATION
    session.refresh(request)
    # prekazka moze zmiznut - ziadost zostava zivá
    assert request.state is ReservationState.PENDING_APPROVAL


def test_approval_rechecks_instrument(
    session, make_instrument, make_user, make_certification, make_reservation,
    supervisor, now, tomorrow,
):
    """REQ-11: pristroj mohol medzitym ist do servisu."""
    instrument = make_instrument(requires_approval=True, is_active=False)
    owner = make_user()
    starts_at, ends_at = tomorrow
    make_certification(owner, instrument.category, ends_at + 24 * HOUR)
    request = _pending(make_reservation, instrument, owner, starts_at, ends_at)

    with pytest.raises(DomainError) as excinfo:
        service.decide_reservation(
            session, reservation_id=request.id, requested_by=supervisor.id,
            approve=True, now=now,
        )

    assert excinfo.value.code is ErrorCode.INSTRUMENT_INACTIVE
    session.refresh(request)
    assert request.state is ReservationState.PENDING_APPROVAL


def test_approval_rechecks_overlap(
    session, guarded_instrument, make_user, make_certification, make_reservation,
    supervisor, now, tomorrow,
):
    """REQ-11: termin mohol medzitym obsadit niekto iny."""
    owner, other = make_user(), make_user()
    starts_at, ends_at = tomorrow
    make_certification(owner, guarded_instrument.category, ends_at + 24 * HOUR)
    request = _pending(make_reservation, guarded_instrument, owner, starts_at, ends_at)
    make_reservation(
        guarded_instrument, other, starts_at, ends_at,
        state=ReservationState.CONFIRMED,
    )

    with pytest.raises(DomainError) as excinfo:
        service.decide_reservation(
            session, reservation_id=request.id, requested_by=supervisor.id,
            approve=True, now=now,
        )

    assert excinfo.value.code is ErrorCode.OVERLAP
    session.refresh(request)
    assert request.state is ReservationState.PENDING_APPROVAL


@pytest.mark.parametrize(
    "state",
    [
        ReservationState.DRAFT,
        ReservationState.CONFIRMED,
        ReservationState.CANCELLED,
        ReservationState.REJECTED,
    ],
)
def test_decide_requires_pending_state(
    session, guarded_instrument, make_user, make_reservation, supervisor,
    now, tomorrow, state,
):
    owner = make_user()
    starts_at, ends_at = tomorrow
    reservation = make_reservation(
        guarded_instrument, owner, starts_at, ends_at, state=state
    )

    with pytest.raises(DomainError) as excinfo:
        service.decide_reservation(
            session, reservation_id=reservation.id, requested_by=supervisor.id,
            approve=True, now=now,
        )

    assert excinfo.value.code is ErrorCode.INVALID_STATE


# =========================================================================
# OP-04 — zrusenie a nove stavy (REQ-14, nalez N-04)
# =========================================================================


def test_cancel_pending_request_has_no_deadline(
    session, guarded_instrument, make_user, make_reservation, now
):
    """REQ-14: ziadost este nie je prislub, takze lehota nema co chranit."""
    owner = make_user()
    starts_at = now + timedelta(minutes=10)  # menej ako 60 min
    request = _pending(
        make_reservation, guarded_instrument, owner, starts_at, starts_at + HOUR
    )

    result = service.cancel_reservation(
        session, reservation_id=request.id, requested_by=owner.id, now=now
    )

    assert result.changed is True
    assert result.reservation.state is ReservationState.CANCELLED

    availability = service.check_availability(
        session, instrument_id=guarded_instrument.id,
        starts_at=starts_at, ends_at=starts_at + HOUR, now=now,
    )
    assert availability.available is True


def test_cancel_expired_request_expires_it(
    session, guarded_instrument, make_user, make_reservation, now
):
    """Nalez N-04: BR-08 ma prednost pred BR-03.

    Je to jedine miesto v OP-04, kde operacia zmeni stav a napriek tomu
    skonci zamietnutim.
    """
    owner = make_user()
    starts_at = now - HOUR
    request = _pending(
        make_reservation, guarded_instrument, owner, starts_at, starts_at + 2 * HOUR
    )

    with pytest.raises(DomainError) as excinfo:
        service.cancel_reservation(
            session, reservation_id=request.id, requested_by=owner.id, now=now
        )

    assert excinfo.value.code is ErrorCode.EXPIRED
    session.refresh(request)
    assert request.state is ReservationState.EXPIRED


@pytest.mark.parametrize(
    "state", [ReservationState.REJECTED, ReservationState.EXPIRED]
)
def test_cancel_terminal_states_is_rejected(
    session, guarded_instrument, make_user, make_reservation, now, tomorrow, state
):
    """REQ-14: idempotencia zrusenia sa na tieto stavy NEVZTAHUJE."""
    owner = make_user()
    starts_at, ends_at = tomorrow
    reservation = make_reservation(
        guarded_instrument, owner, starts_at, ends_at, state=state
    )

    with pytest.raises(DomainError) as excinfo:
        service.cancel_reservation(
            session, reservation_id=reservation.id, requested_by=owner.id, now=now
        )

    assert excinfo.value.code is ErrorCode.INVALID_STATE
    session.refresh(reservation)
    assert reservation.state is state


def test_cancelled_request_cannot_be_approved(
    session, guarded_instrument, make_user, make_reservation, supervisor, now, tomorrow
):
    """Ziadost zrusena ziadatelom uz nie je na stole."""
    owner = make_user()
    starts_at, ends_at = tomorrow
    request = _pending(make_reservation, guarded_instrument, owner, starts_at, ends_at)
    service.cancel_reservation(
        session, reservation_id=request.id, requested_by=owner.id, now=now
    )

    with pytest.raises(DomainError) as excinfo:
        service.decide_reservation(
            session, reservation_id=request.id, requested_by=supervisor.id,
            approve=True, now=now,
        )

    assert excinfo.value.code is ErrorCode.INVALID_STATE


# =========================================================================
# HTTP — OP-05 cez API
# =========================================================================


def test_http_approve_and_reject(
    client, make_instrument, make_user, make_certification, make_reservation
):
    instrument = make_instrument(requires_approval=True)
    owner = make_user()
    boss = make_user(role=UserRole.SUPERVISOR, name="Veduci")
    starts_at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    make_certification(owner, instrument.category, starts_at + 24 * HOUR)

    approved = make_reservation(
        instrument, owner, starts_at, starts_at + HOUR,
        state=ReservationState.PENDING_APPROVAL,
    )
    rejected = make_reservation(
        instrument, owner, starts_at + 2 * HOUR, starts_at + 3 * HOUR,
        state=ReservationState.PENDING_APPROVAL,
    )

    ok = client.post(
        f"/reservations/{approved.id}/approve", json={"requested_by": str(boss.id)}
    )
    no = client.post(
        f"/reservations/{rejected.id}/reject", json={"requested_by": str(boss.id)}
    )

    assert ok.status_code == 200
    assert ok.json()["state"] == ReservationState.CONFIRMED.value
    assert no.status_code == 200
    assert no.json()["state"] == ReservationState.REJECTED.value


def test_http_student_cannot_approve(
    client, make_instrument, make_user, make_reservation
):
    instrument = make_instrument(requires_approval=True)
    owner = make_user()
    starts_at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    request = make_reservation(
        instrument, owner, starts_at, starts_at + HOUR,
        state=ReservationState.PENDING_APPROVAL,
    )

    response = client.post(
        f"/reservations/{request.id}/approve", json={"requested_by": str(owner.id)}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_http_confirm_on_guarded_instrument_returns_pending(
    client, make_instrument, make_user, make_certification, make_reservation
):
    """REQ-10: pouzivatel musi z odpovede vidiet, ze prístroj este nemá."""
    instrument = make_instrument(requires_approval=True)
    owner = make_user()
    starts_at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    make_certification(owner, instrument.category, starts_at + 24 * HOUR)
    reservation = make_reservation(instrument, owner, starts_at, starts_at + HOUR)

    response = client.post(
        f"/reservations/{reservation.id}/confirm", json={"requested_by": str(owner.id)}
    )

    assert response.status_code == 200
    assert response.json()["state"] == ReservationState.PENDING_APPROVAL.value
