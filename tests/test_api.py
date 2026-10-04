"""Zakladne operacie cez HTTP - dokaz, ze aplikacia je skutocne spustitelna
a ze spravanie z docs/specification.md je pozorovatelne zvonku.

Pre kazdu operaciu jeden uspesny a jeden negativny / hranicny priklad.
"""

from datetime import UTC, datetime, timedelta

from swilab.domain.states import ReservationState

HOUR = timedelta(hours=1)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _future(hours: int = 24) -> tuple[datetime, datetime]:
    starts_at = (datetime.now(UTC) + timedelta(hours=hours)).replace(microsecond=0)
    return starts_at, starts_at + HOUR


# --- OP-01 ---------------------------------------------------------------


def test_http_create_reservation(client, make_instrument, make_user):
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = _future()

    response = client.post(
        "/reservations",
        json={
            "instrument_id": str(instrument.id),
            "user_id": str(user.id),
            "starts_at": _iso(starts_at),
            "ends_at": _iso(ends_at),
        },
    )

    assert response.status_code == 201
    assert response.json()["state"] == ReservationState.DRAFT.value


def test_http_create_rejects_unknown_instrument(client, make_user):
    import uuid

    user = make_user()
    starts_at, ends_at = _future()

    response = client.post(
        "/reservations",
        json={
            "instrument_id": str(uuid.uuid4()),
            "user_id": str(user.id),
            "starts_at": _iso(starts_at),
            "ends_at": _iso(ends_at),
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "UNKNOWN_INSTRUMENT"


# --- OP-02 ---------------------------------------------------------------


def test_http_availability_free_instrument(client, make_instrument):
    instrument = make_instrument()
    starts_at, ends_at = _future()

    response = client.get(
        f"/instruments/{instrument.id}/availability",
        params={"starts_at": _iso(starts_at), "ends_at": _iso(ends_at)},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "AVAILABLE"


def test_http_availability_reports_conflict(
    client, make_instrument, make_user, make_reservation
):
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = _future()
    blocking = make_reservation(
        instrument, user, starts_at, ends_at, state=ReservationState.CONFIRMED
    )

    response = client.get(
        f"/instruments/{instrument.id}/availability",
        params={
            "starts_at": _iso(starts_at + timedelta(minutes=30)),
            "ends_at": _iso(ends_at + timedelta(minutes=30)),
        },
    )

    body = response.json()
    assert body["status"] == "UNAVAILABLE"
    assert body["conflicting_reservation_ids"] == [str(blocking.id)]


# --- OP-03 ---------------------------------------------------------------


def test_http_confirm_reservation(
    client, make_instrument, make_user, make_certification, make_reservation
):
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = _future()
    make_certification(user, instrument.category, ends_at + 24 * HOUR)
    reservation = make_reservation(instrument, user, starts_at, ends_at)

    response = client.post(
        f"/reservations/{reservation.id}/confirm",
        json={"requested_by": str(user.id)},
    )

    assert response.status_code == 200
    assert response.json()["state"] == ReservationState.CONFIRMED.value


def test_http_confirm_without_certification_is_rejected(
    client, make_instrument, make_user, make_reservation
):
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = _future()
    reservation = make_reservation(instrument, user, starts_at, ends_at)

    response = client.post(
        f"/reservations/{reservation.id}/confirm",
        json={"requested_by": str(user.id)},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "MISSING_CERTIFICATION"


# --- OP-04 ---------------------------------------------------------------


def test_http_cancel_reservation_is_idempotent(
    client, make_instrument, make_user, make_reservation
):
    instrument, user = make_instrument(), make_user()
    starts_at, ends_at = _future()
    reservation = make_reservation(instrument, user, starts_at, ends_at)

    first = client.post(
        f"/reservations/{reservation.id}/cancel", json={"requested_by": str(user.id)}
    )
    second = client.post(
        f"/reservations/{reservation.id}/cancel", json={"requested_by": str(user.id)}
    )

    assert first.status_code == 200
    assert first.json()["changed"] is True
    assert second.status_code == 200
    assert second.json()["changed"] is False
    assert second.json()["state"] == ReservationState.CANCELLED.value


def test_http_cancel_confirmed_too_late_is_rejected(
    client, make_instrument, make_user, make_reservation
):
    """BR-03: 30 minut pred zaciatkom sa potvrdena rezervacia uz zrusit neda."""
    instrument, user = make_instrument(), make_user()
    starts_at = (datetime.now(UTC) + timedelta(minutes=30)).replace(microsecond=0)
    reservation = make_reservation(
        instrument, user, starts_at, starts_at + HOUR, state=ReservationState.CONFIRMED
    )

    response = client.post(
        f"/reservations/{reservation.id}/cancel", json={"requested_by": str(user.id)}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "TOO_LATE"
