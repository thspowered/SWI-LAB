from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from swilab.api.reservations import router as reservations_router
from swilab.db import engine
from swilab.errors import DomainError, ErrorCode
from swilab.models import Base

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
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tabulky vytvarame pri starte, aby bola aplikacia spustitelna jednym
    # prikazom. Migracie su tema C03 - v C02 by boli predcasnou
    # architekturou.
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="SWI-LAB", version="0.2.0", lifespan=lifespan)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=STATUS_BY_CODE[exc.code],
        content={"code": exc.code.value, "message": exc.message},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(reservations_router)
