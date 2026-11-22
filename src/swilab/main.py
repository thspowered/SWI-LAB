import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from urllib.parse import quote

from sqlalchemy.exc import SQLAlchemyError

from swilab import logs
from swilab.api.reservations import router as reservations_router
from swilab.db import engine, ensure_schema
from swilab.errors import DomainError, ErrorCode
from swilab.models import Base

logs.configure()
log = logging.getLogger(__name__)

#: Preklad zamietnuti na HTTP. Kod v tele odpovede je to podstatne -
#: status je len jeho hruba kategoria.
#:   404 - o tom zazname nemame co tvrdit
#:   403 - ziadatel na to nema pravo (BR-06)
#:   400 - vstup sa neda spracovat
#:   409 - vstup je v poriadku, ale business pravidlo operaciu zamieta
STATUS_BY_CODE = {
    ErrorCode.UNKNOWN_USER: 404,
    ErrorCode.UNKNOWN_INSTRUMENT: 404,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.INVALID_INTERVAL: 400,
    ErrorCode.START_IN_PAST: 400,
    ErrorCode.INSTRUMENT_INACTIVE: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.MISSING_CERTIFICATION: 409,
    ErrorCode.OVERLAP: 409,
    ErrorCode.TOO_LATE: 409,
    ErrorCode.EXPIRED: 409,
    ErrorCode.ALREADY_STARTED: 409,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tabulky vytvarame pri starte, aby bola aplikacia spustitelna jednym
    # prikazom. Migracie projekt nema (ADR-02) a create_all() existujucu
    # tabulku nezmeni - preto hned potom kontrola, ze schema sedi. Nad starou
    # schemou aplikacia radsej nenastartuje, nez by kazda poziadavka skoncila 500.
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    # L-4 (C10): uvicorn pise start a zastavenie bez casu a bez verzie.
    log.info(
        "start: swilab %s, databaza %s, schema sedi s modelmi",
        app.version, _database_target(),
    )
    yield
    log.info("stop: swilab %s", app.version)


def _database_target() -> str:
    """Cielova databaza bez pouzivatela a hesla."""
    url = engine.url
    return f"{url.host}:{url.port or 5432}/{url.database}"


app = FastAPI(title="SWI-LAB", version="0.4.1", lifespan=lifespan)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=STATUS_BY_CODE[exc.code],
        content={"code": exc.code.value, "message": exc.message},
    )


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> PlainTextResponse:
    """Technicka porucha databazy (C10, P3a).

    Predtym ju zachytil az uvicorn: pristupovy riadok a traceback boli dva
    zaznamy bez casu (L-1, L-2). Teraz je to jeden zaznam s casom,
    operaciou, typom problemu a vysledkom; traceback ostava pripojeny, bez
    parametrov SQL (L-3). Odpoved klientovi je ta ista ako predtym.

    SQLAlchemyError, nie iba DBAPIError: vycerpany pool (TimeoutError)
    alebo PendingRollbackError nie su chyby ovladaca, a predsa su to
    poruchy databazy - TimeoutError prave pod zatazou (review C10).
    Ostatne neosetrene vynimky (chyby v kode) handler zamerne nechytaju.

    Klient z 500 nevie, ci sa zapis vykonal. Cesta v zazname hovori, stav
    ktorej rezervacie treba pred opakovanim overit v databaze. Je to vstup
    klienta, preto zakodovana - riadiaci znak sa do logu nedostane.
    """
    orig = getattr(exc, "orig", None) or exc
    detail = (str(orig).splitlines() or [""])[0]
    log.error(
        "%s %s -> 500: chyba databazy %s (%s.%s: %s)",
        request.method, quote(request.url.path), type(exc).__name__,
        type(orig).__module__, type(orig).__qualname__, detail,
        exc_info=exc,
    )
    return PlainTextResponse("Internal Server Error", status_code=500)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(reservations_router)
