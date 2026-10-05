"""Subeh nad BR-02 a REQ-16 po zavedeni ADR-01 (zamky v sluzbe).

Tieto testy boli v C02 xfail(strict=True) - dokladali medzeru, ktoru mala
zavriet architektura. ADR-01 ju zavrel, takze znacky su prec a testy
chrania rozhodnutie.

POZOR na pascu, do ktorej sme uz raz spadli: test moze "prejst" aj tak, ze
obe vlakna spadnu na chybe infrastruktury a do databazy nezapise nikto.
Presne to sa stalo pri prvom pokuse o tuto zmenu: bariera visela ZA
kontrolou prekryvu, teda UZ VNUTRI kritickej sekcie. Druhe vlakno uviazlo
na zamku, k bariere sa nedostalo, prve vlakno vyprsalo a vysledok bol
['BrokenBarrierError', 'BrokenBarrierError'] so stavom DRAFT - a assert
"najviac jedna blokujuca rezervacia" presiel.

Preto tu platia dve pravidla:
  1. bariera je na VSTUPE do operacie, pred akoukolvek pracou s databazou,
     takze zamok ju nikdy nemoze zablokovat;
  2. kazdy test tvrdi aj to, ze vysledky su BUSINESS vysledky - zoznam
     konkretnych kodov. Zlyhanie infrastruktury ma iny nazov a test padne.
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from swilab.domain.states import BLOCKING_STATES, ReservationState
from swilab.errors import DomainError
from swilab.models import Reservation
from swilab.services import reservations as service

HOUR = timedelta(hours=1)

#: Verejne operacie sluzby, na ktorych vstup vesame bariéru. Ked sa
#: premenuju, musi zlyhat test_instrumentacia_sedi_s_kodom - nie ticho
#: prejst s nulou zapisov.
GUARDED_OPERATIONS = ("confirm_reservation", "cancel_reservation", "decide_reservation")


def test_instrumentacia_sedi_s_kodom():
    """Poistka proti tichemu rozpadu testov nizsie."""
    for name in GUARDED_OPERATIONS:
        assert hasattr(service, name), (
            f"{name} v sluzbe neexistuje - bariera nizsie by sa vesala na nic"
        )


@pytest.fixture
def start_barrier(monkeypatch) -> threading.Barrier:
    """Obe vlakna sa stretnu NA VSTUPE do operacie, pred prvym dotykom DB.

    Nemeni spravanie - iba zarucuje, ze obe operacie su naozaj sucasne
    rozbehnute. Co sa stane dalej, uz rozhoduje architektura, nie test.
    """
    barrier = threading.Barrier(2)

    for name in GUARDED_OPERATIONS:
        original = getattr(service, name)

        def wrapped(*args, _original=original, **kwargs):
            barrier.wait(timeout=5)
            return _original(*args, **kwargs)

        monkeypatch.setattr(service, name, wrapped)
    return barrier


def _run_in_parallel(first, second) -> list[str]:
    """Spusti dve operacie sucasne a vrati ich pozorovatelne vysledky."""
    outcomes: dict[int, str] = {}
    lock = threading.Lock()

    def run(index, operation):
        try:
            operation()
            outcome = "OK"
        except DomainError as exc:
            outcome = exc.code.value
        except Exception as exc:  # zlyhanie infrastruktury je tiez vysledok
            outcome = type(exc).__name__
        with lock:
            outcomes[index] = outcome

    threads = [
        threading.Thread(target=run, args=(i, op))
        for i, op in enumerate((first, second))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    return [outcomes.get(0, "NEDOBEHLO"), outcomes.get(1, "NEDOBEHLO")]


#: Jedno kolo nemusi kolidovat - startovacia bariera zarucuje suvislost,
#: nie presne prekrytie. Viac kol robi z testu spolahlivu poistku: bez
#: zamkov padne na prvom kole, ktore sa trafi do okna.
ROUNDS = 8


@pytest.mark.parametrize(
    ("requires_approval", "target_state", "case"),
    [
        (False, ReservationState.CONFIRMED, "dve subezne potvrdenia"),
        (True, ReservationState.PENDING_APPROVAL, "dve subezne ziadosti"),
    ],
)
def test_concurrent_conflicting_confirmations(
    session, new_session, make_instrument, make_user, make_certification,
    make_reservation, start_barrier, requires_approval, target_state, case,
):
    """REQ-05: pri subeznych konfliktnych potvrdeniach smie blokujuci stav
    dosiahnut najviac jedna rezervacia.

    Zarucuje to ADR-01: obe operacie musia prejst cez zamok nad riadkom
    pristroja, takze kontrolu prekryvu a zapis stavu vykonava v jednom
    okamihu vzdy iba jedna z nich.

    Parametrizacia pokryva obe vetvy REQ-10. Obe volaju OP-03; lisia sa
    iba tym, ci ma pristroj requires_approval.
    """
    instrument = make_instrument(requires_approval=requires_approval)
    first, second = make_user(), make_user()
    instrument_id = instrument.id

    now = datetime.now(UTC)
    horizon = now + (24 + ROUNDS + 1) * HOUR
    for user in (first, second):
        make_certification(user, instrument.category, horizon)

    def confirm(reservation_id, user_id):
        def _run():
            with new_session() as own_session:
                service.confirm_reservation(
                    own_session,
                    reservation_id=reservation_id,
                    requested_by=user_id,
                    now=datetime.now(UTC),
                )
        return _run

    for round_index in range(ROUNDS):
        # Kazde kolo ma vlastny interval, aby sa kola navzajom neblokovali.
        starts_at = now + (24 + round_index) * HOUR
        ends_at = starts_at + HOUR

        left = make_reservation(instrument, first, starts_at, ends_at)
        right = make_reservation(instrument, second, starts_at, ends_at)

        outcomes = _run_in_parallel(
            confirm(left.id, first.id), confirm(right.id, second.id)
        )

        # Bez tohto assertu by test prehltol aj stav, ked obe vlakna spadnu
        # na chybe infrastruktury a do databazy nezapise nikto.
        assert sorted(outcomes) == ["OK", "OVERLAP"], (
            f"kolo {round_index}: ocakavame jedno potvrdenie a jedno "
            f"zamietnutie pre prekryv, dostali sme {outcomes} ({case})"
        )

        with new_session() as verify:
            blocking = list(
                verify.scalars(
                    select(Reservation.state).where(
                        Reservation.instrument_id == instrument_id,
                        Reservation.starts_at == starts_at,
                        Reservation.state.in_(BLOCKING_STATES),
                    )
                )
            )

        assert len(blocking) == 1, (
            f"BR-02 porusene v kole {round_index} ({case}): {len(blocking)} "
            f"prekryvajucich sa rezervacii v blokujucom stave, "
            f"vysledky vlakien: {outcomes}"
        )
        assert blocking[0] is target_state, (
            f"kolo {round_index}: vitaz ma byt v stave {target_state} "
            f"({case}), je v {blocking[0]}"
        )


def test_concurrent_cancel_and_confirm_on_same_reservation(
    session, new_session, make_instrument, make_user, make_certification,
    make_reservation, start_barrier,
):
    """REQ-16: rezervacia opusti stav DRAFT najviac raz.

    Toto NIE JE REQ-05: tam ide o okno medzi kontrolou PREKRYVU a zapisom
    nad dvoma roznymi rezervaciami, tu o okno medzi kontrolou ZDROJOVEHO
    STAVU a zapisom nad JEDNOU - a tyka sa aj zrusenia, kde REQ-05
    nefiguruje vobec. Zarucuje to zamok nad riadkom rezervacie (ADR-01).

    Po serializacii su mozne dve legalne historie:
      confirm, potom cancel  -> obe uspesne, koncovy stav CANCELLED
      cancel, potom confirm  -> confirm zamietnuty (INVALID_STATE)
    Zakazana je ta tretia: zrusenie vrati uspech a rezervacia je pritom
    potvrdena. To je stratený zapis, ktory REQ-16 zakazuje.
    """
    now = datetime.now(UTC)
    starts_at = now + 24 * HOUR
    ends_at = starts_at + HOUR

    instrument = make_instrument()
    owner = make_user()
    make_certification(owner, instrument.category, ends_at + 24 * HOUR)
    reservation = make_reservation(instrument, owner, starts_at, ends_at)
    reservation_id = reservation.id

    def confirm():
        with new_session() as own_session:
            service.confirm_reservation(
                own_session, reservation_id=reservation_id,
                requested_by=owner.id, now=datetime.now(UTC),
            )

    def cancel():
        with new_session() as own_session:
            service.cancel_reservation(
                own_session, reservation_id=reservation_id,
                requested_by=owner.id, now=datetime.now(UTC),
            )

    confirm_outcome, cancel_outcome = _run_in_parallel(confirm, cancel)

    with new_session() as verify:
        final_state = verify.get(Reservation, reservation_id).state

    # Poistka proti vakuovemu prechodu: oba vysledky musia byt business
    # vysledky, nie nazvy vynimiek.
    assert confirm_outcome in ("OK", "INVALID_STATE"), confirm_outcome
    assert cancel_outcome == "OK", cancel_outcome

    # Jadro REQ-16: ak zrusenie vratilo uspech, rezervacia nesmie zostat
    # potvrdena - presne to bol pozorovatelny dosledok strateneho zapisu.
    assert final_state is ReservationState.CANCELLED, (
        f"zrusenie vratilo uspech, ale koncovy stav je {final_state} - "
        f"pouzivatel si mysli, ze zrusil rezervaciu, ktora blokuje pristroj "
        f"(confirm={confirm_outcome}, cancel={cancel_outcome})"
    )

    # Rezervacia opustila DRAFT prave raz: ak confirm uspel, bolo to
    # DRAFT->CONFIRMED->CANCELLED; ak nie, rovno DRAFT->CANCELLED.
    assert final_state is not ReservationState.DRAFT
