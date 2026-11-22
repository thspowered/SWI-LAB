"""Prevadzka (C10): co aplikacia zaznamena pri technickej poruche, pri starte
a pri zastaveni - a obnova po strate spojenia s databazou (F-04).

Ocakavania su z rozhodnutia C10 v docs/evidence-and-evolution.md (cast
C10, "Rozhodnutie o zmene"), zapisane PRED kodom. Situacie, ktore ich
vyvolali, vykonal scripts/c10_provoz.py nad izolovanou instanciou.

UROVEN: zaznam poruchy sa overuje bez databazy (porucha je podvrhnuta
v zavislosti get_session); parametre SQL, start/zastavenie a F-04 proti
skutocnemu Postgresu.
"""

import logging
import pathlib
import re
import tomllib
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from swilab.db import create_db_engine, get_session

REPO = pathlib.Path(__file__).resolve().parent.parent


def _database_down():
    raise OperationalError(
        "SELECT 1", {}, psycopg.OperationalError("connection refused")
    )


def _swilab_records(caplog, level):
    return [
        record
        for record in caplog.records
        if record.name.startswith("swilab") and record.levelno >= level
    ]


def test_a_database_failure_is_one_record_with_time_operation_type_and_result(caplog):
    """Zadanie C10: cas, komponent/operacia, typ problemu a pozorovany
    vysledok. V JEDNOM zazname (L-2) - pri subeznych poziadavkach by sa
    oddelene riadky nedali priradit."""
    from swilab.main import app

    app.dependency_overrides[get_session] = _database_down
    try:
        # bez `with`: lifespan nebezi, databaza netreba
        client = TestClient(app)
        reservation_id = uuid.uuid4()
        with caplog.at_level(logging.INFO, logger="swilab"):
            response = client.post(
                f"/reservations/{reservation_id}/confirm",
                json={"requested_by": str(uuid.uuid4())},
            )
    finally:
        app.dependency_overrides.clear()

    # klient dostane to iste ako predtym - 500 bez vnutornych detailov
    assert response.status_code == 500
    assert response.text == "Internal Server Error"

    errors = _swilab_records(caplog, logging.ERROR)
    assert len(errors) == 1
    formatter = logging.getLogger("swilab").handlers[0].formatter
    first_line = formatter.format(errors[0]).splitlines()[0]
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z ERROR ", first_line)
    assert f"POST /reservations/{reservation_id}/confirm" in first_line
    assert "500" in first_line
    assert "OperationalError" in first_line
    assert "connection refused" in first_line


def test_a_database_error_does_not_carry_sql_parameters(engine):
    """L-3: traceback pri poruche ide do logu - parametre SQL (napr. UUID
    pouzivatela) v nom byt nesmu. Chyba je zamerne taka, ktorej spravu
    databaza neskladá z hodnoty parametra."""
    with pytest.raises(DBAPIError) as caught:
        with engine.connect() as conn:
            conn.execute(text("SELECT CAST(:marker AS text), 1 / 0"), {"marker": "S1-tajne"})
    assert "division by zero" in str(caught.value)
    assert "S1-tajne" not in str(caught.value)


def test_start_and_stop_are_logged_with_version_and_target_database(caplog, engine):
    """L-4: z logu sa musi dat povedat, ktora verzia kedy bezala a nad
    ktorou databazou - bez pouzivatela a hesla."""
    from swilab.main import app

    with caplog.at_level(logging.INFO, logger="swilab"):
        with TestClient(app):
            pass

    messages = [record.getMessage() for record in _swilab_records(caplog, logging.INFO)]
    starts = [m for m in messages if m.startswith(f"start: swilab {app.version},")]
    stops = [m for m in messages if m.startswith(f"stop: swilab {app.version}")]
    assert len(starts) == 1 and len(stops) == 1
    url = engine.url
    assert f"{url.host}:{url.port}/{url.database}" in starts[0]
    # cielova databaza iba ako host:port/meno - ziadna URL s pouzivatelom
    # alebo heslom
    assert "@" not in starts[0] and "://" not in starts[0]


def test_the_application_version_is_the_project_version():
    """Verzia v zazname o starte je verzia artefaktu - dve kopie cisla sa
    nesmu rozist."""
    from swilab.main import app

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert app.version == pyproject["project"]["version"]


def test_the_pool_replaces_a_connection_the_database_has_terminated(engine):
    """F-04 (C10, P3b): po restarte databazy prva poziadavka skoncila 500 -
    pool vydal spojenie, ktore databaza medzitym ukoncila. Restart sa tu
    simuluje tak, ze databaza ukonci presne to spojenie, ktore lezi v poole.
    """
    application_engine = create_db_engine(engine.url)
    try:
        with application_engine.connect() as conn:
            pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
        # spojenie je spat v poole; databaza ho ukonci a pocka, kym skonci
        with engine.connect() as admin:
            terminated = admin.execute(
                text("SELECT pg_terminate_backend(:pid, 5000)"), {"pid": pid}
            ).scalar_one()
        assert terminated

        with application_engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar_one() == 1
            assert conn.execute(text("SELECT pg_backend_pid()")).scalar_one() != pid
    finally:
        application_engine.dispose()
