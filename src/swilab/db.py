from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from swilab.config import settings


def _session_in_utc(dbapi_connection, _connection_record) -> None:
    """Pasmo session nastavi na UTC pri kazdom novom spojeni.

    Nalez F-01 z C06: Postgres vracia timestamptz v pasme SESSION, nie vzdy
    v UTC. Bez tohto by ho spojenie zdedilo od servera (postgresql.conf,
    ALTER DATABASE) alebo od klienta (PGTZ) a API by vratilo +01:00
    namiesto Z. Parameter `options=-c timezone=UTC` na to nestaci - PGTZ ho
    prebije, overene. SET az po pripojeni prebije vsetko.
    """
    with dbapi_connection.cursor() as cursor:
        cursor.execute("SET TIME ZONE 'UTC'")
    # SET v nedokoncenej transakcii by zrusil rollback, ktory pool robi pri
    # vrateni spojenia - preto commit hned tu.
    dbapi_connection.commit()


def create_db_engine(url, **kwargs) -> Engine:
    """Jediny sposob, ako vznika engine - pre aplikaciu aj pre testy, aby sa
    predpoklady spravania medzi nimi nemohli rozist.

    READ COMMITTED je predpoklad ADR-01, nie nahoda: dotaz na prekryv musi
    po cakani na zamok vidiet zapis, ktory medzitym commitol sused. Pasmo
    session UTC (F-01) zarucuje, ze casy z databazy maju vzdy offset 0.
    Oboje nastavujeme vyslovne a nespoliehame sa na predvolby servera.
    """
    engine = create_engine(url, future=True, isolation_level="READ COMMITTED", **kwargs)
    event.listen(engine, "connect", _session_in_utc)
    return engine


engine = create_db_engine(settings.database_url)

SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency - jedna session na jeden request."""
    with SessionFactory() as session:
        yield session
