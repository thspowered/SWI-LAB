"""C10 - prevadzkove situacie P1-P4 proti izolovanej testovacej instancii.

Navrh (situacie, Expected, pravidla behu) je v docs/evidence-and-evolution.md,
cast C10. Expected v tomto skripte je prepis odtial - vzniklo pred prvym behom.

Co skript urobi sam:
  - vytvori VLASTNY kontajner swilab-c10-db (PostgreSQL 16, port 5434);
    vyvojovu databazu swilab-db (5433), nad ktorou bezi pytest, nezastavuje
    ani nemeni,
  - spusti aplikaciu (uvicorn, port 8010) nad touto databazou, stdout aj
    stderr do suboru app.log - presne tak, ako ju spusta README,
  - situacie vykona cez HTTP, stav cita SQL NOVYM spojenim priamo
    z databazy, log aplikacie zo suboru,
  - na konci aplikaciu zastavi a kontajner odstrani (C10_KEEP=1 ho necha).

Rovnako ako c08_baseline_tc.py neimportuje balik swilab - log ukazuje, ako
aplikacia reagovala, o stave dat rozhoduje databaza.

Spustenie (Docker musi bezat, prostredie podla README):
    python scripts/c10_provoz.py

Premenne: C10_DB_PORT (5434), C10_APP_PORT (8010), C10_OUT (adresar pre
app.log, predvolene novy v TMPDIR), C10_KEEP=1 (kontajner neodstranit).

Navratovy kod: 0 = vsetky situacie PASS, 1 = niektora FAIL, 2 = BLOCKED.
Riadky LOG (uzitocnost logu) navratovy kod neovplyvnuju.
"""

import os
import pathlib
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
import psycopg

REPO = pathlib.Path(__file__).resolve().parent.parent
DB_PORT = int(os.environ.get("C10_DB_PORT", "5434"))
APP_PORT = int(os.environ.get("C10_APP_PORT", "8010"))
KEEP = os.environ.get("C10_KEEP") == "1"
CONTAINER = "swilab-c10-db"
LABEL = "swilab.c10"
BASE_URL = f"http://127.0.0.1:{APP_PORT}"


def _credentials() -> tuple[str, str, str]:
    """Prihlasovacie udaje testovacej databazy z .env.example - heslo tak nie
    je v skripte druhy raz a vo vypise sa maskuje."""
    for line in (REPO / ".env.example").read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            url = urlsplit(line.split("=", 1)[1].strip())
            return url.username, url.password, url.path.lstrip("/")
    sys.exit("DATABASE_URL nie je v .env.example")


DB_USER, DB_PASSWORD, DB_NAME = _credentials()
APP_DATABASE_URL = (
    f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@127.0.0.1:{DB_PORT}/{DB_NAME}"
)
SQL_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@127.0.0.1:{DB_PORT}/{DB_NAME}"
MASKED_URL = f"postgresql://{DB_USER}:***@127.0.0.1:{DB_PORT}/{DB_NAME}"


def _id(suffix: str) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-{suffix:0>12}")


R1 = _id("a1")  # exkluzivny pristroj BEZ schvalovania: confirm -> CONFIRMED
S1 = _id("51")  # student, vlastnik B1 a B3
S2 = _id("52")  # student, vlastnik B2 a B4
#: Mena pre vypis. Rezervacie zaklada API, ich id sa doplnia za behu.
NAMES: dict[str, str] = {str(R1): "R1", str(S1): "S1", str(S2): "S2"}
IDS: dict[str, str] = {}

DAY = (datetime.now(UTC) + timedelta(days=7)).replace(
    hour=0, minute=0, second=0, microsecond=0
)


def at(hh: int, mm: int = 0) -> datetime:
    return DAY + timedelta(hours=hh, minutes=mm)


# Nezavisly prepis BR-02 + BR-08 (v0.4) do SQL - ten isty ako v C08.
BLOCKING_OVERLAP_PAIRS = """
    SELECT a.id, b.id
    FROM reservations a
    JOIN reservations b
      ON a.instrument_id = b.instrument_id AND a.id < b.id
    WHERE a.starts_at < b.ends_at AND b.starts_at < a.ends_at
      AND (a.state = 'CONFIRMED'
           OR (a.state = 'PENDING_APPROVAL'
               AND a.decision_deadline > now() AND a.starts_at > now()))
      AND (b.state = 'CONFIRMED'
           OR (b.state = 'PENDING_APPROVAL'
               AND b.decision_deadline > now() AND b.starts_at > now()))
"""


# --- vypis -----------------------------------------------------------------


def stamp() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3] + "Z"


def say(text: str) -> None:
    print(f"    [{stamp()}] {text}")


def named(text: str) -> str:
    for value, name in NAMES.items():
        text = text.replace(value, name)
    return text


class Blocked(Exception):
    def __init__(self, cause: str, next_step: str) -> None:
        super().__init__(cause)
        self.cause = cause
        self.next_step = next_step


class Situation:
    """Jedna situacia zo zadania. Verdikt PASS iba vtedy, ked presli
    vsetky kontroly vysledku a stavu."""

    all: list["Situation"] = []

    def __init__(self, sid: str, title: str) -> None:
        self.sid = sid
        self.passed = True
        self.log_notes: list[str] = []
        Situation.all.append(self)
        print(f"\n=== {sid} - {title} ===")

    def check(self, condition: bool, text: str) -> bool:
        print(f"    [{'OK  ' if condition else 'CHYBA'}] {text}")
        self.passed = self.passed and bool(condition)
        return bool(condition)

    @property
    def verdict(self) -> str:
        return "PASS" if self.passed else "FAIL"


# --- Docker a databaza -----------------------------------------------------


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True)


def prepare_container() -> None:
    try:
        docker("info", "--format", "{{.ServerVersion}}")
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise Blocked("Docker nebezi", "spusti Docker (macOS: open -ga Docker) a skript zopakuj")
    found = docker(
        "ps", "-a", "--filter", f"name=^{CONTAINER}$",
        "--format", "{{.Names}}|{{.Label \"" + LABEL + "\"}}",
    ).stdout.strip()
    if found:
        if found.partition("|")[2] != "1":
            raise Blocked(
                f"kontajner {CONTAINER} existuje, ale nevytvoril ho tento skript",
                f"over, co v nom je; ak nic dolezite: docker rm -f {CONTAINER}",
            )
        docker("rm", "-f", CONTAINER)
    try:
        docker(
            "run", "-d", "--name", CONTAINER, "--label", f"{LABEL}=1",
            "-e", f"POSTGRES_USER={DB_USER}", "-e", f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e", f"POSTGRES_DB={DB_NAME}",
            "-p", f"127.0.0.1:{DB_PORT}:5432", "postgres:16",
        )
    except subprocess.CalledProcessError as exc:
        raise Blocked(
            f"databazovy kontajner sa nevytvoril: {exc.stderr.strip()}",
            f"uvolni port {DB_PORT} alebo nastav C10_DB_PORT",
        )
    wait_for_db()


def wait_for_db(timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(SQL_DATABASE_URL, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise Blocked(
                    f"databaza na porte {DB_PORT} nie je pripravena ani po {timeout:.0f} s",
                    f"docker logs {CONTAINER}",
                )
            time.sleep(0.5)


def sql(query: str, params: tuple = ()) -> list[tuple]:
    """Kazde citanie NOVYM spojenim - vidi iba commitnuty stav."""
    with psycopg.connect(SQL_DATABASE_URL, autocommit=True, connect_timeout=5) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        cursor = conn.execute(query, params)
        return cursor.fetchall() if cursor.description else []


def seed() -> None:
    """Pristroj, pouzivatelia a certifikaty nemaju API (TBD-02) - SQL.
    Tabulky uz vytvorila aplikacia pri starte (create_all, ADR-02)."""
    sql(
        "INSERT INTO instruments (id, name, category, location, is_active, "
        "requires_approval) VALUES (%s, 'Olympus BX53', 'MICROSCOPE', "
        "'Lab B2.10', true, false)",
        (R1,),
    )
    for user_id, name, email in [
        (S1, "Student S1", "s1@c10.local"),
        (S2, "Student S2", "s2@c10.local"),
    ]:
        sql(
            "INSERT INTO users (id, full_name, email, role) VALUES (%s, %s, %s, 'STUDENT')",
            (user_id, name, email),
        )
        sql(
            "INSERT INTO certifications (id, user_id, category, valid_until) "
            "VALUES (%s, %s, 'MICROSCOPE', %s)",
            (uuid.uuid4(), user_id, DAY + timedelta(days=30)),
        )


def stored(name: str) -> tuple | None:
    rows = sql(
        "SELECT state::text, starts_at, ends_at FROM reservations WHERE id = %s",
        (uuid.UUID(IDS[name]),),
    )
    return rows[0] if rows else None


def blocking_pairs() -> list[tuple[str, str]]:
    return [(named(str(a)), named(str(b))) for a, b in sql(BLOCKING_OVERLAP_PAIRS)]


# --- aplikacia a jej log ---------------------------------------------------


class App:
    """Aplikacia ako samostatny proces - ako v README, bez vlastnej
    konfiguracie logovania. Pracovny adresar je adresar behu, takze .env
    z repozitara sa nepouzije - databazu urcuje iba DATABASE_URL."""

    def __init__(self, workdir: pathlib.Path) -> None:
        self.workdir = workdir
        self.log_path = workdir / "app.log"
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = os.environ | {
            "DATABASE_URL": APP_DATABASE_URL,
            "PYTHONPATH": str(REPO / "src"),
            # bez bufferovania: stdout (pristupove riadky) a stderr (chyby)
            # idu do jedneho suboru v poradi, v akom vznikli
            "PYTHONUNBUFFERED": "1",
        }
        with open(self.log_path, "a") as log:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "swilab.main:app", "--port", str(APP_PORT)],
                cwd=self.workdir, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 30
        while True:
            if self.proc.poll() is not None:
                raise Blocked(
                    f"aplikacia pri starte skoncila s kodom {self.proc.returncode}",
                    f"pozri {self.log_path}",
                )
            try:
                if httpx.get(f"{BASE_URL}/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise Blocked("aplikacia neodpoveda ani po 30 s", f"pozri {self.log_path}")
            time.sleep(0.3)

    def stop(self, timeout: float = 10) -> int | None:
        """SIGINT ako Ctrl+C. None = do timeoutu neskoncila (potom kill)."""
        if self.proc is None or self.proc.poll() is not None:
            return self.proc.returncode if self.proc else None
        self.proc.send_signal(signal.SIGINT)
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            return None


class Log:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.offset = 0

    def mark(self) -> None:
        self.offset = self.path.stat().st_size if self.path.exists() else 0

    def new_lines(self) -> list[str]:
        with open(self.path, encoding="utf-8", errors="replace") as log:
            log.seek(self.offset)
            return log.read().splitlines()


def show_log(lines: list[str]) -> None:
    """Novy obsah logu. Ramce tracebacku (odsadene riadky) sa iba spocitaju -
    typ a sprava vynimky su neodsadene a zostanu."""
    print("    log aplikacie (nove riadky):")
    if not lines:
        print("      | (ziadny)")
        return
    skipped = 0
    for line in lines:
        if line.startswith((" ", "\t")):
            skipped += 1
            continue
        if skipped:
            print(f"      |   ... {skipped} riadkov ramcov tracebacku")
            skipped = 0
        print("      | " + named(line)[:220])
    if skipped:
        print(f"      |   ... {skipped} riadkov ramcov tracebacku")


TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
ERROR_TYPE_RE = re.compile(r"\b[A-Za-z]+(Error|Exception)\b")
STATUS_5XX_RE = re.compile(r"\b5\d\d\b")
VERSION_RE = re.compile(r"\b\d+\.\d+\.\d+(?!\.\d)\b")


def yes(flag: bool) -> str:
    return "ano" if flag else "nie"


def log_diagnostic(situation: Situation, lines: list[str], path: str) -> None:
    """Zadanie C10: pri technickej poruche cas, komponent/operacia, typ
    problemu a pozorovany vysledok. Pozadujeme to v JEDNOM zazname - inak sa
    pri subeznych poziadavkach neda povedat, ktora chyba patri ku ktorej
    poziadavke."""
    has_time = any(TIME_RE.search(line) for line in lines)
    has_op = any(path in line for line in lines)
    has_type = any(ERROR_TYPE_RE.search(line) for line in lines)
    has_result = any(path in line and STATUS_5XX_RE.search(line) for line in lines)
    one = any(
        TIME_RE.search(line) and path in line and ERROR_TYPE_RE.search(line)
        and STATUS_5XX_RE.search(line)
        for line in lines
    )
    note = (
        f"cas={yes(has_time)}, operacia={yes(has_op)}, typ problemu={yes(has_type)}, "
        f"vysledok 5xx={yes(has_result)}, vsetko v jednom zazname={yes(one)}"
    )
    print(f"    LOG {'OK   ' if one else 'CHYBA'} {note}")
    situation.log_notes.append(("OK" if one else "CHYBA") + " - " + note)


def log_lifecycle(situation: Situation, lines: list[str]) -> None:
    """Zastavenie a start: je vidiet KEDY a KTORA verzia?"""
    stops = [line for line in lines if re.search(r"shut|stop|finished", line, re.I)]
    starts = [line for line in lines if re.search(r"start", line, re.I)]
    stop_time = any(TIME_RE.search(line) for line in stops)
    start_time = any(TIME_RE.search(line) for line in starts)
    version = any("swilab" in line.lower() and VERSION_RE.search(line) for line in starts)
    ok = bool(stops) and bool(starts) and stop_time and start_time and version
    note = (
        f"zastavenie={yes(bool(stops))} (cas {yes(stop_time)}), "
        f"start={yes(bool(starts))} (cas {yes(start_time)}), verzia aplikacie={yes(version)}"
    )
    print(f"    LOG {'OK   ' if ok else 'CHYBA'} {note}")
    situation.log_notes.append(("OK" if ok else "CHYBA") + " - " + note)


# --- HTTP ------------------------------------------------------------------


def call(method: str, path: str, **kwargs) -> httpx.Response | None:
    """None = odpoved neprisla (timeout, spojenie) - vysledok je NEZNAMY.

    Kazda poziadavka NOVYM spojenim: uvicorn po neosetrenej vynimke spojenie
    zatvori (prvy beh: /health hned po 500 dostal po keep-alive spojeni
    "Connection reset by peer"). Opakovane spojenie by tak miesalo stav
    aplikacie s artefaktom klienta."""
    body = kwargs.get("json")
    say(f"HTTP {method} {named(path)}" + (f"  {named(str(body))}" if body else ""))
    try:
        response = httpx.request(method, BASE_URL + path, timeout=30, **kwargs)
    except httpx.HTTPError as exc:
        print(f"      -> bez odpovede: {type(exc).__name__}: {exc}")
        return None
    print(f"      -> {response.status_code} {named(response.text)[:200]}")
    return response


def create(owner, starts_at, ends_at) -> httpx.Response | None:
    return call("POST", "/reservations", json={
        "instrument_id": str(R1), "user_id": str(owner),
        "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat(),
    })


def remember(name: str, response: httpx.Response | None) -> bool:
    if response is None or response.status_code != 201:
        return False
    IDS[name] = response.json()["id"]
    NAMES[IDS[name]] = name
    return True


def confirm(name: str, actor) -> httpx.Response | None:
    return call("POST", f"/reservations/{IDS[name]}/confirm",
                json={"requested_by": str(actor)})


def availability() -> httpx.Response | None:
    return call("GET", f"/instruments/{R1}/availability",
                params={"starts_at": at(10).isoformat(), "ends_at": at(11).isoformat()})


def has(response, http_status: int, **fields) -> bool:
    if response is None or response.status_code != http_status:
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    return all(data.get(key) == value for key, value in fields.items())


def is_state(name: str, state: str, interval: tuple | None = None) -> bool:
    row = stored(name)
    if row is None or row[0] != state:
        return False
    return interval is None or (row[1], row[2]) == interval


# --- situacie --------------------------------------------------------------


def p1_success(log: Log) -> None:
    s = Situation("P1", "uspesne potvrdenie")
    log.mark()
    if not s.check(remember("B1", create(S1, at(10), at(11))), "vytvorenie B1: 201"):
        return
    s.check(is_state("B1", "DRAFT"), "SQL: B1 DRAFT")
    s.check(has(confirm("B1", S1), 200, state="CONFIRMED"), "potvrdenie B1: 200 CONFIRMED")
    s.check(is_state("B1", "CONFIRMED", (at(10), at(11))), "SQL: B1 CONFIRMED, interval bez zmeny")
    s.check(blocking_pairs() == [], "SQL: ziadna blokujuca dvojica (BR-02)")
    show_log(log.new_lines())


def p2_collision(log: Log) -> None:
    s = Situation("P2", "zamietnuta kolizia")
    log.mark()
    if not s.check(remember("B2", create(S2, at(10, 30), at(11, 30))),
                   "vytvorenie B2 (prekryv s B1, REQ-02): 201"):
        return
    s.check(has(confirm("B2", S2), 409, code="OVERLAP"), "potvrdenie B2: 409 OVERLAP")
    s.check(is_state("B1", "CONFIRMED", (at(10), at(11))), "SQL: B1 CONFIRMED bez zmeny")
    s.check(is_state("B2", "DRAFT"), "SQL: B2 ostava DRAFT (REQ-05)")
    s.check(blocking_pairs() == [], "SQL: ziadna blokujuca dvojica (BR-02)")
    lines = log.new_lines()
    show_log(lines)
    logged_code = any("OVERLAP" in line for line in lines)
    note = f"kod zamietnutia OVERLAP v logu={yes(logged_code)} (rozhodnutie tima v evidence)"
    print(f"    LOG INFO  {note}")
    s.log_notes.append("INFO - " + note)


def p3a_outage(log: Log) -> None:
    s = Situation("P3a", "vypadok databazy pocas operacie a obnova")
    log.mark()
    if not s.check(remember("B3", create(S1, at(12), at(13))), "vytvorenie B3: 201"):
        return
    say(f"docker stop {CONTAINER}")
    docker("stop", CONTAINER)
    response = confirm("B3", S1)
    s.check(response is not None and 500 <= response.status_code < 600,
            "potvrdenie B3 pocas vypadku: technicka chyba 5xx (nie 2xx, nie 409)")
    health = call("GET", "/health")
    say("POZOROVANIE /health pocas vypadku: "
        + ("bez odpovede" if health is None else f"{health.status_code} {health.text}"))
    say(f"docker start {CONTAINER}")
    docker("start", CONTAINER)
    wait_for_db()
    say("databaza znova prijima spojenia")
    row = stored("B3")
    say(f"SQL pred akymkolvek opakovanim: B3 = {row[0] if row else 'neexistuje'}")
    b3_draft = s.check(row is not None and row[0] == "DRAFT",
                       "SQL: B3 DRAFT - potvrdenie sa nezapisalo")
    s.check(is_state("B1", "CONFIRMED", (at(10), at(11))), "SQL: B1 CONFIRMED - data vypadok prezili")
    if b3_draft:
        say("B3 je DRAFT -> jedno opakovanie potvrdenia je bezpecne")
        s.check(has(confirm("B3", S1), 200, state="CONFIRMED"),
                "opakovanie bez restartu aplikacie: 200 CONFIRMED")
        s.check(is_state("B3", "CONFIRMED"), "SQL: B3 CONFIRMED")
    else:
        say("B3 nie je DRAFT -> potvrdenie sa NEOPAKUJE")
    s.check(blocking_pairs() == [], "SQL: ziadna blokujuca dvojica (BR-02)")
    lines = log.new_lines()
    show_log(lines)
    log_diagnostic(s, lines, f"/reservations/{IDS['B3']}/confirm")


def p3b_db_restart(log: Log) -> None:
    s = Situation("P3b", "restart databazy, ked aplikacia nic nerobi")
    log.mark()
    say(f"docker restart {CONTAINER}")
    docker("restart", CONTAINER)
    wait_for_db()
    say("databaza znova prijima spojenia")
    first = availability()
    s.check(has(first, 200, status="UNAVAILABLE", conflicting_reservation_ids=[IDS["B1"]]),
            "PRVA poziadavka po obnove: 200 UNAVAILABLE, konflikt B1")
    second = availability()
    s.check(has(second, 200, status="UNAVAILABLE", conflicting_reservation_ids=[IDS["B1"]]),
            "druha poziadavka: 200 UNAVAILABLE, konflikt B1")
    s.check(is_state("B1", "CONFIRMED"), "SQL: B1 CONFIRMED")
    lines = log.new_lines()
    show_log(lines)
    if first is None or first.status_code != 200:
        log_diagnostic(s, lines, f"/instruments/{R1}/availability")


def p4_app_restart(app: App, log: Log) -> None:
    s = Situation("P4", "restart aplikacie")
    log.mark()
    say("SIGINT aplikacii (ako Ctrl+C)")
    code = app.stop(10)
    s.check(code == 0, f"aplikacia skoncila s kodom 0 do 10 s (skutocne: {code})")
    say("start aplikacie")
    app.start()
    s.check(has(call("GET", "/health"), 200, status="ok"), "/health po starte: 200")
    s.check(is_state("B1", "CONFIRMED") and is_state("B3", "CONFIRMED"),
            "SQL: B1 a B3 CONFIRMED - stav je v databaze")
    s.check(has(availability(), 200, status="UNAVAILABLE",
                conflicting_reservation_ids=[IDS["B1"]]),
            "dostupnost po restarte: 200 UNAVAILABLE, konflikt B1")
    if s.check(remember("B4", create(S2, at(14), at(15))), "vytvorenie B4: 201"):
        s.check(has(confirm("B4", S2), 200, state="CONFIRMED"),
                "potvrdenie B4: 200 CONFIRMED")
        s.check(is_state("B4", "CONFIRMED"), "SQL: B4 CONFIRMED")
    s.check(blocking_pairs() == [], "SQL: ziadna blokujuca dvojica (BR-02)")
    lines = log.new_lines()
    show_log(lines)
    log_lifecycle(s, lines)


# --- beh -------------------------------------------------------------------


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


def main() -> int:
    workdir = pathlib.Path(os.environ.get("C10_OUT") or tempfile.mkdtemp(prefix="swilab-c10-"))
    workdir.mkdir(parents=True, exist_ok=True)
    app = App(workdir)
    log = Log(app.log_path)

    print("=== identita behu ===")
    print(f"commit:    {git('rev-parse', 'HEAD')}")
    print(f"kod:       {git('log', '-1', '--format=%h', '--', 'src', 'pyproject.toml')}"
          " (posledny commit, ktory menil src/ alebo pyproject.toml)")
    if git("status", "--porcelain", "--untracked-files=no", "--", "src", "pyproject.toml"):
        print("POZOR:     src/ alebo pyproject.toml maju necommitnute zmeny")
    print(f"python:    {sys.version.split()[0]}")
    print(f"databaza:  {MASKED_URL} (kontajner {CONTAINER}, postgres:16)")
    print(f"aplikacia: {BASE_URL}")
    print(f"log:       {app.log_path}")
    print(f"den D:     {DAY.date()} (UTC)")

    blocked = None
    try:
        try:
            httpx.get(f"{BASE_URL}/health", timeout=1)
            raise Blocked(f"na porte {APP_PORT} uz nieco bezi", "zastav to alebo nastav C10_APP_PORT")
        except httpx.HTTPError:
            pass
        prepare_container()
        print(f"server:    PostgreSQL {sql('SHOW server_version')[0][0]}")
        app.start()
        seed()
        p1_success(log)
        p2_collision(log)
        p3a_outage(log)
        p3b_db_restart(log)
        p4_app_restart(app, log)
    except Blocked as exc:
        blocked = exc
    finally:
        app.stop(10)
        if not KEEP:
            subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)

    print("\n=== suhrn ===")
    for s in Situation.all:
        print(f"{s.sid}: {s.verdict}")
        for note in s.log_notes:
            print(f"      LOG {note}")
    print(f"log aplikacie: {app.log_path}")
    if blocked is not None:
        print(f"BLOCKED - {blocked.cause}")
        print(f"dalsi krok: {blocked.next_step}")
        return 2
    return 0 if all(s.passed for s in Situation.all) else 1


if __name__ == "__main__":
    sys.exit(main())
