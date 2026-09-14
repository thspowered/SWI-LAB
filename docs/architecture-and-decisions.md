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
