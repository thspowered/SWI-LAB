# C01 Engineering Spike

**Varianta:** A — Persistence
**Vykonal:** Tomáš Hrubý
**Dátum:** 2026-09-17

## Question / unknown
Prejde rezervácia cez reálny PostgreSQL tam a späť s neporušenými časmi?
Konkrétne: prežije timezone a mikrosekundová presnosť zápis a opätovné načítanie
v novej session — alebo sa niekde ticho oreže?

Nie je to otázka „funguje SQLAlchemy". Je to otázka, či môžeme stavať pravidlo
o prekryve intervalov na časoch, ktoré nám databáza vráti.

## What we did
Uložili sme rezerváciu s timezone-aware časmi v pásme `+02:00` a zámerne
nekrúhlym počtom mikrosekúnd (`123456`, resp. `654321`), commitli, načítali ju
v **novej** session (aby sme nedostali objekt z identity map pôvodnej session)
a porovnali hodnoty.

Následne sme sa pozreli aj priamo do databázy cez `psql`, aby evidence
nestála len na assertoch v teste.

```
docker compose up -d db
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -v
```

## Observed result

**1. Test prešiel.**

```
============================= test session starts ==============================
platform darwin -- Python 3.12.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/tomashruby/Desktop/SWI-LAB
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1
collected 1 item

tests/test_persistence_spike.py::test_reservation_round_trips_through_postgres PASSED [100%]

============================== 1 passed in 0.12s ===============================
```

**2. Stĺpce sú skutočne `timestamptz` s mikrosekundovou presnosťou.**

```
 column_name  |        data_type         | datetime_precision
--------------+--------------------------+--------------------
 starts_at    | timestamp with time zone |                  6
 ends_at      | timestamp with time zone |                  6
 created_at   | timestamp with time zone |                  6
```

**3. Surová hodnota v databáze — mikrosekundy sedia, ale offset je prepísaný.**

Zapísali sme `2026-10-05 09:30:00.123456+02:00`. V databáze leží:

```
           starts_at           |            ends_at            | state
-------------------------------+-------------------------------+-------
 2026-10-05 07:30:00.123456+00 | 2026-10-05 09:00:00.654321+00 | DRAFT
```

Načítanie v novej Python session vráti to isté: `2026-10-05 07:30:00.123456+00:00`.

Čiže: **okamih v čase prežil presne, mikrosekundy prežili presne — ale pôvodný
offset `+02:00` sa nezachoval.** Postgres `timestamptz` neukladá pásmo, iba
normalizuje na UTC. Náš test si toho nevšimol, lebo `==` na aware datetime
porovnáva okamih, nie zápis: `09:30+02:00 == 07:30+00:00` je `True`.

## Decision / what changes because of the result

1. **Potvrdzujeme `timestamptz` pre `starts_at` / `ends_at`.** Presnosť aj
   okamih prežijú round-trip, takže pravidlo o prekryve môže porovnávať časy
   priamo, bez zaokrúhľovania a bez zavedenia granularity slotu.

2. **Lokálny čas rezervácie nie je odvoditeľný z databázy a treba s tým rátať.**
   Databáza vracia UTC. Ak bude UI potrebovať zobraziť „rezervoval si to na
   9:30", musí si pásmo doplniť z inde — z umiestnenia prístroja alebo
   z preferencie používateľa. Pre C03 to znamená, že prevod na lokálny čas
   patrí do API vrstvy, nie do modelu, a že do `Instrument` pravdepodobne
   pribudne pásmo. Zapísané ako otvorená otázka pre C02.

3. **Nález pribíjame ako assert.** Doplnili sme kontrolu, že načítaný čas má
   offset UTC a hodinu `7`, nie `9`. Existujúce `==` to nezachytí — porovnáva
   okamih, takže prejde rovnako pri `+02:00` aj pri `+00:00`. Explicitný assert
   robí zo zisteného správania spustiteľnú dokumentáciu a upozorní nás, keď sa
   zmení (napríklad ak niekto nastaví `TimeZone` na session).

4. **Zatvorené riziko:** `InstrumentCategory` je použitý ako pomenovaný enum typ
   v dvoch tabuľkách (`instruments`, `certifications`). `create_all` to zvládol
   bez chyby „type already exists", takže migrácie kvôli tomu meniť nemusíme.

---

# Evidence C02: špecifikácia → bežiaca aplikácia

**Stav:** časť A (baseline v0.1). Časť B (zmena — schvaľovací proces) sa dopĺňa
po jej dokončení.

## Prijatá baseline

[docs/specification.md](specification.md) — Specification Baseline v0.1:
pravidlá BR-01..BR-06, operácie OP-01..OP-04, požiadavky REQ-01..REQ-09.
Diagramy v [docs/diagrams.md](diagrams.md). Schválenie beží v PR #3.

## Predvedené základné operácie

Bežiaca aplikácia (FastAPI + PostgreSQL), všetky štyri operácie cez HTTP:

| Operácia | Endpoint |
| -------- | -------- |
| OP-01 Create Reservation | `POST /reservations` |
| OP-02 Check Availability | `GET /instruments/{id}/availability` |
| OP-03 Confirm Reservation | `POST /reservations/{id}/confirm` |
| OP-04 Cancel Reservation | `POST /reservations/{id}/cancel` |

Reprodukovateľné demo: `PYTHONPATH=src .venv/bin/python scripts/demo.py`.

## Skutočne vykonané príklady overenia

`pytest -v` → **40 passed, 1 xfailed** (Python 3.12.11, PostgreSQL 16).

Pre každú operáciu aspoň jeden úspešný a jeden negatívny / hraničný príklad,
skutočne spustený — nie iba napísaný:

| Operácia | Úspešný príklad | Negatívny / hraničný príklad |
| -------- | ---------------- | ---------------------------- |
| OP-01 | platný interval → `DRAFT` (HTTP 201) | `starts_at == ends_at` → `INVALID_INTERVAL` (HTTP 400); neaktívny prístroj; začiatok v minulosti |
| OP-02 | voľný interval → `AVAILABLE` | prekryv → `UNAVAILABLE` + id kolidujúcej rezervácie; dotyk zľava aj sprava → `AVAILABLE` (BR-01) |
| OP-03 | `DRAFT` + platný certifikát → `CONFIRMED` | bez certifikátu → `MISSING_CERTIFICATION` (HTTP 409); `valid_until == starts_at` → zamietnuté, zostáva `DRAFT` |
| OP-04 | `CONFIRMED` > 60 min pred začiatkom → `CANCELLED`, prístroj je opäť `AVAILABLE` | presne 60:00 min pred začiatkom → `TOO_LATE` (HTTP 409); druhé zrušenie → úspech s `changed: false` |

Hraničné prípady, ktoré sme overili zámerne, lebo práve na nich sa
špecifikácia a implementácia najľahšie rozídu:

- `[09:00,10:00)` a `[11:00,12:00)` **nekolidujú** s `[10:00,11:00)`, ale
  `[10:30,11:30)`, `[09:00,12:00)` aj `[10:15,10:45)` áno (BR-01),
- certifikát s `valid_until == starts_at` je **neplatný**, o sekundu neskôr
  už platný (BR-04),
- zrušenie potvrdenej rezervácie presne 60:00 min pred začiatkom je
  **zamietnuté**, 60:00:01 prejde (BR-03),
- `DRAFT` ani `CANCELLED` neblokujú dostupnosť; blokuje iba `CONFIRMED` (BR-02),
- `Create` vytvorí `DRAFT` aj pri úplnom prekryve a bez certifikátu (REQ-02) —
  test je poistkou proti tichému presunu kontrol do `Create`.

## Nájdený nesúlad a spôsob vyriešenia

**Počas špecifikácie (pred kódom)** — tri nálezy, všetky opravené v špecifikácii:
N-01 (potvrdenie na poslednú chvíľu vyrobí nezrušiteľnú rezerváciu — dôsledok
prijatý a pomenovaný), N-02 (dopyt na dostupnosť v minulosti nebol rozhodnutý —
povolený), N-03 (`DRAFT` po začiatku by ostal zaseknutý — zmenené pravidlo
BR-03). Podrobne v časti 8 špecifikácie.

**Počas implementácie** — jeden nesúlad, tentoraz v prostredí, nie v kóde:
editable install prestal fungovať, lebo `.pth` súbor v `site-packages` mal
macOS flag `hidden` a CPython skryté `.pth` zámerne preskakuje. Prejavilo sa to
ako `ModuleNotFoundError: No module named 'swilab'` bez akéhokoľvek náznaku
príčiny. Vyriešené v `pyproject.toml` (`pythonpath = ["src"]`), takže testy už
nezávisia od stavu virtuálneho prostredia u konkrétneho člena tímu.

**Nesúlad medzi špecifikáciou a implementáciou sme nenašli** — ale našli sme
medzeru, ktorú špecifikácia predvídala, viď nižšie.

## Zámerne nesplnená požiadavka: REQ-05

`tests/test_concurrency_req05.py` spúšťa dve súbežné potvrdenia
prekrývajúcich sa rezervácií toho istého prístroja. Výsledok:

```
AssertionError: BR-02 porusene: 2 potvrdene rezervacie na rovnaky interval
toho isteho pristroja, vysledky vlakien: ['CONFIRMED', 'CONFIRMED']
```

Test je označený `xfail(strict=True)`: medzera je **známa, zdokumentovaná
a spustiteľná**, nie prehliadnutá. Keď ju architektúra v C03 zavrie, test začne
prechádzať a `strict=True` si vynúti odstránenie značky.

Prvá verzia testu medzeru neodhalila (výsledok `CONFIRMED` + `OVERLAP`) —
vlákna sa do okna netrafili, lebo prvé stihlo commitnúť skôr, než druhé
čítalo. Test preto používa bariéru, ktorá obe vlákna zosynchronizuje až
potom, čo si obe prečítali stav a ani jedno nezapísalo. Nemení to správanie
systému, iba spoľahlivo vyvolá poradie, ktoré v prevádzke nastane samo.

## Zostávajúce predpoklady / neznáme

TBD-01 až TBD-06 v časti 7 špecifikácie. Najpodstatnejšie pre ďalšie cvičenia:

- **TBD-03** Notification Service — hranica je definovaná, volanie nie je
  implementované (v diagramoch aktivít vyznačené prerušovanou čiarou),
- **TBD-04** zdroj certifikátov (vlastná evidencia vs. študijný systém) —
  ak pribudne sieťové volanie, zmení sa obraz súbehu aj správanie pri výpadku,
- **TBD-06** autentifikácia — v0.1 dôveruje `user_id` v požiadavke.

## Architektonické drivery prenesené do C03

1. **Atomicita kontroly a zápisu pri potvrdení (REQ-05).** Doložené zlyhávajúcim
   testom vyššie. Riešenie (úroveň izolácie, zámok nad prístrojom alebo
   databázový exclusion constraint) je rozhodnutie C03, nie C02.
2. **Zdroj času ako závislosť, nie ako volanie `datetime.now()` kdekoľvek.**
   BR-03 stojí na jednom odčítaní času; dnes to zabezpečuje `swilab/clock.py`
   a odovzdávanie `now` do služieb. Testovateľnosť časových hraníc bez čakania
   v reálnom čase je architektonická požiadavka.
3. **Schéma databázy bez migrácií.** Tabuľky vznikajú cez `create_all` pri
   štarte. Pre C03 to znamená zaviesť migrácie skôr, než sa zmení schéma
   (a časť B ju zmení — pribudnú stavy).

## Commit / tag aplikácie

Vetva `feature/c02-app` (nad `feature/c02-baseline`). Tag sa nastaví po
zlúčení oboch častí C02.
