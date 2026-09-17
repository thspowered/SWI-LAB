# SWI-LAB — rezervačný systém laboratórnych prístrojov

Semestrálny projekt SWI. Rezervácia laboratórnych prístrojov: kto, aký prístroj
a na aký čas — s kontrolou, že sa dve potvrdené rezervácie neprekrývajú a že
prístroj obsluhuje iba certifikovaný človek.

## Tím

**Názov tímu:** SWI-LAB

| Člen | Rola v C01 |
|---|---|
| Tomáš Krišica | založenie repozitára, review PR pred integráciou |
| Tomáš Hrubý | engineering spike, PR |

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
docker compose up -d db

pytest -v
uvicorn swilab.main:app --reload
```

Aplikácia beží na `http://127.0.0.1:8000`, kontrola stavu na `/health`.
Databáza počúva na porte **5433**, aby nekolidovala s lokálnym Postgresom.

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

V C01 je táto cesta iba **definovaná**, nie implementovaná.

## C01 Definition of Done

- [x] tím 3–4 členovia
- [ ] spoločný repozitár, prístup majú všetci
- [x] jasný reservation domain
- [x] Resource + Reservation + User
- [x] zmysluplné stavy rezervácie
- [x] create + confirm/approve + cancel + availability
- [x] spoločné pravidlo o neprekrývaní
- [x] 1 vlastné domain-specific pravidlo
- [x] 1 externá hranica systému
- [x] kompletný Project Frame
- [x] 1 future pressure Q/C/R/L
- [ ] 1 zrecenzovaná a integrovaná zmena
- [x] 1 skutočne vykonaný engineering spike
- [x] evidence + decision zo spiku
- [x] definovaný CP1 walking skeleton

## Dokumentácia

- [docs/intent-and-change.md](docs/intent-and-change.md) — Project Frame, future pressure
- [docs/architecture-and-decisions.md](docs/architecture-and-decisions.md) — stack, vrstvy, rozhodnutia
- [docs/evidence-and-evolution.md](docs/evidence-and-evolution.md) — C01 engineering spike
