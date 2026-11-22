"""Review I-02 - dva doplnkove pokusy k meraniu scripts/i02_meranie.py.

Pouziva data, davky a statistiku skriptu merania (import modulu), aby boli
cisla porovnatelne. Rovnaky kontajner swilab-i02-db (port 5435) a rovnaky
port aplikacie (8020); vyvojovu databazu nemeni.

B - na co caka sused horuceho pristroja (bod 5 v "Co meranie ukazalo"):
    K-3 pri 100 000 riadkoch s predvolenym poolom (5 + 10) a threadpoolom
    (40), s vacsim poolom, s vacsim threadpoolom a s oboma. Aplikacia je ta
    ista, parametre nastavi scripts/i02_review_app.py.

A - R-08: v merani lezi cele pozadie na 20 INYCH pristrojoch, horuci
    pristroj A historiu nema. Tu je A jednym z 20 pristrojov pozadia (1/20
    objemu). Bez indexu, s indexom (instrument_id) a s indexom
    (instrument_id, ends_at) - indexy iba v izolovanej databaze.

Bez PASS/FAIL - prieskum ako E-0 az E-3.

Spustenie (Docker musi bezat, prostredie z requirements-dev.lock):
    python scripts/i02_review.py

Navratovy kod: 0 = dobehlo, vsetky potvrdenia 200 CONFIRMED a BR-02 drzi;
1 = niektora davka dopadla inak; 2 = BLOCKED; 3 = chyba skriptu.
Vysledky do review.json v I02_OUT (predvolene novy adresar v TMPDIR).
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import timedelta

import httpx

import i02_meranie as m

SCRIPTS = pathlib.Path(__file__).resolve().parent

#: B - (nazov, pool, threadpool); None = predvolene. Predvolene na konci
#: znova, aby bolo vidiet rozptyl medzi behmi.
POOL_VARIANTS = [
    ("predvolene 5+10 / 40", None, None),
    ("pool 70 / 40", "70", None),
    ("5+10 / threadpool 200", None, "200"),
    ("pool 70 / threadpool 200", "70", "200"),
    ("predvolene 5+10 / 40 (znova)", None, None),
]

#: A - indexy, iba v izolovanej databaze
INDEXES = [
    ("bez indexu", None),
    ("(instrument_id)", "CREATE INDEX i02_review ON reservations (instrument_id)"),
    ("(instrument_id, ends_at)",
     "CREATE INDEX i02_review ON reservations (instrument_id, ends_at)"),
]
HISTORY_VOLUMES = (m.TEN_X_VOLUME, 1_000_000)


class ReviewApp(m.App):
    """Aplikacia cez i02_review_app s inym poolom alebo threadpoolom."""

    def __init__(self, workdir: pathlib.Path, pool: str | None, threads: str | None) -> None:
        super().__init__(workdir)
        self.pool, self.threads = pool, threads

    def start(self) -> None:
        env = os.environ | {
            "DATABASE_URL": m.APP_DATABASE_URL,
            "PYTHONPATH": f"{SCRIPTS}{os.pathsep}{m.REPO / 'src'}",
            "PYTHONUNBUFFERED": "1",
            "REVIEW_POOL": self.pool or "",
            "REVIEW_THREADS": self.threads or "",
        }
        with open(self.log_path, "a") as log:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "i02_review_app:app",
                 "--port", str(m.APP_PORT)],
                cwd=self.workdir, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 30
        while True:
            if self.proc.poll() is not None:
                raise m.Blocked(f"aplikacia pri starte skoncila s kodom {self.proc.returncode}",
                                f"pozri {self.log_path}")
            try:
                if httpx.get(f"{m.BASE_URL}/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise m.Blocked("aplikacia neodpoveda ani po 30 s", f"pozri {self.log_path}")
            time.sleep(0.3)


def summary(result: dict) -> dict:
    keys = ("label", "scenario", "n", "volume", "p50", "p95", "max", "makespan_median",
            "probe_p95")
    return {
        **{k: result[k] for k in keys if k in result},
        "tallies": [run["tally"] for run in result["runs"]],
        "states": [run["states"] for run in result["runs"]],
        "br02_pairs": sum(run["br02_pairs"] for run in result["runs"]),
    }


def pool_experiment(workdir: pathlib.Path) -> list[dict]:
    print("\n=== B: na co caka sused horuceho pristroja (K-3, 100 000 riadkov) ===")
    out = []
    seeded = False
    for name, pool, threads in POOL_VARIANTS:
        app = ReviewApp(workdir, pool, threads)
        try:
            app.start()
            if not seeded:
                m.seed()
                m.set_volume(m.TEN_X_VOLUME)
                seeded = True
            result = m.point(f"B {name}", "sused", m.TEN_X_N, m.TEN_X_VOLUME, False)
        finally:
            app.stop()
        out.append({"variant": name, "pool": pool, "threads": threads,
                    "correct": m.all_runs_confirmed(result, m.TEN_X_N + m.PROBES),
                    **summary(result)})
    return out


def history_experiment(workdir: pathlib.Path) -> list[dict]:
    """Horuci pristroj A je jednym z 20 pristrojov pozadia."""
    print("\n=== A: horuci pristroj s vlastnou historiou (1/20 objemu) ===")
    m.sql("TRUNCATE reservations")
    m.BACKGROUND = [m.HOT, *m.BACKGROUND[: m.BACKGROUND_INSTRUMENTS - 1]]

    def cleanup_measured_only() -> None:
        # rezervacie behu su v buducnosti (den D), historia v minulosti
        m.sql("DELETE FROM reservations WHERE instrument_id = ANY(%s) AND starts_at >= %s",
              (m.MEASURED_INSTRUMENTS, m.DAY - timedelta(days=1)))
        m.sql("VACUUM reservations")

    m.cleanup = cleanup_measured_only
    # Historia je bez prekryvov z konstrukcie a cela v minulosti. Bez tohto
    # obmedzenia by kontrola BR-02 spajala 50 000 x 50 000 riadkov pristroja A.
    m.BLOCKING_OVERLAP_PAIRS += (
        "  AND a.starts_at > now() - interval '1 day'\n"
        "  AND b.starts_at > now() - interval '1 day'\n"
    )

    out = []
    app = m.App(workdir)            # povodna aplikacia, predvoleny pool
    try:
        app.start()
        for volume in HISTORY_VOLUMES:
            m.set_volume(volume)
            hot_rows = m.sql("SELECT count(*) FROM reservations WHERE instrument_id = %s",
                             (m.HOT,))[0][0]
            print(f"\n--- objem {volume:,} riadkov, z toho pristroj A {hot_rows:,}")
            for name, ddl in INDEXES:
                if ddl:
                    m.sql(ddl)
                    m.sql("ANALYZE reservations")
                plan = m.explain_overlap()
                print(f"    index {name}: plan {plan['plan']}, vykonanie {plan['execution']}")
                result = m.point(f"A {name}", "jeden", m.TEN_X_N, volume, bool(ddl))
                out.append({"index": name, "hot_rows": hot_rows, "plan": plan["plan"],
                            "execution": plan["execution"], "explain": plan["full"],
                            "correct": m.all_runs_confirmed(result, m.TEN_X_N),
                            **summary(result)})
                if ddl:
                    m.sql("DROP INDEX i02_review")
                    m.sql("ANALYZE reservations")
    finally:
        app.stop()
    return out


def main() -> int:
    workdir = pathlib.Path(os.environ.get("I02_OUT") or tempfile.mkdtemp(prefix="swilab-i02r-"))
    workdir.mkdir(parents=True, exist_ok=True)
    info = m.identity(m.App(workdir))
    results: dict = {"identita": info}
    blocked = crashed = None
    try:
        try:
            httpx.get(f"{m.BASE_URL}/health", timeout=1)
            raise m.Blocked(f"na porte {m.APP_PORT} uz nieco bezi",
                            "zastav to alebo nastav I02_APP_PORT")
        except httpx.HTTPError:
            pass
        m.prepare_container()
        info["postgres"] = m.sql("SHOW server_version")[0][0]
        print(f"postgres:  {info['postgres']}")
        results["B"] = pool_experiment(workdir)
        results["A"] = history_experiment(workdir)
    except m.Blocked as exc:
        blocked = exc
    except subprocess.CalledProcessError as exc:
        blocked = m.Blocked(f"prikaz {' '.join(exc.cmd[:2])} zlyhal: {exc.stderr.strip()}",
                            "skontroluj Docker a skript zopakuj")
    except Exception as exc:  # chyba skriptu - vysledok neexistuje
        crashed = exc
        traceback.print_exc()
    finally:
        if m.CREATED_CONTAINER and not m.KEEP:
            subprocess.run(["docker", "rm", "-f", m.CONTAINER], capture_output=True)

    out = workdir / "review.json"
    out.write_text(json.dumps(results, indent=1, default=str))
    print(f"\nvysledky: {out}")
    if crashed is not None:
        print(f"CHYBA SKRIPTU - {type(crashed).__name__}: {crashed}")
        return 3
    if blocked is not None:
        print(f"BLOCKED - {blocked.cause}")
        print(f"dalsi krok: {blocked.next_step}")
        return 2
    wrong = [r["label"] for r in results.get("B", []) + results.get("A", []) if not r["correct"]]
    if wrong:
        print(f"POZOR - ine odpovede alebo stav ako 200 CONFIRMED: {wrong}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
