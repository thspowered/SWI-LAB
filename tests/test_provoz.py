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
import os
import pathlib
import re
import time
import tomllib
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy import exc as sa_exc
from sqlalchemy.exc import DBAPIError

from swilab.db import create_db_engine, get_session

REPO = pathlib.Path(__file__).resolve().parent.parent


#: Poruchy databazy, ktore sa dostanu az k HTTP vrstve. Prve tri su chyby
#: ovladaca (DBAPIError), dalsie dve vznikaju v SQLAlchemy bez ovladaca -
#: TimeoutError prave pod zatazou, ked pool nema volne spojenie (review C10).
FAILURES = [
    sa_exc.OperationalError("SELECT 1", {}, psycopg.OperationalError("connection refused")),
    sa_exc.InterfaceError("SELECT 1", {}, psycopg.InterfaceError("the connection is closed")),
    sa_exc.IntegrityError("INSERT", {}, psycopg.IntegrityError("duplicate key value")),
    sa_exc.TimeoutError("QueuePool limit of size 5 overflow 10 reached"),
    sa_exc.PendingRollbackError("transaction has been rolled back"),
]


def _swilab_records(caplog, level):
    return [
        record
        for record in caplog.records
        if record.name.startswith("swilab") and record.levelno >= level
    ]


def _post_with_failing_database(caplog, failure, path):
    from swilab.main import app

    def database_fails():
        raise failure

    app.dependency_overrides[get_session] = database_fails
    try:
        # bez `with`: lifespan nebezi, databaza netreba
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="swilab"):
            response = client.post(path, json={"requested_by": str(uuid.uuid4())})
    finally:
        app.dependency_overrides.clear()
    return response


def _swilab_formatter() -> logging.Formatter:
    return logging.getLogger("swilab").handlers[0].formatter


@pytest.mark.parametrize("failure", FAILURES, ids=lambda f: type(f).__name__)
def test_a_database_failure_is_one_record_with_time_operation_type_and_result(caplog, failure):
    """Zadanie C10: cas, komponent/operacia, typ problemu a pozorovany
    vysledok. V JEDNOM zazname (L-2) - pri subeznych poziadavkach by sa
    oddelene riadky nedali priradit."""
    reservation_id = uuid.uuid4()
    path = f"/reservations/{reservation_id}/confirm"
    response = _post_with_failing_database(caplog, failure, path)

    # klient dostane to iste ako predtym - 500 bez vnutornych detailov
    assert response.status_code == 500
    assert response.text == "Internal Server Error"

    errors = _swilab_records(caplog, logging.ERROR)
    assert len(errors) == 1
    # traceback ostava pripojeny - hovori, KDE v kode porucha nastala
    assert errors[0].exc_info is not None and errors[0].exc_info[1] is failure
    first_line = _swilab_formatter().format(errors[0]).splitlines()[0]
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z ", first_line)
    assert f" [{os.getpid()}] ERROR swilab.main: POST {path} -> 500: " in first_line
    assert type(failure).__name__ in first_line
    message = str(failure.orig if isinstance(failure, DBAPIError) else failure).splitlines()[0]
    assert message in first_line


def test_the_record_time_is_utc_even_when_the_server_zone_is_not(monkeypatch):
    """Pripona Z sluboje UTC. Bez prevodu by server v inom pasme zapisal
    lokalny cas s priponou Z - rovnaky druh chyby ako F-01 (review C10)."""
    monkeypatch.setenv("TZ", "Europe/Prague")
    time.tzset()
    try:
        record = logging.LogRecord("swilab.test", logging.INFO, __file__, 1, "x", None, None)
        line = _swilab_formatter().format(record)
    finally:
        monkeypatch.undo()
        time.tzset()
    expected = datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    assert line.startswith(f"{expected}.{int(record.msecs):03d}Z ")


def test_the_record_names_the_process(caplog):
    """Pri viacerych workeroch (uvicorn --workers) sa zaznamy inak nedaju
    rozlisit (review C10)."""
    record = logging.LogRecord("swilab.test", logging.INFO, __file__, 1, "x", None, None)
    assert f" [{os.getpid()}] " in _swilab_formatter().format(record)


def test_the_logged_path_carries_no_control_characters(caplog):
    """Cesta je vstup klienta. Riadiaci znak (napr. ESC z %1B) by sa dostal
    do terminalu alebo prehliadaca logu. Novy riadok (%0A) odstrani uz
    Starlette - URL sklada cez urlsplit, ktory \\t\\r\\n zahodi (overene
    v review C10)."""
    _post_with_failing_database(caplog, FAILURES[0], "/reservations/abc%1Bdef/confirm")
    errors = _swilab_records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "\x1b" not in errors[0].getMessage()
    assert "/reservations/abc%1Bdef/confirm" in errors[0].getMessage()


def test_a_database_error_does_not_carry_sql_parameters(engine):
    """L-3: traceback pri poruche ide do logu - parametre SQL (napr. UUID
    pouzivatela) v nom byt nesmu. Chyba je zamerne taka, ktorej spravu
    databaza nesklada z hodnoty parametra. Hodnoty, ktore databaza sama
    vypise v DETAIL (napr. porusena unikatnost), hide_parameters neskryje -
    evidence C10, "Co C10 nepokryva"."""
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
    assert f"{url.host}:{url.port or 5432}/{url.database}" in starts[0]
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
