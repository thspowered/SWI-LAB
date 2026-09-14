from enum import StrEnum


class ReservationState(StrEnum):
    """Stavy rezervacie.

    Zamietnute potvrdenie nechava rezervaciu v stave DRAFT s chybou -
    pouzivatel si doplni certifikat a skusi potvrdit znova. Preto tu
    nie je stav REJECTED.
    """

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


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
