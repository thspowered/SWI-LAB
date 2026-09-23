"""Pat operacii podla docs/specification.md, baseline v0.2.

Jedna funkcia = jedna operacia (OP-01 az OP-05). Poradie kontrol v kazdej
funkcii sedi s diagramom aktivit v docs/diagrams.md; ked sa poradie zmeni
tam, musi sa zmenit aj tu, inak bude aplikacia vracat iny kod chyby, nez
hovori specifikacia.

Struktura je zamerne jednoducha - C02 riesi spravanie, nie architekturu.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from swilab.domain.rules import (
    approval_request_is_alive,
    certification_covers,
    is_interval_valid,
    may_cancel_confirmed,
)
from swilab.domain.states import BLOCKING_STATES, ReservationState, UserRole
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

    Podmienka prekryvu je tu napisana v SQL, aby sa nenacitaval cely
    kalendar pristroja. Je to ta ista nerovnost ako v rules.intervals_overlap
    a testy ju porovnavaju s nou.
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

    REQ-05 (pri subehu najviac jedna rezervacia v blokujucom stave) tato
    implementacia NEGARANTUJE - medzi kontrolou prekryvu a zapisom je
    okno. Je to vedomy stav a hlavny architektonicky driver pre C03.
    """
    reservation = session.get(Reservation, reservation_id)
    if reservation is None:
        raise DomainError(ErrorCode.NOT_FOUND, f"rezervacia {reservation_id} neexistuje")

    requester = _require_user(session, requested_by)
    _require_authorized(reservation, requester)

    if reservation.state is not ReservationState.DRAFT:
        raise DomainError(
            ErrorCode.INVALID_STATE,
            f"potvrdit mozno iba rezervaciu v stave DRAFT, nie {reservation.state}",
        )
    if now >= reservation.starts_at:
        raise DomainError(
            ErrorCode.START_IN_PAST, "rezervaciu po jej zaciatku uz nemozno potvrdit"
        )

    instrument = _require_instrument(session, reservation.instrument_id)
    if not instrument.is_active:
        raise DomainError(ErrorCode.INSTRUMENT_INACTIVE, "pristroj nie je aktivny")

    # BR-04: certifikat sa overuje vlastnikovi rezervacie, nie ziadatelovi -
    # supervisor moze potvrdit cudziu rezervaciu, ale nie za niekoho, kto
    # skolenie nema.
    certification = session.scalars(
        select(Certification).where(
            Certification.user_id == reservation.user_id,
            Certification.category == instrument.category,
        )
    ).all()
    if not any(certification_covers(c.valid_until, reservation.starts_at) for c in certification):
        raise DomainError(
            ErrorCode.MISSING_CERTIFICATION,
            "pouzivatel nema platny certifikat na kategoriu pristroja",
        )

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

    # REQ-10: pristroj rozhoduje, ci rezervacia rovno alokuje, alebo sa
    # stava ziadostou. Blokuje pristroj v oboch pripadoch.
    reservation.state = (
        ReservationState.PENDING_APPROVAL
        if instrument.requires_approval
        else ReservationState.CONFIRMED
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

    Poradie kontrol pre PENDING_APPROVAL je dolezite: expiracia (BR-08)
    ma prednost pred politikou rusenia (BR-03). Obe pravidla davali pre
    ziadost po starts_at opacnu odpoved - nalez N-04.
    """
    reservation = session.get(Reservation, reservation_id)
    if reservation is None:
        raise DomainError(ErrorCode.NOT_FOUND, f"rezervacia {reservation_id} neexistuje")

    requester = _require_user(session, requested_by)
    _require_authorized(reservation, requester)

    # REQ-09: opakovane zrusenie je uspech bez zmeny stavu. Kontrola
    # opravnenia prebieha aj tu - idempotencia neznamena, ze operaciu smie
    # zopakovat ktokolvek.
    if reservation.state is ReservationState.CANCELLED:
        return CancelResult(reservation=reservation, changed=False)

    # REQ-14: REJECTED a EXPIRED su ine koncove stavy. Ticho "uspesne"
    # zrusenie by zakrylo, ze rezervaciu zamietol clovek alebo prepadla.
    if reservation.state in (ReservationState.REJECTED, ReservationState.EXPIRED):
        raise DomainError(
            ErrorCode.INVALID_STATE,
            f"rezervaciu v stave {reservation.state} nemozno zrusit",
        )

    # Nalez N-04: vyprsana ziadost sa v KAZDEJ operacii, ktora ju cita,
    # povazuje za EXPIRED. Stav sa zapise a zrusenie sa zamietne.
    if reservation.state is ReservationState.PENDING_APPROVAL and not (
        approval_request_is_alive(reservation.starts_at, now)
    ):
        reservation.state = ReservationState.EXPIRED
        session.commit()
        raise DomainError(
            ErrorCode.EXPIRED,
            "ziadost o schvalenie vyprsala - jej termin uz zacal",
        )

    if reservation.state is ReservationState.CONFIRMED and not may_cancel_confirmed(
        reservation.starts_at, now
    ):
        raise DomainError(
            ErrorCode.TOO_LATE,
            "potvrdenu rezervaciu mozno zrusit len viac ako 60 minut pred zaciatkom",
        )

    reservation.state = ReservationState.CANCELLED
    session.commit()
    return CancelResult(reservation=reservation, changed=True)


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
    podani ziadosti. Medzitym ubehol lubovolne dlhy cas: certifikat mohol
    vyprsat, pristroj ist do servisu, termin obsadit niekto iny.
    """
    reservation = session.get(Reservation, reservation_id)
    if reservation is None:
        raise DomainError(ErrorCode.NOT_FOUND, f"rezervacia {reservation_id} neexistuje")

    if reservation.state is not ReservationState.PENDING_APPROVAL:
        raise DomainError(
            ErrorCode.INVALID_STATE,
            f"rozhodnut mozno iba o ziadosti v stave PENDING_APPROVAL, "
            f"nie {reservation.state}",
        )

    # BR-07: schvaluje iba SUPERVISOR a nikdy nie vlastnu ziadost. Bez
    # druhej podmienky by schvalovanie pre veduceho neexistovalo a pravidlo
    # by platilo len pre studentov.
    approver = _require_user(session, requested_by)
    if approver.role is not UserRole.SUPERVISOR:
        raise DomainError(
            ErrorCode.FORBIDDEN, "o ziadosti smie rozhodnut iba veduci laboratoria"
        )
    if reservation.user_id == approver.id:
        raise DomainError(
            ErrorCode.FORBIDDEN, "veduci nesmie rozhodovat o vlastnej ziadosti"
        )

    # REQ-15: vyprsana ziadost sa nedostane k rozhodnutiu, ale stav sa
    # zapise - inak by zostala navzdy visiet v PENDING_APPROVAL.
    if not approval_request_is_alive(reservation.starts_at, now):
        reservation.state = ReservationState.EXPIRED
        session.commit()
        raise DomainError(
            ErrorCode.EXPIRED, "ziadost o schvalenie vyprsala - jej termin uz zacal"
        )

    # REQ-12: zamietnut sa da aj ziadost, ktora by sa uz schvalit nedala
    # (pristroj v servise, expirovany certifikat). Inak by taka ziadost
    # visela az do vyprsania.
    if not approve:
        reservation.state = ReservationState.REJECTED
        session.commit()
        return reservation

    instrument = _require_instrument(session, reservation.instrument_id)
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
            "interval medzitym obsadila ina rezervacia toho isteho pristroja",
        )

    reservation.state = ReservationState.CONFIRMED
    session.commit()
    return reservation
