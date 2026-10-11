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

from swilab.domain.states import BLOCKING_STATES, ReservationState, UserRole
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


def test_engine_aplikacie_bezi_v_read_committed():
    """Predpoklad ADR-01. V REPEATABLE READ by dotaz na prekryv po cakani
    na zamok videl stary snapshot a presli by obe potvrdenia - overene
    pokusom, viz docs/evidence-and-evolution.md."""
    from swilab.db import engine as app_engine

    with app_engine.connect() as connection:
        assert connection.get_isolation_level() == "READ COMMITTED"


def _call(new_session, operation: str, **kwargs):
    """Operacia sluzby vo vlastnej session - ako samostatny request."""

    def _run():
        with new_session() as own_session:
            getattr(service, operation)(own_session, now=datetime.now(UTC), **kwargs)

    return _run


def _state_of(new_session, reservation_id) -> ReservationState:
    with new_session() as verify:
        return verify.get(Reservation, reservation_id).state


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


@pytest.mark.parametrize("requires_approval", [False, True])
def test_concurrent_cancel_and_confirm_on_same_reservation(
    session, new_session, make_instrument, make_user, make_certification,
    make_reservation, start_barrier, requires_approval,
):
    """REQ-16: zrusenie nikdy nevrati uspech nad rezervaciou, ktora potom
    blokuje pristroj.

    Toto NIE JE REQ-05: tam ide o okno medzi kontrolou PREKRYVU a zapisom
    nad dvoma roznymi rezervaciami, tu o okno medzi kontrolou ZDROJOVEHO
    STAVU a zapisom nad JEDNOU - a tyka sa aj zrusenia, kde REQ-05
    nefiguruje vobec. Zarucuje to zamok nad riadkom rezervacie (ADR-01).

    Termin je daleko (24 h), takze podla OP-04 su po serializacii mozne
    dve legalne historie:
      confirm, potom cancel  -> obe uspesne, koncovy stav CANCELLED
                                (zrusenie sa posudi podla noveho stavu)
      cancel, potom confirm  -> confirm zamietnuty (INVALID_STATE)
    Zakazana je ta tretia: zrusenie vrati uspech a rezervacia pritom
    blokuje pristroj. Variant, v ktorom smie uspiet NAJVIAC JEDNA
    operacia, je test_at_most_one_operation_leaves_the_source_state.
    """
    now = datetime.now(UTC)
    starts_at = now + 24 * HOUR
    ends_at = starts_at + HOUR
    owner = make_user()

    for round_index in range(ROUNDS):
        # Novy pristroj v kazdom kole: vitaz minuleho kola by inak blokoval.
        instrument = make_instrument(requires_approval=requires_approval)
        if round_index == 0:
            make_certification(owner, instrument.category, ends_at + 24 * HOUR)
        reservation_id = make_reservation(instrument, owner, starts_at, ends_at).id

        confirm_outcome, cancel_outcome = _run_in_parallel(
            _call(new_session, "confirm_reservation",
                  reservation_id=reservation_id, requested_by=owner.id),
            _call(new_session, "cancel_reservation",
                  reservation_id=reservation_id, requested_by=owner.id),
        )
        final_state = _state_of(new_session, reservation_id)

        # Poistka proti vakuovemu prechodu: oba vysledky musia byt business
        # vysledky, nie nazvy vynimiek.
        assert confirm_outcome in ("OK", "INVALID_STATE"), (round_index, confirm_outcome)
        assert cancel_outcome == "OK", (round_index, cancel_outcome)

        # Jadro REQ-16: ak zrusenie vratilo uspech, rezervacia nesmie
        # blokovat - presne to bol pozorovatelny dosledok strateneho zapisu.
        assert final_state is ReservationState.CANCELLED, (
            f"kolo {round_index}: zrusenie vratilo uspech, ale koncovy stav je "
            f"{final_state} - pouzivatel si mysli, ze zrusil rezervaciu, ktora "
            f"blokuje pristroj (confirm={confirm_outcome}, cancel={cancel_outcome})"
        )


@pytest.mark.parametrize(
    ("rival", "source_state"),
    [
        ("confirm", ReservationState.DRAFT),
        ("approve", ReservationState.PENDING_APPROVAL),
    ],
)
def test_at_most_one_operation_leaves_the_source_state(
    session, new_session, make_instrument, make_user, make_certification,
    make_reservation, start_barrier, rival, source_state,
):
    """REQ-16 doslova: ked sa zrusenie stretne s potvrdenim alebo so
    schvalenim tej istej rezervacie, uspeje NAJVIAC JEDNO a druhe skonci
    zamietnutim podla uz zmeneneho zdrojoveho stavu.

    Termin je o 30 minut, teda vnutri lehoty BR-03. Preto plati "najviac
    jedno" pri OBOCH poradiach:
      vyhra confirm/approve -> CONFIRMED, cancel zamietnuty TOO_LATE
      vyhra cancel          -> CANCELLED, confirm/approve INVALID_STATE
    Strateny zapis sa prejavi ako ['OK', 'OK'] pri kazdom poradi zapisov,
    takze tento test ho zachyti aj tam, kde ho test vyssie neuvidi
    (confirm prepisany zrusenim konci v CANCELLED rovnako ako legalna
    historia).

    Vetva "approve" doklada sucasne druhu polovicu REQ-16 - subeh
    zrusenia so SCHVALENIM (OP-05), ktory v C02 test nemal.
    """
    now = datetime.now(UTC)
    starts_at = now + timedelta(minutes=30)
    ends_at = starts_at + HOUR
    owner = make_user()
    supervisor = make_user(role=UserRole.SUPERVISOR)

    for round_index in range(ROUNDS):
        instrument = make_instrument(
            requires_approval=source_state is ReservationState.PENDING_APPROVAL
        )
        if round_index == 0:
            make_certification(owner, instrument.category, ends_at + 24 * HOUR)
        reservation_id = make_reservation(
            instrument, owner, starts_at, ends_at, state=source_state
        ).id

        if rival == "confirm":
            rival_call = _call(new_session, "confirm_reservation",
                               reservation_id=reservation_id, requested_by=owner.id)
        else:
            rival_call = _call(new_session, "decide_reservation",
                               reservation_id=reservation_id,
                               requested_by=supervisor.id, approve=True)

        rival_outcome, cancel_outcome = _run_in_parallel(
            rival_call,
            _call(new_session, "cancel_reservation",
                  reservation_id=reservation_id, requested_by=owner.id),
        )
        final_state = _state_of(new_session, reservation_id)

        legal = {
            ("OK", "TOO_LATE"): ReservationState.CONFIRMED,
            ("INVALID_STATE", "OK"): ReservationState.CANCELLED,
        }
        assert (rival_outcome, cancel_outcome) in legal, (
            f"kolo {round_index}: {rival}={rival_outcome}, cancel={cancel_outcome} - "
            f"REQ-16 dovoluje uspech najviac jednej operacie"
        )
        assert final_state is legal[(rival_outcome, cancel_outcome)], (
            f"kolo {round_index}: vysledky ({rival}={rival_outcome}, "
            f"cancel={cancel_outcome}) nesedia s koncovym stavom {final_state}"
        )


def test_concurrent_approve_and_reject_on_same_request(
    session, new_session, make_instrument, make_user, make_certification,
    make_reservation, start_barrier,
):
    """REQ-16 nad OP-05: dvaja veduci rozhodnu o tej istej ziadosti naraz
    opacne. Ziadost opusti PENDING_APPROVAL prave raz - druhe rozhodnutie
    vidi uz zmeneny stav a je zamietnute. Bez zamku nad riadkom rezervacie
    by uspeli obe a platil by ten zapis, ktory prisiel neskor.
    """
    now = datetime.now(UTC)
    starts_at = now + 24 * HOUR
    ends_at = starts_at + HOUR
    owner = make_user()
    approving = make_user(role=UserRole.SUPERVISOR)
    rejecting = make_user(role=UserRole.SUPERVISOR)

    for round_index in range(ROUNDS):
        instrument = make_instrument(requires_approval=True)
        if round_index == 0:
            make_certification(owner, instrument.category, ends_at + 24 * HOUR)
        reservation_id = make_reservation(
            instrument, owner, starts_at, ends_at,
            state=ReservationState.PENDING_APPROVAL,
        ).id

        approve_outcome, reject_outcome = _run_in_parallel(
            _call(new_session, "decide_reservation", reservation_id=reservation_id,
                  requested_by=approving.id, approve=True),
            _call(new_session, "decide_reservation", reservation_id=reservation_id,
                  requested_by=rejecting.id, approve=False),
        )
        final_state = _state_of(new_session, reservation_id)

        legal = {
            ("OK", "INVALID_STATE"): ReservationState.CONFIRMED,
            ("INVALID_STATE", "OK"): ReservationState.REJECTED,
        }
        assert (approve_outcome, reject_outcome) in legal, (
            f"kolo {round_index}: approve={approve_outcome}, reject={reject_outcome} - "
            f"o ziadosti smie rozhodnut prave jedno z dvoch rozhodnuti"
        )
        assert final_state is legal[(approve_outcome, reject_outcome)], (
            f"kolo {round_index}: koncovy stav {final_state} nesedi s vitazom "
            f"(approve={approve_outcome}, reject={reject_outcome})"
        )


def test_concurrent_approvals_of_colliding_requests(
    session, new_session, make_instrument, make_user, make_certification,
    make_reservation, start_barrier,
):
    """Priklad overenia OP-05: dve subezne schvalenia kolidujucich ziadosti
    -> najviac jedna CONFIRMED (REQ-05), ostatne zostanu PENDING_APPROVAL.

    Dve zive prekryvajuce sa ziadosti cez operacie vzniknut nemozu - BR-02
    druhu zamietne uz pri potvrdeni. Zakladame ich preto priamo v databaze,
    rovnako ako test_approval_rechecks_overlap. Kazde schvalenie vidi tu
    druhu ziadost ako blokujucu, takze test NEROZLISI kod so zamkom od kodu
    bez neho; doklada, ze priklad zo specifikacie plati a ze dve schvalenia
    nad tym istym pristrojom skoncia business vysledkom, nie zaseknutim.
    """
    now = datetime.now(UTC)
    starts_at = now + 24 * HOUR
    ends_at = starts_at + HOUR
    first_owner, second_owner = make_user(), make_user()
    supervisor = make_user(role=UserRole.SUPERVISOR)

    for round_index in range(ROUNDS):
        instrument = make_instrument(requires_approval=True)
        if round_index == 0:
            for owner in (first_owner, second_owner):
                make_certification(owner, instrument.category, ends_at + 24 * HOUR)
        ids = [
            make_reservation(
                instrument, owner, starts_at, ends_at,
                state=ReservationState.PENDING_APPROVAL,
            ).id
            for owner in (first_owner, second_owner)
        ]

        outcomes = _run_in_parallel(*(
            _call(new_session, "decide_reservation", reservation_id=reservation_id,
                  requested_by=supervisor.id, approve=True)
            for reservation_id in ids
        ))
        states = [_state_of(new_session, reservation_id) for reservation_id in ids]

        assert set(outcomes) <= {"OK", "OVERLAP"}, (round_index, outcomes)
        assert states.count(ReservationState.CONFIRMED) <= 1, (round_index, states)
        for outcome, state in zip(outcomes, states):
            expected = (
                ReservationState.CONFIRMED
                if outcome == "OK"
                else ReservationState.PENDING_APPROVAL
            )
            assert state is expected, (round_index, outcomes, states)
