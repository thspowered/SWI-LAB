"""Unit testy Transition Policy - rozhodovanie o prechodoch rezervacie.

Odvodene z NAVRHU, nie z implementacie: kazdy parameter je riadok
rozhodovacej tabulky z docs/architecture-and-decisions.md, C05.8, alebo
veta z kontraktu v C05.4. Ked sa zmeni tabulka v navrhu, ma zlyhat test.

UROVEN: unit. Politika je cista funkcia (DD-1), takze tieto testy
nepotrebuju session ani databazu - prejdu aj so zastavenym Postgresom.
Presne preto je rozhodnutie oddelene od vykonania: hranice 60:00
a starts_at sa daju overit na mikrosekundu bez jedineho zapisu.
"""

from datetime import UTC, datetime, timedelta

import pytest

from swilab.domain import rules
from swilab.domain.states import BLOCKING_STATES, ReservationState
from swilab.domain.transitions import (
    Decision,
    cancel_decision,
    confirm_decision,
    expiry_decision,
)
from swilab.errors import ErrorCode

S = ReservationState

ZERO = timedelta(0)
HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)
#: Najmensi krok, ktory timestamptz rozlisi (C01 spike) - hranica o jeden
#: tik je najprisnejsia hranica, aku system vobec moze stretnut.
TICK = timedelta(microseconds=1)

#: Pevny okamih. Politika cas NECITA, dostava ho - testy preto nesmu
#: zavisiet od skutocnych hodin.
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)

#: Casy okolo oboch hranic (starts_at a 60:00) aj daleko od nich.
LEADS = [-HOUR, ZERO, TICK, 30 * MINUTE, HOUR, HOUR + TICK, 24 * HOUR]


def _starts(lead: timedelta) -> datetime:
    """starts_at rezervacie, ktora zacina o `lead` od NOW (zaporne = uz zacala)."""
    return NOW + lead


# =========================================================================
# Rozhodovacie tabulky (C05.8)
# =========================================================================


@pytest.mark.parametrize(
    ("state", "lead", "target", "rejection", "case"),
    [
        (S.DRAFT, HOUR, S.CANCELLED, None, "REQ-07"),
        (S.DRAFT, -HOUR, S.CANCELLED, None, "N-03: DRAFT sa rusi aj po zaciatku"),
        (S.PENDING_APPROVAL, 10 * MINUTE, S.CANCELLED, None, "REQ-14: bez lehoty"),
        (S.PENDING_APPROVAL, TICK, S.CANCELLED, None, "BR-08: tik pred zaciatkom zije"),
        (S.PENDING_APPROVAL, ZERO, S.EXPIRED, ErrorCode.EXPIRED, "BR-08: v okamihu starts_at uz nie"),
        (S.PENDING_APPROVAL, -HOUR, S.EXPIRED, ErrorCode.EXPIRED, "N-04"),
        (S.CONFIRMED, HOUR + TICK, S.CANCELLED, None, "BR-03: tik nad 60:00"),
        (S.CONFIRMED, HOUR, None, ErrorCode.TOO_LATE, "BR-03: presne 60:00, hranica je ostra"),
        (S.CONFIRMED, 30 * MINUTE, None, ErrorCode.TOO_LATE, "BR-03"),
        (S.CONFIRMED, -3 * HOUR, None, ErrorCode.TOO_LATE, "BR-03: po skonceni rezervacie"),
        (S.CANCELLED, HOUR, None, None, "REQ-09"),
        (S.CANCELLED, -HOUR, None, None, "REQ-09 neplati iba pred zaciatkom"),
        (S.REJECTED, HOUR, None, ErrorCode.INVALID_STATE, "REQ-14"),
        (S.REJECTED, -HOUR, None, ErrorCode.INVALID_STATE, "REQ-14 plati aj po termine"),
        (S.EXPIRED, HOUR, None, ErrorCode.INVALID_STATE, "REQ-14"),
        (S.EXPIRED, -HOUR, None, ErrorCode.INVALID_STATE, "zapisany EXPIRED nie je 'prave vyprsala'"),
    ],
)
def test_cancel_decision_table(state, lead, target, rejection, case):
    decision = cancel_decision(state, _starts(lead), NOW)

    assert (decision.target, decision.rejection) == (target, rejection), case


@pytest.mark.parametrize("state", [S.DRAFT, S.CANCELLED, S.REJECTED, S.EXPIRED])
def test_cancel_decision_depends_on_time_only_where_the_table_says_so(state):
    """BR-03: casovu podmienku maju iba CONFIRMED a PENDING_APPROVAL. Pre
    ostatne stavy je rozhodnutie rovnake pred zaciatkom, na hranici aj po
    nom - tabulka pri nich ziadnu podmienku nema."""
    decisions = {
        (decision.target, decision.rejection)
        for decision in (
            cancel_decision(state, _starts(lead), NOW) for lead in LEADS
        )
    }

    assert len(decisions) == 1, decisions


@pytest.mark.parametrize(
    ("lead", "requires_approval", "target", "rejection", "case"),
    [
        (HOUR, False, S.CONFIRMED, None, "REQ-10: bezny pristroj"),
        (HOUR, True, S.PENDING_APPROVAL, None, "REQ-10: pristroj so schvalovanim"),
        (TICK, False, S.CONFIRMED, None, "REQ-06: tik pred zaciatkom este ano"),
        (ZERO, False, None, ErrorCode.ALREADY_STARTED, "REQ-06: v okamihu starts_at uz nie"),
        (ZERO, True, None, ErrorCode.ALREADY_STARTED, "REQ-06 nezavisi od pristroja"),
        (-HOUR, False, None, ErrorCode.ALREADY_STARTED, "REQ-06"),
    ],
)
def test_confirm_decision_table_for_draft(lead, requires_approval, target, rejection, case):
    decision = confirm_decision(
        S.DRAFT, _starts(lead), NOW, requires_approval=requires_approval
    )

    assert (decision.target, decision.rejection) == (target, rejection), case


@pytest.mark.parametrize("state", [s for s in S if s is not S.DRAFT])
@pytest.mark.parametrize("lead", [HOUR, -HOUR])
@pytest.mark.parametrize("requires_approval", [False, True])
def test_confirm_decision_judges_state_before_time(state, lead, requires_approval):
    """DD-7: rezervacia, ktora nie je DRAFT, dostane INVALID_STATE aj po
    svojom zaciatku - nie ALREADY_STARTED. Poradie je z diagramu aktivit 3.3."""
    decision = confirm_decision(
        state, _starts(lead), NOW, requires_approval=requires_approval
    )

    assert (decision.target, decision.rejection) == (None, ErrorCode.INVALID_STATE)


@pytest.mark.parametrize(
    ("lead", "expires"), [(TICK, False), (ZERO, True), (-HOUR, True)]
)
def test_expiry_decision_boundary(lead, expires):
    """BR-08: hranica je ostra - v okamihu starts_at uz ziadost neplati."""
    decision = expiry_decision(S.PENDING_APPROVAL, _starts(lead), NOW)

    expected = (S.EXPIRED, ErrorCode.EXPIRED) if expires else (None, None)
    assert (decision.target, decision.rejection) == expected


@pytest.mark.parametrize("state", [s for s in S if s is not S.PENDING_APPROVAL])
def test_only_a_pending_request_expires(state):
    """N-05: DRAFT nevyprsi. Ostatne stavy tiez nie - ani po zaciatku."""
    decision = expiry_decision(state, _starts(-HOUR), NOW)

    assert (decision.target, decision.rejection) == (None, None)


@pytest.mark.parametrize("lead", [TICK, ZERO, -HOUR])
def test_cancel_takes_its_expiry_row_from_expiry_decision(lead):
    """C05.8: vyprsanie ma JEDNO znenie pre OP-04 aj OP-05. Keby zrusenie
    malo vlastnu kopiu podmienky, mohli by sa na hranici rozist."""
    expiry = expiry_decision(S.PENDING_APPROVAL, _starts(lead), NOW)
    cancel = cancel_decision(S.PENDING_APPROVAL, _starts(lead), NOW)

    if expiry.target is S.EXPIRED:
        assert cancel == expiry
    else:
        assert cancel.target is S.CANCELLED


# =========================================================================
# Kontrakt Transition Policy (C05.4)
# =========================================================================

#: Co smie ktora funkcia vratit: (ciele, zamietnutia).
CONTRACT = {
    "confirm": (
        {None, S.CONFIRMED, S.PENDING_APPROVAL},
        {None, ErrorCode.INVALID_STATE, ErrorCode.ALREADY_STARTED},
    ),
    "cancel": (
        {None, S.CANCELLED, S.EXPIRED},
        {None, ErrorCode.INVALID_STATE, ErrorCode.TOO_LATE, ErrorCode.EXPIRED},
    ),
    "expiry": ({None, S.EXPIRED}, {None, ErrorCode.EXPIRED}),
}


def _every_decision():
    for state in S:
        for lead in LEADS:
            starts_at = _starts(lead)
            yield "cancel", state, lead, cancel_decision(state, starts_at, NOW)
            yield "expiry", state, lead, expiry_decision(state, starts_at, NOW)
            for requires_approval in (False, True):
                yield "confirm", state, lead, confirm_decision(
                    state, starts_at, NOW, requires_approval=requires_approval
                )


def test_policy_is_total_and_returns_only_shapes_from_the_contract():
    """DD-2 a kontrakt: pre KAZDY stav a cas vrati politika rozhodnutie,
    a to iba v tvare, ktory kontrakt pozna."""
    checked = 0
    for name, state, lead, decision in _every_decision():
        where = f"{name}_decision({state}, lead={lead})"
        targets, rejections = CONTRACT[name]

        assert isinstance(decision, Decision), where
        assert decision.target in targets, where
        assert decision.rejection in rejections, where
        if decision.rejection is not None:
            assert decision.reason, f"{where}: zamietnutie bez dovodu"
            # Zmena stavu spolu so zamietnutim existuje iba ako vyprsanie.
            assert decision.target in (None, S.EXPIRED), where
        if name == "confirm":
            # Potvrdenie "uspech bez zmeny" nepozna: vzdy prave jedno.
            assert (decision.target is None) != (decision.rejection is None), where
        checked += 1

    assert checked == len(S) * len(LEADS) * 4, "kontrola presla naprazdno"


def test_only_confirm_ever_asks_for_a_blocking_state():
    """DD-3: zrusenie ani vyprsanie nikdy nevratia blokujuci ciel - do
    blokujuceho stavu vedie iba potvrdenie, a to cez straznika invariantu."""
    for name, state, lead, decision in _every_decision():
        if name != "confirm":
            assert decision.target not in BLOCKING_STATES, f"{name} {state} {lead}"


def test_cancel_table_has_no_default_row():
    """DD-2: stav bez riadku v tabulke je hlasna chyba programu. Tichy
    predvoleny vysledok by novy stav bud zrusil, alebo zamietol - a ani
    jedno by nikto nerozhodol."""
    with pytest.raises(AssertionError):
        cancel_decision("COMPLETED", _starts(HOUR), NOW)


@pytest.mark.parametrize(
    ("target", "rejection", "reason", "case"),
    [
        (S.CANCELLED, ErrorCode.TOO_LATE, "x", "zmena + zamietnutie mimo vyprsania"),
        (S.CONFIRMED, ErrorCode.EXPIRED, "x", "zmena + zamietnutie mimo vyprsania"),
        (S.EXPIRED, ErrorCode.INVALID_STATE, "x", "vyprsanie s inym kodom"),
        (S.EXPIRED, None, None, "EXPIRED zapisany bez zamietnutia"),
        (None, ErrorCode.EXPIRED, "x", "kod EXPIRED bez zapisu stavu"),
        (None, ErrorCode.TOO_LATE, None, "zamietnutie bez dovodu"),
        (S.CANCELLED, None, "x", "dovod bez zamietnutia"),
        ("EXPIRED", "EXPIRED", "x", "retazce namiesto clenov enumu"),
    ],
)
def test_decision_refuses_a_shape_the_specification_does_not_know(
    target, rejection, reason, case
):
    """Kontrakt: 'stav sa zmeni A operacia sa zamietne' smie existovat iba
    ako EXPIRED / EXPIRED (N-04, REQ-15) - a vyprsanie zasa iba ako oboje
    naraz. Zamietnutie vzdy nesie dovod. Ine rozhodnutie sa neda ani
    vytvorit."""
    with pytest.raises(ValueError):
        Decision(target=target, rejection=rejection, reason=reason)


@pytest.mark.parametrize(
    ("target", "rejection", "reason"),
    [
        (S.CANCELLED, None, None),
        (None, None, None),
        (None, ErrorCode.TOO_LATE, "neskoro"),
        (S.EXPIRED, ErrorCode.EXPIRED, "ziadost vyprsala"),
    ],
)
def test_decision_accepts_exactly_the_four_shapes(target, rejection, reason):
    """Protivaha k testu vyssie: styri tvary z kontraktu sa vytvorit daju."""
    decision = Decision(target=target, rejection=rejection, reason=reason)

    assert (decision.target, decision.rejection) == (target, rejection)


@pytest.mark.parametrize("state", list(S))
@pytest.mark.parametrize("lead", [HOUR, -HOUR])
def test_all_three_tables_read_the_state_the_same_way(state, lead):
    """ReservationState je StrEnum a `match` v cancel_decision porovnava
    hodnotou. Keby expiry_decision alebo confirm_decision porovnavali
    identitou, ten isty vstup by v dvoch tabulkach znamenal dva rozne
    stavy - a zrusenie by pre neho vynechalo riadok vyprsania."""
    starts_at = _starts(lead)

    assert cancel_decision(state.value, starts_at, NOW) == cancel_decision(
        state, starts_at, NOW
    )
    assert expiry_decision(state.value, starts_at, NOW) == expiry_decision(
        state, starts_at, NOW
    )
    assert confirm_decision(
        state.value, starts_at, NOW, requires_approval=False
    ) == confirm_decision(state, starts_at, NOW, requires_approval=False)


# =========================================================================
# Ciste predikaty, o ktore sa politika a predpoklady opieraju
# =========================================================================


LEAD_TIME = rules.CANCELLATION_LEAD_TIME


@pytest.mark.parametrize(
    ("predicate", "args", "expected", "case"),
    [
        # is_interval_valid(starts_at, ends_at)
        (rules.is_interval_valid, (NOW, NOW), False, "BR-01: prazdny interval neplati"),
        (rules.is_interval_valid, (NOW, NOW + TICK), True, "BR-01: o tik dlhsi uz plati"),
        # certification_covers(valid_until, starts_at)
        (rules.certification_covers, (NOW, NOW), False, "BR-04: valid_until == starts_at"),
        (rules.certification_covers, (NOW + TICK, NOW), True, "BR-04: o tik dlhsie plati"),
        # approval_request_is_alive(starts_at, now)
        (rules.approval_request_is_alive, (NOW, NOW), False, "BR-08: v okamihu starts_at"),
        (rules.approval_request_is_alive, (NOW + TICK, NOW), True, "BR-08: tik pred nim"),
        # may_cancel_confirmed(starts_at, now)
        (rules.may_cancel_confirmed, (NOW + LEAD_TIME, NOW), False, "BR-03: presne 60:00"),
        (rules.may_cancel_confirmed, (NOW + LEAD_TIME + TICK, NOW), True, "BR-03: tik nad"),
    ],
)
def test_rule_boundaries_are_strict(predicate, args, expected, case):
    """Vsetky styri hranice su ostre a polootvorene rovnakym smerom - aby
    v systeme nezili dva vyznamy slova 'do' (BR-04)."""
    assert predicate(*args) is expected, case
