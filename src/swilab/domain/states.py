from enum import StrEnum


class ReservationState(StrEnum):
    """Stavy rezervacie (baseline v0.2).

    REJECTED nie je zamietnute potvrdenie - to necha rezervaciu v DRAFT
    (rozhodnutie z C01). REJECTED znamena ROZHODNUTIE CLOVEKA, ktore ma
    zostat v historii vidiet.
    """

    DRAFT = "DRAFT"
    #: v0.2 - ziadost caka na rozhodnutie veduceho a BLOKUJE pristroj.
    PENDING_APPROVAL = "PENDING_APPROVAL"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    #: v0.2 - veduci ziadost zamietol. Koncovy stav, neblokuje.
    REJECTED = "REJECTED"
    #: v0.2 - ziadosti nastal starts_at skor, nez o nej niekto rozhodol.
    EXPIRED = "EXPIRED"


#: BR-02 - stavy, ktore blokuju pristroj. Definovane raz, pouzivaju ich
#: vsetky operacie; PENDING_APPROVAL blokuje iba kym nevyprsi (BR-08).
BLOCKING_STATES = (ReservationState.CONFIRMED, ReservationState.PENDING_APPROVAL)


class InstrumentCategory(StrEnum):
    """Kategoria pristroja. Certifikat sa viaze na kategoriu, nie na
    konkretny pristroj."""

    SPECTROMETER = "SPECTROMETER"
    MICROSCOPE = "MICROSCOPE"
    PRINTER_3D = "PRINTER_3D"
    CENTRIFUGE = "CENTRIFUGE"


class UserRole(StrEnum):
    STUDENT = "STUDENT"
    SUPERVISOR = "SUPERVISOR"

