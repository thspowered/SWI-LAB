"""I-02 - meranie serializacie na pristroj (ADR-01) proti izolovanej instancii.

Navrh (model zataze, kriteria K-1 az K-3, prieskum E-0 az E-3, metoda) je
v docs/evidence-and-evolution.md, cast I-02. Zapisany bol PRED prvym behom
tohto skriptu; skript ho iba vykonava.

Co skript urobi sam:
  - vytvori VLASTNY kontajner swilab-i02-db (PostgreSQL 16, port 5435);
    vyvojovu databazu swilab-db (5433) nezastavuje ani nemeni,
  - spusti aplikaciu (uvicorn, port 8020, jeden proces, bez vlastnej
    konfiguracie) nad touto databazou, log do suboru app.log,
  - pripravi data SQL (pristroje, pouzivatelia, certifikaty, DRAFT
    rezervacie, objem na pozadi) - ich vznik sa nemeria,
  - meria IBA potvrdenie (OP-03) cez HTTP: N vlakien, kazde jedna poziadavka
    novym spojenim, vsetky uvolni jedna bariera naraz,
  - po kazdom behu overi stav SQL novym spojenim (pocty stavov, BR-02),
  - na konci aplikaciu zastavi a kontajner odstrani (I02_KEEP=1 ho necha).
    Odstrani iba kontajner, ktory v tomto behu sam vytvoril.

Rovnako ako c08_baseline_tc.py a c10_provoz.py neimportuje balik swilab.

Spustenie (Docker musi bezat, prostredie z requirements-dev.lock):
    python scripts/i02_meranie.py

Premenne: I02_DB_PORT (5435), I02_APP_PORT (8020), I02_OUT (adresar pre
app.log a results.json, predvolene novy v TMPDIR), I02_KEEP=1.

Navratovy kod: 0 = K-1 az K-3 PASS, 1 = niektore FAIL, 2 = BLOCKED,
3 = chyba skriptu (verdikt neexistuje).

Percentil: najblizsie poradie (nearest-rank) - p95 z n hodnot je hodnota
na poradi ceil(0,95 * n) vo vzostupne zoradenom zozname. Ziadna interpolacia.
"""

import json
import math
import os
import pathlib
import platform
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
import psycopg

REPO = pathlib.Path(__file__).resolve().parent.parent
DB_PORT = int(os.environ.get("I02_DB_PORT", "5435"))
APP_PORT = int(os.environ.get("I02_APP_PORT", "8020"))
KEEP = os.environ.get("I02_KEEP") == "1"
CONTAINER = "swilab-i02-db"
LABEL = "swilab.i02"
BASE_URL = f"http://127.0.0.1:{APP_PORT}"

#: Z navrhu v evidence - zmena tu bez zmeny tam je chyba.
THRESHOLD_S = 1.0        # K-2, K-3: p95 <= 1 s
TEN_X_N = 50             # 10x: 50 subeznych poziadaviek na jeden pristroj
TEN_X_VOLUME = 100_000   # 10x: 100 000 riadkov
TODAY_N = 5
TODAY_VOLUME = 10_000
PROBES = 5               # K-3: 5 poziadaviek na 5 inych pristrojoch
WARMUP_RUNS = 1
MEASURED_RUNS = 5
CURVE_N = (1, 5, 10, 25, 50, 100, 200)               # E-1
CURVE_VOLUME = (0, 10_000, 100_000, 1_000_000)       # E-2
INDEX_VOLUMES = (100_000, 1_000_000)                 # E-3
BACKGROUND_INSTRUMENTS = 20
MAX_N = max(CURVE_N)
REQUEST_TIMEOUT_S = 60


def _credentials() -> tuple[str, str, str]:
    """Prihlasovacie udaje testovacej databazy z .env.example (ako C10)."""
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

CREATED_CONTAINER = False

#: Den merania: dost daleko, aby lehoty (BR-03) ani termin nehrali rolu.
DAY = (datetime.now(UTC) + timedelta(days=30)).replace(
    hour=0, minute=0, second=0, microsecond=0
)


class Blocked(Exception):
    def __init__(self, cause: str, next_step: str) -> None:
        super().__init__(cause)
        self.cause = cause
        self.next_step = next_step


# --- Docker, databaza, aplikacia (ako C10) ----------------------------------


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True)


def prepare_container() -> None:
    global CREATED_CONTAINER
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
        CREATED_CONTAINER = True
    except subprocess.CalledProcessError as exc:
        raise Blocked(
            f"databazovy kontajner sa nevytvoril: {exc.stderr.strip()}",
            f"uvolni port {DB_PORT} alebo nastav I02_DB_PORT",
        )
    deadline = time.monotonic() + 60
    while True:
        try:
            with psycopg.connect(SQL_DATABASE_URL, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise Blocked("databaza nie je pripravena ani po 60 s", f"docker logs {CONTAINER}")
            time.sleep(0.5)


def sql(query: str, params=(), *, client_side: bool = False) -> list[tuple]:
    """Kazde volanie NOVYM spojenim - vidi iba commitnuty stav."""
    with psycopg.connect(SQL_DATABASE_URL, autocommit=True, connect_timeout=5) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        cursor = psycopg.ClientCursor(conn) if client_side else conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall() if cursor.description else []


class App:
    """Aplikacia ako samostatny proces - ako v README, bez --reload."""

    def __init__(self, workdir: pathlib.Path) -> None:
        self.workdir = workdir
        self.log_path = workdir / "app.log"
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = os.environ | {
            "DATABASE_URL": APP_DATABASE_URL,
            "PYTHONPATH": str(REPO / "src"),
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
                raise Blocked(f"aplikacia pri starte skoncila s kodom {self.proc.returncode}",
                              f"pozri {self.log_path}")
            try:
                if httpx.get(f"{BASE_URL}/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise Blocked("aplikacia neodpoveda ani po 30 s", f"pozri {self.log_path}")
            time.sleep(0.3)

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


# --- data --------------------------------------------------------------------


def _id(prefix: str, number: int) -> uuid.UUID:
    return uuid.UUID(f"{prefix}000000-0000-0000-0000-{number:012d}")


HOT = _id("a0", 1)                                               # pristroj A
PROBE_INSTRUMENTS = [_id("b0", i) for i in range(PROBES)]        # K-3
OTHER_INSTRUMENTS = [_id("c0", i) for i in range(MAX_N)]         # E-1 "rozne"
BACKGROUND = [_id("d0", i) for i in range(BACKGROUND_INSTRUMENTS)]
MEASURED_INSTRUMENTS = [HOT, *PROBE_INSTRUMENTS, *OTHER_INSTRUMENTS]
USERS = [_id("e0", i) for i in range(MAX_N + PROBES)]


def seed() -> None:
    """Pristroje, pouzivatelia a certifikaty nemaju API (TBD-02) - SQL.
    Tabulky vytvorila aplikacia pri starte (create_all, ADR-02)."""
    rows = [(HOT, "A")] + [(i, f"P{n}") for n, i in enumerate(PROBE_INSTRUMENTS)]
    rows += [(i, f"C{n}") for n, i in enumerate(OTHER_INSTRUMENTS)]
    rows += [(i, f"BG{n}") for n, i in enumerate(BACKGROUND)]
    with psycopg.connect(SQL_DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO instruments (id, name, category, location, is_active, "
                "requires_approval) VALUES (%s, %s, 'MICROSCOPE', 'Lab I02', true, false)",
                rows,
            )
            cur.executemany(
                "INSERT INTO users (id, full_name, email, role) VALUES (%s, %s, %s, 'STUDENT')",
                [(u, f"Student {n}", f"s{n}@i02.local") for n, u in enumerate(USERS)],
            )
            cur.executemany(
                "INSERT INTO certifications (id, user_id, category, valid_until) "
                "VALUES (gen_random_uuid(), %s, 'MICROSCOPE', %s)",
                [(u, DAY + timedelta(days=365)) for u in USERS],
            )


def background_count() -> int:
    return sql("SELECT count(*) FROM reservations WHERE instrument_id = ANY(%s)",
               (BACKGROUND,))[0][0]


def set_volume(target: int) -> None:
    """Objem na pozadi: CONFIRMED v minulosti na 20 pristrojoch, kazda
    rezervacia pristroja v inej hodine - ziadny prekryv. Iba pridava."""
    current = background_count()
    if target < current:
        raise RuntimeError(f"objem sa da iba zvysit ({current} -> {target})")
    if target > current:
        sql(
            """
            INSERT INTO reservations (id, instrument_id, user_id, starts_at, ends_at,
                                      state, created_at)
            SELECT gen_random_uuid(),
                   (%(bg)s::uuid[])[1 + g %% %(k)s::int],
                   (%(users)s::uuid[])[1 + g %% %(u)s::int],
                   date_trunc('hour', now()) - interval '1 hour' * (2 + g / %(k)s::int),
                   date_trunc('hour', now()) - interval '1 hour' * (1 + g / %(k)s::int),
                   'CONFIRMED',
                   date_trunc('hour', now()) - interval '1 hour' * (2 + g / %(k)s::int)
                     - interval '1 day'
            FROM generate_series(%(start)s::int, %(end)s::int) AS g
            """,
            {"bg": BACKGROUND, "users": USERS, "k": BACKGROUND_INSTRUMENTS,
             "u": len(USERS), "start": current, "end": target - 1},
        )
    sql("ANALYZE reservations")


def explain_overlap() -> dict:
    """Plan dotazu na prekryv - prepis _overlapping_blocking() do SQL, ako
    ho posle aplikacia pri potvrdeni na pristroji A. EXPLAIN ANALYZE dotaz
    vykona (iba cita)."""
    lines = [row[0] for row in sql(
        """
        EXPLAIN (ANALYZE, BUFFERS)
        SELECT * FROM reservations
        WHERE instrument_id = %s
          AND state IN ('CONFIRMED', 'PENDING_APPROVAL')
          AND (state = 'CONFIRMED' OR decision_deadline > now())
          AND starts_at < %s AND ends_at > %s AND id <> %s
        """,
        (HOT, DAY + timedelta(hours=11), DAY + timedelta(hours=10), uuid.uuid4()),
        client_side=True,
    )]
    scan = next((line.strip().split("  ")[0].lstrip("-> ") for line in lines
                 if "Scan" in line), "?")
    execution = next((line.split(":", 1)[1].strip() for line in lines
                      if line.startswith("Execution Time")), "?")
    return {"plan": scan, "execution": execution, "full": lines}


# --- jeden beh -----------------------------------------------------------------


def insert_drafts(scenario: str, n: int) -> list[tuple[str, uuid.UUID, uuid.UUID]]:
    """DRAFT rezervacie pre beh. Vrati (rola, id rezervacie, vlastnik)."""
    one_hour = timedelta(hours=1)
    plan: list[tuple[str, uuid.UUID, uuid.UUID, datetime]] = []
    if scenario == "kolizia":          # K-1: rovnaky interval, jeden pristroj
        plan = [("hlavna", HOT, USERS[i], DAY + timedelta(hours=10)) for i in range(n)]
    elif scenario in ("jeden", "sused"):  # K-2: rozne intervaly, jeden pristroj
        plan = [("hlavna", HOT, USERS[i], DAY + i * one_hour) for i in range(n)]
    elif scenario == "rozne":          # E-1: kazda poziadavka na inom pristroji
        plan = [("hlavna", OTHER_INSTRUMENTS[i], USERS[i], DAY + timedelta(hours=10))
                for i in range(n)]
    else:
        raise ValueError(scenario)
    if scenario == "sused":            # K-3: 5 sondaz na 5 inych pristrojoch
        plan += [("sonda", PROBE_INSTRUMENTS[i], USERS[MAX_N + i], DAY + timedelta(hours=10))
                 for i in range(PROBES)]
    rows = [(role, uuid.uuid4(), instrument, owner, starts)
            for role, instrument, owner, starts in plan]
    with psycopg.connect(SQL_DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO reservations (id, instrument_id, user_id, starts_at, ends_at, "
                "state, created_at) VALUES (%s, %s, %s, %s, %s, 'DRAFT', now())",
                [(rid, inst, owner, starts, starts + one_hour)
                 for _, rid, inst, owner, starts in rows],
            )
    return [(role, rid, owner) for role, rid, _, owner, _ in rows]


def fire(requests: list[tuple[str, uuid.UUID, uuid.UUID]]) -> tuple[list[dict], float]:
    """Vsetky potvrdenia naraz. Kazde vlakno jedna poziadavka novym spojenim."""
    barrier = threading.Barrier(len(requests))
    results: list[dict | None] = [None] * len(requests)

    def worker(index: int, role: str, reservation_id: uuid.UUID, owner: uuid.UUID) -> None:
        barrier.wait(timeout=30)
        started = time.perf_counter()
        outcome = {"role": role}
        try:
            response = httpx.post(
                f"{BASE_URL}/reservations/{reservation_id}/confirm",
                json={"requested_by": str(owner)}, timeout=REQUEST_TIMEOUT_S,
            )
            outcome["status"] = response.status_code
            try:
                body = response.json()
                outcome["code"] = body.get("code") or body.get("state")
            except ValueError:
                outcome["code"] = response.text[:60]
        except httpx.HTTPError as exc:
            outcome["status"] = None
            outcome["code"] = type(exc).__name__
        finished = time.perf_counter()
        outcome.update(start=started, end=finished, latency=finished - started)
        results[index] = outcome

    threads = [threading.Thread(target=worker, args=(i, *request))
               for i, request in enumerate(requests)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(REQUEST_TIMEOUT_S + 30)
    done = [r for r in results if r is not None]
    if len(done) != len(requests):
        done += [{"role": "?", "status": None, "code": "NEDOBEHLO", "latency": math.inf,
                  "start": math.inf, "end": math.inf}] * (len(requests) - len(done))
    makespan = max(r["end"] for r in done) - min(r["start"] for r in done)
    return done, makespan


#: BR-02 a BR-08 (v0.4) do SQL - ten isty dotaz ako C08 a C10, obmedzeny na
#: pristroje merania (pozadie je bez prekryvov z konstrukcie a nemeni sa).
BLOCKING_OVERLAP_PAIRS = """
    SELECT count(*)
    FROM reservations a
    JOIN reservations b
      ON a.instrument_id = b.instrument_id AND a.id < b.id
    WHERE a.instrument_id = ANY(%s)
      AND a.starts_at < b.ends_at AND b.starts_at < a.ends_at
      AND (a.state = 'CONFIRMED'
           OR (a.state = 'PENDING_APPROVAL'
               AND a.decision_deadline > now() AND a.starts_at > now()))
      AND (b.state = 'CONFIRMED'
           OR (b.state = 'PENDING_APPROVAL'
               AND b.decision_deadline > now() AND b.starts_at > now()))
"""


def stored_states(ids: list[uuid.UUID]) -> dict[str, int]:
    return dict(sql("SELECT state::text, count(*) FROM reservations WHERE id = ANY(%s) "
                    "GROUP BY state", (ids,)))


def cleanup() -> None:
    sql("DELETE FROM reservations WHERE instrument_id = ANY(%s)", (MEASURED_INSTRUMENTS,))
    sql("VACUUM reservations")


def run_once(scenario: str, n: int) -> dict:
    requests = insert_drafts(scenario, n)
    outcomes, makespan = fire(requests)
    ids = [rid for _, rid, _ in requests]
    run = {
        "outcomes": outcomes,
        "makespan": makespan,
        "states": stored_states(ids),
        "br02_pairs": sql(BLOCKING_OVERLAP_PAIRS, (MEASURED_INSTRUMENTS,))[0][0],
    }
    cleanup()
    return run


# --- bod merania -----------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def tally(outcomes: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in outcomes:
        key = f"{o['status']} {o['code']}"
        counts[key] = counts.get(key, 0) + 1
    return counts


RESULTS: list[dict] = []


def point(label: str, scenario: str, n: int, volume: int, index: bool) -> dict:
    """1 zahrievaci a 5 meranych behov. Spravnost sa overuje vo VSETKYCH
    behoch, cas iba v meranych."""
    runs = [run_once(scenario, n) for _ in range(WARMUP_RUNS + MEASURED_RUNS)]
    measured = runs[WARMUP_RUNS:]
    main = [o["latency"] for r in measured for o in r["outcomes"] if o["role"] == "hlavna"]
    probe = [o["latency"] for r in measured for o in r["outcomes"] if o["role"] == "sonda"]
    result = {
        "label": label, "scenario": scenario, "n": n, "volume": volume, "index": index,
        "runs": [{"tally": tally(r["outcomes"]), "states": r["states"],
                  "br02_pairs": r["br02_pairs"], "makespan": r["makespan"]} for r in runs],
        "p50": percentile(main, 0.50), "p95": percentile(main, 0.95), "max": max(main),
        "makespan_median": statistics.median(r["makespan"] for r in measured),
        "latencies": main,
    }
    if probe:
        result.update(probe_p50=percentile(probe, 0.50), probe_p95=percentile(probe, 0.95),
                      probe_max=max(probe), probe_latencies=probe)
    RESULTS.append(result)
    probe_text = (f" | sonda p95 {result['probe_p95'] * 1000:7.1f} ms" if probe else "")
    print(f"  {label:<14} {scenario:<8} N={n:<4} objem={volume:>9,} index={'ano' if index else 'nie'}"
          f" | p50 {result['p50'] * 1000:7.1f} ms  p95 {result['p95'] * 1000:7.1f} ms"
          f"  max {result['max'] * 1000:7.1f} ms | davka {result['makespan_median'] * 1000:7.1f} ms"
          f"{probe_text}")
    odd = {k: v for r in result["runs"] for k, v in r["tally"].items()
           if not k.startswith(("200 ", "409 OVERLAP"))}
    if odd:
        print(f"      POZOR - ine odpovede: {odd}")
    return result


def all_runs_correct_collision(result: dict, n: int) -> bool:
    return all(
        r["tally"] == {"200 CONFIRMED": 1, "409 OVERLAP": n - 1}
        and r["states"] == {"CONFIRMED": 1, "DRAFT": n - 1}
        and r["br02_pairs"] == 0
        for r in result["runs"]
    )


def all_runs_confirmed(result: dict, total: int) -> bool:
    return all(
        r["tally"] == {"200 CONFIRMED": total}
        and r["states"] == {"CONFIRMED": total}
        and r["br02_pairs"] == 0
        for r in result["runs"]
    )


# --- beh -------------------------------------------------------------------------


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


def identity(app: App) -> dict:
    cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                         capture_output=True, text=True).stdout.strip() or platform.processor()
    info = {
        "commit": git("rev-parse", "HEAD"),
        "kod": git("log", "-1", "--format=%h", "--", "src", "pyproject.toml"),
        "python": sys.version.split()[0],
        "system": f"{platform.system()} {platform.machine()}, {cpu}, {os.cpu_count()} jadier",
        "docker": subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                                 capture_output=True, text=True).stdout.strip() or "nezistene",
        "databaza": MASKED_URL,
    }
    print("=== identita behu ===")
    for key, value in info.items():
        print(f"{key + ':':<10} {value}")
    if git("status", "--porcelain", "--untracked-files=no", "--", "src", "pyproject.toml", "scripts"):
        print("POZOR:     src/, pyproject.toml alebo scripts/ maju necommitnute zmeny")
        info["pozor"] = "necommitnute zmeny"
    print(f"log:       {app.log_path}")
    print(f"den D:     {DAY.date()} (UTC)")
    return info


def measure() -> tuple[dict[str, bool], dict]:
    explains: dict[int, dict] = {}

    def at_volume(volume: int) -> None:
        set_volume(volume)
        explains[volume] = explain_overlap()
        e = explains[volume]
        print(f"\n--- objem {volume:,} riadkov: plan dotazu na prekryv: {e['plan']}, "
              f"vykonanie {e['execution']}")

    def with_index(volume: int) -> None:
        sql("CREATE INDEX i02_reservations_instrument ON reservations (instrument_id)")
        sql("ANALYZE reservations")
        e = explain_overlap()
        explains[f"{volume}+index"] = e
        print(f"--- objem {volume:,} s indexom: plan {e['plan']}, vykonanie {e['execution']}")
        point("E-3", "jeden", TEN_X_N, volume, True)
        sql("DROP INDEX i02_reservations_instrument")
        sql("ANALYZE reservations")

    print("\n=== meranie (cas odozvy potvrdenia, klient) ===")
    at_volume(0)
    point("E-2", "jeden", TEN_X_N, 0, False)

    at_volume(TODAY_VOLUME)
    point("E-0 (K-1)", "kolizia", TODAY_N, TODAY_VOLUME, False)
    point("E-0 (K-2)", "jeden", TODAY_N, TODAY_VOLUME, False)
    point("E-2", "jeden", TEN_X_N, TODAY_VOLUME, False)

    at_volume(TEN_X_VOLUME)
    k1 = point("K-1", "kolizia", TEN_X_N, TEN_X_VOLUME, False)
    k2 = point("K-2", "jeden", TEN_X_N, TEN_X_VOLUME, False)
    k3 = point("K-3", "sused", TEN_X_N, TEN_X_VOLUME, False)
    for n in CURVE_N:
        if n != TEN_X_N:
            point("E-1", "jeden", n, TEN_X_VOLUME, False)
    for n in CURVE_N:
        point("E-1", "rozne", n, TEN_X_VOLUME, False)
    with_index(TEN_X_VOLUME)

    at_volume(CURVE_VOLUME[-1])
    point("E-2", "jeden", TEN_X_N, CURVE_VOLUME[-1], False)
    with_index(CURVE_VOLUME[-1])

    verdicts = {
        "K-1": all_runs_correct_collision(k1, TEN_X_N),
        "K-2": all_runs_confirmed(k2, TEN_X_N) and k2["p95"] <= THRESHOLD_S,
        "K-3": all_runs_confirmed(k3, TEN_X_N + PROBES) and k3["probe_p95"] <= THRESHOLD_S,
    }
    print("\n=== kriteria (model 10x: 50 poziadaviek, 100 000 riadkov) ===")
    print(f"K-1 kolizia:      {'PASS' if verdicts['K-1'] else 'FAIL'}  "
          f"odpovede vo vsetkych behoch: {[r['tally'] for r in k1['runs']][0]}, "
          f"BR-02 dvojice: {sum(r['br02_pairs'] for r in k1['runs'])}")
    print(f"K-2 jeden pristroj: {'PASS' if verdicts['K-2'] else 'FAIL'}  "
          f"p95 {k2['p95'] * 1000:.1f} ms (hranica {THRESHOLD_S * 1000:.0f} ms)")
    print(f"K-3 sused:        {'PASS' if verdicts['K-3'] else 'FAIL'}  "
          f"sonda p95 {k3['probe_p95'] * 1000:.1f} ms (hranica {THRESHOLD_S * 1000:.0f} ms)")
    return verdicts, explains


def main() -> int:
    workdir = pathlib.Path(os.environ.get("I02_OUT") or tempfile.mkdtemp(prefix="swilab-i02-"))
    workdir.mkdir(parents=True, exist_ok=True)
    app = App(workdir)
    info = identity(app)

    blocked = crashed = None
    verdicts: dict[str, bool] = {}
    explains: dict = {}
    try:
        try:
            httpx.get(f"{BASE_URL}/health", timeout=1)
            raise Blocked(f"na porte {APP_PORT} uz nieco bezi", "zastav to alebo nastav I02_APP_PORT")
        except httpx.HTTPError:
            pass
        prepare_container()
        info["postgres"] = sql("SHOW server_version")[0][0]
        print(f"postgres:  {info['postgres']}")
        app.start()
        seed()
        verdicts, explains = measure()
    except Blocked as exc:
        blocked = exc
    except subprocess.CalledProcessError as exc:
        blocked = Blocked(f"prikaz {' '.join(exc.cmd[:2])} zlyhal: {exc.stderr.strip()}",
                          "skontroluj Docker a skript zopakuj")
    except Exception as exc:  # chyba skriptu - verdikt neexistuje
        crashed = exc
        traceback.print_exc()
    finally:
        app.stop()
        if CREATED_CONTAINER and not KEEP:
            subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)

    out = workdir / "results.json"
    out.write_text(json.dumps(
        {"identita": info, "kriteria": verdicts,
         "plany": {str(k): v for k, v in explains.items()}, "body": RESULTS},
        indent=1, default=str,
    ))
    print(f"\nvysledky: {out}")
    print(f"log aplikacie: {app.log_path}")
    if crashed is not None:
        print(f"CHYBA SKRIPTU - {type(crashed).__name__}: {crashed}")
        return 3
    if blocked is not None:
        print(f"BLOCKED - {blocked.cause}")
        print(f"dalsi krok: {blocked.next_step}")
        return 2
    return 0 if verdicts and all(verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
