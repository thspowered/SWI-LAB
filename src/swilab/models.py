import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from swilab.domain.states import InstrumentCategory, ReservationState, UserRole


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Instrument(Base):
    """Resource - to, co sa rezervuje."""

    __tablename__ = "instruments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[InstrumentCategory] = mapped_column(
        Enum(InstrumentCategory, name="instrument_category"), nullable=False
    )
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    reservations: Mapped[list["Reservation"]] = relationship(back_populates="instrument")


class User(Base):
    """Kto rezervaciu vytvara."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), nullable=False, default=UserRole.STUDENT
    )

    certifications: Mapped[list["Certification"]] = relationship(back_populates="user")


class Certification(Base):
    """Opravnenie pouzivatela na kategoriu pristroja.

    Toto je pojem, ktory si vyziada nase vlastne business rule.
    """

    __tablename__ = "certifications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    category: Mapped[InstrumentCategory] = mapped_column(
        Enum(InstrumentCategory, name="instrument_category"), nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped[User] = relationship(back_populates="certifications")


class Reservation(Base):
    """Rezervacia pristroja na casovy interval [starts_at, ends_at).

    Interval je polootvoreny, takze 10:00-11:00 a 11:00-12:00 nekoliduju.
    """

    __tablename__ = "reservations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("instruments.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[ReservationState] = mapped_column(
        Enum(ReservationState, name="reservation_state"),
        nullable=False,
        default=ReservationState.DRAFT,
    )
    # Default je tu zamerne: bez neho by kazde vytvorenie rezervacie cez API
    # muselo created_at nastavovat rucne a prve zabudnutie by skoncilo
    # chybou az v databaze.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    instrument: Mapped[Instrument] = relationship(back_populates="reservations")
    user: Mapped[User] = relationship()
