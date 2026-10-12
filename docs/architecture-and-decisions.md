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
kódu na uvedených riadkoch. Testová sada bola pre tento oddiel spustená proti
čistej databáze z `docker-compose.yml`:

```
86 passed, 3 xfailed in 4.41s
```

Pri review PR #6 zopakované nad rovnakým commitom s rovnakým výsledkom.

Všetky testy menované v stĺpcoch *Doklad* v tomto oddiele sú v tomto behu
zahrnuté a prechádzajú. Tri `xfailed` sú tri testovacie prípady pre **dve**
doložené medzery z A3 — `REQ-05` (dva parametrizované prípady) a `REQ-16`:

```
XFAIL test_concurrent_conflicting_confirmations[False-dve subezne potvrdenia -> najviac jedna CONFIRMED]
XFAIL test_concurrent_conflicting_confirmations[True-dve subezne ziadosti -> najviac jedna PENDING_APPROVAL]
XFAIL test_concurrent_cancel_and_confirm_on_same_reservation
```

Oba prípady `REQ-05` volajú **OP-03 Confirm** — líšia sa iba príznakom
`requires_approval` prístroja, teda vetvou `REQ-10`. Ani jeden nevolá
schválenie (OP-05); súbeh pri schvaľovaní test nemá (viď A3).

Pri `--runxfail` padajú všetky tri na vlastnom asserte (`['OK', 'OK']`), nie na
chybe inštrumentácie — sú to skutočné dôkazy, nie prehltnuté zlyhania.

Sada predpokladá, že PostgreSQL beží s `TimeZone = UTC` (predvolené v obraze
`postgres:16` z `docker-compose.yml`). Proti serveru s iným pásmom padne
`test_persistence_spike.py` na asserte `utcoffset() == 0` — to je zámer spiku
z C01, nie chyba aplikácie.

Riadkové odkazy platia pre kód v commite `ec7ccdc` — stav `main` pred C03.
Časť A je **snímka AS-IS** a zámerne sa neprepisuje. Implementácia ADR-01
(oddiel „C03 — Architektúra", delta v J) službu zmenila: tie isté miesta ležia
v aktuálnom kóde inde a dve rozhodovacie miesta z A5 už neexistujú. Odkazy sa
dajú overiť cez `git show ec7ccdc:src/swilab/services/reservations.py`.

## A1. Sledovaný scenár

| Položka | Hodnota |
| ------- | ------- |
| Scenár / operácia | `OP-03 Confirm Reservation` |
| Požiadavky | `REQ-04`, `REQ-05`, `REQ-06`, `REQ-10`, `REQ-16` |
| Pravidlá / invarianty | `BR-01` (prekryv intervalov), `BR-02` (neprekrývanie — invariant), `BR-04` (certifikácia), `BR-05` (neaktívny prístroj), `BR-06` (oprávnenie), `BR-08` (živosť žiadosti) |
| Baseline | `v0.2` |

Zoznam zodpovedá „Odkazu na pravidlá" a požiadavkám pri OP-03 v
`specification.md`; `BR-08` vstupuje cez `REQ-04` (živá žiadosť blokuje).

Zvolili sme `Confirm`, lebo je to prechod, ktorým rezervácia **začne blokovať**
prístroj (`DRAFT → CONFIRMED` alebo `DRAFT → PENDING_APPROVAL`), a teda prvé
miesto, kde sa vyhodnocuje invariant `BR-02`. Od v0.2 už nie jediné — rovnakú
kontrolu znova robí schválenie (OP-05), ako hovorí aj zdôvodnenie OP-03
v špecifikácii. Práve z toho vychádza nález v A5 a otázka v A8.

## A2. Hlavný priebeh scenára namapovaný na kód

| Krok scenára (v0.2) | Realizácia v kóde | Doklad |
| ------------------- | ----------------- | ------ |
| prijať požiadavku o potvrdenie | `api/reservations.py:129` `confirm_reservation()` — endpoint `POST /reservations/{id}/confirm` | `tests/test_api.py::test_http_confirm_reservation` |
| odčítať čas pre pravidlá | `api/reservations.py:136` `now=clock.now()` → `clock.py:4`; služba čas sama neodčítava, dostane ho ako parameter `now` | `git grep "datetime.now" src/swilab/services` → žiadny výskyt |
| načítať `Reservation` | `services/reservations.py:218` `session.get(Reservation, …)` | `test_unknown_reservation_is_rejected` |
| overiť oprávnenie žiadateľa (BR-06) | `services/reservations.py:222` `_require_user()` (žiadateľ musí existovať), `:223` → `_require_authorized()` na `:65` | `test_confirm_by_foreign_student_is_forbidden`, `test_supervisor_confirms_foreign_reservation` |
| overiť, že prechod je povolený (stav `DRAFT`) | `services/reservations.py:225` | `test_confirm_is_not_idempotent`, `test_confirm_of_pending_request_is_rejected` |
| overiť, že rezervácia ešte nezačala (REQ-06) | `services/reservations.py:230` | `test_confirm_after_start_is_rejected` |
| overiť, že prístroj je aktívny (BR-05) | `services/reservations.py:236–237` | `test_confirm_rejects_inactive_instrument` |
| overiť certifikát vlastníka (BR-04) | `services/reservations.py:243–253` (dotaz `:243–248`, rozhodnutie `:249`), predikát `domain/rules.py:34` `certification_covers()` | `test_certification_boundary`, `test_confirm_rejects_wrong_category_certification` |
| vyhodnotiť konflikt (BR-02) | `services/reservations.py:255` → `_overlapping_blocking()` na `:74` (dotaz skladaný v službe) | `test_confirm_rejects_overlap_and_keeps_draft`, `test_confirm_allows_adjacent_interval` |
| zmeniť stav (REQ-10) | `services/reservations.py:272–276` — `CONFIRMED`, alebo `PENDING_APPROVAL` podľa `Instrument.requires_approval` | `test_confirm_allocates_instrument` (`CONFIRMED`), `test_confirm_on_guarded_instrument_creates_request` (`PENDING_APPROVAL`) |
| uložiť výsledok | `services/reservations.py:277` `session.commit()` | tie isté testy |

Certifikát sa overuje **vlastníkovi rezervácie, nie žiadateľovi** — vedúci môže
potvrdiť cudziu rezerváciu, ale nie za niekoho, kto školenie nemá
(komentár `services/reservations.py:240–242`, podmienka `:245`
`Certification.user_id == reservation.user_id`).

## A3. Alternatívna vetva: konflikt → zamietnutie

| Čo hovorí v0.2 | Kde sa podmienka zistí | Kde sa rozhodne výsledok | Čo dostane volajúci |
| -------------- | ---------------------- | ------------------------ | ------------------- |
| `BR-02`/`REQ-04`: prekryv s blokujúcou rezerváciou → potvrdenie zamietnuté, rezervácia **zostáva v `DRAFT`** | `_overlapping_blocking()`, `services/reservations.py:74–106` — podmienka je v SQL `WHERE`, nie v Pythone | `services/reservations.py:263` `if conflicts: raise DomainError(OVERLAP, …)` | HTTP **409** s telom `{"code": "OVERLAP", …}`; preklad robí `main.py` `domain_error_handler()` + `STATUS_BY_CODE` |

Stav zostáva `DRAFT`, lebo zápis `reservation.state` je až za kontrolou
(`:272`) a pred ním sa nič necommituje.

### Rozdiely medzi v0.2 a implementáciou

| Špecifikácia | Implementácia | Doklad |
| ------------ | ------------- | ------ |
| `REQ-05`: pri súbehu smie byť najviac jedna rezervácia v blokujúcom stave | Medzi kontrolou prekryvu (`:255`) a zápisom (`:272–277`) je okno. Dve súbežné potvrdenia prejdú obe. | `tests/test_concurrency_req05.py::test_concurrent_conflicting_confirmations`, `xfail(strict=True)` |
| `REQ-16`: rezervácia opustí stav `DRAFT` najviac raz | Súbežné zrušenie a potvrdenie tej istej rezervácie uspejú obe. | `tests/test_concurrency_req05.py::test_concurrent_cancel_and_confirm_on_same_reservation`, `xfail(strict=True)` |
| OP-03 ani diagram aktivít 3.3 vetvu „neznámy žiadateľ" nepoznajú — po „neznáma rezervácia" nasleduje rovno oprávnenie | `:222` `_require_user()` vráti `UNKNOWN_USER` → HTTP 404 ešte pred kontrolou oprávnenia | iba kód; **test chýba** — `test_unknown_user_is_rejected` pokrýva len OP-01 |

Prvé dva rozdiely sú **vedomé a zdokumentované** — nie sú to prehliadnutia.
`REQ-05` je driver č. 1 prenesený z C02 (`evidence-and-evolution.md`,
„Architektonické drivery prenesené do C03"); `REQ-16` pribudol ako driver po
nezávislej revízii v0.2 (nález N-07, `specification.md` časť 9). Tretí rozdiel
je neošpecifikovaná vetva: správanie je rozumné, ale špecifikácia o ňom mlčí.

Mimo tohto scenára, no s tou istou príčinou: `REQ-05` hovorí aj o súbežnom
**schvaľovaní** a `REQ-16` aj o súbehu zrušenia so schválením. Štrukturálne
rovnaké okno je v `decide_reservation()` (kontrola `:428`, zápis `:442–443`),
ale test, ktorý by ho doložil, neexistuje.

## A4. Hlavné časti implementácie

Päť modulov, všetky na rovnakej úrovni detailu (modul, nie trieda). Každý
súbor z `src/swilab` patrí práve do jedného z nich. `Clock` je malý, ale ako
samostatný blok je zámerne: zdroj času je driver č. 2 prenesený z C02.

| Časť implementácie | Typ / obsah | Rola v tomto scenári | Doklad |
| ------------------ | ----------- | -------------------- | ------ |
| `Reservation API` | *module* — `api/reservations.py`, `main.py` | prijme command, otvorí session pre request, odčíta čas, preloží zamietnutie na HTTP status | `api/reservations.py:22` `SessionDep`, `:124–138`, `main.py` `STATUS_BY_CODE` |
| `Reservation services` | *module* — `services/reservations.py` | rozhoduje o prechode; skladá dotazy a commituje; drží poradie kontrol zhodné s diagramom aktivít | `services/reservations.py:200–278` |
| `Domain rules` | *module* — `domain/rules.py`, `domain/states.py`, `errors.py` | čisté predikáty pravidiel, množina `BLOCKING_STATES`, kódy zamietnutí `ErrorCode` | `domain/rules.py:34`, `domain/states.py:25`, `errors.py` `DomainError` |
| `Persistence model` | *module* — `models.py`, `db.py`, `config.py` | mapovanie na tabuľky, engine a session | `models.py` `Reservation`, `db.py` `get_session()` |
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
| Kde sa zistí podmienka pravidla? | `_overlapping_blocking()`, `services/reservations.py:74–106`. Nerovnosť `starts_at < ends_at AND ends_at > starts_at` je napísaná **v SQL** (`:101–102`), aby sa nenačítaval celý kalendár prístroja; podmienka živosti žiadosti podľa `BR-08` je hneď nad ňou (`:97–100`). | `services/reservations.py:93–106` |
| Kde sa podľa výsledku rozhodne? | Na **dvoch miestach**: `confirm_reservation()` `:263` a `decide_reservation()` `:436` (schválenie žiadosti). | `test_confirm_rejects_overlap_and_keeps_draft`, `tests/test_approval.py::test_approval_rechecks_overlap` |
| Kde sa vykoná výsledná zmena stavu? | `:272–277` pri potvrdení, `:442–443` v `decide_reservation()` pri schválení. | ako vyššie |

**Nález: predikát prekryvu existuje v dvoch formách a jedna z nich je mŕtva.**
`domain/rules.py:23` definuje `intervals_overlap()` ako čistú funkciu. V celom
repozitári ju **nič neimportuje ani nevolá** — overené `git grep intervals_overlap`,
ktorý v kóde vráti presne dva výskyty: definíciu a zmienku v komentári. Komentár na
`services/reservations.py:90–91` pritom tvrdí, že *„testy ju porovnávajú s ňou"*;
taký test neexistuje. Podmienka `BR-02` teda reálne žije **iba v SQL** a jej
dokumentovaný dvojník nie je ničím krytý.

## A6. Relevantné závislosti

| Závislosť | Kde sa napája na kód | Ktorá časť pozná jej technické API | Doklad |
| --------- | -------------------- | ---------------------------------- | ------ |
| **PostgreSQL 16** | `db.py` `create_engine(settings.database_url)`; `get_session()` ako FastAPI dependency | `Persistence model` (mapovanie, PostgreSQL typ `UUID` v `models.py`, engine v `db.py`), **`Reservation services`** — skladá dotazy cez SQLAlchemy (`select(Certification)` `:243–248`, `select(Reservation)` v `_overlapping_blocking()` `:93–106`) a commituje (`:277`), a **`Reservation API`** — otvára session pre request (`api/reservations.py:22`) | `db.py`, `services/reservations.py:93–106`, `docker-compose.yml` |
| **Notification Service** | **nikde — v kóde neexistuje žiadne volanie** | žiadna | hranica definovaná v `specification.md`; v diagrame prípadov užitia prerušovanou čiarou, v aktivite OP-03 ako krok „TBD-03 – neimplementované"; `TBD-03` |
| **IdP / autentifikácia** | **neexistuje** — `user_id` a `requested_by` sa berú z tela requestu a dôveruje sa im | `Reservation API` (`ActorRequest`) | `api/reservations.py:35–39`; `TBD-06` |

Bežné knižnice frameworku (FastAPI, Pydantic) neuvádzame.

Poznámka k prvému riadku: znalosť technického API databázy **nie je sústredená
v jednej vrstve**. Mapovanie drží `Persistence model`, ale dotazy — vrátane
predikátu `BR-02` aj dotazu na certifikáty — skladá servisná vrstva sama, bez
repozitára. O hranici transakcie rozhodujú dve vrstvy: API session otvorí,
služba commituje. Fyzicky však do databázy vedie **jediná cesta**, cez `Session`
a engine z `db.py`; raw SQL ani vlastné pripojenie v kóde nie je.

## A7. AS-IS štrukturálny diagram

```
+----------------------------------- Application code ------------------------------------+
|                                                                                         |
|  [Reservation API]  (module)                                                            |
|  role: prijme confirm command, otvorí session pre request, odčíta čas,                  |
|        preloží DomainError na HTTP status                                               |
|     |          |                     |                                   |              |
|     |          | now()               | confirm_reservation(              | get_session()|
|     |          v                     |   session, id, by, now)           | (Depends),   |
|     |      [Clock] (module)          v                                   | create_all() |
|     |      role: jediný      [Reservation services]  (module)            | pri štarte   |
|     |      zdroj času        role: rozhoduje o prechode DRAFT ->         |              |
|     |                        CONFIRMED / PENDING_APPROVAL;               |              |
|     |                        sama skladá dotazy na DB                    |              |
|     |                           |                    |                   |              |
|     | ErrorCode      certification_covers(),    Session.get(),           |              |
|     | -> HTTP        BLOCKING_STATES,           scalars(select(...)),    |              |
|     | status         raise DomainError          commit()                 |              |
|     v                           v                    v                   v              |
|  +-----------------------------------+    +------------------------------------------+  |
|  | [Domain rules]  (module)          |    | [Persistence model]  (module)            |  |
|  | role: čisté predikáty, stavy,     |<---| role: mapovanie na tabuľky,              |  |
|  |       kódy zamietnutí             |enum| engine, session na request               |  |
|  +-----------------------------------+    +------------------------------------------+  |
|                                                              |                          |
+--------------------------------------------------------------|--------------------------+
                                                               | SQL (SQLAlchemy engine,
                                                               v      psycopg)
                                                   [Reservation DB]  (database, PostgreSQL 16)

   [Notification Service]  (external system) — NENAPOJENÁ, v kóde neexistuje volanie (TBD-03)
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
- errors.py         (DomainError, ErrorCode)

Persistence model:
- models.py  (Reservation, Instrument, User, Certification)
- db.py      (engine, SessionFactory, get_session)
- config.py  (DATABASE_URL)

Clock:
- clock.py
```

Každá šípka zodpovedá importu a volaniu v kóde:

| Šípka | Doklad |
| ----- | ------ |
| `Reservation API → Clock` | `api/reservations.py:136` `clock.now()` |
| `Reservation API → Reservation services` | `api/reservations.py:132` `service.confirm_reservation(session, …)` |
| `Reservation API → Persistence model` | `api/reservations.py:22` `Depends(get_session)`; `main.py` `lifespan()` → `Base.metadata.create_all(engine)` |
| `Reservation API → Domain rules` | `main.py` `domain_error_handler()` + `STATUS_BY_CODE` nad `ErrorCode` |
| `Reservation services → Domain rules` | `services/reservations.py:18–25` — `certification_covers`, `BLOCKING_STATES`, `DomainError` |
| `Reservation services → Persistence model` | `:218` `session.get()`, `:243` a `:106` `session.scalars(select(…))`, `:277` `session.commit()` |
| `Persistence model → Domain rules` | `models.py:8` — enumy stavov, kategórií a rolí; importná závislosť, nie volanie |
| `Persistence model → Reservation DB` | `db.py` `create_engine()`, ovládač `psycopg` |

Do databázy vedie **jedna** šípka. Zvláštne na kóde nie je druhá cesta do DB
(neexistuje), ale to, *kto* skladá dotazy: `Reservation services` pracuje so
`Session` priamo, bez repozitára, takže predikát `BR-02` aj dotaz na
certifikáty sú napísané v servisnej vrstve (viď A6). `Reservation services`
nepozná `db.py` — session dostane ako parameter od API.

## A8. Otázka pre ďalší krok C03

| Položka | Obsah |
| ------- | ----- |
| **Otázka** | Kde má byť vynútený invariant `BR-02`, keď o ňom dnes rozhodujú dve operácie — každá s vlastnou kontrolou a vlastným zápisom — a jeho predikát existuje v dvoch formách, z ktorých jedna je mŕtvy kód? |
| **Doklad** | `_overlapping_blocking()` (`services/reservations.py:74`) je volaný z `confirm_reservation()` `:255` (zápis `:272–277`) aj z `decide_reservation()` `:428` (zápis `:442–443`). Podmienka je napísaná v SQL. Paralelná čistá funkcia `rules.intervals_overlap()` (`domain/rules.py:23`) nie je v kóde nikde volaná ani testovaná, hoci komentár na `services/reservations.py:90–91` tvrdí opak. |
| **Prečo je dôležitá** | `BR-02` je jediný invariant systému nad viacerými záznamami (`specification.md`, BR-02). Vo v0.1 ho vynucovalo jedno miesto, vo v0.2 už dve — pridanie ďalšieho blokujúceho stavu alebo ďalšej operácie ho rozšíri znova, a každé miesto musí pamätať aj na podmienku živosti žiadosti (`BR-08`). Zároveň je to tá istá hranica, na ktorej zlyháva `REQ-05`: kontrola a zápis nie sú atomické. Rozhodnutie, *kam* invariant patrí — do servisnej vrstvy, do jedného doménového predikátu, alebo do databázového constraintu — určí, či sa `REQ-05` dá vôbec uzavrieť bez toho, aby sa oprava musela urobiť dvakrát. |

---

# C03 — Architektúra

Nadväzuje na časť A (AS-IS mapovanie scenára `Confirm Reservation`).
Baseline správania zostáva **v0.2**; táto časť nemení špecifikáciu, mení
*štruktúru* implementácie a dopĺňa rozhodnutie, ktoré v nej doteraz chýbalo.

## B. Architektonické drivery

Štyri drivery, všetky prenesené z C02 (`evidence-and-evolution.md`,
„Architektonické drivery prenesené do C03" a „Architektonické drivery, ktoré
zmena odkryla") alebo z revízie v0.2. Prvé dva boli pri vstupe do C03 doložené
padajúcimi testami, tretí a štvrtý vznikli zmenou v0.2.

| Podklad / zdroj | Prečo ovplyvňuje architektúru | Otázka, ktorú musí architektúra vyriešiť |
| --------------- | ----------------------------- | ---------------------------------------- |
| **D-1** · `BR-02`, `REQ-04`, `REQ-05`; časť A A3/A5 | Kontrola prekryvu a zápis stavu sú dva kroky bez spoločnej ochrany. Dve súbežné potvrdenia oba prečítajú „bez konfliktu" a oba zapíšu. Navyše o invariante rozhodujú **dve** operácie — `confirm` aj `approve` — každá s vlastnou kópiou postupu. | Kde sa má urobiť autoritatívne rozhodnutie o potvrdení, aby `BR-02` platilo aj pri súbehu, a kto ten invariant vlastní? |
| **D-2** · `REQ-16`, nález N-07 (revízia v0.2); časť A A3 | To isté okno, ale nad **jednou** rezerváciou: medzi kontrolou zdrojového stavu a zápisom. Týka sa aj zrušenia, kde `REQ-05` nefiguruje. Pozorovateľný dôsledok: používateľ dostane na zrušenie úspech a rezervácia pritom blokuje prístroj. | Kto zaručí, že rezervácia opustí daný stav najviac raz, aj keď o ňu súperia dve rôzne operácie? |
| **D-3** · zmena v0.2 (schvaľovanie), `BR-08`, `TBD-08` | Žiadosť žije ďalej po skončení requestu, ktorý ju vytvoril. `EXPIRED` sa dnes zapíše len vtedy, keď na záznam niekto siahne — zabudnutá žiadosť ticho blokuje prístroj až do `starts_at`. | Kto vlastní stav čakajúcej žiadosti a kto vykoná neskorší prechod, keď ho nevyvolá žiadny používateľ? |
| **D-4** · `TBD-03`, `TBD-09`; časť A A6 | Notification Service nie je v kóde napojená a systém nemá **žiadnu** čítaciu operáciu. Vedúci sa o žiadosti nedozvie a žiadateľ sa nedozvie výsledok. Business zmena sa pritom uloží skôr, než by sa správa odoslala. | Kde má byť integrácia izolovaná a má jej zlyhanie zmeniť výsledok potvrdenia? |

Žiadny z driverov nepomenúva riešenie. `transakcia`, `zámok`, `constraint`,
`worker` sú kandidáti na mechanizmus, nie drivery.

Dva drivery prenesené z C02 v tabuľke samostatne **nie sú** — nie preto, že by
prestali platiť:

- **Zdroj času ako závislosť** je už vyriešený (`clock.py`, odovzdávanie `now`
  do služieb). Do tejto architektúry vstupuje ako odpovednosť O-6 a prvok
  `Clock`; v delte je `KEEP`.
- **Schéma bez migrácií** nie je otázka tohto scenára, ale obmedzenie pri
  výbere: vstupuje do porovnania v E2 ako prevádzková zložitosť a hovorí proti
  alternatíve B. Samostatné rozhodnutie o migráciách zostáva otvorené.

## C1. Doménový model

```mermaid
classDiagram
    class Reservation {
        +UUID id
        +ReservationState state
        +TimeInterval interval
        +datetime created_at
    }
    class Instrument {
        +UUID id
        +String name
        +InstrumentCategory category
        +bool is_active
        +bool requires_approval
    }
    class User {
        +UUID id
        +String full_name
        +UserRole role
    }
    class Certification {
        +InstrumentCategory category
        +datetime valid_until
    }
    class TimeInterval {
        <<value object>>
        +datetime starts_at
        +datetime ends_at
        +overlaps(other) bool
    }
    class ReservationState {
        <<enumeration>>
        DRAFT
        PENDING_APPROVAL
        CONFIRMED
        CANCELLED
        REJECTED
        EXPIRED
    }

    Instrument "1" --> "0..*" Reservation : je rezervovaný
    User "1" --> "0..*" Reservation : žiada
    User "1" --> "0..*" Certification : má
    Reservation "1" *-- "1" TimeInterval
    Reservation "1" --> "1" ReservationState
    Certification ..> Instrument : pokrýva kategóriu
```

Invariant pri vzťahu `Instrument → Reservation`:

> Pre jeden `Instrument` sa nesmú prekrývať `TimeInterval`-y dvoch rezervácií
> v **blokujúcom** stave (`CONFIRMED`, alebo živá `PENDING_APPROVAL` podľa
> `BR-08`). — `BR-02`

`TimeInterval` je v modeli ako value object zámerne: `BR-01` je vlastnosť
intervalu, nie rezervácie. V dnešnom kóde sú to dva stĺpce — rozdiel je
zaznamenaný v delte (J) ako `KEEP`, lebo pre rozhodnutie z ADR-01 nemá dôsledok.

Model neobsahuje `Controller`, `Repository` ani frameworkové triedy.

## C2. Odpovednosti systému

| Zdroj | Odpovednosť | Čo musí rozhodovať / vlastniť | Jeden jasný vlastník? | Dôvod |
| ----- | ----------- | ----------------------------- | --------------------- | ----- |
| **O-1** · OP-03 + statechart | rozhodnúť, či je prechod rezervácie povolený | životný cyklus jednej rezervácie vrátane vypršania žiadosti (`BR-08`) | **áno** | dve operácie nesmú o tom istom zdrojovom stave rozhodnúť rozdielne (`D-2`) |
| **O-2** · `BR-02`, `REQ-05` | vyhodnotiť konflikt a zachovať invariant | rozhodnutie o vstupe do blokujúceho stavu a kritická sekcia okolo neho | **áno** | súbeh nesmie porušiť invariant; dnes o ňom rozhodujú dve miesta (`D-1`) |
| **O-3** · `BR-04`, `BR-05`, `BR-06`, `BR-07` | overiť predpoklady operácie | predikáty pravidiel | nie | sú to čisté funkcie bez stavu; smie ich volať ktokoľvek |
| **O-4** · OP-02 | odpovedať na dostupnosť | nič — len číta | nie | čítacia operácia, výsledok nič nealokuje |
| **O-5** · zmena v0.2, OP-05 | rozhodnúť o čakajúcej žiadosti | rozhodnutie vedúceho: schváliť alebo zamietnuť (`REJECTED`) | **áno** | žiadosť prežije request, ktorý ju vytvoril, a nesmú o nej platiť dve rozhodnutia (`D-3`, `D-2`) |
| **O-6** · `BR-03`, `BR-08` | poskytnúť čas pravidlám | jediný zdroj „teraz" | **áno** | dve pravidlá v jednej operácii musia vidieť ten istý okamih |
| **O-7** · `TBD-03` | doručiť notifikáciu | doručenie a opakovanie | podľa návrhu | externé zlyhanie musí mať definovaný význam (`D-4`) |
| **O-8** · perzistencia | uložiť a načítať stav | mapovanie na tabuľky, pripojenie a session, úroveň izolácie | **áno** | do databázy má viesť jedna cesta s jedným nastavením (časť A, A6) |

Zoskupenie a oddelenie:

- **O-1 a O-2 musia byť spolu.** Zdieľajú ten istý zápis. Keby rozhodnutie
  o prekryve robilo iné miesto než zápis stavu, vznikne medzi nimi okno —
  presne to, čo dokladajú `REQ-05` a `REQ-16`.
- **O-5 má byť oddelené od O-1 v zodpovednosti, nie v zápise.** Schvaľovanie má
  vlastný dôvod na zmenu (politika vedúceho — kto smie rozhodnúť a o čom), ale
  výsledný prechod do blokujúceho stavu musí prejsť cez vlastníka `O-2`. Inak
  má invariant dvoch vlastníkov, čo je dnešný stav.
- **Vypršanie žiadosti patrí k O-1, nie k O-5.** Nevyvoláva ho rozhodnutie
  človeka, ale čas, a tá istá podmienka živosti (`BR-08`) určuje, či žiadosť
  blokuje. Keby ho zapisovalo schvaľovanie aj zrušenie, mal by stav `EXPIRED`
  dvoch vlastníkov.
- **Hranica transakcie patrí k operácii, ktorá prechod vykonáva (O-1, O-5),
  nie k O-8.** Commit kritickú sekciu z `O-2` ukončuje a uvoľňuje zámky;
  úložisko nevie, kedy je rozhodnutie hotové. O-8 dodáva session a mapovanie.
- **O-7 má byť oddelené od všetkého ostatného.** Iná technológia, iná
  dôveryhodnostná hranica, iný režim zlyhania.
- **O-3 a O-6 nesmú poznať perzistenciu.** Sú to čisté funkcie; práve preto sa
  dajú hranice (`60:00`, `valid_until == starts_at`) testovať bez databázy.

Názvy komponentov tu zámerne nie sú — tie prichádzajú až v G2.

## D. Hlavná rozhodovacia otázka

```
Rozhodovacia otázka:
Kde sa má vynucovať invariant BR-02, aby platil aj pri súbežnom potvrdzovaní
a schvaľovaní — a kto ho vlastní, keď dnes o ňom rozhodujú dve nezávislé
operácie?
```

Vychádza z `D-1`, pokračuje otázkou A8 z časti A a má dôsledok na štruktúru
(kto vlastní), na interakciu (kadiaľ musí prechod prejsť) aj na runtime
(kde vzniká serializácia).

## E1. Dve alternatívy

**Alternatíva A — jeden vlastník invariantu v aplikácii, serializácia zámkom**

```
+---------------------- Reservation Application -----------------------+
|                                                                      |
|  [Confirm]        [Approve]                                          |
|      \               /                                               |
|       v             v                                                |
|   [Reservation Lifecycle]   <- JEDINÝ vlastník BR-02                 |
|    enter_blocking_state():                                           |
|      lock(Instrument) -> check overlap -> write state                |
+----------------------------------|-----------------------------------+
                                   | SELECT ... FOR NO KEY UPDATE
                                   v
                          [Reservation DB]
```

**Alternatíva B — invariant vynútený databázou**

```
+---------------------- Reservation Application -----------------------+
|  [Confirm]        [Approve]                                          |
|      \               /                                               |
|       v             v                                                |
|   [Reservation Lifecycle]  -> zapíše a zachytí porušenie             |
+----------------------------------|-----------------------------------+
                                   | INSERT/UPDATE
                                   v
                          [Reservation DB]
                EXCLUDE USING gist (instrument_id WITH =,
                       tstzrange(starts_at, ends_at) WITH &&)
                WHERE state IN ('CONFIRMED','PENDING_APPROVAL')
```

## E2. Porovnanie voči driverom

| Driver / kritérium | Alternatíva A (zámok v službe) | Alternatíva B (DB constraint) |
| ------------------ | ------------------------------ | ----------------------------- |
| **D-1** konzistencia `BR-02` | Platí, kým každá cesta zámok vezme. Vynútiteľné testom nad kódom, nie databázou. | Platí vždy, aj pri zápise mimo aplikácie (migrácia, `psql`, iná služba). Silnejšie. |
| **D-3** živosť žiadosti (`BR-08`) | Podmienka „žiadosť je živá, kým nenastal `starts_at`" sa vyhodnotí v SQL dotaze s aktuálnym časom. Žiadny problém. | **Nedá sa vyjadriť.** Index ani constraint nesmie volať `now()` — nie je immutable. Constraint by blokoval aj vypršané žiadosti, takže `EXPIRED` by sa musel zapisovať dávkovo. Vynúti si vyriešiť `TBD-08` v tom istom kroku. |
| **D-2** stratený zápis nad jednou rezerváciou | Rieši ten istý mechanizmus — zámok nad riadkom rezervácie. Jedno rozhodnutie pokryje oba drivery. | **Nerieši vôbec.** Constraint hovorí o prekryve dvoch záznamov, nie o dvoch zápisoch do jedného. `REQ-16` by zostal otvorený. |
| **zmena / ownership** | Invariant je v kóde, čitateľný a testovateľný. Riziko: ďalšia operácia zámok „zabudne" — preto kontrola v L2. | Invariant je v schéme. Riziko: nie je vidieť pri čítaní kódu a chyba sa prejaví až ako `IntegrityError`, ktorý treba preložiť na `OVERLAP`. |
| **prevádzková zložitosť** | Žiadna nová závislosť. Zámok nad riadkom prístroja serializuje potvrdenia a schválenia **pre ten istý prístroj**; rôzne prístroje bežia súbežne ďalej a vytváranie `DRAFT` (OP-01) neblokuje ani na tom istom prístroji. Stojí na úrovni izolácie `READ COMMITTED`. | Vyžaduje rozšírenie `btree_gist` a migráciu. Migrácie zatiaľ nemáme (`create_all` pri štarte), takže by to bolo druhé architektonické rozhodnutie v tom istom kroku. |

## E3. Ten istý scenár cez obe alternatívy

Scenár: `Confirm Reservation` pre prístroj so schvaľovaním.
Komplikácia: **dve súbežné potvrdenia prekrývajúcich sa intervalov.**

| Krok / udalosť | Alternatíva A | Alternatíva B |
| -------------- | ------------- | ------------- |
| `Confirm` začne (dve vlákna naraz) | obe prejdú kontrolou oprávnenia, stavu, certifikátu | rovnako |
| vstup do kritickej sekcie | prvé vlákno vezme zámok nad riadkom prístroja, druhé čaká | žiadna kritická sekcia; obe pokračujú |
| vyhodnotenie prekryvu | prvé nevidí konflikt a zapíše `PENDING_APPROVAL`; druhé sa k dotazu dostane **až po uvoľnení zámku** a konflikt už vidí | obe nevidia konflikt; prvý zápis prejde, druhý počká na commit prvého a zlyhá na constrainte |
| request skončí | prvé `201`/`200`, druhé `409 OVERLAP` | prvé uspeje, druhé dostane `IntegrityError` z databázy |
| volajúci dostane | definovaný business kód `OVERLAP` | technickú chybu, ktorú treba preložiť na `OVERLAP` — inak unikne `500` |
| schválenie príde neskôr | prechod `PENDING_APPROVAL → CONFIRMED` ide cez toho istého vlastníka a znova pod zámkom | constraint drží, ale medzitým vypršaná žiadosť stále blokuje (`BR-08` sa nedá vyjadriť) |
| výsledok invariantu | najviac jedna blokujúca rezervácia | najviac jedna blokujúca rezervácia, ale aj vypršané žiadosti blokujú |

Obe alternatívy požadované správanie **realizovať vedia**. Líšia sa v tom, čo
pritom rozbijú: B rieši `D-1` silnejšie, ale nerieši `D-2` a rozbíja `D-3`.

## F. ADR-01

```
## ADR-01 — Kde sa vynucuje invariant BR-02

Kontext:
  Baseline v0.2 má jediný invariant nad viacerými záznamami: dve blokujúce
  rezervácie toho istého prístroja sa nesmú prekrývať (BR-02). Vo v0.1 o ňom
  rozhodovalo jedno miesto, vo v0.2 dve — confirm aj approve, každé s vlastnou
  kontrolou a vlastným zápisom. Medzi kontrolou a zápisom je okno; doložené
  padajúcimi testami REQ-05 a REQ-16 (časť A, A3). Predikát prekryvu navyše
  existoval v dvoch formách a jedna z nich bola mŕtvy kód.

Drivery:
  D-1 (BR-02 pri súbehu), D-2 (stratený zápis nad jednou rezerváciou),
  D-3 (živosť žiadosti podľa BR-08).

Alternatíva A:
  Jediný vlastník invariantu v aplikácii. Všetky prechody do blokujúceho stavu
  idú cez enter_blocking_state(); kritickú sekciu serializuje zámok nad riadkom
  prístroja (SELECT ... FOR NO KEY UPDATE), ktorý si vlastník berie sám, a
  prechod nad jednou rezerváciou zámok nad jej riadkom (SELECT ... FOR UPDATE).

Alternatíva B:
  Invariant vynútený databázou — EXCLUDE constraint cez btree_gist nad
  (instrument_id, tstzrange(starts_at, ends_at)) pre blokujúce stavy.

Rozhodnutie:
  Prijímame alternatívu A.

Dôvod:
  Jedno rozhodnutie zatvára D-1 aj D-2 tým istým mechanizmom. Alternatíva B
  rieši D-1 silnejšie, ale D-2 nerieši vôbec — constraint hovorí o vzťahu dvoch
  záznamov, nie o dvoch zápisoch do jedného. A rozbíja D-3: podmienka živosti
  žiadosti závisí od aktuálneho času a constraint ani index now() volať nesmie,
  takže by blokovali aj vypršané žiadosti. B by si vynútila vyriešiť TBD-08
  (dávkový zápis EXPIRED) a zaviesť migrácie v tom istom kroku — dve ďalšie
  rozhodnutia, na ktoré zatiaľ nemáme podklad.

Prijaté negatívne dôsledky:
  1. Invariant drží dohoda v kóde, nie schéma. Zápis mimo aplikácie — migrácia,
     psql, budúca druhá služba — ho poruší a databáza to nezachytí.
  2. Zámok nad riadkom prístroja serializuje VŠETKY potvrdenia a schválenia pre
     ten istý prístroj. Pri desaťnásobku súbežných rezervácií (future pressure
     z C01) to je bod, ktorý treba merať. Vytváranie DRAFT neblokuje - preto
     režim FOR NO KEY UPDATE, nie FOR UPDATE, ktorý by sa zrazil s kontrolou
     cudzieho kľúča pri vkladaní rezervácie.
  3. Správnosť závisí od toho, že žiadna budúca operácia zámok neobíde. Preto
     si zámok berie priamo enter_blocking_state(), nie jeho volajúci, a
     súčasťou rozhodnutia je automatická kontrola (L2), nie len code review.
  4. Odstránili sme rules.intervals_overlap(). Prekryv sa dá odteraz testovať
     iba proti databáze, nie ako čistá funkcia.
  5. Riešenie stojí na úrovni izolácie READ COMMITTED: dotaz na prekryv musí po
     čakaní na zámok vidieť zápis, ktorý medzitým commitol sused. V REPEATABLE
     READ by videl starý snapshot a prešli by obe potvrdenia (overené pokusom).
     Úroveň preto nastavuje db.py výslovne a stráži ju test.

Rozhodnutie znovu otvoríme, keď:
  - meranie ukáže, že serializácia na prístroj je úzke hrdlo; alebo
  - pribudne druhý zapisovateľ do tej istej databázy (druhá služba, import,
    dávka), lebo vtedy dohoda v kóde prestane stačiť; alebo
  - aplikácia bude potrebovať vyššiu úroveň izolácie než READ COMMITTED; alebo
  - vyriešime TBD-08 tak, že EXPIRED sa zapisuje dávkovo — vtedy prestane
    platiť hlavná námietka proti alternatíve B a constraint sa stane
    realizovateľným doplnkom, nie náhradou.
```

## G1. Kontext systému

```
            [Študent]                      [Vedúci laboratória]
                |                                   |
                | create / confirm / cancel,        | approve / reject
                | check availability                |
                v                                   v
          +-------------------------------------------------+
          |              Reservation System                  |
          +-------------------------------------------------+
                :                                   :
                : identity claims                   : notification request
                v                                   v
              [IdP]                        [Notification Service]
          NEEXISTUJE — identite            NENAPOJENÁ — TBD-03
          v požiadavke sa dôveruje
          (TBD-06)
```

Obe hranice sú nakreslené bodkovane ako nenapojené zámerne: v kóde pre ne nie
je žiadne volanie (časť A, A6). Kresliť ich ako funkčné by bolo tvrdenie bez
dokladu. Databáza v kontexte **nie je** — je to vlastné úložisko systému, nie
externý systém, a patrí do G4.

## G2. TO-BE statická architektúra

```
+---------------------------- Reservation System ---------------------------+
|                                                                           |
|  [Reservation API]                                                        |
|  role: prijme command, otvorí session, odčíta čas, preloží chybu          |
|     |                  |                    |                    |        |
|     | create /         | approve /          | check              | now()  |
|     | confirm /        | reject             | availability       v        |
|     | cancel           v                    v                  [Clock]    |
|     |          [Approval Workflow]   [Availability Query]      role:      |
|     |          role: rozhodnutie     role: reportuje BR-02,    jediný     |
|     |                vedúceho              nemení stav         zdroj času |
|     |          owns: zamietnutie            |                  owns:      |
|     |                (REJECTED)             |                  "teraz"    |
|     |                  |                    |                             |
|     |                  | žiada prechod      | predikát BR-02              |
|     |                  | a vypršanie        | (iba číta)                  |
|     v                  v                    v                             |
|  +-------------------------------------------------------------+          |
|  | [Reservation Lifecycle]                                     |          |
|  | role: rozhoduje o prechodoch rezervácie                     |          |
|  | owns: Reservation lifecycle (vrátane vypršania, BR-08)      |          |
|  | owns: invariant BR-02 a kritická sekcia okolo neho          |          |
|  +-------------------------------------------------------------+          |
|          |                                   |                            |
|          | predikáty pravidiel               | Session: get / select /    |
|          v                                   v          zámok / commit    |
|  [Domain Rules]                      [Reservation Store]                  |
|  role: čisté predikáty,              role: mapovanie na tabuľky,          |
|        stavy, kódy zamietnutí              engine, session na request     |
|                                      owns: schéma, úroveň izolácie        |
|                                           |                               |
|  [Notification Integration] — plánované   |                               |
|          :                                |                               |
+----------:--------------------------------|-------------------------------+
           : (zatiaľ žiadne                 | SQL vrátane
           v  volanie, TBD-03)              v riadkových zámkov
  [Notification Service]            [Reservation DB]
```

Diagram ukazuje hlavnú štruktúru. Úplný zoznam povolených závislostí — vrátane
tých, ktoré by v obrázku iba krížili čiary:

| Od | K | Rozhranie / interakcia |
| -- | - | ---------------------- |
| Reservation API | Reservation Lifecycle | create / confirm / cancel |
| Reservation API | Approval Workflow | approve / reject |
| Reservation API | Availability Query | check availability |
| Reservation API | Clock | `now()` — čas sa odčíta raz na operáciu |
| Reservation API | Reservation Store | `get_session()` — session na request |
| Reservation API | Domain Rules | `ErrorCode` → HTTP status |
| Approval Workflow | Reservation Lifecycle | žiada prechod `PENDING_APPROVAL → CONFIRMED` (`enter_blocking_state()`) a zápis vypršania (`expire_request_if_dead()`) |
| Availability Query | Reservation Lifecycle | predikát `BR-02`, iba číta |
| Reservation Lifecycle, Approval Workflow, Availability Query | Domain Rules | predikáty pravidiel, stavy, `DomainError` |
| Reservation Lifecycle, Approval Workflow, Availability Query | Reservation Store | `Session`: get / select / zámok / commit; entity |
| Reservation Store | Domain Rules | enumy stavov, kategórií a rolí (import) |
| Reservation Store | Reservation DB | SQL |

Nič iné povolené nie je. Najmä: do `Approval Workflow` vedie iba API,
`Reservation Lifecycle` ho nevolá, a `Domain Rules` ani `Clock` nezávisia od
ničoho.

**Rozhodnutie z ADR-01 je v diagrame viditeľné takto:** `Reservation Lifecycle`
je jediný prvok s `owns: invariant BR-02` a patrí mu aj kritická sekcia —
zámok, kontrola a zápis. `Approval Workflow` vlastní rozhodnutie vedúceho, ale
prechod do blokujúceho stavu si od `Reservation Lifecycle` **žiada**; sám
zapisuje iba zamietnutie, ktoré nič nealokuje. `Availability Query` `BR-02`
iba reportuje a nič nevlastní.

Priradenie odpovedností z C2:

| Odpovednosť | Vlastník |
| ----------- | -------- |
| O-1 prechody rezervácie vrátane vypršania | Reservation Lifecycle |
| O-2 invariant `BR-02` a kritická sekcia | Reservation Lifecycle |
| O-3 predikáty pravidiel | Domain Rules |
| O-4 dostupnosť | Availability Query |
| O-5 rozhodnutie o žiadosti | Approval Workflow |
| O-6 zdroj času | Clock |
| O-7 notifikácia | Notification Integration *(plánované)* |
| O-8 perzistencia | Reservation Store |

Kde prvky ležia v kóde:

| Prvok | Kód |
| ----- | --- |
| Reservation API | `api/reservations.py`, `main.py` |
| Reservation Lifecycle | `services/reservations.py` — `create_reservation`, `confirm_reservation`, `cancel_reservation`, `enter_blocking_state`, `expire_request_if_dead`, `_lock_reservation`, `_lock_instrument`, `_overlapping_blocking` |
| Approval Workflow | `services/reservations.py` — `decide_reservation` |
| Availability Query | `services/reservations.py` — `check_availability` |
| Domain Rules | `domain/rules.py`, `domain/states.py`, `errors.py` |
| Clock | `clock.py` |
| Reservation Store | `models.py`, `db.py`, `config.py` |
| Notification Integration | — *(plánované)* |

Tri prvky sú **funkcie jedného modulu**, nie samostatné súbory (delta J,
`KEEP`). Spoločné kontroly predpokladov `_require_user`, `_require_instrument`
a `_require_authorized` smie volať ktorýkoľvek z nich. To isté priradenie je
zapísané v `tests/test_architecture_conformance.py` (`ELEMENTS`); test padne,
keď v module pribudne funkcia bez prvku.

## G3. Vlastníctvo prechodov v statecharte

| Prechod | Owner rozhodnutia | Kto smie prechod iba vyžiadať |
| ------- | ----------------- | ----------------------------- |
| `DRAFT → CONFIRMED` | Reservation Lifecycle | Reservation API (na podnet študenta alebo vedúceho) |
| `DRAFT → PENDING_APPROVAL` | Reservation Lifecycle | Reservation API; o tom, že sa ide touto vetvou, rozhoduje `Instrument.requires_approval` (`REQ-10`) |
| `PENDING_APPROVAL → CONFIRMED` | **Reservation Lifecycle** (vstup do blokujúceho stavu) | **Approval Workflow** po kladnom rozhodnutí vedúceho |
| `PENDING_APPROVAL → REJECTED` | Approval Workflow | Reservation API (vedúci) |
| `PENDING_APPROVAL → EXPIRED` | **Reservation Lifecycle** (`expire_request_if_dead()`) | výslovne nikto — vyvolá ho plynutie času (`BR-08`); zapíše sa pri najbližšom zrušení (Reservation Lifecycle) alebo rozhodnutí (Approval Workflow), ktoré na žiadosť smeruje (`TBD-08`) |
| `DRAFT → CANCELLED`, `CONFIRMED → CANCELLED`, `PENDING_APPROVAL → CANCELLED` | Reservation Lifecycle | Reservation API (vlastník alebo vedúci) |

Rozdiel medzi tretím a štvrtým riadkom je celý obsah ADR-01. Zamietnutie je
koncový stav, ktorý nič nealokuje, preto ho `Approval Workflow` rozhodne sám.
Schválenie **alokuje prístroj**, takže ho musí vykonať vlastník invariantu.

Vypršanie (piaty riadok) patrí lifecycle, nie schvaľovaniu: nie je to
rozhodnutie vedúceho a tá istá podmienka živosti určuje, či žiadosť blokuje
(C2). Prvý návrh ho dával `Approval Workflow` — v kóde ho však zapisovalo aj
zrušenie, teda lifecycle. Rozpor našlo review PR #7, viď I.

## G4. Runtime / deployment

```
[Reservation Application — jeden proces, uvicorn]
obsahuje:
  - Reservation API
  - Reservation Lifecycle      <- vlastník BR-02
  - Approval Workflow
  - Availability Query
  - Domain Rules
  - Clock
  - Reservation Store

        | SQL, riadkové zámky (SELECT ... FOR UPDATE / FOR NO KEY UPDATE),
        v úroveň izolácie READ COMMITTED
  [PostgreSQL 16 — kontajner db z docker-compose.yml, port 5433]

  [Notification Service]  - plánované, žiadne volanie (TBD-03)
  [IdP]                   - neexistuje (TBD-06)
```

Jeden proces, jedna databáza. **Serializácia z ADR-01 nevzniká v procese, ale
v databáze** — preto rozhodnutie platí aj vtedy, keď procesov bude viac.
Keby bol zámok v pamäti procesu, druhá inštancia aplikácie by ho obišla.

## H1. Návrhový sekvenčný diagram — Confirm Reservation

```mermaid
sequenceDiagram
    actor Student
    participant API as Reservation API
    participant Clock
    participant Life as Reservation Lifecycle
    participant Rules as Domain Rules
    participant Store as Reservation Store
    participant DB as Reservation DB

    Student->>API: POST /reservations/{id}/confirm
    API->>Store: get_session()
    API->>Clock: now()
    API->>Life: confirm_reservation(session, id, requested_by, now)

    Life->>Store: načítaj rezerváciu so zámkom
    Store->>DB: SELECT ... FOR UPDATE
    DB-->>Life: Reservation(DRAFT)
    Note over Life: predpoklady v poradí diagramu aktivít 3.3 —<br/>BR-06, stav DRAFT, REQ-06, BR-05

    Life->>Rules: certification_covers() — BR-04
    Rules-->>Life: certifikát vlastníka platí

    Life->>Life: enter_blocking_state(CONFIRMED / PENDING_APPROVAL)
    Life->>Store: zamkni riadok prístroja
    Store->>DB: SELECT ... FOR NO KEY UPDATE
    Note over Life,DB: od tejto chvíle je BR-02 pre tento<br/>prístroj serializované (ADR-01)

    Life->>Store: nájdi prekrývajúce blokujúce rezervácie
    Store->>DB: SELECT ... WHERE state IN (...) AND overlap

    alt žiadny konflikt
        DB-->>Life: prázdny výsledok
        Life->>Life: reservation.state = cieľový stav
        Life->>Store: commit
        Store->>DB: COMMIT (uvoľní oba zámky)
        Life-->>API: Reservation
        API-->>Student: 200 OK + stav
    else konflikt (BR-02)
        DB-->>Life: kolidujúca rezervácia
        Life-->>API: DomainError(OVERLAP)
        API-->>Student: 409 {"code": "OVERLAP"}
        Note over Life,DB: stav zostáva DRAFT, zámky uvoľní<br/>rollback pri zatvorení session
    end
```

Zámok nad prístrojom, kontrola prekryvu a zápis stavu sú vnútri
`enter_blocking_state()` — zámok si berie vlastník invariantu, nie operácia,
ktorá ho volá.

Pri schvaľovaní je sekvencia rovnaká s tým rozdielom, že
`enter_blocking_state()` volá `Approval Workflow` (`decide_reservation`), keď
predtým overil `BR-07` a živosť žiadosti — prechod do blokujúceho stavu ide
cez toho istého vlastníka.

## H2. Návrhový triedny diagram

```mermaid
classDiagram
    class ReservationApi {
        <<module>>
        +confirm_reservation(id, actor) Response
        +cancel_reservation(id, actor) Response
        +approve_reservation(id, actor) Response
        +reject_reservation(id, actor) Response
    }
    class ReservationLifecycle {
        <<module>>
        +confirm_reservation(session, id, by, now) Reservation
        +cancel_reservation(session, id, by, now) CancelResult
        +enter_blocking_state(session, reservation, target, now)
        +expire_request_if_dead(session, reservation, now)
        -_lock_reservation(session, id) Reservation
        -_lock_instrument(session, id) Instrument
        -_overlapping_blocking(...) list
    }
    class ApprovalWorkflow {
        <<module>>
        +decide_reservation(session, id, by, approve, now) Reservation
    }
    class DomainRules {
        <<module>>
        +certification_covers(valid_until, starts_at) bool
        +may_cancel_confirmed(starts_at, now) bool
        +approval_request_is_alive(starts_at, now) bool
        +is_interval_valid(starts_at, ends_at) bool
    }
    class Clock {
        <<module>>
        +now() datetime
    }
    class ReservationStore {
        <<module>>
        +get_session() Session
    }
    class Session {
        <<interface>>
        +get(entity, id, with_for_update) entity
        +scalars(select) list
        +commit()
    }
    class Reservation {
        +UUID id
        +ReservationState state
        +datetime starts_at
        +datetime ends_at
    }
    class Instrument {
        +bool is_active
        +bool requires_approval
    }
    class DomainError {
        +ErrorCode code
        +str message
    }

    ReservationApi ..> ReservationLifecycle : confirm / cancel
    ReservationApi ..> ApprovalWorkflow : approve / reject
    ReservationApi ..> Clock : now()
    ReservationApi ..> ReservationStore : get_session()
    ReservationApi ..> DomainError : → HTTP status
    ApprovalWorkflow ..> ReservationLifecycle : enter_blocking_state(), expire_request_if_dead()
    ReservationStore ..> Session : vytvára na request
    ReservationLifecycle ..> Session : get / scalars / commit
    ReservationLifecycle ..> DomainRules
    ReservationLifecycle ..> Reservation : mení state
    ReservationLifecycle ..> Instrument : zamyká riadok
    ReservationLifecycle ..> DomainError
    Instrument "1" --> "0..*" Reservation
```

Každá lifeline z H1 má tu štrukturálneho vlastníka: `Reservation Store`
z H1 je `ReservationStore` so `Session`, cez ktorú lifecycle číta, zamyká
a commituje; `Reservation DB` je externý runtime prvok (G4), nie trieda.
`enter_blocking_state()` je jediná operácia, ktorá priraďuje blokujúci stav —
a `ApprovalWorkflow` k nej vedie závislosť, nie vlastný zápis.

## I. Cross-view kontrola

| Kontrola | Výsledok |
| -------- | -------- |
| C02 ↔ G2 | **OK.** Všetkých päť operácií má v G2 vlastníka; `BR-01`–`BR-08` sú pokryté `Domain Rules` a `Reservation Lifecycle`. |
| C2 ↔ G2 | **OK po oprave.** Prvý návrh dával `Approval Workflow` vlastníctvo prechodu `PENDING_APPROVAL → CONFIRMED`, čím by invariant mal dvoch vlastníkov — presne to, čo ADR-01 ruší. Opravené v G2 aj G3: Approval Workflow prechod *žiada*. Pri review ešte R-2 a R-3 nižšie. |
| G2 ↔ H1 | **OK po oprave (R-4).** Sekvencia používa iba závislosti zo zoznamu v G2. `Reservation Lifecycle` nevolá `Reservation API` späť. |
| H1 ↔ H2 | **OK po oprave (R-1).** Každá lifeline a správa z H1 má v H2 vlastníka. |
| statechart ↔ G3/H1 | **OK po oprave.** Statechart v `diagrams.md` nerozlišuje, kto prechod rozhoduje a kto žiada — G3 to dopĺňa, statechart sa nemení (baseline v0.2 zostáva). Pri review ešte R-3. |
| G2 ↔ G4 | **OK.** Sedem logických prvkov beží v jednom procese; `Notification Integration` nie je ani v jednom, lebo neexistuje. |
| ADR ↔ G2/G4 | **OK po oprave (R-6).** `owns: invariant BR-02` je v G2 práve raz; v G4 je vidieť, že serializácia vzniká v databáze, nie v procese. |

Dva rozpory nájdené pri tejto kontrole sú opravené **v diagramoch, pred zmenou
kódu** — nie v kóde.

### Doplnenie po review PR #7

Prvá kontrola šesť rozporov prehliadla. Našlo ich až review, teda **po** zmene
kódu — poradie, ktorému sa mala kontrola vyhnúť, a preto ich tu nechávame
viditeľné namiesto toho, aby sme prepísali výsledky vyššie.

| # | Kontrola | Rozpor | Oprava |
| - | -------- | ------ | ------ |
| R-1 | H1 ↔ H2 | Lifeline `Reservation Store` nemala v H2 žiadnu triedu, hoci kontrola hlásila OK. | H2 doplnený o `ReservationStore` a `Session`. |
| R-2 | C2 ↔ G2 ↔ kód | G2 pripisoval úložisku „zámky a hranicu transakcie". Zámky sú však kritická sekcia vlastníka invariantu a commit robí operácia. | Opravené v C2 (`O-2`, `O-8`) a G2; kód sa nemenil, v delte `KEEP`. |
| R-3 | G2 ↔ G3 ↔ kód | G2 dával `Approval Workflow` stavy `PENDING_APPROVAL` a `EXPIRED`. Do prvého však podľa G3 vstupuje lifecycle a druhý zapisovalo aj zrušenie — dvaja vlastníci. | `Approval Workflow` vlastní rozhodnutie vedúceho, vypršanie patrí lifecycle (C2, G2, G3); `EXPIRED` dostal jedno miesto zápisu, v delte `CHANGE`. |
| R-4 | G2 ↔ H1 | H1 používal `Clock` a session bez šípky v G2; G2 viedol `approve / reject` do lifecycle a `Domain Rules` do úložiska; H1 kreslil certifikát pred zámkom prístroja a kontroly oprávnenia, stavu a času ako volania `Domain Rules`, hoci ich robí lifecycle. | G2 prekreslený a doplnený o úplný zoznam závislostí, H1 zoradený podľa kódu. |
| R-5 | G1 | Databáza bola v kontexte ako externý systém. | Presunutá do G4; v kontexte ostali IdP a Notification Service. |
| R-6 | ADR ↔ G2/G4 ↔ kód | Zámok nad prístrojom bol „predpokladom volajúceho", nie vecou vlastníka invariantu; režim zámku a úroveň izolácie, na ktorej riešenie stojí, neboli nikde zapísané. | Zámok si berie `enter_blocking_state()` sám; ADR-01 doplnené o režim zámku a dôsledok č. 5; v delte dva riadky `CHANGE`. |

## J. AS-IS → TO-BE delta

| Oblasť | AS-IS (časť A) | TO-BE (G2/H2) | Akcia |
| ------ | --------------- | ------------- | ----- |
| rozhodnutie o `BR-02` | dve miesta: `confirm_reservation():263` a `decide_reservation():436` | jedno: `enter_blocking_state()` | `CHANGE` |
| kritická sekcia | žiadna — kontrola a zápis bez ochrany | zámok nad riadkom prístroja pred kontrolou prekryvu; berie si ho sám vlastník invariantu | `CHANGE` |
| prechod nad jednou rezerváciou | `session.get()` bez zámku, `REQ-16` otvorený | `_lock_reservation()` vo všetkých troch meniacich operáciách | `CHANGE` |
| predikát prekryvu | dve formy, `rules.intervals_overlap()` mŕtva a komentár o nej klamal | jedna forma v SQL | `CHANGE` |
| zápis `EXPIRED` | dve kópie: `cancel_reservation():323` a `decide_reservation():395` | jedno miesto: `expire_request_if_dead()` v lifecycle | `CHANGE` |
| úroveň izolácie | predvolená hodnota servera, nikde nezapísaná | `READ COMMITTED` nastavená výslovne v `db.py` | `CHANGE` |
| testy súbehu | `xfail(strict=True)`, bariéra vnútri kritickej sekcie; súbeh nad OP-05 bez testu | bariéra na vstupe operácie, bez `xfail`; doplnený súbeh zrušenia so schválením a dvoch rozhodnutí | `CHANGE` |
| ochrana vlastníctva invariantu | žiadna | opakovateľná statická kontrola | `CHANGE` |
| `Reservation` ako dátový model bez metód | prechod robí servisná funkcia | rovnako — vlastník je modul, nie entita | `KEEP` |
| `TimeInterval` ako value object | dva stĺpce `starts_at`, `ends_at` | v doménovom modeli value object | `KEEP` |
| čas ako závislosť | `clock.now()` v API, odovzdáva sa službám | rovnako | `KEEP` |
| perzistencia | jedna cesta do DB cez `Session`/engine | rovnako | `KEEP` |
| dotazy a commit | skladá a robí ich servisná vrstva, bez repozitára (časť A, A6) | rovnako, zámky pribudli v tej istej vrstve — `Reservation Store` je mapovanie a session, nie repozitár; ADR-01 repozitár nevyžaduje a bolo by to druhé rozhodnutie | `KEEP` |
| logické prvky a súbory | jeden modul `services/reservations.py` | `Reservation Lifecycle`, `Approval Workflow` a `Availability Query` sú funkcie toho istého modulu; priradenie stráži test | `KEEP` |
| smer závislostí | neoverený | `Approval Workflow → Reservation Lifecycle`, nikdy opačne | `VERIFY` |

Notifikácie (`D-4`, `TBD-03`) a dávkové dočisťovanie `EXPIRED` (`D-3`,
`TBD-08`) v tejto delte **nie sú**. ADR-01 ich nerieši a implementovať ich bez
rozhodnutia by bolo presne to, pred čím varuje sekcia K zadania.

---

# C05 — Detailed Design

Nadväzuje na C03 (G2, G3, ADR-01). Architektúra rozhodla, **kde** leží
odpovednosť za životný cyklus rezervácie. Tento oddiel hovorí, **ako** má tá
jednotka fungovať vo vnútri — tak, aby sa z návrhu dali odvodiť testy.
Baseline správania zostáva **v0.2**: žiadny pozorovateľný výsledok sa nemení.

Oddiel je napísaný **pred zmenou kódu**, nad stavom `main` po C03 (commit
`63ec3f7`). Čo v tej chvíli v kóde nebolo, je označené **(nové)**. Porovnanie
návrhu s implementáciou je v `evidence-and-evolution.md`, oddiel
„C05 — DD Verification Evidence".

## C05.1 Vybraná jednotka

| Položka | Hodnota |
| ------- | ------- |
| Selected architecture unit / responsibility | `Reservation Lifecycle` — `O-1` (prechody rezervácie vrátane vypršania) a `O-2` (invariant `BR-02` a kritická sekcia okolo neho) z C2 a G2 |
| Related requirement(s) | potvrdenie: `REQ-04`, `REQ-05`, `REQ-06`, `REQ-10`, `REQ-16` · zrušenie: `REQ-07`, `REQ-08`, `REQ-09`, `REQ-14` · tá časť `REQ-11` a `REQ-15`, ktorú lifecycle vykonáva pre Approval Workflow |
| Related scenario(s) | `OP-03 Confirm Reservation`, `OP-04 Cancel Reservation` (diagramy aktivít 3.3 a 3.4); z `OP-05` iba vstup do blokujúceho stavu a zápis vypršania |
| Relevant business rule(s) | `BR-02`, `BR-03`, `BR-04`, `BR-05`, `BR-06`, `BR-08` |

**Hranica.** Vnútri sú funkcie prvku `Reservation Lifecycle` podľa G2 a to,
čo si od `Domain Rules` berú. Mimo zostáva vlastné rozhodnutie vedúceho
(`Approval Workflow`: `BR-07`, zápis `REJECTED`), `Availability Query` a HTTP
vrstva. `OP-01 Create` do jednotky patrí, ale nerozhoduje nad uloženým stavom —
viď C05.9.

**Prečo táto jednotka.** C03 dalo invariantu jedného vlastníka, ale vnútro
jednotky ostalo nenavrhnuté. V kóde `63ec3f7` to vidno na troch miestach:

1. Rozhodnutie o prechode je rozložené v reťazcoch `if` troch funkcií
   (`services/reservations.py:351–360`, `:419–440`, `:225–233`) a prepletené
   s dotazmi. Hranice pravidiel (`60:00`, `starts_at`) sa preto dajú overiť iba
   proti databáze; čisté funkcie v `domain/rules.py` nemajú ani jeden priamy
   test — žiadny test ich neimportuje.
2. Kontrola predpokladov alokácie (`BR-05`, `BR-04`) existuje v dvoch kópiách:
   `confirm_reservation` (`:362–379`) a `decide_reservation` (`:509–526`).
   Je to ten istý druh zdvojenia, aký C03 našlo pri `BR-02` a pri zápise
   `EXPIRED`.
3. Kontrakt `enter_blocking_state()` — čo musí platiť pred volaním a po ňom —
   je iba v docstringu. Že funkcia necommituje a že zámok drží až do commitu
   volajúceho, neoveruje nič priamo; testy súbehu to dokladajú len nepriamo
   a pravdepodobnostne (8 kôl).

## C05.2 Podrobné odpovednosti

| # | Detailed responsibility | Why is it needed? |
| - | ----------------------- | ----------------- |
| **L-1** | serializovať operácie nad jednou rezerváciou | `REQ-16`: kontrola zdrojového stavu a zápis musia byť jeden krok, inak rezervácia opustí stav dvakrát |
| **L-2** | overiť žiadateľa a jeho oprávnenie skôr, než operácia prezradí čokoľvek o stave | `BR-06`; nález `RV-6` — z kódu chyby sa neoprávnený nesmie dozvedieť stav cudzej rezervácie |
| **L-3** | rozhodnúť o prechode: zo zdrojového stavu a času určiť cieľový stav alebo dôvod zamietnutia | stavový diagram z C02, `BR-03`, `BR-08`, `REQ-06`, `REQ-09`, `REQ-10`, `REQ-14`; driver `D-2` — dve operácie nesmú o tom istom stave rozhodnúť rozdielne |
| **L-4** | overiť predpoklady alokácie: aktívny prístroj a platný certifikát vlastníka | `BR-05`, `BR-04`; `REQ-04` aj `REQ-11` ich žiadajú rovnako, takže nesmú mať dve znenia |
| **L-5** | strážiť `BR-02` pri vstupe do blokujúceho stavu | `REQ-05`, ADR-01 |
| **L-6** | zapísať vypršanie tak, aby prežilo zamietnutie operácie | `BR-08`, nález `N-04`, `REQ-15` — jediný prípad, keď operácia zmení stav a aj tak skončí zamietnutím |
| **L-7** | určiť hranicu transakcie a výsledok operácie | C2: commit ukončuje kritickú sekciu a uvoľňuje zámky; `REQ-09` potrebuje rozlíšiť zrušenie od zopakovania (`changed`) |

## C05.3 Návrhové prvky a vlastníctvo

| Responsibility | Owner | State/data | Why here? |
| -------------- | ----- | ---------- | --------- |
| L-1 | `_lock_reservation()` | riadok `reservations` pod zámkom `FOR UPDATE` až do konca transakcie | jediná cesta, ktorou služba rezerváciu načíta (stráži test z C03) |
| L-2 | `_require_user()`, `_require_authorized()` | `User.role`, `Reservation.user_id`; iba čítajú | bez stavu, zdieľajú ich všetky operácie |
| L-3 | **Transition Policy** *(nové)* — `domain/transitions.py`: `confirm_decision()`, `cancel_decision()`, `expiry_decision()`, výsledok `Decision` | žiadny vlastný stav; vstupom sú hodnoty `state`, `starts_at`, `now`, `requires_approval` | čistá funkcia nepozná session ani entity — celá tabuľka prechodov je na jednom mieste a dá sa overiť bez databázy |
| L-4 | `_require_allocatable()` *(nové)* | `Instrument.is_active`, `Instrument.category`, `Certification.valid_until` **vlastníka**; iba číta | jedna implementácia pre potvrdenie aj schválenie |
| L-5 | **Invariant Guard** — `enter_blocking_state()` s `_lock_instrument()` a `_overlapping_blocking()` | riadok `instruments` pod zámkom `FOR NO KEY UPDATE`, blokujúce rezervácie prístroja; **jediný zapisovateľ blokujúceho stavu** | ADR-01 |
| L-6 | `_write_expired()` *(nové)* — volá ho zrušenie a `expire_request_if_dead()` | **jediný zapisovateľ `EXPIRED`**; commit je súčasťou zápisu | zápis musí byť potvrdený skôr, než operácia vyhodí zamietnutie, inak ho rollback zahodí |
| L-7 | operácie `confirm_reservation()`, `cancel_reservation()` | transakcia: jeden commit na operáciu | hranica transakcie patrí operácii, ktorá prechod vykonáva (C2), nie strážcovi ani politike |

`Transition Policy` leží fyzicky v balíku `domain/`, teda v prvku `Domain
Rules` z G2 — nesmie poznať perzistenciu (C2, „`O-3` a `O-6` nesmú poznať
perzistenciu"). Lifecycle ju používa cez závislosť, ktorú G2 už povoľuje
(`Reservation Lifecycle → Domain Rules`). Nová hrana medzi prvkami nevzniká.

`_require_allocatable()` patrí medzi spoločné kontroly predpokladov z G2
(vedľa `_require_user`, `_require_instrument`, `_require_authorized`): je bez
stavu a smie ju volať `Reservation Lifecycle` aj `Approval Workflow`.

### Návrhové rozhodnutia

Na tieto rozhodnutia sa odkazujú testy a porovnanie s kódom.

| # | Rozhodnutie | Odkiaľ |
| - | ----------- | ------ |
| **DD-1** | Rozhodnutie o prechode je oddelené od jeho vykonania. Rozhoduje čistá `Transition Policy`; operácia iba načíta, opýta sa, vykoná a commitne. | *(nové)* |
| **DD-2** | Politika je **totálna**: pre každý stav vráti rozhodnutie. Stav, ktorý v tabuľke nemá riadok, je hlasná chyba programu, nie tichý predvolený výsledok. | *(nové)* |
| **DD-3** | Blokujúci cieľ sa nikdy nezapisuje priamo — iba cez `enter_blocking_state()`. Neblokujúci stav zapisuje operácia menovaným literálom. | ADR-01, platí od C03 |
| **DD-4** | `EXPIRED` má jedného zapisovateľa a jeho zápis je commitnutý **skôr**, než operácia skončí zamietnutím. | C03, `R-3`; spresnené |
| **DD-5** | Predpoklady alokácie majú jednu implementáciu a pevné poradie: `BR-05`, potom `BR-04`, obe pred `BR-02`. | *(nové)* |
| **DD-6** | Hranica transakcie patrí operácii. Strážca invariantu ani politika necommitujú; zamietnutie bez cieľového stavu nezapíše nič. | C2; doteraz neoverené |
| **DD-7** | Poradie zamietnutí je pevné (C05.8). Určuje, ktorý kód chyby volajúci dostane, keď neplatí viac vecí naraz. | diagramy aktivít 3.3, 3.4; doteraz iba poradím príkazov v kóde |

## C05.4 Kľúčové rozhrania a kontrakty

### Transition Policy *(nové)*

```
<<interface>> TransitionPolicy                       modul domain/transitions.py

confirm_decision(state, starts_at, now, requires_approval) : Decision
cancel_decision(state, starts_at, now)                     : Decision
expiry_decision(state, starts_at, now)                     : Decision

Decision = (target: ReservationState | None,
            rejection: ErrorCode | None,
            reason: str | None)

Contract:

Preconditions:
- state je stav prečítaný pod zámkom rezervácie (L-1) — inak rozhodnutie
  zastará skôr, než sa vykoná
- starts_at a now sú timezone-aware; now je jedno odčítanie času pre celú
  operáciu (BR-03)

Postconditions:
- funkcia je čistá: nič nečíta, nič nezapisuje, pre ten istý vstup vráti
  to isté
- funkcia je totálna: pre každý zo šiestich stavov vráti Decision
- Decision má práve jeden zo štyroch tvarov (tabuľka nižšie)
- confirm_decision: target je CONFIRMED, PENDING_APPROVAL alebo žiadny
- cancel_decision:  target je CANCELLED, EXPIRED alebo žiadny — nikdy
  blokujúci stav
- expiry_decision:  target je EXPIRED alebo žiadny

Failures (ako Decision.rejection, nie ako výnimka):
- INVALID_STATE
- ALREADY_STARTED
- TOO_LATE
- EXPIRED

Stav bez riadku v tabuľke -> AssertionError (chyba programu, DD-2).
```

Štyri tvary rozhodnutia:

| `target` | `rejection` | Význam | Príklad |
| -------- | ----------- | ------ | ------- |
| stav | — | prechod: operácia uspeje a stav sa zmení | `DRAFT → CANCELLED` |
| — | — | úspech bez zmeny stavu | zrušenie už zrušenej rezervácie (`REQ-09`) |
| — | kód | zamietnutie, stav sa nemení | `TOO_LATE`, `INVALID_STATE`, `ALREADY_STARTED` |
| `EXPIRED` | `EXPIRED` | stav sa zmení **a** operácia sa zamietne | vypršaná žiadosť (`N-04`, `REQ-15`) |

Štvrtý tvar smie existovať iba ako dvojica `EXPIRED` / `EXPIRED`. `Decision`
inú kombináciu cieľa so zamietnutím odmietne už pri vytvorení — rozhodnutie,
ktoré špecifikácia nepozná, sa nedá ani zapísať.

Blokujúci `target` z `confirm_decision` nie je pokyn „zapíš". Znamená
„požiadaj o vstup do tohto stavu": ešte ho čakajú predpoklady alokácie (L-4)
a `BR-02` (L-5).

### Invariant Guard

Kontrakt z ADR-01, tu po prvý raz zapísaný tak, aby sa dal testovať:

```
enter_blocking_state(session, reservation, target_state, now) : None

Contract:

Preconditions:
- reservation je načítaná cez _lock_reservation() v tej istej transakcii
  (poradie zámkov: najprv rezervácia, potom prístroj)
- target_state je blokujúci stav (CONFIRMED, PENDING_APPROVAL)
- transakcia beží v READ COMMITTED
- volajúci už overil zdrojový stav (L-3) a predpoklady alokácie (L-4)

Postconditions (úspech):
- reservation.state == target_state, NECOMMITNUTÉ
- zámok nad riadkom prístroja drží transakcia až do commitu alebo rollbacku
  volajúceho
- v okamihu rozhodnutia neexistovala iná blokujúca rezervácia toho istého
  prístroja s prekrývajúcim sa intervalom (BR-02)

Failures:
- OVERLAP            — stav nezmenený, nič sa nezapísalo
- UNKNOWN_INSTRUMENT
- ValueError         — target_state nie je blokujúci; chyba programu,
                       vyhodená skôr, než funkcia siahne na databázu
```

### Predpoklady alokácie *(nové)*

```
_require_allocatable(session, reservation, instrument) : None

Preconditions:
- instrument je prístroj tejto rezervácie

Postcondition:
- nič sa nezmenilo; prístroj je aktívny a VLASTNÍK rezervácie (nie
  žiadateľ) má na jeho kategóriu certifikát platný k starts_at

Failures, v tomto poradí:
- INSTRUMENT_INACTIVE      (BR-05)
- MISSING_CERTIFICATION    (BR-04)
```

## C05.5 Zameraný štrukturálny pohľad

```mermaid
classDiagram
    class LifecycleOperations {
        <<module>>
        +confirm_reservation(session, id, by, now) Reservation
        +cancel_reservation(session, id, by, now) CancelResult
        +expire_request_if_dead(session, reservation, now)
        -_lock_reservation(session, id) Reservation
        -_write_expired(session, reservation)
    }
    class TransitionPolicy {
        <<interface>>
        +confirm_decision(state, starts_at, now, requires_approval) Decision
        +cancel_decision(state, starts_at, now) Decision
        +expiry_decision(state, starts_at, now) Decision
    }
    class Decision {
        <<value object>>
        +ReservationState target
        +ErrorCode rejection
        +str reason
    }
    class InvariantGuard {
        <<module>>
        +enter_blocking_state(session, reservation, target_state, now)
        -_lock_instrument(session, id) Instrument
        -_overlapping_blocking(session, instrument_id, starts_at, ends_at, now) list
    }
    class SharedPreconditions {
        <<module>>
        +_require_user(session, id) User
        +_require_authorized(reservation, requester)
        +_require_allocatable(session, reservation, instrument)
    }
    class Reservation {
        <<entity>>
        +ReservationState state
        +datetime starts_at
        +datetime ends_at
    }
    class Session {
        <<interface>>
        +get(entity, id, with_for_update) entity
        +scalars(select) list
        +commit()
    }
    class ApprovalWorkflow {
        <<module>>
        +decide_reservation(session, id, by, approve, now) Reservation
    }

    LifecycleOperations ..> TransitionPolicy : pýta sa na rozhodnutie
    TransitionPolicy ..> Decision : vracia
    LifecycleOperations ..> SharedPreconditions : L-2, L-4
    LifecycleOperations ..> InvariantGuard : blokujúci cieľ
    LifecycleOperations ..> Reservation : zapisuje CANCELLED, EXPIRED
    InvariantGuard ..> Reservation : zapisuje CONFIRMED, PENDING_APPROVAL
    LifecycleOperations ..> Session : zámok rezervácie, commit
    InvariantGuard ..> Session : zámok prístroja, dotaz na prekryv
    SharedPreconditions ..> Session : iba číta
    ApprovalWorkflow ..> InvariantGuard : žiada schválenie
    ApprovalWorkflow ..> LifecycleOperations : expire_request_if_dead()
    ApprovalWorkflow ..> SharedPreconditions : L-4
```

Čo má byť z diagramu vidno:

- **Do `Session` nevedie od `TransitionPolicy` žiadna šípka.** To je `DD-1`:
  rozhodnutie nepozná perzistenciu. Časové predikáty si politika berie
  z `domain/rules.py` (`may_cancel_confirmed`, `approval_request_is_alive`).
- **Do stavu `Reservation` zapisujú práve dva prvky** a majú rozdelené, čo:
  blokujúce stavy iba `InvariantGuard` (`DD-3`), `CANCELLED` a `EXPIRED`
  iba `LifecycleOperations`.
- **`commit()` volá iba `LifecycleOperations`** (`DD-6`). `InvariantGuard`
  session používa na zámok a dotaz, nie na ukončenie transakcie.
- `LifecycleOperations`, `InvariantGuard` a `SharedPreconditions` sú skupiny
  funkcií jedného súboru `services/reservations.py`, nie triedy — rovnako ako
  prvky v G2. `ApprovalWorkflow` je mimo jednotky; v diagrame je preto, že je
  druhým klientom tých istých kontraktov.

## C05.6 Podrobná sekvencia — Cancel Reservation

Zrušenie je zvolené preto, že na jednom scenári ukáže všetky štyri tvary
rozhodnutia. `Confirm` na úrovni architektúry je v H1.

```mermaid
sequenceDiagram
    participant API as Reservation API
    participant Op as cancel_reservation
    participant Pre as SharedPreconditions
    participant Pol as TransitionPolicy
    participant S as Session / DB

    API->>Op: cancel_reservation(session, id, by, now)
    Op->>S: _lock_reservation(): SELECT ... FOR UPDATE
    S-->>Op: Reservation(state, starts_at)
    Op->>Pre: _require_user(by), _require_authorized()
    Pre-->>Op: žiadateľ je vlastník alebo SUPERVISOR
    Op->>Pol: cancel_decision(state, starts_at, now)
    Note over Pol: bez I/O — iba tabuľka z C05.8
    Pol-->>Op: Decision(target, rejection)

    alt target = CANCELLED
        Op->>S: state = CANCELLED, commit
        Op-->>API: CancelResult(changed = true)
    else bez cieľa, bez zamietnutia
        Note over Op,S: už CANCELLED — nič sa nezapisuje (REQ-09)
        Op-->>API: CancelResult(changed = false)
    else target = EXPIRED, rejection = EXPIRED
        Op->>S: _write_expired(): state = EXPIRED, commit
        Note over Op,S: commit PRED zamietnutím (DD-4)
        Op-->>API: DomainError(EXPIRED)
    else iba rejection
        Note over Op,S: TOO_LATE alebo INVALID_STATE — nič sa nezapísalo,<br/>zámok uvoľní rollback pri zatvorení session (DD-6)
        Op-->>API: DomainError(kód)
    end
```

`Confirm` má tú istú kostru. Oproti H1 sa menia tri veci:

1. prístroj sa načíta **pred** rozhodnutím, lebo `confirm_decision`
   potrebuje `requires_approval`;
2. kontrolu stavu a času (`INVALID_STATE`, `ALREADY_STARTED`) aj voľbu
   cieľového stavu (`REQ-10`) robí jedno volanie `confirm_decision`;
3. `BR-05` a `BR-04` overí `_require_allocatable()`, až potom ide blokujúci
   cieľ do `enter_blocking_state()`.

## C05.7 Významné operácie

```
Operation:        cancel_decision(state, starts_at, now)
Responsibility:   L-3 — rozhodnúť, čo zrušenie urobí s rezerváciou v danom stave
Input:            zdrojový stav, začiatok rezervácie, čas operácie
Output:           Decision
Preconditions:    kontrakt Transition Policy (C05.4)
Postconditions:   riadok tabuľky z C05.8; target nikdy nie je blokujúci stav
Side effects:     žiadne
Failure behavior: zamietnutie je hodnota (Decision.rejection), nie výnimka
```

```
Operation:        confirm_decision(state, starts_at, now, requires_approval)
Responsibility:   L-3 — rozhodnúť, či a do ktorého blokujúceho stavu smie
                  rezervácia požiadať o vstup
Input:            zdrojový stav, začiatok, čas operácie, príznak prístroja
Output:           Decision
Preconditions:    kontrakt Transition Policy
Postconditions:   stav sa posudzuje PRED časom: rezervácia, ktorá nie je
                  DRAFT, dostane INVALID_STATE aj po svojom začiatku
Side effects:     žiadne
Failure behavior: INVALID_STATE, ALREADY_STARTED ako Decision.rejection
```

```
Operation:        cancel_reservation(session, reservation_id, requested_by, now)
Responsibility:   L-1, L-2, L-7 — vykonať rozhodnutie o zrušení v jednej
                  transakcii
Input:            session, id rezervácie, id žiadateľa, čas operácie
Output:           CancelResult(reservation, changed)
Preconditions:    session v READ COMMITTED; now odčítané raz
Postconditions:   target = CANCELLED -> stav zapísaný a commitnutý,
                                        changed = true
                  bez cieľa        -> nič sa nezapísalo, changed = false
Side effects:     zámok nad riadkom rezervácie do konca transakcie;
                  najviac jeden commit
Failure behavior: NOT_FOUND, UNKNOWN_USER, FORBIDDEN — pred rozhodnutím,
                  stav nezmenený
                  TOO_LATE, INVALID_STATE — stav nezmenený, bez commitu
                  EXPIRED — stav EXPIRED je commitnutý, až potom zamietnutie
                  cieľ, ktorý operácia nepozná -> AssertionError
```

```
Operation:        confirm_reservation(session, reservation_id, requested_by, now)
Responsibility:   L-1, L-2, L-7 — vykonať rozhodnutie o potvrdení; blokujúci
                  cieľ odovzdať strážcovi invariantu
Input:            session, id rezervácie, id žiadateľa, čas operácie
Output:           Reservation v stave CONFIRMED alebo PENDING_APPROVAL
Preconditions:    session v READ COMMITTED; now odčítané raz
Postconditions:   stav je cieľ z confirm_decision, commitnutý; BR-02 platí
Side effects:     zámok nad riadkom rezervácie, potom nad riadkom prístroja;
                  jeden commit
Failure behavior: v poradí z C05.8; pri každom zamietnutí zostáva DRAFT
                  a nič sa necommituje
```

```
Operation:        expire_request_if_dead(session, reservation, now)
Responsibility:   L-6 — rozhranie, cez ktoré o zápis vypršania žiada
                  Approval Workflow
Input:            session, rezervácia pod zámkom, čas operácie
Output:           None, ak žiadosť žije alebo rezervácia nie je žiadosť
Preconditions:    reservation načítaná cez _lock_reservation()
Postconditions:   vypršaná žiadosť má stav EXPIRED, commitnutý
Side effects:     commit (cez _write_expired)
Failure behavior: DomainError(EXPIRED) po commite; rozhoduje expiry_decision
```

`enter_blocking_state()` a `_require_allocatable()` majú kontrakt v C05.4.

## C05.8 Rozhodovacia logika

### Tabuľka `cancel_decision`

| Zdrojový stav | Podmienka | `target` | `rejection` | Zdroj |
| ------------- | --------- | -------- | ----------- | ----- |
| `DRAFT` | — | `CANCELLED` | — | `REQ-07`, `N-03` |
| `PENDING_APPROVAL` | `now < starts_at` | `CANCELLED` | — | `REQ-14` |
| `PENDING_APPROVAL` | `now >= starts_at` | `EXPIRED` | `EXPIRED` | `BR-08`, `N-04` |
| `CONFIRMED` | `now + 60 min < starts_at` | `CANCELLED` | — | `REQ-08` |
| `CONFIRMED` | `now + 60 min >= starts_at` | — | `TOO_LATE` | `BR-03` |
| `CANCELLED` | — | — | — | `REQ-09` |
| `REJECTED`, `EXPIRED` | — | — | `INVALID_STATE` | `REQ-14` |

### Tabuľka `confirm_decision`

| Zdrojový stav | Podmienka | `target` | `rejection` | Zdroj |
| ------------- | --------- | -------- | ----------- | ----- |
| `DRAFT` | `now < starts_at` ∧ prístroj nevyžaduje schválenie | `CONFIRMED` | — | `REQ-10` |
| `DRAFT` | `now < starts_at` ∧ prístroj vyžaduje schválenie | `PENDING_APPROVAL` | — | `REQ-10` |
| `DRAFT` | `now >= starts_at` | — | `ALREADY_STARTED` | `REQ-06` |
| každý iný | — | — | `INVALID_STATE` | OP-03 |

### Tabuľka `expiry_decision`

| Zdrojový stav | Podmienka | `target` | `rejection` | Zdroj |
| ------------- | --------- | -------- | ----------- | ----- |
| `PENDING_APPROVAL` | `now >= starts_at` | `EXPIRED` | `EXPIRED` | `BR-08`, `REQ-15` |
| každý iný prípad | — | — | — | `N-05` |

Riadok `PENDING_APPROVAL` / `now >= starts_at` v tabuľke zrušenia **je**
`expiry_decision` — zrušenie ho preberá, nemá vlastnú kópiu. Vypršanie je
prechod riadený časom, nie operáciou, a preto má jedno znenie pre `OP-04`
aj `OP-05`.

### Vykonanie rozhodnutia

```
operácia(session, id, by, now):
    r = _lock_reservation(id)                    # L-1
    over žiadateľa a oprávnenie                  # L-2
    d = politika(r.state, r.starts_at, now)      # L-3, bez I/O

    podľa d.target:
        blokujúci stav -> _require_allocatable() # L-4
                          enter_blocking_state() # L-5
                          commit                 # L-7
        CANCELLED      -> zápis menovaným literálom, commit
        EXPIRED        -> _write_expired()       # L-6, commit je jeho súčasťou
        žiadny         -> nič
        čokoľvek iné   -> AssertionError

    ak d.rejection: vyhoď DomainError(d.rejection, d.reason)
    vráť výsledok
```

Posledná vetva nie je formalita. Keby politika po budúcej zmene vrátila
cieľ, ktorý operácia nevie vykonať, tiché pokračovanie by vrátilo úspech bez
zápisu.

### Poradie zamietnutí (`DD-7`)

```
NOT_FOUND -> UNKNOWN_USER -> FORBIDDEN
          -> INVALID_STATE -> ALREADY_STARTED | TOO_LATE | EXPIRED    (politika)
          -> INSTRUMENT_INACTIVE -> MISSING_CERTIFICATION             (L-4)
          -> OVERLAP                                                  (L-5)
```

Poradie zodpovedá diagramom aktivít 3.3 a 3.4. `UNKNOWN_USER` v nich nie je —
vetvu „neznámy žiadateľ" pri `OP-03` pomenovalo až C03 časť A (A3) a pri
`OP-04` je to rovnako. Oporu má v `BR-06` („používateľ uvedený v požiadavke
musí existovať"); návrh ju zaraďuje pred `FORBIDDEN`, lebo oprávnenie
neexistujúceho žiadateľa sa nedá posúdiť.

### Súbeh

Politika sa vyhodnocuje **pod zámkom rezervácie**: medzi prečítaním stavu
a vykonaním rozhodnutia ho nemôže zmeniť iná operácia. Zámky sa berú vždy
v poradí rezervácia → prístroj. Nič z toho C05 nemení — je to mechanizmus
ADR-01; návrh iba hovorí, že rozhodnutie politiky je platné práve preto, že
vzniklo vnútri tejto ochrany.

## C05.9 Čo zámerne zostáva v kóde

- **Text zamietnutí** (`Decision.reason`) a preklad kódov na HTTP status.
  Kód zamietnutia je návrh, veta pre človeka nie.
- **Tvar SQL dotazov** — na prekryv aj na certifikáty. Návrh hovorí, čo
  dotaz znamená (`BR-01`, `BR-02`, `BR-04`), nie ako je napísaný.
- **`create_reservation()` (`OP-01`).** Reťaz validácií vstupu bez uloženého
  stavu: nemá zdrojový stav, o ktorom by politika rozhodovala, a súbeh jej
  výsledok nezmení (`REQ-02`).
- **`_require_user()`, `_require_instrument()`, `_lock_reservation()`,
  `_lock_instrument()`** — jednoriadkové načítania s jedným zamietnutím.
- **Ako je tabuľka v politike zapísaná** (`match`, poradie vetiev). Záväzná
  je tabuľka, nie jej zápis.
- **Vlastné rozhodnutie `Approval Workflow`** (`BR-07`, `INVALID_STATE` pre
  stav iný než `PENDING_APPROVAL`, zápis `REJECTED`). Je mimo vybranej
  jednotky. To isté rozdelenie na rozhodnutie a vykonanie by sa naň dalo
  použiť; tento návrh ho nerobí.
