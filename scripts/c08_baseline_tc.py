"""Test cases C08 (TC-C08-01..03) proti BEZIACEJ aplikacii a skutocnej databaze.

Navrh test cases - test basis, data, kroky a Expected - je
v docs/evidence-and-evolution.md, cast C08. Expected v tomto skripte je
prepis odtial, nie to, co vratil kod: vzniklo pred prvym behom.

Skript je zamerne NEZAVISLY od kodu aplikacie - neimportuje balik swilab:
  - operacie vola cez HTTP (rovnako ako klient),
  - data pripravi a stav po operacii cita priamo SQL dotazmi (psycopg),
    teda z autoritativnej perzistencie, nie z odpovede API ani z ORM.
Invariant BR-02 overuje vlastnym SQL dotazom, nezavislym od
_overlapping_blocking() v aplikacii.

Spustenie (databaza a aplikacia musia bezat):
    docker compose up -d --wait db
    PYTHONPATH=src .venv/bin/python -m uvicorn swilab.main:app --port 8000 &
    .venv/bin/python scripts/c08_baseline_tc.py

Pozor: skript na zaciatku kazdeho behu robi TRUNCATE vsetkych tabuliek.
Nespustat sucasne s pytest ani s mutation_check.py - zdielaju databazu.

Verdikt kazdeho test case je PASS, FAIL alebo BLOCKED (aplikacia alebo
databaza nebezi). Navratovy kod je 0 iba vtedy, ked su vsetky PASS.

C09 (specifikacia v0.4, CR-08): TC-C08-01 a TC-C08-02 su regresia a ich
Expected sa nemeni. TC-C08-03 sa podla planu z C08 stal oracle novej
poziadavky - nove Expected je zapisane PRED behom proti kodu v0.4
(evidence, cast C09). Nezavisly SQL oracle BR-02 je prepisany na pravidlo
v0.4.
"""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import psycopg

REPO = pathlib.Path(__file__).resolve().parent.parent
BASELINE = "b0974e7"
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")


def _database_url() -> str:
    """DATABASE_URL z prostredia, inak z .env.example - heslo tak nie je
    v skripte druhy raz a vo vypise sa maskuje."""
    url = os.environ.get("DATABASE_URL")
    if url is None:
        for line in (REPO / ".env.example").read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    if url is None:
        sys.exit("DATABASE_URL nie je nastavena ani v .env.example")
    return url.replace("postgresql+psycopg://", "postgresql://")


def _masked(url: str) -> str:
    head, _, tail = url.partition("@")
    scheme, _, user_pass = head.partition("://")
    user = user_pass.split(":", 1)[0]
    return f"{scheme}://{user}:***@{tail}"


DATABASE_URL = _database_url()

# --- pevne identifikatory, aby sa dal vypis citat -------------------------


def _id(suffix: str) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-{suffix:0>12}")


R1 = _id("a1")  # exkluzivny pristroj so schvalovanim
S1 = _id("51")  # student, vlastnik B2 (TC-01, TC-02) a B1 (TC-03)
S2 = _id("52")  # student, vlastnik B1 (TC-01, TC-02) a B2 (TC-03)
V1 = _id("f1")  # veduci laboratoria, schvalovatel - nie je vlastnikom
B1 = _id("b1")
B2 = _id("b2")
NAMES = {R1: "R1", S1: "S1", S2: "S2", V1: "V1", B1: "B1", B2: "B2"}

# Den testu: o 7 dni, aby boli vsetky intervaly v buducnosti voci hodinam
# servera (API cita skutocny cas, posunut ho zvonku nemozno).
DAY = (datetime.now(UTC) + timedelta(days=7)).replace(
    hour=0, minute=0, second=0, microsecond=0
)


def at(hh: int, mm: int = 0) -> datetime:
    return DAY + timedelta(hours=hh, minutes=mm)


RESERVATION_COLUMNS = [
    "id", "instrument_id", "user_id", "starts_at", "ends_at", "state", "created_at",
    "decision_deadline",
]

# Nezavisly prepis BR-02 + BR-08 (v0.4) do SQL: dvojice blokujucich
# rezervacii toho isteho pristroja, ktore sa prekryvaju podla BR-01.
# Blokuje CONFIRMED a ziva ziadost - pred terminom rozhodnutia AJ pred
# starts_at. Obe podmienky su tu zamerne, hoci aplikacia porovnava iba
# termin (DD-8): oracle prepisuje pravidlo zo specifikacie, nie navrh,
# takze sa nespolieha ani na obmedzenie DD-9.
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

# Prekryv v case bez ohladu na stav - aby bolo vidiet, ze kolizia v case
# existuje, hoci blokujuca dvojica nie.
TIME_OVERLAP_PAIRS = """
    SELECT a.id, b.id
    FROM reservations a
    JOIN reservations b
      ON a.instrument_id = b.instrument_id AND a.id < b.id
    WHERE a.starts_at < b.ends_at AND b.starts_at < a.ends_at
"""


# --- databaza --------------------------------------------------------------


def connect() -> psycopg.Connection:
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def reset_and_seed_common(conn: psycopg.Connection) -> None:
    """Izolovany vychodiskovy stav: prazdne tabulky + R1, S1, S2, V1
    a platne certifikaty studentov. Priamo v databaze (TBD-02)."""
    conn.execute(
        "TRUNCATE reservations, certifications, instruments, users "
        "RESTART IDENTITY CASCADE"
    )
    conn.execute(
        "INSERT INTO instruments (id, name, category, location, is_active, "
        "requires_approval) VALUES (%s, 'Zeiss LSM 900', 'MICROSCOPE', "
        "'Lab B2.14', true, true)",
        (R1,),
    )
    for user_id, name, email, role in [
        (S1, "Student S1", "s1@c08.local", "STUDENT"),
        (S2, "Student S2", "s2@c08.local", "STUDENT"),
        (V1, "Veduci V1", "v1@c08.local", "SUPERVISOR"),
    ]:
        conn.execute(
            "INSERT INTO users (id, full_name, email, role) VALUES (%s, %s, %s, %s)",
            (user_id, name, email, role),
        )
    for user_id in (S1, S2):
        conn.execute(
            "INSERT INTO certifications (id, user_id, category, valid_until) "
            "VALUES (%s, %s, 'MICROSCOPE', %s)",
            (uuid.uuid4(), user_id, DAY + timedelta(days=30)),
        )


def insert_reservation(
    conn, rid, owner, starts_at, ends_at, state, created_at=None, decision_deadline=None
):
    """Priamy zapis - musi splnit obmedzenie DD-9 rovnako ako aplikacia:
    ziadost (PENDING_APPROVAL) ma termin rozhodnutia, najneskor v starts_at.
    Bez zadaneho terminu dostane ziadost termin = starts_at, teda ziadost
    spred v0.4 (AC-09)."""
    if state == "PENDING_APPROVAL" and decision_deadline is None:
        decision_deadline = starts_at
    conn.execute(
        "INSERT INTO reservations (id, instrument_id, user_id, starts_at, "
        "ends_at, state, created_at, decision_deadline) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (rid, R1, owner, starts_at, ends_at, state,
         created_at or datetime.now(UTC), decision_deadline),
    )


def reservations(conn) -> dict:
    rows = conn.execute(
        "SELECT id, state, starts_at, ends_at, user_id, instrument_id, created_at "
        "FROM reservations ORDER BY starts_at, id"
    ).fetchall()
    return {row[0]: row for row in rows}


def pairs(conn, query) -> list:
    return [tuple(NAMES.get(i, str(i)) for i in row) for row in conn.execute(query)]


def schema_fingerprint(conn) -> str:
    """Odtlacok schemy - stlpce, enumy a obmedzenia. Schema nema verziu
    ani migracie (R-04), toto je jej porovnatelna identita."""
    parts = []
    parts += conn.execute(
        "SELECT table_name, column_name, data_type, is_nullable, "
        "coalesce(column_default, '') FROM information_schema.columns "
        "WHERE table_schema = 'public' ORDER BY 1, 2"
    ).fetchall()
    parts += conn.execute(
        "SELECT t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) "
        "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid GROUP BY 1 ORDER BY 1"
    ).fetchall()
    parts += conn.execute(
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
        "FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
    ).fetchall()
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


# --- HTTP -----------------------------------------------------------------


def call(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    """Posle request a vypise ho presne tak, ako odisiel - s kodovanou query
    (napr. `+00:00` ako `%2B00:00`), aby sa dal skopirovat do curl."""
    body = kwargs.get("json")
    response = client.request(method, path, **kwargs)
    shown = response.request.url.raw_path.decode()
    for value, name in NAMES.items():
        shown = shown.replace(str(value), name)
    print(f"      HTTP {method} {shown}" + (f"  {_named(body)}" if body else ""))
    print(f"      -> {response.status_code} {_named(_body(response))}")
    return response


def _body(response: httpx.Response):
    """Telo odpovede; ak nie je JSON (napr. 500), aspon text - inak by
    neocakavana chyba servera skoncila padom skriptu, nie verdiktom FAIL."""
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _named(data) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str)
    for value, name in NAMES.items():
        text = text.replace(str(value), name)
    return text


# --- kostra test case -----------------------------------------------------


class Case:
    def __init__(self, tc_id: str, run: int) -> None:
        self.tc_id = tc_id
        self.run = run
        self.results: list[tuple[int, bool]] = []
        self.observed: list = []

    def step(self, number: int, action: str, expected: str, actual, ok: bool) -> None:
        self.results.append((number, ok))
        self.observed.append(actual)
        mark = "OK  " if ok else "FAIL"
        print(f"  [{mark}] krok {number}: {action}")
        print(f"         expected: {expected}")
        print(f"         actual:   {actual}")

    @property
    def verdict(self) -> str:
        return "PASS" if all(ok for _, ok in self.results) else "FAIL"


def state_of(rows: dict) -> dict:
    return {NAMES[rid]: row[1] for rid, row in rows.items()}


def interval_of(row) -> str:
    return f"[{row[2]:%H:%M}, {row[3]:%H:%M})"


def tc_01(client, run: int) -> Case:
    """Pozitivny: schvalenie ziadosti susediacej s potvrdenou rezervaciou."""
    case = Case("TC-C08-01", run)
    with connect() as conn:
        reset_and_seed_common(conn)
        insert_reservation(conn, B1, S2, at(10), at(11), "CONFIRMED")
        insert_reservation(conn, B2, S1, at(11), at(12), "PENDING_APPROVAL")
        initial = reservations(conn)
        case.step(
            1, "reset + R1, B1, B2",
            "presne jedna CONFIRMED (B1); B2 PENDING_APPROVAL",
            state_of(initial),
            state_of(initial) == {"B1": "CONFIRMED", "B2": "PENDING_APPROVAL"},
        )
        intervals = {NAMES[r]: interval_of(row) for r, row in initial.items()}
        blocking = pairs(conn, BLOCKING_OVERLAP_PAIRS)
        case.step(
            2, "nezavisle precitat intervaly a stavy (SQL)",
            "B1 [10:00, 11:00), B2 [11:00, 12:00); ziadna blokujuca dvojica v prekryve",
            {"intervaly": intervals, "blokujuce_prekryvy": blocking},
            intervals == {"B1": "[10:00, 11:00)", "B2": "[11:00, 12:00)"} and blocking == [],
        )
    response = call(
        client, "POST", f"/reservations/{B2}/approve", json={"requested_by": str(V1)}
    )
    body = _body(response)
    case.step(
        3, "POST approve(B2), schvaluje V1 (SUPERVISOR, nie vlastnik)",
        "200, state CONFIRMED (REQ-11; susedny interval nekoliduje, BR-01)",
        f"{response.status_code} {body.get('state', body.get('code'))}",
        response.status_code == 200 and body.get("state") == "CONFIRMED",
    )
    with connect() as conn:
        after = reservations(conn)
        blocking = pairs(conn, BLOCKING_OVERLAP_PAIRS)
        case.step(
            4, "znovu nacitat commitnuty stav z databazy (nove spojenie)",
            "B1 CONFIRMED, B2 CONFIRMED; ziadna blokujuca dvojica v prekryve (BR-02)",
            {"stavy": state_of(after), "blokujuce_prekryvy": blocking},
            state_of(after) == {"B1": "CONFIRMED", "B2": "CONFIRMED"} and blocking == [],
        )
    return case


def tc_02(client, run: int) -> Case:
    """Negativny: potvrdenie rezervacie, ktora sa prekryva s potvrdenou."""
    case = Case("TC-C08-02", run)
    with connect() as conn:
        reset_and_seed_common(conn)
        insert_reservation(conn, B1, S2, at(10), at(11), "CONFIRMED")
        insert_reservation(conn, B2, S1, at(10, 30), at(11, 30), "DRAFT")
        initial = reservations(conn)
        case.step(
            1, "reset + R1, B1, B2",
            "iba B1 je CONFIRMED; B2 DRAFT",
            state_of(initial),
            state_of(initial) == {"B1": "CONFIRMED", "B2": "DRAFT"},
        )
        overlap = pairs(conn, TIME_OVERLAP_PAIRS)
        blocking = pairs(conn, BLOCKING_OVERLAP_PAIRS)
        case.step(
            2, "nezavisle precitat vychodiskovy stav (SQL)",
            "prekryv v case B1-B2 existuje, blokujuca dvojica nie",
            {"prekryvy_v_case": overlap, "blokujuce_prekryvy": blocking},
            overlap == [("B1", "B2")] and blocking == [],
        )
    response = call(
        client, "POST", f"/reservations/{B2}/confirm", json={"requested_by": str(S1)}
    )
    body = _body(response)
    case.step(
        3, "POST confirm(B2), potvrdzuje vlastnik S1",
        "zamietnute: 409, code OVERLAP (REQ-04, BR-02)",
        f"{response.status_code} {body.get('code', body.get('state'))}",
        response.status_code == 409 and body.get("code") == "OVERLAP",
    )
    with connect() as conn:
        after = reservations(conn)
        blocking = pairs(conn, BLOCKING_OVERLAP_PAIRS)
        b1_unchanged = after[B1] == initial[B1]
        case.step(
            4, "precitat ulozeny stav po rollbacku (nove spojenie)",
            "B1 bez zmeny (cely riadok), B2 DRAFT; ziadna blokujuca dvojica v prekryve",
            {"stavy": state_of(after), "B1_bez_zmeny": b1_unchanged,
             "blokujuce_prekryvy": blocking},
            state_of(after) == {"B1": "CONFIRMED", "B2": "DRAFT"}
            and b1_unchanged and blocking == [],
        )
    return case


def tc_03(client, run: int) -> Case:
    """Oracle CR-08 (v0.4). Do C08 charakterizacia obmedzenia - zabudnuta
    ziadost blokovala pristroj az do zaciatku a dala sa schvalit, nech bola
    podana kedykolvek. Nove Expected je z planu v C08 (evidence, TC-C08-03,
    *Rozhodnutie*) a zo specifikacie v0.4, zapisane pred behom."""
    case = Case("TC-C08-03", run)
    long_ago = datetime.now(UTC) - timedelta(days=10)
    # Ziadost podana pred 10 dnami: termin rozhodnutia = podanie + 48 h
    # (DD-8; starts_at je az o 7 dni). Bez terminu v minulosti by ju AC-09
    # povazovalo za ziadost spred zmeny a ostala by ziva.
    deadline = long_ago + timedelta(hours=48)
    with connect() as conn:
        reset_and_seed_common(conn)
        insert_reservation(
            conn, B1, S1, at(10), at(11), "PENDING_APPROVAL",
            created_at=long_ago, decision_deadline=deadline,
        )
        insert_reservation(conn, B2, S2, at(10), at(11), "DRAFT")
        initial = reservations(conn)
        case.step(
            1, "reset + R1, B1 (ziadost podana pred 10 dnami, termin rozhodnutia "
            "pred 8 dnami), B2",
            "B1 PENDING_APPROVAL, B2 DRAFT",
            state_of(initial),
            state_of(initial) == {"B1": "PENDING_APPROVAL", "B2": "DRAFT"},
        )
        columns = [
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'reservations' "
                "ORDER BY ordinal_position"
            )
        ]
        case.step(
            2, "precitat stlpce tabulky reservations (information_schema)",
            ", ".join(RESERVATION_COLUMNS) + " - termin rozhodnutia sa uklada (DD-8)",
            columns,
            columns == RESERVATION_COLUMNS,
        )
    response = call(
        client, "GET", f"/instruments/{R1}/availability",
        params={"starts_at": at(10).isoformat(), "ends_at": at(11).isoformat()},
    )
    body = _body(response)
    conflicts = [NAMES.get(uuid.UUID(i), i) for i in body.get("conflicting_reservation_ids", [])]
    case.step(
        3, "GET availability R1 [10:00, 11:00)",
        "200, AVAILABLE, ziadna kolizia - ziadost po termine neblokuje (AC-03)",
        f"{response.status_code} {body.get('status')} {body.get('reason')} {conflicts}",
        response.status_code == 200 and body.get("status") == "AVAILABLE"
        and conflicts == [],
    )
    response = call(
        client, "POST", f"/reservations/{B2}/confirm", json={"requested_by": str(S2)}
    )
    body = _body(response)
    case.step(
        4, "POST confirm(B2), potvrdzuje vlastnik S2",
        "200, state PENDING_APPROVAL a v odpovedi termin rozhodnutia - B1 "
        "neblokuje (AC-03), R1 vyzaduje schvalenie (REQ-10, AC-06)",
        # Do pozorovania ide iba to, ci termin v odpovedi je - nie jeho
        # hodnota. Ta zavisi od skutocnych hodin servera, takze dva behy by
        # sa v nej vzdy lisili o cas medzi nimi a porovnanie behov by hlasilo
        # rozdiel, ktory nie je chybou (nalez prveho behu na kode v0.4).
        # Hodnota je vidiet v riadku HTTP vyssie; presne ju overuje
        # test_http_confirm_of_a_request_returns_its_decision_deadline.
        f"{response.status_code} {body.get('state', body.get('code'))} "
        f"decision_deadline="
        f"{'uvedeny' if body.get('decision_deadline') is not None else 'chyba'}",
        response.status_code == 200 and body.get("state") == "PENDING_APPROVAL"
        and body.get("decision_deadline") is not None,
    )
    response = call(
        client, "POST", f"/reservations/{B1}/approve", json={"requested_by": str(V1)}
    )
    body = _body(response)
    case.step(
        5, "POST approve(B1), schvaluje V1",
        "409 EXPIRED - termin rozhodnutia uplynul (AC-02, REQ-15)",
        f"{response.status_code} {body.get('code', body.get('state'))}",
        response.status_code == 409 and body.get("code") == "EXPIRED",
    )
    with connect() as conn:
        after = reservations(conn)
        blocking = pairs(conn, BLOCKING_OVERLAP_PAIRS)
        case.step(
            6, "precitat ulozeny stav (nove spojenie)",
            "B1 EXPIRED, B2 PENDING_APPROVAL; ziadna blokujuca dvojica (BR-02)",
            {"stavy": state_of(after), "blokujuce_prekryvy": blocking},
            state_of(after) == {"B1": "EXPIRED", "B2": "PENDING_APPROVAL"}
            and blocking == [],
        )
    return case


# --- beh ------------------------------------------------------------------


def header() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    app_diff = subprocess.run(
        ["git", "diff", "--quiet", BASELINE, "--", "src", "pyproject.toml",
         "docker-compose.yml"], cwd=REPO
    ).returncode
    print(f"cas behu (UTC):   {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}")
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    print(f"HEAD:             {head}" + (" + necommitnute zmeny" if dirty else ""))
    print(f"kod aplikacie:    {'= ' + BASELINE if app_diff == 0 else 'LISI SA od ' + BASELINE}")
    print(f"databaza:         {_masked(DATABASE_URL)}")
    print(f"aplikacia:        {BASE_URL}")
    print(f"den testu (UTC):  {DAY:%Y-%m-%d}")


def main() -> int:
    header()
    try:
        with connect() as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            print(f"PostgreSQL:       {version}")
            print(f"odtlacok schemy:  {schema_fingerprint(conn)}")
        health = httpx.get(f"{BASE_URL}/health", timeout=5)
        health.raise_for_status()
    except (psycopg.OperationalError, httpx.HTTPError) as exc:
        print(f"\nBLOCKED - infrastruktura nebezi: {exc}")
        print("dalsi krok: docker compose up -d --wait db; spustit uvicorn (docstring)")
        return 2

    try:
        verdicts = run_all()
    except (psycopg.OperationalError, httpx.TransportError) as exc:
        # Vypadok uprostred behu je prekazka, nie business vysledok - FAIL by
        # tvrdil, ze system sa sprava zle, hoci sa nedal ani overit.
        print(f"\nBLOCKED - infrastruktura vypadla pocas behu: {exc}")
        print("dalsi krok: overit databazu a aplikaciu, beh zopakovat od zaciatku")
        return 2

    print("\n=== suhrn ===")
    for tc_id, verdict in verdicts.items():
        print(f"{tc_id}: {verdict}")
    return 0 if all(v == "PASS" for v in verdicts.values()) else 1


def run_all() -> dict:
    verdicts = {}
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        for tc in (tc_01, tc_02, tc_03):
            observed = []
            for run in (1, 2):
                print(f"\n=== {tc.__name__.replace('tc_', 'TC-C08-')} - beh {run} ===")
                case = tc(client, run)
                print(f"  verdikt behu {run}: {case.verdict}")
                observed.append((case.verdict, case.observed))
            # Pozorovania neobsahuju casove peciatky, takze dva behy nad
            # tymi istymi vstupmi sa musia zhodovat doslova.
            same = observed[0] == observed[1]
            verdict = observed[0][0] if same else "FAIL"
            print(f"  opakovanie po resete dalo {'ROVNAKY' if same else 'INY'} vysledok")
            verdicts[case.tc_id] = verdict
        with connect() as conn:
            reset_and_seed_common(conn)
    return verdicts


if __name__ == "__main__":
    sys.exit(main())
