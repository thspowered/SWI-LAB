"""Pat operacii podla docs/specification.md, baseline v0.2.

Jedna funkcia = jedna operacia (OP-01 az OP-05). Poradie kontrol v kazdej
funkcii sedi s diagramom aktivit v docs/diagrams.md; ked sa poradie zmeni
tam, musi sa zmenit aj tu, inak bude aplikacia vracat iny kod chyby, nez
hovori specifikacia. Od C05 lezi cast toho poradia - stav a cas pri
potvrdeni a zruseni - v swilab/domain/transitions.py.

Struktura podla C03 (docs/architecture-and-decisions.md, G2 a ADR-01).
Logicke prvky architektury su FUNKCIE tohto modulu, nie samostatne subory:

  Reservation Lifecycle   confirm_reservation, cancel_reservation,
                          create_reservation, enter_blocking_state,
                          expire_request_if_dead, _write_expired
                          + zamky a predikat BR-02
  Approval Workflow       decide_reservation
  Availability Query      check_availability

Priradenie funkcia -> prvok a smer zavislosti medzi prvkami strazi
tests/test_architecture_conformance.py.

Vnutro Reservation Lifecycle podla C05 (ten isty dokument, C05.3):
o prechode ROZHODUJE cista politika swilab/domain/transitions.py,
funkcie tu rozhodnutie VYKONAVAJU - zamok, zapis, commit. Lehotu (BR-03)
ani zivost ziadosti pre PRECHOD si sluzba sama nevyhodnocuje. Druha forma
BR-08 ostava v SQL dotaze na prekryv (_overlapping_blocking) - tam nejde
o prechod, ale o to, ci cudzia ziadost blokuje.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from swilab.domain.rules import certification_covers, is_interval_valid
from swilab.domain.states import BLOCKING_STATES, ReservationState, UserRole
from swilab.domain.transitions import (
    cancel_decision,
    confirm_decision,
    expiry_decision,
)
from swilab.errors import DomainError, ErrorCode
from swilab.models import Certification, Instrument, Reservation, User


@dataclass
class AvailabilityResult:
    """Vysledok OP-02. Zoznam kolidujucich rezervacii je sucastou
    pozorovatelneho vysledku - bez neho sa neda odlisit, PRECO je
    pristroj nedostupny."""

    available: bool
    reason: str | None = None
    conflicting_reservation_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class CancelResult:
    """Vysledok OP-04. `changed` odlisuje skutocne zrusenie od
    idempotentneho zopakovania (REQ-09)."""

    reservation: Reservation
    changed: bool


def _require_user(session: Session, user_id: uuid.UUID) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise DomainError(ErrorCode.UNKNOWN_USER, f"pouzivatel {user_id} neexistuje")
    return user


def _require_instrument(session: Session, instrument_id: uuid.UUID) -> Instrument:
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise DomainError(
            ErrorCode.UNKNOWN_INSTRUMENT, f"pristroj {instrument_id} neexistuje"
        )
    return instrument


def _require_authorized(reservation: Reservation, requester: User) -> None:
    """BR-06: nad cudzou rezervaciou smie operovat iba SUPERVISOR."""
    if requester.role is not UserRole.SUPERVISOR and reservation.user_id != requester.id:
        raise DomainError(
            ErrorCode.FORBIDDEN,
            "rezervaciu smie menit iba jej vlastnik alebo veduci laboratoria",
        )


def _require_allocatable(
    session: Session, *, reservation: Reservation, instrument: Instrument
) -> None:
    """Predpoklady alokacie: BR-05, potom BR-04 (C05, DD-5).

    Jedina implementacia pre potvrdenie (OP-03) aj schvalenie (OP-05) -
    REQ-04 aj REQ-11 ziadaju to iste. Poradie je z diagramov aktivit 3.3
    a 3.5; BR-02 prichadza az po nich, v enter_blocking_state().

    Certifikat sa overuje VLASTNIKOVI rezervacie, nie ziadatelovi:
    supervisor moze potvrdit alebo schvalit cudziu rezervaciu, ale nie za
    niekoho, kto skolenie nema.
    """
    if not instrument.is_active:
        raise DomainError(ErrorCode.INSTRUMENT_INACTIVE, "pristroj nie je aktivny")

    certifications = session.scalars(
        select(Certification).where(
            Certification.user_id == reservation.user_id,
            Certification.category == instrument.category,
        )
    ).all()
    if not any(
        certification_covers(c.valid_until, reservation.starts_at)
        for c in certifications
    ):
        raise DomainError(
            ErrorCode.MISSING_CERTIFICATION,
            "vlastnik rezervacie nema platny certifikat na kategoriu pristroja",
        )


def _lock_reservation(session: Session, reservation_id: uuid.UUID) -> Reservation:
    """Nacita rezervaciu so zamkom nad jej riadkom (ADR-01).

    Chrani prechod nad JEDNOU rezervaciou: kontrola zdrojoveho stavu
    a zapis sa tym stavaju nedelitelnym krokom (REQ-16).
    """
    reservation = session.get(Reservation, reservation_id, with_for_update=True)
    if reservation is None:
        raise DomainError(ErrorCode.NOT_FOUND, f"rezervacia {reservation_id} neexistuje")
    return reservation


def _lock_instrument(session: Session, instrument_id: uuid.UUID) -> Instrument:
    """Nacita pristroj so zamkom nad jeho riadkom (ADR-01).

    Zamok nad pristrojom serializuje VSETKY rozhodnutia o BR-02 pre ten
    isty pristroj. Riadok pristroja je tu zastupny zamok - nemeni sa,
    sluzi len ako jediny bod, na ktorom sa stretnu sucasne potvrdenia
    a schvalenia.

    Rezim je FOR NO KEY UPDATE, nie FOR UPDATE: dva taketo zamky sa
    navzajom vylucuju, ale nekoliduju s FOR KEY SHARE, ktory si berie
    kontrola cudzieho kluca. Pri FOR UPDATE by kriticka sekcia blokovala
    aj vytvorenie DRAFT rezervacie toho isteho pristroja (OP-01), hoci
    DRAFT o BR-02 nerozhoduje.

    Vola ho IBA enter_blocking_state() - zamok si berie vlastnik invariantu
    sam, nie jeho volajuci.

    PORADIE ZAMKOV je v celej sluzbe rovnake: najprv rezervacia, potom
    pristroj. Opacne poradie na inom mieste by vyrobilo deadlock.
    """
    instrument = session.get(
        Instrument, instrument_id, with_for_update={"key_share": True}
    )
    if instrument is None:
        raise DomainError(
            ErrorCode.UNKNOWN_INSTRUMENT, f"pristroj {instrument_id} neexistuje"
        )
    return instrument


def _overlapping_blocking(
    session: Session,
    instrument_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
    exclude_id: uuid.UUID | None = None,
) -> list[Reservation]:
    """BR-02: rezervacie toho isteho pristroja v BLOKUJUCOM stave, ktore sa
    s intervalom prekryvaju podla BR-01.

    Blokuju CONFIRMED a PENDING_APPROVAL. Ziadost, ktorej uz nastal
    starts_at, je vyprsana (BR-08) a do blokovania sa nepocita - preto do
    dotazu vstupuje cas.

    Podmienka prekryvu (BR-01) je napisana v SQL, aby sa nenacitaval cely
    kalendar pristroja. Je to JEDINE miesto, kde tato nerovnost v kode
    existuje - do C03 stala aj ako cista funkcia rules.intervals_overlap(),
    ktoru vsak nic nevolalo ani netestovalo (nalez C03 cast A). ADR-01 dal
    invariantu jedineho vlastnika, takze dvojnik bol zruseny.

    Volat sa smie iba z enter_blocking_state() a z check_availability() -
    strazi to tests/test_architecture_conformance.py.
    """
    stmt = select(Reservation).where(
        Reservation.instrument_id == instrument_id,
        Reservation.state.in_(BLOCKING_STATES),
        # vyprsana ziadost neblokuje (BR-08)
        or_(
            Reservation.state == ReservationState.CONFIRMED,
            Reservation.starts_at > now,
        ),
        Reservation.starts_at < ends_at,
        Reservation.ends_at > starts_at,
    )
    if exclude_id is not None:
        stmt = stmt.where(Reservation.id != exclude_id)
    return list(session.scalars(stmt))


def enter_blocking_state(
    session: Session,
    *,
    reservation: Reservation,
    target_state: ReservationState,
    now: datetime,
) -> None:
    """JEDINE miesto, kde rezervacia vstupuje do blokujuceho stavu (ADR-01).

    Vezme zamok nad pristrojom, vyhodnoti BR-02 a zapise cielovy stav.
    Zamok, kontrola a zapis su tu zamerne vedla seba a bez commitu medzi
    nimi - to je obsah REQ-05. Zamok si funkcia berie SAMA: keby bol
    predpokladom volajuceho, stacilo by ho v jednej operacii vynechat
    a kontrola prekryvu by bola iba odporucanie.

    Kriticku sekciu ukonci commit (alebo rollback) volajucej operacie.

    PREDPOKLAD: transakcia bezi v READ COMMITTED (nastavuje db.py). Dotaz
    na prekryv musi po cakani na zamok vidiet zapis, ktory medzitym
    commitol sused; v REPEATABLE READ by videl stary snapshot a presli by
    obe potvrdenia.

    Invariant BR-02 ma jedineho vlastnika. Kazdy dalsi blokujuci stav
    alebo dalsia operacia musi prejst tadialto - strazi to
    tests/test_architecture_conformance.py.
    """
    if target_state not in BLOCKING_STATES:
        raise ValueError(f"{target_state} nie je blokujuci stav")

    _lock_instrument(session, reservation.instrument_id)
    conflicts = _overlapping_blocking(
        session,
        reservation.instrument_id,
        reservation.starts_at,
        reservation.ends_at,
        now,
        exclude_id=reservation.id,
    )
    if conflicts:
        raise DomainError(
            ErrorCode.OVERLAP,
            "interval koliduje s inou rezervaciou toho isteho pristroja "
            "v blokujucom stave",
        )
    reservation.state = target_state


def _write_expired(session: Session, reservation: Reservation) -> None:
    """JEDINE miesto, kde sa zapisuje EXPIRED (BR-08, nalez N-04; C05, DD-4).

    Commit je sucastou zapisu. Vyprsanie je jediny pripad, ked operacia
    zmeni stav a napriek tomu skonci zamietnutim - bez commitu PRED
    vynimkou by zapis zahodil rollback pri zatvoreni session a ziadost by
    ostala visiet v PENDING_APPROVAL.

    CI ziadost vyprsala, tu nerozhoduje nikto - to je expiry_decision().
    """
    reservation.state = ReservationState.EXPIRED
    session.commit()


def expire_request_if_dead(
    session: Session, *, reservation: Reservation, now: datetime
) -> None:
    """Zapis vyprsania pre operaciu, ktora na ziadost smeruje (REQ-15).

    Rozhranie, cez ktore o vyprsanie ziada Approval Workflow (G2). Ci
    ziadost vyprsala, rozhoduje expiry_decision() - to iste znenie pouziva
    aj zrusenie (OP-04). Vyprsana ziadost sa zapise ako EXPIRED a operacia
    sa zamietne; pre ine stavy nez PENDING_APPROVAL sa nedeje nic (N-05).
    """
    decision = expiry_decision(reservation.state, reservation.starts_at, now)
    match decision.target:
        case None:
            return
        case ReservationState.EXPIRED:
            _write_expired(session, reservation)
            raise DomainError(decision.rejection, decision.reason)
        case _:
            raise AssertionError(
                f"vyprsanie nevie vykonat prechod do {decision.target}"
            )


def create_reservation(
    session: Session,
    *,
    instrument_id: uuid.UUID,
    user_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> Reservation:
    """OP-01 - vytvorenie navrhu rezervacie (REQ-01, REQ-02).

    Prekryv (BR-02) ani certifikat (BR-04) sa tu ZAMERNE nevyhodnocuju:
    DRAFT pristroj nealokuje. Obe kontroly patria do confirm_reservation.
    """
    _require_user(session, user_id)
    instrument = _require_instrument(session, instrument_id)

    if not instrument.is_active:
        raise DomainError(ErrorCode.INSTRUMENT_INACTIVE, "pristroj nie je aktivny")
    if not is_interval_valid(starts_at, ends_at):
        raise DomainError(
            ErrorCode.INVALID_INTERVAL, "ends_at musi byt neskor ako starts_at"
        )
    if starts_at <= now:
        raise DomainError(
            ErrorCode.START_IN_PAST, "rezervovat spatne nie je mozne"
        )

    reservation = Reservation(
        instrument_id=instrument_id,
        user_id=user_id,
        starts_at=starts_at,
        ends_at=ends_at,
        state=ReservationState.DRAFT,
        created_at=now,
    )
    session.add(reservation)
    session.commit()
    return reservation


def check_availability(
    session: Session,
    *,
    instrument_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> AvailabilityResult:
    """OP-02 - zistenie dostupnosti (REQ-03, REQ-13).

    Citacia operacia: nemeni ziadny stav - ani vyprsanym ziadostiam,
    ktore do blokovania nezapocitava (BR-08).

    Interval v minulosti je platny dopyt (nalez N-02) - odpoved 'kto
    pristroj vtedy drzal' je legitimna.

    Od v0.2 do vyhodnotenia vstupuje cas: ta ista ziadost je rano
    blokujuca a po starts_at uz nie. Je to cena za expiraciu bez
    planovaca a specifikacia ju priznava.
    """
    instrument = _require_instrument(session, instrument_id)

    if not is_interval_valid(starts_at, ends_at):
        raise DomainError(
            ErrorCode.INVALID_INTERVAL, "ends_at musi byt neskor ako starts_at"
        )

    if not instrument.is_active:
        return AvailabilityResult(
            available=False, reason=ErrorCode.INSTRUMENT_INACTIVE.value
        )

    conflicts = _overlapping_blocking(session, instrument_id, starts_at, ends_at, now)
    if conflicts:
        # REQ-13: dovod musi odlisit "pristroj JE pridelený" od "niekto
        # oň požiadal". CONFIRMED ma prednost - je definitivny.
        states = {r.state for r in conflicts}
        reason = (
            ReservationState.CONFIRMED
            if ReservationState.CONFIRMED in states
            else ReservationState.PENDING_APPROVAL
        )
        return AvailabilityResult(
            available=False,
            reason=reason.value,
            conflicting_reservation_ids=[r.id for r in conflicts],
        )
    return AvailabilityResult(available=True)


def confirm_reservation(
    session: Session,
    *,
    reservation_id: uuid.UUID,
    requested_by: uuid.UUID,
    now: datetime,
) -> Reservation:
    """OP-03 - potvrdenie rezervacie (REQ-04, REQ-05, REQ-06, REQ-10).

    Vysledny stav zavisi od pristroja (REQ-10): bez requires_approval
    CONFIRMED, s nim PENDING_APPROVAL. Pre pouzivatela je to ta ista
    operacia - to, ci za nou nasleduje rozhodnutie veduceho, je vlastnost
    pristroja, nie iny zamer.

    REQ-05 (pri subehu najviac jedna rezervacia v blokujucom stave)
    a REQ-16 (rezervacia opusti DRAFT najviac raz) garantuje ADR-01:
    zamok nad riadkom rezervacie drzi tato funkcia od prveho kroku
    a o BR-02 rozhoduje enter_blocking_state() pod zamkom pristroja.

    Stav, cas aj volbu cieloveho stavu posudzuje confirm_decision();
    tato funkcia rozhodnutie vykona.
    """
    reservation = _lock_reservation(session, reservation_id)

    requester = _require_user(session, requested_by)
    _require_authorized(reservation, requester)

    # Pristroj sa nacita PRED rozhodnutim, lebo politika potrebuje
    # requires_approval (REQ-10). Poradie zamietnuti to nemeni: neznamy
    # pristroj cudzi kluc nepripusti.
    instrument = _require_instrument(session, reservation.instrument_id)
    decision = confirm_decision(
        reservation.state,
        reservation.starts_at,
        now,
        requires_approval=instrument.requires_approval,
    )
    if decision.rejection is not None:
        raise DomainError(decision.rejection, decision.reason)
    if decision.target not in BLOCKING_STATES:
        # Politika vratila ciel, ktory potvrdenie nevie vykonat. Potvrdenie
        # bud poziada o blokujuci stav, alebo sa zamietne - "uspech bez
        # zmeny" nepozna. Kontrola je TU, hned pri rozhodnuti: strazca by
        # to iste odmietol tiez, ale az po predpokladoch alokacie, ktore by
        # chybu programu mohli prekryt beznym zamietnutim.
        raise AssertionError(
            f"potvrdenie nevie vykonat prechod do {decision.target}"
        )

    _require_allocatable(session, reservation=reservation, instrument=instrument)
    enter_blocking_state(
        session, reservation=reservation, target_state=decision.target, now=now
    )
    session.commit()
    return reservation


def cancel_reservation(
    session: Session,
    *,
    reservation_id: uuid.UUID,
    requested_by: uuid.UUID,
    now: datetime,
) -> CancelResult:
    """OP-04 - zrusenie rezervacie (REQ-07, REQ-08, REQ-09, REQ-14).

    CANCELLED nie je zmazanie dat: zaznam zostava, iba prestava blokovat
    pristroj.

    Co zrusenie s rezervaciou v danom stave urobi, rozhoduje
    cancel_decision() - vratane toho, ze expiracia (BR-08) ma prednost
    pred politikou rusenia (BR-03, nalez N-04). Tato funkcia rozhodnutie
    vykona.
    """
    reservation = _lock_reservation(session, reservation_id)

    requester = _require_user(session, requested_by)
    # Opravnenie sa overuje PRED rozhodnutim, teda aj pri uz zrusenej
    # rezervacii: idempotencia (REQ-09) neznamena, ze operaciu smie
    # zopakovat ktokolvek.
    _require_authorized(reservation, requester)

    decision = cancel_decision(reservation.state, reservation.starts_at, now)
    match decision.target:
        case None:
            pass
        case ReservationState.CANCELLED:
            reservation.state = ReservationState.CANCELLED
            session.commit()
        case ReservationState.EXPIRED:
            _write_expired(session, reservation)
        case _:
            # Politika vratila ciel, ktory zrusenie nevie vykonat. Tiche
            # pokracovanie by vratilo uspech bez zapisu.
            raise AssertionError(
                f"zrusenie nevie vykonat prechod do {decision.target}"
            )

    if decision.rejection is not None:
        raise DomainError(decision.rejection, decision.reason)
    return CancelResult(reservation=reservation, changed=decision.target is not None)


def decide_reservation(
    session: Session,
    *,
    reservation_id: uuid.UUID,
    requested_by: uuid.UUID,
    approve: bool,
    now: datetime,
) -> Reservation:
    """OP-05 - rozhodnutie o ziadosti (REQ-11, REQ-12, REQ-15). Nove v v0.2.

    Schvalenie a zamietnutie su jedna operacia s dvoma vysledkami: je to
    jeden ciel aktera - rozhodnut o ziadosti - s rovnakymi predpokladmi.

    Kontroly BR-04, BR-05 a BR-02 sa tu opakuju, hoci prebehli uz pri
    podani ziadosti. Medzitym ubehol lubovolne dlhy cas: zaznam certifikatu
    sa mohol zmenit, pristroj ist do servisu, termin obsadit niekto iny.

    Tato funkcia je Approval Workflow z G2: vlastni rozhodnutie veduceho
    (BR-07, zapis REJECTED). Schvalenie pristroj ALOKUJE, preto ho sama
    nezapisuje - prechod si ziada od enter_blocking_state(), vlastnika
    BR-02 (ADR-01). Rovnako vyprsanie nezapisuje sama, ale cez
    expire_request_if_dead(). Zamok nad riadkom rezervacie chrani subeh
    so zrusenim a s druhym rozhodnutim (REQ-16).
    """
    reservation = _lock_reservation(session, reservation_id)

    # BR-07: schvaluje iba SUPERVISOR a nikdy nie vlastnu ziadost. Bez
    # druhej podmienky by schvalovanie pre veduceho neexistovalo a pravidlo
    # by platilo len pre studentov.
    #
    # Opravnenie sa overuje PRED stavom, rovnako ako v OP-03 a OP-04: inak
    # by sa neopravneny pouzivatel z kodu chyby dozvedel stav cudzej
    # rezervacie.
    approver = _require_user(session, requested_by)
    if approver.role is not UserRole.SUPERVISOR:
        raise DomainError(
            ErrorCode.FORBIDDEN, "o ziadosti smie rozhodnut iba veduci laboratoria"
        )
    if reservation.user_id == approver.id:
        raise DomainError(
            ErrorCode.FORBIDDEN, "veduci nesmie rozhodovat o vlastnej ziadosti"
        )

    if reservation.state is not ReservationState.PENDING_APPROVAL:
        raise DomainError(
            ErrorCode.INVALID_STATE,
            f"rozhodnut mozno iba o ziadosti v stave PENDING_APPROVAL, "
            f"nie {reservation.state}",
        )

    # REQ-15: vyprsana ziadost sa nedostane k rozhodnutiu, ale stav sa
    # zapise - inak by zostala navzdy visiet v PENDING_APPROVAL.
    expire_request_if_dead(session, reservation=reservation, now=now)

    # REQ-12: zamietnut sa da aj ziadost, ktora by sa uz schvalit nedala
    # (pristroj v servise, expirovany certifikat). Inak by taka ziadost
    # visela az do vyprsania.
    if not approve:
        reservation.state = ReservationState.REJECTED
        session.commit()
        return reservation

    instrument = _require_instrument(session, reservation.instrument_id)
    _require_allocatable(session, reservation=reservation, instrument=instrument)

    enter_blocking_state(
        session,
        reservation=reservation,
        target_state=ReservationState.CONFIRMED,
        now=now,
    )
    session.commit()
    return reservation
