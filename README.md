# SWI-LAB — rezervačný systém laboratórnych prístrojov

Semestrálny projekt SWI. Rezervácia laboratórnych prístrojov: kto, aký prístroj
a na aký čas — s kontrolou, že sa dve potvrdené rezervácie neprekrývajú a že
prístroj obsluhuje iba certifikovaný človek.

## Tím

**Názov tímu:** SWI-LAB

| Člen          | Rola v C01                                       |
| ------------- | ------------------------------------------------ |
| Tomáš Krišica | založenie repozitára, review PR pred integráciou |
| Tomáš Hrubý   | engineering spike, PR                            |

**Repozitár:** https://github.com/thspowered/SWI-LAB

## Doména

- **Instrument** — rezervovaný prístroj (mikroskop, spektrometer, 3D tlačiareň, centrifúga).
- **Reservation** — rezervácia na interval `[starts_at, ends_at)`, v jednom zo šiestich
  stavov: `DRAFT`, `PENDING_APPROVAL`, `CONFIRMED`, `CANCELLED`, `REJECTED`, `EXPIRED`.
- **User** — študent alebo vedúci laboratória.
- **Certification** — oprávnenie používateľa na kategóriu prístroja, platné do dátumu.

**Operácie:** create · confirm/approve · cancel · check availability

**Pravidlá:**

1. Dve **blokujúce** rezervácie toho istého prístroja sa nesmú prekrývať —
   od v0.2 blokuje `CONFIRMED` aj živá žiadosť `PENDING_APPROVAL` (BR-02).
2. Rezerváciu možno potvrdiť len s platným certifikátom na kategóriu prístroja.

**Externá hranica:** Notification Service.

Záväzné znenie je v [docs/specification.md](docs/specification.md) — baseline v0.4.
Táto sekcia je len prehľad.

Podrobne v [docs/intent-and-change.md](docs/intent-and-change.md).

## Stack

Python 3.12+ · FastAPI · SQLAlchemy 2.0 · PostgreSQL 16 · pytest

Zdôvodnenie v [docs/architecture-and-decisions.md](docs/architecture-and-decisions.md).

## Ako to spustiť

Predpoklady: nainštalovaný a **spustený** Docker, Python **3.12+**.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.lock
pip install --no-deps --no-build-isolation -e .

cp .env.example .env
docker compose up -d --wait db

pytest -v
uvicorn swilab.main:app --reload
```

Ak `docker compose up` hlási *container name "/swilab-db" is already in use*,
na stroji už existuje databáza z iného checkoutu tohto repozitára
(`docker-compose.yml` má pevné `container_name`). Odstráň ju
`docker rm -f swilab-db` a príkaz zopakuj — dáta nemá, kontajner je bez
volume (`ADR-02`). Našiel to čistý checkout druhého člena v C09.

Závislosti sa inštalujú zo **zamknutého** súboru `requirements-dev.lock`, nie
z `pyproject.toml` — ten uvádza iba spodné hranice a dával by pri každej
inštalácii iné verzie (časť C09). Celú kontrolu, ktorú robí CI, spustí lokálne
`scripts/ci_check.sh`.

Aplikácia beží na `http://127.0.0.1:8000`, kontrola stavu na `/health`,
interaktívna dokumentácia API na `/docs`.
Databáza počúva na porte **5433**, aby nekolidovala s lokálnym Postgresom.

Ak `import swilab` zlyhá aj po `pip install -e`, spusti s `PYTHONPATH=src`.
Na macOS stačí, aby mal `.pth` súbor v `site-packages` flag `hidden` —
CPython skryté `.pth` zámerne preskakuje a editable install prestane fungovať
bez akejkoľvek chybovej hlášky (`chflags nohidden` to opraví).

## Operácie (baseline v0.4)

| Operácia | Endpoint |
| -------- | -------- |
| OP-01 Create Reservation | `POST /reservations` |
| OP-02 Check Availability | `GET /instruments/{id}/availability?starts_at=&ends_at=` |
| OP-03 Confirm Reservation | `POST /reservations/{id}/confirm` |
| OP-04 Cancel Reservation | `POST /reservations/{id}/cancel` |
| OP-05 Approve Reservation | `POST /reservations/{id}/approve` · `POST /reservations/{id}/reject` |

Prístroje s príznakom `requires_approval` idú cez schvaľovanie: potvrdenie
vytvorí žiadosť (`PENDING_APPROVAL`), ktorá prístroj blokuje, kým o nej vedúci
nerozhodne alebo kým neuplynie jej **termín rozhodnutia** — 48 h od podania,
najneskôr začiatok rezervácie (`EXPIRED`, BR-08 v0.4). Termín vráti odpoveď
potvrdenia v poli `decision_deadline`.

Stavy rezervácie: `DRAFT` · `PENDING_APPROVAL` · `CONFIRMED` · `CANCELLED` ·
`REJECTED` · `EXPIRED`.

Správa prístrojov, používateľov a certifikátov **nie je** súčasťou v0.2
(TBD-02) — tieto dáta sa zakladajú priamo v databáze.

Schéma sa zatiaľ vytvára cez `create_all`, bez migrácií (architektonický driver
pre C03). Po prechode z v0.1 na v0.2 preto databázu prestav:
`docker compose down && docker compose up -d --wait db`.

Celé chovanie sa dá predviesť jedným príkazom proti bežiacej aplikácii;
pre každú operáciu jeden úspešný a jeden negatívny / hraničný príklad:

```bash
PYTHONPATH=src .venv/bin/python scripts/demo.py
```

Zastavenie databázy: `docker compose down` (dáta sa nezachovávajú, kontajner
nemá volume — v tejto fáze projektu je to zámer).

## CP1 walking skeleton

Jedna end-to-end cesta, ktorá bude skutočne spustiteľná po C03 / pred C04:

```
POST /reservations
  -> validate    (prístroj existuje a je aktívny; ends_at > starts_at)
  -> persist     (uloženie do PostgreSQL v stave DRAFT)
  -> return      (201 Created + ID rezervácie)
  -> automated check
       integračný test overí status 201, vrátené ID
       a prítomnosť riadku v databáze
```

V C01 bola táto cesta iba **definovaná**. V C02 je **implementovaná a overená**
(`tests/test_api.py::test_http_create_reservation`) — vrátane zvyšných štyroch
operácií. Architektúru tejto cesty rieši až C03.

## C01 Definition of Done

- [x] tím — 2 členovia
- [x] spoločný repozitár, prístup majú všetci
- [x] jasný reservation domain
- [x] Resource + Reservation + User
- [x] zmysluplné stavy rezervácie
- [x] create + confirm/approve + cancel + availability
- [x] spoločné pravidlo o neprekrývaní
- [x] 1 vlastné domain-specific pravidlo
- [x] 1 externá hranica systému
- [x] kompletný Project Frame
- [x] 1 future pressure Q/C/R/L
- [x] 1 zrecenzovaná a integrovaná zmena — [issue #1](https://github.com/thspowered/SWI-LAB/issues/1) → [PR #2](https://github.com/thspowered/SWI-LAB/pull/2), review pred integráciou
- [x] 1 skutočne vykonaný engineering spike
- [x] evidence + decision zo spiku
- [x] definovaný CP1 walking skeleton

## C02 Definition of Done

- [x] všetky štyri základné operácie majú úplnú textovú časť špecifikácie
- [x] každý prijatý požiadavok prešiel kontrolou prijatia (REQ-01..REQ-16)
- [x] spoločné doménové pravidlá a invarianty sú definované iba raz (BR-01..BR-08)
- [x] existuje diagram prípadov užitia
- [x] existuje stavový diagram celého životného cyklu Reservation
- [x] text, požiadavky a oba pohľady sú vzájomne konzistentné — *rozpor
  `REQ-16` ↔ OP-04, zistený v C03 a potvrdený v C06 (nález F-03), je
  **vyriešený v špecifikácii v0.3**; rozbor je na konci
  [časti 9](docs/specification.md)*
- [x] baseline v0.1 explicitne schválená tímom — [PR #3](https://github.com/thspowered/SWI-LAB/pull/3)
- [x] aplikácia demonštruje všetky štyri základné operácie — [PR #4](https://github.com/thspowered/SWI-LAB/pull/4)
- [x] pre každú operáciu spustený úspešný aj negatívny / hraničný príklad
- [x] dopad zmeny analyzovaný pred úpravou špecifikácie — commit `ada6d6e` pred `e6df8cf`
- [x] dotknuté i nedotknuté časti explicitne identifikované
- [x] nová operácia Approve je úplne špecifikovaná (OP-05)
- [x] diagram prípadov užitia a stavový diagram aktualizované na v0.2
- [x] bežiaca aplikácia zodpovedá schválenej baseline v0.2 — [PR #5](https://github.com/thspowered/SWI-LAB/pull/5)
- [x] evidence prepája špecifikáciu s bežiacim chovaním
- [x] aspoň jeden konkrétny architektonický driver pripravený pre C03

Tri doložené medzery (REQ-05 v oboch vetvách REQ-10 a REQ-16) boli v C02
zámerne nesplnené a boli vstupom pre C03 — tam sú zatvorené, viď nižšie.

## C03 Definition of Done

- [x] 3–5 driverov podložených požiadavkami a evidenciou (D-1..D-4)
- [x] doménový model konzistentný s C02
- [x] významné odpovednosti explicitné, s ownership požiadavkami (O-1..O-8)
- [x] jedna rozhodovacia otázka, dve materiálne odlišné alternatívy
- [x] alternatívy porovnané voči driverom a prevedené tým istým scenárom
- [x] ADR-01 vrátane prijatých negatívnych dôsledkov a „reconsider when"
- [x] kontext, statická architektúra, ownership prechodov a runtime sú konzistentné
- [x] scenár realizovaný návrhovým sekvenčným diagramom
- [x] návrhový triedny diagram podporuje tú istú realizáciu
- [x] cross-view kontrola vykonaná pred zmenou kódu (našla dva rozpory; review PR #7 ďalších šesť)
- [x] AS-IS → TO-BE delta s `CHANGE` / `KEEP` / `VERIFY`
- [x] behaviour verification z C02 po zmene prechádza — `102 passed`
- [x] opakovateľná kontrola chráni architektonické pravidlo z ADR-01
- [x] evidence a commity zaznamenané; tag `c03-architecture` po zlúčení

Tri medzery prenesené z C02 (`REQ-05` v oboch vetvách, `REQ-16`) sú
**zatvorené** — testy už nie sú `xfail`.

## C05 Definition of Done

- [x] jedna architecture responsibility / unit — `Reservation Lifecycle`
- [x] detailed responsibilities (L-1..L-7)
- [x] ownership — návrhové prvky a rozhodnutia DD-1..DD-7
- [x] významné interface / contract — Transition Policy, `enter_blocking_state()`
- [x] focused structural UML view
- [x] detailed sequence — `Cancel Reservation`
- [x] významné methods / operations
- [x] algorithm / decision logic — rozhodovacie tabuľky zrušenia, potvrdenia a vypršania
- [x] minimálne 5 testov odvodených z DD — 92 unit, 49 component, 6 statických
- [x] testy spustené — `249 passed`; 32 vložených chýb, každú zachytí aspoň jeden test (`scripts/mutation_check.py`)
- [x] DD porovnané s implementáciou — s kódom z C03 aj s výsledkom; rozdiely a ich riešenie sú v evidence
- [x] commity v poradí návrh → testy → implementácia → evidence → opravy po kontrole
- [x] tag `c05-detailed-design` — merge commit `c7df7a0` (PR #8)

Stavy, kódy zamietnutí ani HTTP statusy sa v C05 nemenili — mení sa vnútro
jednej jednotky. Pred review v tíme prešla vetva tromi nezávislými kontrolami;
čo našli a čo sa po nich zmenilo, je v evidence.

## C08 — Controlled Change

Zmena `CR-08` — **lehota na rozhodnutie o žiadosti** (48 h od podania). V C08
sa kód nemení: vzniká rozhodnutie a plán overenia, implementácia je C09.

*Pozor na dve „C08".* `docs/SWI-LAB_C08_Project_Status.pptx` a register rizík
označený „C07/C08" patria k Project Status Reportu z predošlého zadania
(prezentácia 2026-11-02). Táto časť je zadanie C08 **Controlled Change**.

- Change Request, analýza dopadu, varianty, rozhodnutie a odovzdanie do C09:
  [docs/intent-and-change.md, časť C08](docs/intent-and-change.md#c08--controlled-change-cr-08)
- baseline a test cases s výsledkami:
  [docs/evidence-and-evolution.md, časť C08](docs/evidence-and-evolution.md#c08--controlled-change-baseline-a-overenie-dnešného-správania)

**Východisková baseline:** commit `b0974e7` (`main`), špecifikácia v0.2.
Obnovenie rovnakého stavu — z vetvy C08, lebo na samotnom `b0974e7` ešte nie je
skript s test cases; kód aplikácie je na vetve rovnaký ako na `b0974e7`:

```bash
git checkout feature/c08-controlled-change
git diff --stat b0974e7 -- src tests pyproject.toml docker-compose.yml   # prázdne
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
docker compose up -d --wait db
.venv/bin/python -m pytest -q                 # 253 passed
.venv/bin/python scripts/mutation_check.py    # 34 mutácií, 0 prežilo
```

Test cases `TC-C08-01..03` proti **bežiacej** aplikácii — operácie cez HTTP,
stav číta skript novým spojením priamo z databázy:

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn swilab.main:app --port 8000 &
.venv/bin/python scripts/c08_baseline_tc.py   # 3× PASS, návratový kód 0
```

Skript robí `TRUNCATE` všetkých tabuliek — nespúšťať súčasne s `pytest` ani
s `mutation_check.py`. Ak aplikácia alebo databáza nebeží alebo vypadne počas
behu, verdikt je `BLOCKED` (návratový kód 2), nie `FAIL`.

## C08 Definition of Done

- [x] jednoznačná východisková baseline a zrozumiteľné before správanie —
  [evidence, krok 0](docs/evidence-and-evolution.md#krok-0--východisková-baseline-before);
  obmedzenie, ktoré CR mení, doložené behom `TC-C08-03`
- [x] dva krokované test cases so špecifikovanými dátami a Expected pred
  vykonaním — `TC-C08-01`, `TC-C08-02` (+ `TC-C08-03`); Expected v `bbfbcfe`,
  prvý beh o 10 s neskôr
- [x] skutočne spustené na reálnej hranici a perzistencii — všetky tri cez HTTP
  API nad PostgreSQL, stav čítaný priamo z databázy, dva behy po resete;
  kontrolné behy: vložená chyba → `FAIL`, nebežiaca aplikácia → `BLOCKED`
- [x] jeden vierohodný CR — `CR-08` so zdrojom a dokladom potreby (C02 R-2, I-01)
- [x] Impact Matrix v relevantných oblastiach so zdôvodneným NO IMPACT,
  vrátane inventára zapisovateľov (worker, import, admin neexistujú; priamy SQL áno)
- [x] Options Matrix porovnávajúca reálne reakcie — päť variant vrátane
  „bez zmeny kódu" a dôsledku nečinnosti
- [x] **peer-reviewed rozhodnutie** — ACCEPT, varianta A so zmenou `CA-5`
  (T je konštanta, nie konfigurácia). Review T. Krišicu vykonané 2026-11-15
  v [PR #11](https://github.com/thspowered/SWI-LAB/pull/11): `CA-1` až `CA-4`,
  `CA-6`, `CA-7` potvrdené vrátane prijatia za rolu vedúceho
- [x] **obnovenie baseline druhým členom** — T. Krišica 2026-11-15 na vlastnom
  stroji: `253 passed`, 34 mutácií / 0 prežilo, `TC-C08-01..03` 3× `PASS`,
  zhodný odtlačok schémy; zopakované aj obe kontroly oracle
- [x] ACCEPT handoff do C09 — ohraničená delta, nový oracle, regresia,
  riziká `R-06`, `R-07`, stav PLANNED; pred kódom v C09 ostávajú dve
  podmienky ACCEPT: rozhodnutie o migráciách (`R-04`) a schválená v0.3
- [x] odkazy na existujúce živé dokumenty, evidence a známe obmedzenia —
  CR v `intent-and-change.md`, test cases v `evidence-and-evolution.md`,
  príkazy tu; žiadny nový dokument, pribudol iba skript `scripts/c08_baseline_tc.py`

V C08 sa kód aplikácie nemenil: `git diff b0974e7 -- src tests` je prázdny.

## C09 — Integration, CI and Release Record

Šablóna zo zadania C09. Vyplnené je iba to, čo naozaj platí; čo ešte
neprebehlo, je výslovne označené (`PENDING`, `NOT RUN`). Podrobné záznamy sú
v [evidence, časť C09](docs/evidence-and-evolution.md#c09--integrácia-ci-a-reprodukovateľný-release).

C09 sa delí na **vetvu A** (CI, zamknuté závislosti, artefakt —
[PR #13](https://github.com/thspowered/SWI-LAB/pull/13)) a **vetvu B**
(implementácia `CR-08`): špecifikácia v0.4 a návrh v
[PR #14](https://github.com/thspowered/SWI-LAB/pull/14), kód v PR #15.
Review T. Hrubého 2026-11-18 — všetky tri schválené; opravy z review sú v `eaf10e0` (#13), `13c3c32` (#14) a `8200655` + nasledujúci commit (#15). Mergovať iba cez **Create a merge commit**, v poradí #13 → #14 → #15 — squash alebo rebase by zmenili commit s kódom a tým aj hash artefaktu.

### Príkazy

Celú kontrolu, ktorú robí CI, spustí jeden príkaz — CI volá ten istý skript:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.lock
pip install --no-deps --no-build-isolation -e .
docker compose up -d --wait db
scripts/ci_check.sh            # návratový kód 0 = všetkých 6 krokov prešlo
```

Skript skončí na prvom zlyhanom kroku a vypíše, ktorý to bol. Ak prostredie
nezodpovedá locku, skončí už v kroku 1 — zelená na iných verziách by nič
nedokazovala. Artefakt a jeho SHA256 nájdeš v `dist/` a `dist/SHA256SUMS`.

**Obnova locku** — iba keď sa mení `pyproject.toml`, a vždy z čistého venv:

```bash
python3.12 -m venv /tmp/lockenv && /tmp/lockenv/bin/pip install ".[dev]"
/tmp/lockenv/bin/pip freeze --exclude-editable | grep -v '^swilab'   # -> requirements-dev.lock
```

Runtime lock sa vygeneruje rovnako, iba `pip install -c requirements-dev.lock .`
bez `[dev]`. Hlavičku súborov zachovaj.

### Záznam

| Položka | Stav |
| ------- | ---- |
| **C08 Change Card a rozhodnutie** | [CR-08](docs/intent-and-change.md#c08--controlled-change-cr-08) — **ACCEPT, varianta A** so zmenou `CA-5` (T je konštanta). Peer review [PR #11](https://github.com/thspowered/SWI-LAB/pull/11). Podmienky ACCEPT: (1)(2) review, (3) [`ADR-02`](docs/architecture-and-decisions.md#adr-02--migrácie-schémy-pred-zmenou-v-c09) migrácie, (4) špecifikácia — v0.3 uzavrela F-03, `CR-08` ide do **v0.4** ([PR #12](https://github.com/thspowered/SWI-LAB/pull/12)). |
| **Before / after** | before: `b0974e7` (kód v0.3; od `b17d58c` nezmenený). after: kód **`8200655`** — posledný commit, ktorý mení `src/` alebo `pyproject.toml` (review opravilo komentáre a docstringy; funkčný kód je z `9ede5a6`). |
| **Dotknuté tvrdenia, nové AC, regresia** | nové: `AC-01` až `AC-10` z CR-08. Regresia: `TC-C08-01/02`, celá sada, súbeh REQ-05 a REQ-16. |
| **Zmenený kód / dáta / konfigurácia / ADR** | vetva A: CI, lock, build tooling. Vetva B: špecifikácia v0.4 a diagramy; návrh `DD-8`, `DD-9`; `rules` (lehota, termín), politika (termín ako povinný parameter), strážca zapíše termín, SQL forma živosti, stĺpec `decision_deadline` + obmedzenie, kontrola schémy pri štarte (`ADR-02`), pole v API, verzia `0.4.0`. Konfigurácia sa nemení (`CA-5`). N/A: worker, import, admin — neexistujú. |
| **Peer review** | **Vykonané 2026-11-18** — T. Hrubý: PR #13, #14, #15 schválené. Napadnuté `DD-8` a `DD-9` — potvrdené: jedno uložené porovnanie odstraňuje hlavný failure mode varianty A z C08. Špecifikácia v0.4 schválená (§11). Overené vlastným behom: testy pred kódom na `8ce4035` → `218 failed, 82 passed, 10 errors`, ako tvrdí evidence. Nálezy a opravy sú v evidence (*Vetva B — review a nezávislá reprodukcia*). |
| **Nové správanie (AC-01..AC-10)** | **PASS** — 57 nových testov a `TC-C08-03` ako oracle CR-08; tabuľka expected / actual v evidence. Expected pred kódom: commit `8ce4035`, beh pred kódom `218 failed`, po kóde `310 passed`. |
| **Regresia `TC-C08-01`** | **PASS** — `ci_check.sh` krok 5 na kóde v0.4 (`2eaa82f`), aj predtým na baseline (`35d684f`, lokálne aj v CI) |
| **Regresia `TC-C08-02`** | **PASS** — tamtiež |
| **Mutácie** | 45, žiadna neprežila — 34 z C05/C06 a 11 nových pre CR-08 (D1–D11) |
| **Skutočná integračná hranica** | HTTP → aplikácia (`uvicorn`) → PostgreSQL 16. Stav po commite číta `c08_baseline_tc.py` **novým spojením priamo z databázy**, nie z odpovede API. Bez skutočnej databázy sada nebeží vôbec. |
| **Lock + runtime + schéma + konfigurácia** | `requirements.lock` (24 runtime balíčkov), `requirements-dev.lock` (35); Python 3.12; schéma z `models.py` cez `create_all()`, bez migrácií podľa `ADR-02` — od v0.4 so stĺpcom `reservations.decision_deadline` a obmedzením `ck_reservations_request_has_deadline`, aplikácia nad inou schémou nenaštartuje; jediná konfigurácia `DATABASE_URL` (`.env.example`). |
| **Príkazy pre čistý checkout** | časť *Príkazy* vyššie a *Ako to spustiť* |
| **CI** | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) pri každom PR a pri push do `main`. Prvý beh: [`success`](https://github.com/thspowered/SWI-LAB/actions/runs/43767331765) na `35d684f` — všetkých 6 krokov, rovnaké výsledky ako lokálne. |
| **Nezávislý reprodukujúci člen** | **T. Hrubý, 2026-11-18** — dva čisté checkouty z GitHubu do nového adresára, macOS 26.6.2 arm64, Python 3.12.11, Docker 28.3.2. (1) `0769da8`: všetkých 6 krokov `ci_check.sh`, wheel `2d3adcc3…788e7d` — **bajtovo rovnaký ako u T. Krišicu**; odchýlka od README: databázový kontajner z pôvodného checkoutu (`container_name`), opravené v README (#13). (2) `8200655`: všetkých 6 krokov **presne podľa README** vrátane nového postupu pri konflikte kontajnera, wheel `cf090f0b…626ef`. Podrobne v evidence. |
| **Artefakt** | **`swilab-0.4.0-py3-none-any.whl`**, kód `8200655`, SHA256 **`cf090f0b6615c13ec80c49afc8e7d0813d27f033945ee4ea2f45743f905626ef`** — čistý checkout T. Hrubého; rovnaký v CI ([run 44278224079](https://github.com/thspowered/SWI-LAB/actions/runs/44278224079), Linux x86_64, Python 3.12.16). Pred review (kód `9ede5a6`): `2d3adcc3…788e7d`, zhodný u oboch členov. Reprodukovateľný: ten istý kód dá bajtovo ten istý wheel aj po commitoch iba s dokumentáciou (`SOURCE_DATE_EPOCH` z posledného commitu s kódom). Rebase vetvy by hash zmenil — merge commit nie. Vetva A (`0.2.0`, `35d684f`): `a4e80878…fb7592`, zhodný z macOS aj z Linuxu v CI. |
| **Obnova / rollback** | Rollback kódu **nevráti dáta**: dáta legálne vo v0.4 sú podľa v0.2 porušením BR-02 (overené). Postup `scripts/cr08_rollback.sql` → kód v0.3 → `scripts/cr08_forward.sql` → kód v0.4 **raz vyskúšaný nad dátami**, 8 krokov v evidence. Aplikácia musí pri rollbacku stáť. Prestavba podľa `ADR-02` dáta zmaže (bez volume). |
| **Rozhodnutie o RC** | **CONDITIONAL** — owner tím; navrhol T. Krišica, potvrdil T. Hrubý v review 2026-11-18. Tri dôkazy, pre ktoré bolo pôvodne HOLD, sú doplnené: (1) špecifikácia v0.4 schválená (PR #14), (2) peer review kódu (PR #15), (3) čistý checkout druhým členom s rovnakým hashom wheelu. **Podmienky / známe obmedzenia:** víkendová diera (`CA-1`); žiadateľ ani vedúci nedostanú oznámenie (I-01, TBD-03/09); lock bez hashov balíčkov a mimo Windows; prestavba DB maže dáta (`ADR-02`); merge iba cez merge commit, inak sa identita artefaktu zmení. Zmenu na READY by priniesli oznámenia (TBD-03) a lock s hashmi. Zelená CI nie je súhlas s vydaním. |

## Dokumentácia

- [docs/specification.md](docs/specification.md) — špecifikácia správania, baseline v0.4
- [docs/diagrams.md](docs/diagrams.md) — prípady užitia, stavový diagram, diagramy aktivít
- [docs/change-impact-c02.md](docs/change-impact-c02.md) — analýza dopadu zmeny (schvaľovanie)
- [docs/intent-and-change.md](docs/intent-and-change.md) — Project Frame, future pressure, C08 Change Request `CR-08`
- [docs/architecture-and-decisions.md](docs/architecture-and-decisions.md) — stack, vrstvy, rozhodnutia, architektúra C03 (ADR-01), detailed design C05
- [docs/evidence-and-evolution.md](docs/evidence-and-evolution.md) — C01 spike, evidence C02, C03, C05, C07 (nálezy, register rizík) a C08 (baseline, test cases)
