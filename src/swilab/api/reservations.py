"""HTTP vrstva nad styrmi operaciami. Tenka: parsuje vstup, odcita cas,
zavola sluzbu a prelozi vysledok.

Ziadne business pravidlo tu nesmie vzniknut - ak by tu bolo, specifikacia
by sa dala porusit zmenou, ktoru v service vrstve nikto neuvidi.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import AwareDatetime, BaseModel
from sqlalchemy.orm import Session

from swilab import clock
from swilab.db import get_session
from swilab.services import reservations as service

router = APIRouter(tags=["reservations"])

SessionDep = Annotated[Session, Depends(get_session)]


class CreateReservationRequest(BaseModel):
    instrument_id: uuid.UUID
    user_id: uuid.UUID
    # AwareDatetime: naivny cas bez pasma je odmietnuty uz pri validacii.
    # Bez pasma by sa nedalo porovnavat s ulozenymi timestamptz hodnotami
    # (nalez C01 spiku).
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class ActorRequest(BaseModel):
    """Kto operaciu vykonava. V v0.1 je identita doverovana (BR-06,
    TBD-06) - autentifikacia je mimo rozsah predmetu."""

    requested_by: uuid.UUID


class ReservationResponse(BaseModel):
    id: uuid.UUID
    instrument_id: uuid.UUID
    user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    state: str


class CancelResponse(ReservationResponse):
    #: False = rezervacia uz bola zrusena a nic sa nezmenilo (REQ-09).
    changed: bool


class AvailabilityResponse(BaseModel):
    instrument_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    #: REQ-13: CONFIRMED (pristroj JE pridelený), PENDING_APPROVAL (niekto
    #: oň požiadal a čaká sa na vedúceho) alebo INSTRUMENT_INACTIVE.
    reason: str | None = None
    conflicting_reservation_ids: list[uuid.UUID] = []


def _to_response(reservation) -> dict:
    return {
        "id": reservation.id,
        "instrument_id": reservation.instrument_id,
        "user_id": reservation.user_id,
        "starts_at": reservation.starts_at,
        "ends_at": reservation.ends_at,
        "state": reservation.state.value,
    }


@router.post(
    "/reservations",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="OP-01 Create Reservation",
)
def create_reservation(payload: CreateReservationRequest, session: SessionDep):
    reservation = service.create_reservation(
        session,
        instrument_id=payload.instrument_id,
        user_id=payload.user_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        now=clock.now(),
    )
    return _to_response(reservation)


@router.get(
    "/instruments/{instrument_id}/availability",
    response_model=AvailabilityResponse,
    summary="OP-02 Check Availability",
)
def check_availability(
    instrument_id: uuid.UUID,
    session: SessionDep,
    starts_at: Annotated[AwareDatetime, Query()],
    ends_at: Annotated[AwareDatetime, Query()],
):
    result = service.check_availability(
        session,
        instrument_id=instrument_id,
        starts_at=starts_at,
        ends_at=ends_at,
        now=clock.now(),
    )
    return {
        "instrument_id": instrument_id,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "status": "AVAILABLE" if result.available else "UNAVAILABLE",
        "reason": result.reason,
        "conflicting_reservation_ids": result.conflicting_reservation_ids,
    }


@router.post(
    "/reservations/{reservation_id}/confirm",
    response_model=ReservationResponse,
    summary="OP-03 Confirm Reservation",
)
def confirm_reservation(
    reservation_id: uuid.UUID, payload: ActorRequest, session: SessionDep
):
    reservation = service.confirm_reservation(
        session,
        reservation_id=reservation_id,
        requested_by=payload.requested_by,
        now=clock.now(),
    )
    return _to_response(reservation)


@router.post(
    "/reservations/{reservation_id}/cancel",
    response_model=CancelResponse,
    summary="OP-04 Cancel Reservation",
)
def cancel_reservation(
    reservation_id: uuid.UUID, payload: ActorRequest, session: SessionDep
):
    result = service.cancel_reservation(
        session,
        reservation_id=reservation_id,
        requested_by=payload.requested_by,
        now=clock.now(),
    )
    return _to_response(result.reservation) | {"changed": result.changed}


@router.post(
    "/reservations/{reservation_id}/approve",
    response_model=ReservationResponse,
    summary="OP-05 Approve Reservation - schvalenie",
)
def approve_reservation(
    reservation_id: uuid.UUID, payload: ActorRequest, session: SessionDep
):
    reservation = service.decide_reservation(
        session,
        reservation_id=reservation_id,
        requested_by=payload.requested_by,
        approve=True,
        now=clock.now(),
    )
    return _to_response(reservation)


@router.post(
    "/reservations/{reservation_id}/reject",
    response_model=ReservationResponse,
    summary="OP-05 Approve Reservation - zamietnutie",
)
def reject_reservation(
    reservation_id: uuid.UUID, payload: ActorRequest, session: SessionDep
):
    """Druhy vysledok tej istej operacie (OP-05, REQ-12) - preto dva
    endpointy nad jednou funkciou sluzby, nie dve operacie."""
    reservation = service.decide_reservation(
        session,
        reservation_id=reservation_id,
        requested_by=payload.requested_by,
        approve=False,
        now=clock.now(),
    )
    return _to_response(reservation)
