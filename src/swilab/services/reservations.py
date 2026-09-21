"""Styri zakladne operacie podla docs/specification.md, baseline v0.1.

Jedna funkcia = jedna operacia (OP-01 az OP-04). Poradie kontrol v kazdej
funkcii sedi s diagramom aktivit v docs/diagrams.md; ked sa poradie zmeni
tam, musi sa zmenit aj tu, inak bude aplikacia vracat iny kod chyby, nez
hovori specifikacia.

Struktura je zamerne jednoducha - C02 riesi spravanie, nie architekturu.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from swilab.domain.rules import (
    certification_covers,
    is_interval_valid,
    may_cancel_confirmed,
)
from swilab.domain.states import ReservationState, UserRole
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


def _overlapping_confirmed(
    session: Session,
    instrument_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: uuid.UUID | None = None,
) -> list[Reservation]:
    """BR-02: potvrdene rezervacie toho isteho pristroja, ktore sa s
    intervalom prekryvaju podla BR-01.

    Podmienka prekryvu je tu napisana v SQL, aby sa nenacitaval cely
    kalendar pristroja. Je to ta ista nerovnost ako v rules.intervals_overlap
    a testy ju porovnavaju s nou.
    """
    stmt = select(Reservation).where(
        Reservation.instrument_id == instrument_id,
        Reservation.state == ReservationState.CONFIRMED,
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
) -> AvailabilityResult:
    """OP-02 - zistenie dostupnosti (REQ-03).

    Citacia operacia: nemeni ziadny stav. Interval v minulosti je platny
    dopyt (nalez N-02) - odpoved 'kto pristroj vtedy drzal' je legitimna.
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

    conflicts = _overlapping_confirmed(session, instrument_id, starts_at, ends_at)
    if conflicts:
        return AvailabilityResult(
            available=False,
            reason=ErrorCode.OVERLAP.value,
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
    """OP-03 - potvrdenie rezervacie (REQ-04, REQ-05, REQ-06).

    Jediny prechod, ktory meni obsadenost pristroja, a teda jedine miesto,
    kde sa vynucuju BR-02 a BR-04.

    REQ-05 (pri subehu najviac jedna CONFIRMED) tato implementacia
    NEGARANTUJE - medzi kontrolou prekryvu a zapisom je okno. Je to vedomy
    stav baseline v0.1 a hlavny architektonicky driver pre C03.
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

    conflicts = _overlapping_confirmed(
        session,
        reservation.instrument_id,
        reservation.starts_at,
        reservation.ends_at,
        exclude_id=reservation.id,
    )
    if conflicts:
        raise DomainError(
            ErrorCode.OVERLAP,
            "interval koliduje s inou potvrdenou rezervaciou toho isteho pristroja",
        )

    reservation.state = ReservationState.CONFIRMED
    session.commit()
    return reservation


def cancel_reservation(
    session: Session,
    *,
    reservation_id: uuid.UUID,
    requested_by: uuid.UUID,
    now: datetime,
) -> CancelResult:
    """OP-04 - zrusenie rezervacie (REQ-07, REQ-08, REQ-09).

    CANCELLED nie je zmazanie dat: zaznam zostava, iba prestava blokovat
    pristroj.
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
