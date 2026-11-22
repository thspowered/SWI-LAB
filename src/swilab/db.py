from collections.abc import Iterator

from sqlalchemy import CheckConstraint, Connection, Engine, create_engine, event, inspect
from sqlalchemy import Enum as SqlEnum
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

    pool_pre_ping (F-04 z C10): po restarte databazy pool vydal spojenie,
    ktore databaza medzitym ukoncila, a prva poziadavka skoncila 500. Ping
    pri vydani spojenia take spojenie nahradi novym. hide_parameters (L-3
    z C10): chyba databazy ide s tracebackom do logu a parametre SQL
    (napr. UUID pouzivatela) v nej byt nemaju.
    """
    engine = create_engine(
        url,
        future=True,
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
        hide_parameters=True,
        **kwargs,
    )
    event.listen(engine, "connect", _session_in_utc)
    return engine


engine = create_db_engine(settings.database_url)

SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency - jedna session na jeden request."""
    with SessionFactory() as session:
        yield session


class SchemaMismatch(RuntimeError):
    """Schema databazy nezodpoveda modelom - aplikacia nesmie nastartovat."""


#: Postup z ADR-02. Databaza nema volume, prestavba data nezachovava.
REBUILD = "docker compose down && docker compose up -d --wait db"


def verify_schema(bind: Engine | Connection) -> list[str]:
    """Rozdiely medzi modelmi a schemou databazy (ADR-02). Prazdny zoznam =
    sedi.

    Migracie projekt nema a create_all() existujucu tabulku nezmeni. Bez
    tejto kontroly by aplikacia nad starou schemou nastartovala a az kazda
    poziadavka by skoncila 500 - presne pri zivej ukazke.

    Porovnava: tabulky, stlpce (typ a nullability), hodnoty enumov
    a pomenovane CHECK obmedzenia z modelov. Typ sa porovnava tak, ze sa
    typ z modelu aj typ z databazy prelozia tym istym dialektom. Co je
    v databaze navyse, rozdielom nie je.
    """
    from swilab.models import Base

    inspector = inspect(bind)
    dialect = bind.dialect
    differences: list[str] = []
    tables = set(inspector.get_table_names())
    enums = {enum["name"]: set(enum["labels"]) for enum in inspector.get_enums()}

    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            differences.append(f"chyba tabulka {table.name}")
            continue
        columns = {column["name"]: column for column in inspector.get_columns(table.name)}
        for column in table.columns:
            where = f"{table.name}.{column.name}"
            actual = columns.get(column.name)
            if actual is None:
                differences.append(f"chyba stlpec {where}")
                continue
            expected_type = column.type.compile(dialect=dialect)
            actual_type = actual["type"].compile(dialect=dialect)
            if expected_type != actual_type:
                differences.append(f"{where}: typ {actual_type}, model {expected_type}")
            if column.nullable != actual["nullable"]:
                differences.append(
                    f"{where}: nullable {actual['nullable']}, model {column.nullable}"
                )
            if isinstance(column.type, SqlEnum):
                missing = set(column.type.enums) - enums.get(column.type.name, set())
                if missing:
                    differences.append(
                        f"enum {column.type.name} nema hodnoty {sorted(missing)}"
                    )
        present = {check["name"] for check in inspector.get_check_constraints(table.name)}
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) and constraint.name not in present:
                differences.append(f"chyba obmedzenie {table.name}.{constraint.name}")

    return differences


def ensure_schema(bind: Engine | Connection) -> None:
    """Pri rozchode schemy zastavi start a povie, co spravit (ADR-02)."""
    differences = verify_schema(bind)
    if differences:
        raise SchemaMismatch(
            "schema databazy nezodpoveda modelom:\n  "
            + "\n  ".join(differences)
            + "\nMigracie projekt nema (ADR-02). Databazu prestav: "
            + REBUILD
            + " - data sa tym zmazu."
        )
