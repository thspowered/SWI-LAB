from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from swilab.config import settings

# READ COMMITTED je predpoklad ADR-01, nie nahoda: dotaz na prekryv musi po
# cakani na zamok vidiet zapis, ktory medzitym commitol sused. Nastavujeme
# ho preto vyslovne a nespoliehame sa na predvolenu hodnotu servera.
engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    isolation_level="READ COMMITTED",
)

SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency - jedna session na jeden request."""
    with SessionFactory() as session:
        yield session
