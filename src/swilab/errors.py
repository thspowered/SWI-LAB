from enum import StrEnum


class ErrorCode(StrEnum):
    """Kody zamietnuti. Su to presne tie kody, ktore pouzivaju diagramy
    aktivit v docs/diagrams.md - kto cita diagram, najde ich v kode."""

    UNKNOWN_USER = "UNKNOWN_USER"
    UNKNOWN_INSTRUMENT = "UNKNOWN_INSTRUMENT"
    INSTRUMENT_INACTIVE = "INSTRUMENT_INACTIVE"
    INVALID_INTERVAL = "INVALID_INTERVAL"
    START_IN_PAST = "START_IN_PAST"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    INVALID_STATE = "INVALID_STATE"
    MISSING_CERTIFICATION = "MISSING_CERTIFICATION"
    OVERLAP = "OVERLAP"
    TOO_LATE = "TOO_LATE"
    #: v0.2 - ziadosti o schvalenie nastal starts_at (BR-08).
    EXPIRED = "EXPIRED"
    #: Rezervacia uz zacala, takze ju uz nemozno potvrdit (REQ-06).
    #: Odlisne od START_IN_PAST: ten hovori o CHYBNOM VSTUPE pri vytvarani,
    #: toto o ulozenom stave a case, takze ma iny HTTP status.
    ALREADY_STARTED = "ALREADY_STARTED"


class DomainError(Exception):
    """Zamietnutie operacie podla specifikacie baseline v0.1.

    Nie je to chyba behu - je to jeden z pozorovatelnych vysledkov
    popisanych v docs/specification.md, sekcia 'Alternativne / chybove
    vysledky'.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
