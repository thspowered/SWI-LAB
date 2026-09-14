from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from swilab.config import settings

engine = create_engine(settings.database_url, echo=False, future=True)

SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency - jedna session na jeden request."""
    with SessionFactory() as session:
        yield session
