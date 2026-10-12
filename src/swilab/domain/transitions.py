"""Politika prechodov rezervacie - rozhodnutie BEZ vykonania (C05, DD-1).

Tri ciste funkcie odpovedaju na otazku "co ma operacia urobit
s rezervaciou v tomto stave a v tomto case". Nic necitaju ani
nezapisuju: nepoznaju session, entity ani hodiny - cas dostanu ako `now`.
Vykonanie rozhodnutia (zamky, zapis, commit) patri sluzbe
v swilab/services/reservations.py.

Rozhodovacie tabulky su v docs/architecture-and-decisions.md, C05.8.
Zavazna je tabulka, nie jej zapis tu: ked sa zmeni tam, musi sa zmenit
aj tu. Po riadkoch ju strazi tests/test_transitions.py.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import assert_never

from swilab.domain.rules import approval_request_is_alive, may_cancel_confirmed
from swilab.domain.states import ReservationState
from swilab.errors import ErrorCode


@dataclass(frozen=True)
class Decision:
    """Co sa ma stat so stavom rezervacie a co dostane volajuci.

    Styri tvary (C05.4):
      target, bez rejection   prechod - operacia uspeje a stav sa zmeni
      nic                     uspech bez zmeny stavu (REQ-09)
      rejection, bez target   zamietnutie, stav sa nemeni
      EXPIRED + EXPIRED       stav sa zmeni A operacia sa zamietne (N-04)

    Blokujuci `target` nie je pokyn "zapis". Znamena "poziadaj o vstup do
    tohto stavu" - este ho cakaju predpoklady alokacie a BR-02.
    """

    target: ReservationState | None = None
    rejection: ErrorCode | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        # Zmenu stavu spolu so zamietnutim pozna specifikacia iba ako
        # vyprsanie. Ine take rozhodnutie sa nesmie dat ani vytvorit.
        changes_and_rejects = self.target is not None and self.rejection is not None
        is_expiry = (self.target, self.rejection) == (
            ReservationState.EXPIRED,
            ErrorCode.EXPIRED,
        )
        if changes_and_rejects and not is_expiry:
            raise ValueError(
                f"rozhodnutie {self.target} + {self.rejection} specifikacia nepozna"
            )


def expiry_decision(
    state: ReservationState, starts_at: datetime, now: datetime
) -> Decision:
    """BR-08, REQ-15: ziadost, ktorej nastal starts_at, je vyprsana.

    Prechod riadi cas, nie operacia - preto ma jedno znenie pre OP-04
    aj OP-05. Tyka sa IBA stavu PENDING_APPROVAL; DRAFT nevyprsi, lebo
    nikoho neblokuje (N-05).
    """
    if state is ReservationState.PENDING_APPROVAL and not approval_request_is_alive(
        starts_at, now
    ):
        return Decision(
            target=ReservationState.EXPIRED,
            rejection=ErrorCode.EXPIRED,
            reason="ziadost o schvalenie vyprsala - jej termin uz zacal",
        )
    return Decision()


def cancel_decision(
    state: ReservationState, starts_at: datetime, now: datetime
) -> Decision:
    """OP-04: co zrusenie urobi s rezervaciou v danom stave (BR-03).

    Kazdy stav ma vlastny riadok a predvoleny neexistuje (DD-2): novy stav
    bez riadku skonci chybou programu, nie ticho zrusenou alebo ticho
    zamietnutou rezervaciou.
    """
    match state:
        case ReservationState.DRAFT:
            # N-03: navrh nikoho neblokuje, lehota by nemala co chranit.
            return Decision(target=ReservationState.CANCELLED)
        case ReservationState.PENDING_APPROVAL:
            # N-04: expiracia (BR-08) ma prednost pred politikou rusenia.
            # 60-minutova lehota tu neplati - ziadost este nie je zavazok.
            expiry = expiry_decision(state, starts_at, now)
            if expiry.target is not None:
                return expiry
            return Decision(target=ReservationState.CANCELLED)
        case ReservationState.CONFIRMED:
            if may_cancel_confirmed(starts_at, now):
                return Decision(target=ReservationState.CANCELLED)
            return Decision(
                rejection=ErrorCode.TOO_LATE,
                reason="potvrdenu rezervaciu mozno zrusit len viac ako "
                "60 minut pred zaciatkom",
            )
        case ReservationState.CANCELLED:
            # REQ-09: opakovane zrusenie je uspech bez zmeny stavu.
            return Decision()
        case ReservationState.REJECTED | ReservationState.EXPIRED:
            # REQ-14: ticho "uspesne" zrusenie by zakrylo, ze rezervaciu
            # zamietol clovek alebo prepadla.
            return Decision(
                rejection=ErrorCode.INVALID_STATE,
                reason=f"rezervaciu v stave {state} nemozno zrusit",
            )
    assert_never(state)


def confirm_decision(
    state: ReservationState,
    starts_at: datetime,
    now: datetime,
    *,
    requires_approval: bool,
) -> Decision:
    """OP-03: ci a do ktoreho blokujuceho stavu smie rezervacia poziadat
    o vstup (REQ-06, REQ-10).

    Stav sa posudzuje PRED casom, ako v diagrame aktivit 3.3: rezervacia,
    ktora nie je DRAFT, dostane INVALID_STATE aj po svojom zaciatku.
    """
    if state is not ReservationState.DRAFT:
        return Decision(
            rejection=ErrorCode.INVALID_STATE,
            reason=f"potvrdit mozno iba rezervaciu v stave DRAFT, nie {state}",
        )
    if now >= starts_at:
        return Decision(
            rejection=ErrorCode.ALREADY_STARTED,
            reason="rezervaciu po jej zaciatku uz nemozno potvrdit",
        )
    # REQ-10: pristroj rozhoduje, ci rezervacia rovno alokuje, alebo sa
    # stava ziadostou. Blokuje pristroj v oboch pripadoch.
    return Decision(
        target=ReservationState.PENDING_APPROVAL
        if requires_approval
        else ReservationState.CONFIRMED
    )
