"""REQ-05 - pri subeznych konfliktnych potvrdeniach smie vzniknut najviac
jedna CONFIRMED rezervacia.

Baseline v0.1 tuto poziadavku NESPLNA a tento test to dokazuje. Nie je to
zabudnuty rozbity test: je to spustitelny dokaz medzery, ktoru ma zavriet
architektura v C03.

Preco barrier: okno medzi kontrolou prekryvu a zapisom je v praxi kratke,
takze nahodne spustene vlakna sa doň zvycajne netrafia (prve stihne
commitnut skor, nez druhe cita - vysledok CONFIRMED + OVERLAP). Barrier
vlakna zosynchronizuje presne v tom okamihu, ked obe uz PRECITALI stav
a ani jedno este nezapisalo. Ziadne spravanie sa tym nemeni, iba sa
spolahlivo vyvola poradie, ktore v prevadzke nastane samo - a prave to
je poradie, na ktorom REQ-05 zalezi.
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from swilab.domain.states import ReservationState
from swilab.errors import DomainError
from swilab.models import Reservation
from swilab.services import reservations as service

HOUR = timedelta(hours=1)


@pytest.mark.xfail(
    reason="REQ-05 nie je v baseline v0.1 zaruceny: kontrola prekryvu a zapis "
    "stavu nie su jeden nedelitelny krok. Architektonicky driver pre C03 - "
    "ked sa medzera zavrie, tento test zacne prechadzat a strict=True na to "
    "upozorni.",
    strict=True,
)
def test_concurrent_conflicting_confirmations(
    session,
    new_session,
    monkeypatch,
    make_instrument,
    make_user,
    make_certification,
    make_reservation,
):
    now = datetime.now(UTC)
    starts_at = now + 24 * HOUR
    ends_at = starts_at + HOUR

    instrument = make_instrument()
    first, second = make_user(), make_user()
    for user in (first, second):
        make_certification(user, instrument.category, ends_at + 24 * HOUR)

    # Dva navrhy na uplne ten isty interval toho isteho pristroja.
    left = make_reservation(instrument, first, starts_at, ends_at)
    right = make_reservation(instrument, second, starts_at, ends_at)
    instrument_id = instrument.id

    read_done = threading.Barrier(2)
    original_lookup = service._overlapping_confirmed

    def lookup_then_wait(*args, **kwargs):
        """Obe vlakna sa tu stretnu az POTOM, co si precitali stav."""
        conflicts = original_lookup(*args, **kwargs)
        read_done.wait(timeout=5)
        return conflicts

    monkeypatch.setattr(service, "_overlapping_confirmed", lookup_then_wait)

    outcomes: list[str] = []
    lock = threading.Lock()

    def confirm(reservation_id, user_id):
        try:
            with new_session() as own_session:
                service.confirm_reservation(
                    own_session,
                    reservation_id=reservation_id,
                    requested_by=user_id,
                    now=datetime.now(UTC),
                )
            outcome = "CONFIRMED"
        except DomainError as exc:
            outcome = exc.code.value
        except Exception as exc:  # zlyhanie z databazy je tiez pozorovatelny vysledok
            outcome = type(exc).__name__
        with lock:
            outcomes.append(outcome)

    threads = [
        threading.Thread(target=confirm, args=(left.id, first.id)),
        threading.Thread(target=confirm, args=(right.id, second.id)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    with new_session() as verify:
        confirmed_count = verify.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(
                Reservation.instrument_id == instrument_id,
                Reservation.state == ReservationState.CONFIRMED,
            )
        )

    assert confirmed_count <= 1, (
        f"BR-02 porusene: {confirmed_count} potvrdene rezervacie na rovnaky "
        f"interval toho isteho pristroja, vysledky vlakien: {outcomes}"
    )
