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
- **Reservation** — rezervácia na interval `[starts_at, ends_at)`, v stave `DRAFT`, `CONFIRMED` alebo `CANCELLED`.
- **User** — študent alebo vedúci laboratória.
- **Certification** — oprávnenie používateľa na kategóriu prístroja, platné do dátumu.

**Operácie:** create · confirm/approve · cancel · check availability

**Pravidlá:**

1. Dve `CONFIRMED` rezervácie toho istého prístroja sa nesmú prekrývať.
2. Rezerváciu možno potvrdiť len s platným certifikátom na kategóriu prístroja.

**Externá hranica:** Notification Service.

Podrobne v [docs/intent-and-change.md](docs/intent-and-change.md).

## Stack

Python 3.12+ · FastAPI · SQLAlchemy 2.0 · PostgreSQL 16 · pytest

Zdôvodnenie v [docs/architecture-and-decisions.md](docs/architecture-and-decisions.md).

## Ako to spustiť

Predpoklady: nainštalovaný a **spustený** Docker, Python **3.12+**.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
docker compose up -d --wait db

pytest -v
uvicorn swilab.main:app --reload
```

Aplikácia beží na `http://127.0.0.1:8000`, kontrola stavu na `/health`,
interaktívna dokumentácia API na `/docs`.
Databáza počúva na porte **5433**, aby nekolidovala s lokálnym Postgresom.

Ak `import swilab` zlyhá aj po `pip install -e`, spusti s `PYTHONPATH=src`.
Na macOS stačí, aby mal `.pth` súbor v `site-packages` flag `hidden` —
CPython skryté `.pth` zámerne preskakuje a editable install prestane fungovať
bez akejkoľvek chybovej hlášky (`chflags nohidden` to opraví).

## Operácie (baseline v0.2)

| Operácia | Endpoint |
| -------- | -------- |
| OP-01 Create Reservation | `POST /reservations` |
| OP-02 Check Availability | `GET /instruments/{id}/availability?starts_at=&ends_at=` |
| OP-03 Confirm Reservation | `POST /reservations/{id}/confirm` |
| OP-04 Cancel Reservation | `POST /reservations/{id}/cancel` |
| OP-05 Approve Reservation | `POST /reservations/{id}/approve` · `POST /reservations/{id}/reject` |

Prístroje s príznakom `requires_approval` idú cez schvaľovanie: potvrdenie
vytvorí žiadosť (`PENDING_APPROVAL`), ktorá prístroj blokuje, kým o nej vedúci
nerozhodne alebo kým nenastane jej začiatok (`EXPIRED`).

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

## Dokumentácia

- [docs/specification.md](docs/specification.md) — špecifikácia správania, baseline v0.2
- [docs/diagrams.md](docs/diagrams.md) — prípady užitia, stavový diagram, diagramy aktivít
- [docs/change-impact-c02.md](docs/change-impact-c02.md) — analýza dopadu zmeny (schvaľovanie)
- [docs/intent-and-change.md](docs/intent-and-change.md) — Project Frame, future pressure
- [docs/architecture-and-decisions.md](docs/architecture-and-decisions.md) — stack, vrstvy, rozhodnutia
- [docs/evidence-and-evolution.md](docs/evidence-and-evolution.md) — C01 engineering spike
