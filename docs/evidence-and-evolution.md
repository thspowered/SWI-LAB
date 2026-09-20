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
