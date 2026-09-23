"""Subeh: co baseline v0.2 NEGARANTUJE a preco to vieme.

Tieto testy su spustitelne dokazy medzier, ktore ma zavriet architektura
v C03. Preto su xfail(strict=True): ked sa medzera zavrie, test zacne
prechadzat a pytest si vyziada odstranenie znacky.

POZOR na pascu, do ktorej sme uz raz spadli: xfail(strict=True) prehltne
KAZDE zlyhanie vratane preklepu v nazve funkcie. Test potom nedokazuje
nic a sada je zelena. Preto:
  - instrumentacia sa instaluje vo FIXTURE (zlyhanie tam je ERROR, nie
    xfail),
  - test_instrumentacia_sedi_s_kodom strazi nazvy, na ktore sa bariery
    vesaju.
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

#: Interne funkcie sluzby, do ktorych vesame bariery. Ked sa premenuju,
#: musi zlyhat test_instrumentacia_sedi_s_kodom - nie ticho xfail.
OVERLAP_LOOKUP = "_overlapping_blocking"
AUTHORIZATION_CHECK = "_require_authorized"


def test_instrumentacia_sedi_s_kodom():
    """Poistka proti tichemu rozpadu testov nizsie."""
    assert hasattr(service, OVERLAP_LOOKUP), (
        f"{OVERLAP_LOOKUP} v sluzbe neexistuje - bariery nizsie by sa "
        f"vesali na nic a xfail by to zakryl"
    )
    assert hasattr(service, AUTHORIZATION_CHECK), (
        f"{AUTHORIZATION_CHECK} v sluzbe neexistuje"
    )


def _barrier_fixture(monkeypatch, function_name: str) -> threading.Barrier:
    """Vsunie bariéru ZA volanie danej funkcie.

    Bariera nemeni spravanie - iba spolahlivo vyvola poradie, ktore
    v prevadzke nastane samo: obe vlakna uz CITALI stav a ani jedno
    este nezapisalo.
    """
    original = getattr(service, function_name)
    barrier = threading.Barrier(2)

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(service, function_name, wrapped)
    return barrier


@pytest.fixture
def barrier_after_overlap_lookup(monkeypatch):
    """Obe vlakna sa stretnu az po kontrole prekryvu (REQ-05)."""
    return _barrier_fixture(monkeypatch, OVERLAP_LOOKUP)


@pytest.fixture
def barrier_after_authorization(monkeypatch):
    """Obe vlakna sa stretnu tesne po nacitani rezervacie a overeni
    opravnenia - teda pred kontrolou zdrojoveho stavu."""
    return _barrier_fixture(monkeypatch, AUTHORIZATION_CHECK)


def _run_in_parallel(first, second) -> list[str]:
    """Spusti dve operacie sucasne a vrati ich pozorovatelne vysledky."""
    outcomes: list[str] = []
    lock = threading.Lock()

    def run(operation):
        try:
            operation()
            outcome = "OK"
        except DomainError as exc:
            outcome = exc.code.value
        except Exception as exc:  # zlyhanie z databazy je tiez vysledok
            outcome = type(exc).__name__
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=run, args=(op,)) for op in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return outcomes


@pytest.mark.parametrize(
    ("requires_approval", "case"),
    [
        (False, "dve subezne potvrdenia -> najviac jedna CONFIRMED"),
        (True, "dve subezne ziadosti -> najviac jedna PENDING_APPROVAL"),
    ],
)
@pytest.mark.xfail(
    reason="REQ-05 nie je v baseline v0.2 zaruceny: kontrola prekryvu a zapis "
    "stavu nie su jeden nedelitelny krok. Architektonicky driver pre C03.",
    strict=True,
)
def test_concurrent_conflicting_confirmations(
    session, new_session, make_instrument, make_user, make_certification,
    make_reservation, barrier_after_overlap_lookup, requires_approval, case,
):
    """REQ-05: pri subeznych konfliktnych potvrdeniach smie blokujuci stav
    dosiahnut najviac jedna rezervacia.

    Parametrizacia pokryva obe vetvy REQ-10: bez schvalovania vzniknu dve
    CONFIRMED, so schvalovanim dve PENDING_APPROVAL. BR-02 je porusene
    v oboch pripadoch.
    """
    now = datetime.now(UTC)
    starts_at = now + 24 * HOUR
    ends_at = starts_at + HOUR

    instrument = make_instrument(requires_approval=requires_approval)
    first, second = make_user(), make_user()
    for user in (first, second):
        make_certification(user, instrument.category, ends_at + 24 * HOUR)

    left = make_reservation(instrument, first, starts_at, ends_at)
    right = make_reservation(instrument, second, starts_at, ends_at)
    instrument_id = instrument.id

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

    outcomes = _run_in_parallel(
        confirm(left.id, first.id), confirm(right.id, second.id)
    )

    with new_session() as verify:
        blocking_count = verify.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(
                Reservation.instrument_id == instrument_id,
                Reservation.state.in_(BLOCKING_STATES),
            )
        )

    assert blocking_count <= 1, (
        f"BR-02 porusene ({case}): {blocking_count} prekryvajucich sa rezervacii "
        f"v blokujucom stave, vysledky vlakien: {outcomes}"
    )


@pytest.mark.xfail(
    reason="Straten zapis nad tou istou rezervaciou: kontrola zdrojoveho stavu "
    "a zapis nie su nedelitelny krok, takze cancel aj confirm mozu obe "
    "vratit uspech. Specifikacia (OP-04, 'Subeh s potvrdenim') tvrdi opak. "
    "Architektonicky driver pre C03.",
    strict=True,
)
def test_concurrent_cancel_and_confirm_on_same_reservation(
    session, new_session, make_instrument, make_user, make_certification,
    make_reservation, barrier_after_authorization,
):
    """OP-04: 'Rezervacia opusti stav DRAFT najviac raz.'

    Toto NIE JE REQ-05: tam ide o okno medzi kontrolou PREKRYVU a zapisom
    a tyka sa dvoch roznych rezervacii. Tu ide o okno medzi kontrolou
    ZDROJOVEHO STAVU a zapisom nad JEDNOU rezervaciou - a tyka sa aj
    zrusenia, kde REQ-05 nefiguruje vobec.

    Pozorovatelny dosledok: pouzivatel dostane na zrusenie uspech, ale
    rezervacia je potvrdena a blokuje pristroj.
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

    outcomes = _run_in_parallel(confirm, cancel)

    with new_session() as verify:
        final_state = verify.get(Reservation, reservation_id).state

    succeeded = [o for o in outcomes if o == "OK"]
    assert len(succeeded) <= 1, (
        f"rezervacia opustila DRAFT dvakrat: obe operacie vratili uspech "
        f"{outcomes}, konecny stav je {final_state} - pouzivatel si mysli, "
        f"ze zrusil rezervaciu, ktora blokuje pristroj"
    )
    assert final_state is not ReservationState.CONFIRMED or "OK" not in outcomes[1:]
