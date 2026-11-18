"""Kontrola schemy pri starte aplikacie (ADR-02, C09).

Migracie projekt nema (ADR-02). create_all() existujucu tabulku nezmeni,
takze aplikacia nad starou schemou by nastartovala a az kazda poziadavka
by skoncila 500. Namiesto toho aplikacia pri starte porovna schemu
s modelmi a pri rozchode odmietne nastartovat.

Ocakavania su z ADR-02 a z navrhu C09, zapisane PRED kodom.

UROVEN: integracia proti skutocnemu Postgresu. Stara schema sa simuluje
DDL zmenou vnutri transakcie, ktora sa na konci vrati - v PostgreSQL je
DDL transakcne, takze ostatne testy ju nikdy nevidia.
"""

import pytest
from sqlalchemy import text

from swilab import db


@pytest.fixture
def connection(engine):
    """Spojenie s otvorenou transakciou, ktora sa vzdy vrati."""
    with engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()


def test_the_current_schema_matches_the_models(connection):
    assert db.verify_schema(connection) == []


@pytest.mark.parametrize(
    ("ddl", "expected"),
    [
        (
            "ALTER TABLE reservations DROP CONSTRAINT ck_reservations_request_has_deadline",
            "ck_reservations_request_has_deadline",
        ),
        ("ALTER TABLE reservations DROP COLUMN decision_deadline", "decision_deadline"),
        ("ALTER TABLE reservations ALTER COLUMN starts_at DROP NOT NULL", "starts_at"),
        ("ALTER TABLE reservations ALTER COLUMN created_at TYPE timestamp", "created_at"),
        (
            "ALTER TYPE reservation_state RENAME VALUE 'EXPIRED' TO 'LAPSED'",
            "reservation_state",
        ),
    ],
    ids=["chyba-obmedzenie-DD9", "chyba-stlpec", "nullability", "typ", "enum"],
)
def test_each_kind_of_difference_is_reported(connection, ddl, expected):
    """ADR-02: tabulky, stlpce (typ a nullability), hodnoty enumov
    a pomenovane obmedzenia. Kontrola iba nad stlpcami by pustila
    databazu bez obmedzenia DD-9 alebo so starym enumom."""
    connection.execute(text(ddl))

    differences = db.verify_schema(connection)

    assert any(expected in difference for difference in differences), differences


def test_a_database_from_before_cr08_is_refused(connection):
    """Schema pred CR-08: bez stlpca s terminom aj bez obmedzenia - presne
    databaza, nad ktorou by stary kontajner nastartoval novu aplikaciu."""
    connection.execute(
        text(
            "ALTER TABLE reservations "
            "DROP CONSTRAINT ck_reservations_request_has_deadline, "
            "DROP COLUMN decision_deadline"
        )
    )

    with pytest.raises(db.SchemaMismatch) as error:
        db.ensure_schema(connection)

    message = str(error.value)
    assert "decision_deadline" in message
    assert "docker compose down" in message, "chyba postup prestavby z ADR-02"


def test_the_application_refuses_to_start_on_a_mismatched_schema(monkeypatch):
    """ADR-02: rozchod schemy zastavi start aplikacie, nie az prvu poziadavku."""
    from fastapi.testclient import TestClient

    from swilab.main import app

    monkeypatch.setattr(db, "verify_schema", lambda bind: ["simulovany rozdiel"])

    with pytest.raises(db.SchemaMismatch):
        with TestClient(app):
            pass
