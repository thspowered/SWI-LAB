"""Review I-02: ta ista aplikacia (swilab.main:app), iba iny pool spojeni
alebo threadpool. Pouziva ju scripts/i02_review.py, pokus B.

Kod aplikacie sa nemeni - premenne prostredia upravia iba parametre:
  REVIEW_POOL=70      -> pool_size=70, max_overflow=0 (predvolene 5 + 10)
  REVIEW_THREADS=200  -> limit threadpoolu anyio (predvolene 40)

Spustenie: uvicorn i02_review_app:app (adresar scripts/ v PYTHONPATH).
"""

import os

import anyio.to_thread
import sqlalchemy

POOL = os.environ.get("REVIEW_POOL")
THREADS = os.environ.get("REVIEW_THREADS")

_create_engine = sqlalchemy.create_engine


def _create_engine_with_pool(*args, **kwargs):
    if POOL:
        kwargs.update(pool_size=int(POOL), max_overflow=0)
    return _create_engine(*args, **kwargs)


# swilab.db robi `from sqlalchemy import create_engine` pri importe - preto
# nahradit PRED importom aplikacie
sqlalchemy.create_engine = _create_engine_with_pool

from swilab.db import engine  # noqa: E402
from swilab.main import app as _application  # noqa: E402

print(f"review: pool {engine.pool.size()} + overflow {engine.pool._max_overflow}", flush=True)


class _WithThreadLimit:
    """Limit threadpoolu sa da nastavit az v bezicej slucke udalosti -
    pri prvom volani (lifespan)."""

    def __init__(self, inner):
        self.inner = inner
        self.ready = False

    async def __call__(self, scope, receive, send):
        if not self.ready:
            limiter = anyio.to_thread.current_default_thread_limiter()
            if THREADS:
                limiter.total_tokens = int(THREADS)
            print(f"review: threadpool {limiter.total_tokens}", flush=True)
            self.ready = True
        await self.inner(scope, receive, send)


app = _WithThreadLimit(_application)
