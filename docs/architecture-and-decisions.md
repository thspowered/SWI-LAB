# Architecture and Decisions

## Stack
Python 3.12+ · FastAPI · SQLAlchemy 2.0 · PostgreSQL 16 · pytest

**Zdôvodnenie.** Tím vie Python, takže čas nejde na učenie jazyka ale na návrh
systému. FastAPI dáva HTTP vrstvu s validáciou vstupov bez vlastného kódu na
parsovanie. PostgreSQL je zvolený zámerne a nie je zameniteľný za SQLite:
future pressure, ktorú sme si vybrali (súbežné potvrdzovanie), sa bude riešiť
transakčnou izoláciou a databázovými constraintmi, a tie SQLite v potrebnej
podobe nemá.

## Vrstvy
- `swilab/models.py` — perzistentné entity (SQLAlchemy).
- `swilab/domain/` — stavy a pravidlá; nezávisí na HTTP ani na databáze.
- `swilab/api/` — HTTP endpointy, tenká vrstva nad doménou.
- `swilab/db.py` — engine a session.
- `swilab/config.py` — konfigurácia z prostredia.

Pravidlá zámerne nesedia v entitách. Pri vybranej future pressure očakávame, že
vyhodnotenie prekryvu sa presunie bližšie k databáze (constraint alebo zámok);
ak je pravidlo oddelené, je to zmena na jednom mieste.

## Rozhodnutie: tri stavy, nie štyri
`DRAFT`, `CONFIRMED`, `CANCELLED`.

Zvažovali sme samostatný stav `REJECTED` pre zamietnuté potvrdenie. Zamietli sme
ho: zamietnutie nie je koncový stav rezervácie, ale výsledok pokusu o prechod.
Používateľ si doplní certifikát a potvrdí znova. Stav `REJECTED` by musel mať
cestu späť do `DRAFT` a nepridal by žiadnu informáciu, ktorú by sme nemali
z chybovej odpovede.

**Revidované v baseline v0.2.** Stavov je šesť a `REJECTED` medzi nimi je.
Pôvodné rozhodnutie tým nepadlo — platí presne tak, ako bolo napísané: zamietnuté
*potvrdenie* koncový stav nevyrába a rezervácia zostáva v `DRAFT`. `REJECTED`
v v0.2 znamená niečo iné: **rozhodnutie vedúceho** o žiadosti o schválenie, teda
záznam o konaní človeka, ktorý má zostať viditeľný. Zdôvodnenie je v
[change-impact-c02.md](change-impact-c02.md), rozhodnutie R-4.

## Rozhodnutie: polootvorený interval
Rezervácia pokrýva `[starts_at, ends_at)`. Susediace rezervácie teda nekolidujú.
Bez tohto rozhodnutia každá implementácia prekryvu skončí pri chybe o jedna
a tím sa o nej dozvie až z testu, ktorý niekto napíše opačne.

## Rozhodnutie: časy ako timestamptz
Všetky časy sú timezone-aware a v databáze `timestamptz`. Naive časy sú zdroj
chýb pri prechode na letný čas, a laboratórium rezervuje aj cez koniec marca.
Toto rozhodnutie je zároveň predmetom nášho C01 spiku — overili sme, že
presnosť a timezone prežijú zápis a načítanie.

## Rozhodnutie: spike proti docker-compose, nie testcontainers
Testy čítajú `DATABASE_URL`. Predvolene mieria na Postgres z `docker-compose.yml`
tohto repa, ale rozbehnú sa proti akémukoľvek Postgresu. Testcontainers by boli
pohodlnejšie, ale pribíjajú testy na bežiaci Docker daemon u každého člena tímu.

## Open / neriešené v C01
- Súbežné potvrdzovanie (naša future pressure) — pomenované, neimplementované.
- Notification Service — definovaná hranica, bez implementácie.
- Autentifikácia — mimo rozsah predmetu.

---

# C03 časť A — AS-IS realizácia scenára *Confirm Reservation*

Tento oddiel popisuje, ako **súčasná** implementácia realizuje jeden scenár
z baseline v0.2. Nie je to cieľový návrh.

**Spôsob overenia.** Mapovanie krokov, vetiev a závislostí je overené čítaním
kódu na uvedených riadkoch a existenciou menovaných testov (`git grep`). Testová
sada **nebola pri písaní tohto oddielu spustená** — posledný doložený beh je
`86 passed, 3 xfailed` z C02, viď [evidence-and-evolution.md](evidence-and-evolution.md).
Pred review treba sadu pustiť znova; riadkové odkazy platia pre stav vetvy
`feature/c03-as-is-confirm`.

## A1. Sledovaný scenár

| Položka | Hodnota |
| ------- | ------- |
| Scenár / operácia | `OP-03 Confirm Reservation` |
| Požiadavky | `REQ-04`, `REQ-05`, `REQ-06`, `REQ-10` |
| Pravidlá / invarianty | `BR-02` (neprekrývanie), `BR-04` (certifikácia), `BR-06` (oprávnenie) |
| Baseline | `v0.2` |

Zvolili sme `Confirm`, lebo je to jediný prechod, ktorý alokuje prístroj, a teda
jediné miesto, kde sa vyhodnocuje invariant `BR-02`.

## A2. Hlavný priebeh scenára namapovaný na kód

| Krok scenára (v0.2) | Realizácia v kóde | Doklad |
| ------------------- | ----------------- | ------ |
| prijať požiadavku o potvrdenie | `api/reservations.py:129` `confirm_reservation()` — endpoint `POST /reservations/{id}/confirm` | `tests/test_api.py::test_http_confirm_reservation` |
| odčítať čas pre pravidlá | `clock.now()`, odovzdaný ako parameter `now` | `src/swilab/clock.py:4` |
| načítať `Reservation` | `services/reservations.py:218` `session.get(Reservation, …)` | `test_unknown_reservation_is_rejected` |
| overiť oprávnenie žiadateľa (BR-06) | `services/reservations.py:223` → `_require_authorized()` na `:65` | `test_confirm_by_foreign_student_is_forbidden`, `test_supervisor_confirms_foreign_reservation` |
| overiť, že prechod je povolený (stav `DRAFT`) | `services/reservations.py:225` | `test_confirm_is_not_idempotent` |
| overiť, že rezervácia ešte nezačala (REQ-06) | `services/reservations.py:230` | `test_confirm_after_start_is_rejected` |
| overiť, že prístroj je aktívny (BR-05) | `services/reservations.py:236–237` | `test_confirm_rejects_inactive_instrument` |
| overiť certifikát vlastníka (BR-04) | `services/reservations.py:244–249`, predikát `domain/rules.py:34` `certification_covers()` | `test_certification_boundary`, `test_confirm_rejects_wrong_category_certification` |
| vyhodnotiť konflikt (BR-02) | `services/reservations.py:255` → `_overlapping_blocking()` na `:74` (SQL dotaz) | `test_confirm_rejects_overlap_and_keeps_draft`, `test_confirm_allows_adjacent_interval` |
| zmeniť stav (REQ-10) | `services/reservations.py:272–276` — `CONFIRMED`, alebo `PENDING_APPROVAL` podľa `Instrument.requires_approval` | `test_confirm_allocates_instrument` |
| uložiť výsledok | `services/reservations.py:277` `session.commit()` | ten istý test |

Certifikát sa overuje **vlastníkovi rezervácie, nie žiadateľovi** — vedúci môže
potvrdiť cudziu rezerváciu, ale nie za niekoho, kto školenie nemá
(`services/reservations.py:241–243`).

## A3. Alternatívna vetva: konflikt → zamietnutie

| Čo hovorí v0.2 | Kde sa podmienka zistí | Kde sa rozhodne výsledok | Čo dostane volajúci |
| -------------- | ---------------------- | ------------------------ | ------------------- |
| `BR-02`/`REQ-04`: prekryv s blokujúcou rezerváciou → potvrdenie zamietnuté, rezervácia **zostáva v `DRAFT`** | `_overlapping_blocking()`, `services/reservations.py:74–107` — podmienka je v SQL `WHERE`, nie v Pythone | `services/reservations.py:263` `if conflicts: raise DomainError(OVERLAP, …)` | HTTP **409** s telom `{"code": "OVERLAP", …}`; preklad robí `main.py` `domain_error_handler()` + `STATUS_BY_CODE` |

Stav zostáva `DRAFT`, lebo zápis `reservation.state` je až za kontrolou
(`:272`) a pred ním sa nič necommituje.

### Rozdiely medzi v0.2 a implementáciou

| Špecifikácia | Implementácia | Doklad |
| ------------ | ------------- | ------ |
| `REQ-05`: pri súbehu smie byť najviac jedna rezervácia v blokujúcom stave | Medzi kontrolou prekryvu (`:255`) a zápisom (`:272–277`) je okno. Dve súbežné potvrdenia prejdú obe. | `tests/test_concurrency_req05.py::test_concurrent_conflicting_confirmations`, `xfail(strict=True)` |
| `REQ-16`: rezervácia opustí stav `DRAFT` najviac raz | Súbežné zrušenie a potvrdenie tej istej rezervácie uspejú obe. | `tests/test_concurrency_req05.py::test_concurrent_cancel_and_confirm_on_same_reservation`, `xfail(strict=True)` |

Oba rozdiely sú **vedomé a zdokumentované** — nie sú to prehliadnutia. Sú to
dva z troch architektonických driverov prenesených do C03.

## A4. Hlavné časti implementácie

Päť modulov, všetky na rovnakej úrovni detailu (modul, nie trieda).

| Časť implementácie | Typ / obsah | Rola v tomto scenári | Doklad |
| ------------------ | ----------- | -------------------- | ------ |
| `Reservation API` | *module* — `api/reservations.py`, `main.py` | prijme command, odčíta čas, preloží zamietnutie na HTTP status | `api/reservations.py:124–138`, `main.py` `STATUS_BY_CODE` |
| `Reservation services` | *module* — `services/reservations.py` | rozhoduje o prechode; drží poradie kontrol zhodné s diagramom aktivít | `services/reservations.py:200–278` |
| `Domain rules` | *module* — `domain/rules.py`, `domain/states.py` | čisté predikáty pravidiel a množina `BLOCKING_STATES` | `domain/rules.py:34`, `domain/states.py:25` |
| `Persistence model` | *module* — `models.py`, `db.py` | mapovanie na tabuľky, engine a session | `models.py` `Reservation`, `db.py` `get_session()` |
| `Clock` | *module* — `clock.py` | jediný zdroj času pre pravidlá | `clock.py:4` |

Notification integration medzi blokmi **nie je** — v kóde neexistuje (viď A6).

## A5. Stav, zmena stavu a jedno pravidlo

### Stav

| Otázka | Odpoveď | Doklad |
| ------ | ------- | ------ |
| Kde je stav `Reservation` trvale uložený? | PostgreSQL, tabuľka `reservations`, stĺpec `state` ako pomenovaný enum `reservation_state` | `models.py` `Reservation.state`; schéma vzniká cez `Base.metadata.create_all()` v `main.py` `lifespan()` |
| Ktorý kód rozhoduje a vykonáva prechod použitý v scenári? | `services/reservations.py:272–277` — priradenie `reservation.state` a `session.commit()` v tej istej funkcii | `test_confirm_allocates_instrument` |

Prechod **nerobí entita**. `Reservation` je čistý dátový model bez metód;
rozhodnutie aj zápis sú v servisnej funkcii.

### Business pravidlo: BR-02 (neprekrývanie)

| Otázka | Odpoveď | Doklad |
| ------ | ------- | ------ |
| Kde sa zistí podmienka pravidla? | `_overlapping_blocking()`, `services/reservations.py:74–107`. Nerovnosť `starts_at < ends_at AND ends_at > starts_at` je napísaná **v SQL** (`:101–102`), aby sa nenačítaval celý kalendár prístroja; podmienka živosti žiadosti podľa `BR-08` je o dva riadky vyššie (`:97–100`). | `services/reservations.py:91–103` |
| Kde sa podľa výsledku rozhodne? | Na **dvoch miestach**: `confirm_reservation()` `:263` a `decide_reservation()` `:436` (schválenie žiadosti). | `test_confirm_rejects_overlap_and_keeps_draft`, `tests/test_approval.py` |
| Kde sa vykoná výsledná zmena stavu? | `:272–277` pri potvrdení, obdobne v `decide_reservation()` pri schválení. | ako vyššie |

**Nález: predikát prekryvu existuje v dvoch formách a jedna z nich je mŕtva.**
`domain/rules.py:23` definuje `intervals_overlap()` ako čistú funkciu. V celom
repozitári ju **nič neimportuje ani nevolá** — overené `git grep intervals_overlap`,
ktorý vráti presne dva výskyty: definíciu a zmienku v komentári. Komentár na
`services/reservations.py:90` pritom tvrdí, že *„testy ju porovnávajú s ňou"*;
taký test neexistuje. Podmienka `BR-02` teda reálne žije **iba v SQL** a jej
dokumentovaný dvojník nie je ničím krytý.

## A6. Relevantné závislosti

| Závislosť | Kde sa napája na kód | Ktorá časť pozná jej technické API | Doklad |
| --------- | -------------------- | ---------------------------------- | ------ |
| **PostgreSQL 16** | `db.py` `create_engine(settings.database_url)`; `get_session()` ako FastAPI dependency | `Persistence model` (SQLAlchemy ORM) **a `Reservation services`** — `_overlapping_blocking()` skladá SQL dotaz priamo | `db.py`, `services/reservations.py:91–104`, `docker-compose.yml` |
| **Notification Service** | **nikde — v kóde neexistuje žiadne volanie** | žiadna | hranica definovaná v `specification.md`, v diagramoch aktivít kreslená prerušovanou čiarou; `TBD-03` |
| **IdP / autentifikácia** | **neexistuje** — `user_id` a `requested_by` sa berú z tela requestu a dôveruje sa im | `Reservation API` (`ActorRequest`) | `api/reservations.py:35–40`; `TBD-06` |

Bežné knižnice frameworku (FastAPI, Pydantic) neuvádzame.

Poznámka k prvému riadku: znalosť technického API databázy **nie je sústredená
v jednej vrstve**. ORM ju drží v `Persistence model`, ale servisná vrstva skladá
vlastný SQL dotaz, takže databázu pozná tiež.

## A7. AS-IS štrukturálny diagram

```
+------------------------------ Application code ------------------------------+
|                                                                              |
|  [Reservation API]  (module)                                                 |
|  role: prijme confirm command, preloží zamietnutie na HTTP status            |
|        |                                        |                            |
|        | now()                                  | confirm(id, by, now)       |
|        v                                        v                            |
|  [Clock] (module)                     [Reservation services]  (module)       |
|  role: jediný zdroj času              role: rozhoduje o prechode DRAFT ->     |
|                                             CONFIRMED / PENDING_APPROVAL      |
|                                              |                |              |
|                       certification_covers() |                | load/save    |
|                                              v                v  Reservation |
|                                      [Domain rules]   [Persistence model]    |
|                                       (module)          (module)             |
|                                       role: čisté        role: mapovanie     |
|                                       predikáty          na tabuľky          |
|                                                               |              |
+---------------------------------------------------------------|--------------+
                                   |                            |
          SELECT blokujúce prekryvy |                            | read/write
          (SQL priamo zo services)  v                            v
                            [Reservation DB]  (database, PostgreSQL 16)

          [Notification Service] (external system) — NENAPOJENÁ, v kóde
                                 neexistuje volanie (TBD-03)
```

Obsah blokov:

```
Reservation API:
- api/reservations.py  (endpointy, Pydantic modely)
- main.py              (STATUS_BY_CODE, domain_error_handler)

Reservation services:
- services/reservations.py  (confirm_reservation, _overlapping_blocking, …)

Domain rules:
- domain/rules.py   (certification_covers, …)
- domain/states.py  (ReservationState, BLOCKING_STATES)

Persistence model:
- models.py  (Reservation, Instrument, User, Certification)
- db.py      (engine, SessionFactory, get_session)

Clock:
- clock.py
```

Dve šípky do databázy sú zámerne nakreslené zvlášť: jedna vedie cez ORM
z `Persistence model`, druhá priamo zo `Reservation services`, ktoré skladá
SQL dotaz na prekryvy. Je to skutočný stav kódu, nie zjednodušenie.

## A8. Otázka pre ďalší krok C03

| Položka | Obsah |
| ------- | ----- |
| **Otázka** | Kde má byť vynútený invariant `BR-02`, keď ho dnes vyhodnocujú dve nezávislé miesta a jeho predikát existuje v dvoch formách, z ktorých jedna je mŕtvy kód? |
| **Doklad** | `_overlapping_blocking()` (`services/reservations.py:74`) je volaný z `confirm_reservation()` `:255` aj z `decide_reservation()` `:428`. Podmienka je napísaná v SQL. Paralelná čistá funkcia `rules.intervals_overlap()` (`domain/rules.py:23`) nie je v repozitári nikde volaná ani testovaná, hoci komentár na `services/reservations.py:90` tvrdí opak. |
| **Prečo je dôležitá** | `BR-02` je jediný skutočný invariant systému. Vo v0.1 ho vynucovalo jedno miesto, vo v0.2 už dve — pridanie ďalšieho blokujúceho stavu alebo ďalšej operácie ho rozšíri znova, a každé miesto musí pamätať aj na podmienku živosti žiadosti (`BR-08`). Zároveň je to tá istá hranica, na ktorej zlyháva `REQ-05`: kontrola a zápis nie sú atomické. Rozhodnutie, *kam* invariant patrí — do servisnej vrstvy, do jedného doménového predikátu, alebo do databázového constraintu — určí, či sa `REQ-05` dá vôbec uzavrieť bez toho, aby sa oprava musela urobiť dvakrát. |
