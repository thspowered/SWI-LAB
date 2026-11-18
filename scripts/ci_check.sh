#!/usr/bin/env bash
# Jeden prikaz pre build, testy a integracnu kontrolu (C09, krok 4).
#
# Ten isty skript spusta CI (.github/workflows/ci.yml) aj clovek lokalne,
# takze "zelene v CI" a "zelene u mna" znamenaju to iste. Co tu nie je,
# CI nekontroluje.
#
# Predpoklady (README, cast C09):
#   - prostredie z requirements-dev.lock + projekt:
#       pip install -r requirements-dev.lock && pip install --no-deps -e .
#   - bezaca databaza na DATABASE_URL (lokalne docker compose up -d --wait db)
#
# Premenne: PYTHON (predvolene python), PORT (8000), DATABASE_URL.
# Navratovy kod: 0 = vsetky kroky presli; inak skript skonci na prvom
# kroku, ktory zlyhal, a vypise ktory to bol.

set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"
# Absolutna cesta - krok 6 meni adresar a relativna by prestala platit.
PYTHON="$(command -v "$PYTHON")"
PORT="${PORT:-8000}"
STEP="priprava"
APP_PID=""

on_exit() {
    local rc=$?
    if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" 2>/dev/null; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
    if [[ $rc -ne 0 ]]; then
        echo
        echo "ZLYHANIE v kroku: $STEP (navratovy kod $rc)"
    fi
}
trap on_exit EXIT

step() {
    STEP="$1"
    echo
    echo "=== $1 ==="
}

sha256() {
    "$PYTHON" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
}

# --- 1. identita behu ------------------------------------------------------
step "1/6 identita behu"
echo "commit:   $(git rev-parse HEAD)"
echo "python:   $("$PYTHON" -c 'import sys; print(sys.version.split()[0])')"
echo "system:   $(uname -sm)"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "POZOR:    pracovny strom ma necommitnute zmeny - artefakt nezodpoveda ziadnemu commitu"
fi

# Zelena kontrola na inych verziach, nez ake su zamknute, by nic nedokazovala.
# Kazda zamknuta verzia musi byt nainstalovana presne; balicky navyse sa iba
# vypisu (lokalne prostredie moze mat nastroje, ktore projekt nepouziva).
"$PYTHON" - <<'PY'
import subprocess, sys
pinned = {}
for line in open("requirements-dev.lock", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#"):
        name, _, ver = line.partition("==")
        pinned[name.lower().replace("_", "-")] = ver
out = subprocess.run([sys.executable, "-m", "pip", "freeze", "--exclude-editable"],
                     capture_output=True, text=True, check=True).stdout
installed = {}
for line in out.splitlines():
    if "==" in line:
        name, _, ver = line.partition("==")
        installed[name.lower().replace("_", "-")] = ver
wrong = {n: (v, installed.get(n)) for n, v in pinned.items() if installed.get(n) != v}
extra = sorted(set(installed) - set(pinned) - {"swilab", "pip"})
if wrong:
    for n, (want, got) in sorted(wrong.items()):
        print(f"  {n}: zamknute {want}, nainstalovane {got}")
    sys.exit("prostredie nezodpoveda requirements-dev.lock")
print(f"lock:     {len(pinned)} balickov, vsetky v zamknutej verzii")
if extra:
    print(f"navyse:   {', '.join(extra)} (mimo lock, kontrolu neovplyvnuje)")
PY

# --- 2. testy --------------------------------------------------------------
step "2/6 pytest"
"$PYTHON" -m pytest -q -p no:cacheprovider

# --- 3. F-01: vysledok nesmie zavisiet od pasma servera --------------------
step "3/6 pytest pri PGTZ=Europe/Prague (ochrana nalezu F-01)"
PGTZ=Europe/Prague "$PYTHON" -m pytest -q -p no:cacheprovider

# --- 4. mutacie: testy musia chybu naozaj zachytit -------------------------
step "4/6 mutacne testovanie"
"$PYTHON" scripts/mutation_check.py

# --- 5. skutocna integracna hranica: HTTP -> aplikacia -> PostgreSQL -------
step "5/6 TC-C08-01..03 proti bezacej aplikacii"
PYTHONPATH=src "$PYTHON" -m uvicorn swilab.main:app --port "$PORT" \
    > "${TMPDIR:-/tmp}/swilab-ci-uvicorn.log" 2>&1 &
APP_PID=$!
for _ in $(seq 1 30); do
    if "$PYTHON" -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=1)" \
        "http://127.0.0.1:$PORT/health" 2>/dev/null; then
        break
    fi
    sleep 1
done
BASE_URL="http://127.0.0.1:$PORT" "$PYTHON" scripts/c08_baseline_tc.py
kill "$APP_PID" 2>/dev/null || true; wait "$APP_PID" 2>/dev/null || true; APP_PID=""

# --- 6. artefakt -----------------------------------------------------------
# SOURCE_DATE_EPOCH = cas posledneho commitu, ktory menil KOD (src/,
# pyproject.toml): ten isty kod da bajtovo ten isty wheel - aj ked medzitym
# pribudli commity iba s dokumentaciou. Identita produktu sa tak nemeni
# zmenou README.
# --no-isolation: pouzije setuptools z locku, nie najnovsi z PyPI.
step "6/6 zostavenie artefaktu"
rm -rf dist build src/*.egg-info
echo "kod naposledy zmeneny v: $(git log -1 --format=%h -- src pyproject.toml)"
SOURCE_DATE_EPOCH="$(git log -1 --format=%ct -- src pyproject.toml)" \
    "$PYTHON" -m build --wheel --no-isolation --outdir dist > /dev/null
rm -rf build src/*.egg-info
(
    cd dist
    for f in *.whl; do
        echo "$(sha256 "$f")  $f"
    done
) | tee dist/SHA256SUMS

STEP="hotovo"
echo
echo "VSETKY KROKY PRESLI"
