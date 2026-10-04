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

**Stav:** časť A (baseline v0.1) aj časť B (zmena — schvaľovací proces, v0.2).
Zhrnutie zmeny je na konci dokumentu.

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
- `DRAFT` ani `CANCELLED` neblokujú dostupnosť (v v0.1 blokoval iba
  `CONFIRMED`; od v0.2 aj živá žiadosť — BR-02),
- `Create` vytvorí `DRAFT` aj pri úplnom prekryve a bez certifikátu (REQ-02) —
  test je poistkou proti tichému presunu kontrol do `Create`.

## Nájdený nesúlad a spôsob vyriešenia

**Počas špecifikácie (pred kódom)** — tri nálezy, všetky opravené v špecifikácii:
N-01 (potvrdenie na poslednú chvíľu vyrobí nezrušiteľnú rezerváciu — dôsledok
prijatý a pomenovaný), N-02 (dopyt na dostupnosť v minulosti nebol rozhodnutý —
povolený), N-03 (`DRAFT` po začiatku by ostal zaseknutý — zmenené pravidlo
BR-03). Podrobne v časti 9 špecifikácie.

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

TBD-01 až TBD-09 v časti 8 špecifikácie. Najpodstatnejšie pre ďalšie cvičenia:

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


---

## Zhrnutie dopadu zmeny (časť B — schvaľovací proces)

**Analýza dopadu vznikla pred prepisom** špecifikácie a je v
[docs/change-impact-c02.md](change-impact-c02.md) — samostatný commit `ada6d6e`,
aby bolo v histórii vidieť poradie.

### Rozhodnutia tímu k zmene

| # | Rozhodnutie | Prečo |
| - | ----------- | ----- |
| R-1 | Schvaľovanie zapína príznak `Instrument.requires_approval` | zmenová karta hovorí „niektoré Resources“ |
| R-2 | `PENDING_APPROVAL` **blokuje** prístroj | vedúci nikdy nedostane dve žiadosti na ten istý čas |
| R-3 | Žiadosť vyprší pri `starts_at` | hranica odvodená z dát — žiadne vymyslené číslo |
| R-4 | Zamietnutie končí v `REJECTED` | rozhodnutie človeka má zostať v histórii |
| R-5 | `PENDING_APPROVAL` sa ruší bez lehoty | žiadosť ešte nie je prísľub, BR-03 nemá čo chrániť. **Neskôr zúžené nálezom N-04:** vypršaná žiadosť sa zrušiť nedá, lebo predmet zrušenia zanikol. |

### Čo zmena zasiahla

- **Pravidlá:** BR-02 (blokujúce stavy), BR-03 (dva nové riadky), BR-05;
  nové BR-07 (kto smie schvaľovať) a BR-08 (expirácia).
- **Požiadavky:** zmenené REQ-03, REQ-04, REQ-05; nové REQ-10 až REQ-15.
- **Operácie:** OP-02, OP-03, OP-04 upravené; **nová OP-05**.
- **Diagramy:** use case (nový cieľ, nie nový aktér), stavový diagram (3 nové
  stavy, 5 nových prechodov), aktivity OP-02/03/04 + nová aktivita OP-05.
- **Kód:** `ReservationState` (+3 hodnoty), `Instrument.requires_approval`,
  `rules.approval_request_is_alive`, `services.decide_reservation`, dva nové
  endpointy, `check_availability` dostáva čas.

### Čo sa výslovne nezmenilo

OP-01 Create (REQ-01, REQ-02), BR-01 (význam intervalu), BR-04 (certifikácia —
mení sa iba to, koľkokrát sa vyhodnocuje), BR-06 (oprávnenie vlastník/supervisor),
REQ-09 (idempotencia zrušenia zostala presne v rozsahu `CANCELLED`), lehota
60 minút pre `CONFIRMED` a riešenia nálezov N-01 až N-03.

### Nálezy pri kontrole konzistencie v0.2

**N-04 — Zrušenie vypršanej žiadosti si odporovalo s expiráciou.** BR-03
dovoľovalo zrušiť `PENDING_APPROVAL` kedykoľvek, BR-08 hovorí, že po `starts_at`
je žiadosť vypršaná a `EXPIRED` sa zrušiť nedá. Pre žiadosť po termíne dávali
obe pravidlá opačnú odpoveď a implementácia by si vybrala podľa poradia
podmienok v kóde.
*Vyriešené v špecifikácii:* expirácia má prednosť, pretože BR-08 je vlastnosť
žiadosti, nie správanie jednej operácie. OP-04 stav zapíše a zrušenie zamietne.
Overené: `tests/test_approval.py::test_cancel_expired_request_expires_it`.

**N-05 — Nebolo povedané, či vypršať môže aj `DRAFT`.** Doplnené do BR-08:
expirácia sa týka iba `PENDING_APPROVAL`, lebo iba ten blokuje prístroj.

### Skutočne vykonané príklady overenia (v0.2)

`pytest -v` → **70 passed, 1 xfailed**. Nové príklady pre zmenu:

| Jav zo zmenovej karty | Ako je overený |
| --------------------- | -------------- |
| schválenie | `PENDING_APPROVAL → CONFIRMED`, dôvod v OP-02 sa mení z `PENDING_APPROVAL` na `CONFIRMED` |
| **oneskorenie** | žiadosť zostáva `PENDING_APPROVAL` a celý čas blokuje prístroj; kontroly sa pri rozhodnutí opakujú (certifikát, prístroj, prekryv) |
| **zamietnutie** | `→ REJECTED`, prístroj je znova `AVAILABLE`; ďalšie zrušenie skončí `INVALID_STATE` |
| **vypršanie** | rozhodnutie v okamihu `starts_at` → `EXPIRED` (stav sa zapíše); o sekundu skôr prejde |
| oprávnenie | študent nesmie rozhodovať; vedúci nesmie rozhodnúť o vlastnej žiadosti; iný vedúci smie |

Demo proti bežiacej aplikácii (`scripts/demo.py`) predvádza celý tok vrátane
`PENDING_APPROVAL → CONFIRMED`, zamietnutia aj vypršania.

### Architektonické drivery, ktoré zmena odkryla

1. **Perzistentný asynchrónny proces.** Rezervácia žije v stave, ktorý nikto
   neuzavrie automaticky. `EXPIRED` sa dnes zapíše až pri operácii, ktorá
   záznam číta — dávka či plánovač je rozhodnutie C03 (TBD-08).
2. **Čas ako vstup pravidla.** Expirácia zaviedla čas do vyhodnotenia
   dostupnosti: ten istý dopyt vráti ráno iný výsledok než po `starts_at`.
   Zdroj času prestal byť detail.
3. **REQ-05 sa rozšírilo na dve miesta.** Okno medzi kontrolou a zápisom je
   teraz v `confirm` aj v `approve`, a do blokujúcej množiny pribudol druhý stav.
4. **Notifikácie prestali byť ozdoba.** Pri okamžitom potvrdení sa používateľ
   výsledok dozvedel z odpovede; pri schvaľovaní sa ho inak nedozvie vôbec.
5. **Migrácie.** Zmena pridala hodnoty do enum typu a stĺpec do tabuľky.
   `create_all` existujúcu schému nezmení, takže testy aj demo si schému
   prestavujú. Pred ďalšou zmenou dát to treba vyriešiť.

### Zostávajúci predpoklad / neznáma po zmene

TBD-07: ak je v laboratóriu jediný vedúci, jeho vlastné žiadosti nemá kto
schváliť a prepadnú. Je to dôsledok BR-07, ktorý riešime personálne (druhý
schvaľovateľ), nie zmenou pravidla.

### Commit / tag aplikácie

Vetvy `feature/c02-baseline` → `feature/c02-app` → `feature/c02-approval`
(PR #3, #4, #5). Tag sa nastaví po zlúčení do `main`.


---

## Nezávislá revízia baseline v0.2

Po dokončení zmeny sme špecifikáciu aj implementáciu dali skontrolovať
nezávisle od toho, kto ich písal. Revízia našla nálezy, ktoré by inak prežili
do C03. Tu je to podstatné — celý zoznam a spôsob riešenia je v časti 9
špecifikácie.

### Najvážnejší nález: test, ktorý nič netestoval

Pri zmene v0.2 sme premenovali internú funkciu služby
`_overlapping_confirmed` → `_overlapping_blocking`. Test súbehu
(`tests/test_concurrency_req05.py`) na ňu siahal starým menom a padal na
`AttributeError` **ešte pred spustením vlákien**. Keďže bol označený
`xfail(strict=True)`, pytest zlyhanie prijal ako očakávané a **sada zostala
zelená**.

Dôsledky, ktoré si zaslúžia byť napísané:

1. Test by „prešiel“ pri ľubovoľnej implementácii — správnej aj rozbitej.
2. Poistka, ktorú sme sľubovali („keď sa medzera zavrie, test začne
   prechádzať a `strict=True` na to upozorní“), by nevystrelila nikdy.
3. Tento dokument citoval ako dôkaz výstup, ktorý sa v tom čase **nedal
   zreprodukovať spustením testu**.

*Vyriešené:* inštrumentácia sa inštaluje vo **fixture** — zlyhanie tam pytest
hlási ako ERROR, ktorý `xfail` neprehltne. Pribudol strážny test
`test_instrumentacia_sedi_s_kodom`, ktorý padne, keď sa funkcia premenuje.
Ponaučenie je všeobecnejšie: `xfail(strict=True)` je užitočný na doloženie
známej medzery, ale zakrýva **každé** iné zlyhanie toho testu.

### Druhý nález: medzera, o ktorej sme nevedeli

Revízia ukázala, že súbežné **zrušenie a potvrdenie tej istej rezervácie** obe
uspejú:

```
AssertionError: rezervacia opustila DRAFT dvakrat: obe operacie vratili uspech
['OK', 'OK'], konecny stav je CONFIRMED - pouzivatel si mysli, ze zrusil
rezervaciu, ktora blokuje pristroj
```

Špecifikácia v OP-04 tvrdila presný opak — lenže to tvrdenie nebolo
požiadavkou a nikto ho neoveroval.

*Vyriešené:* tvrdenie sa stalo požiadavkou **REQ-16** a dostalo spustiteľný
dôkaz. Nie je to to isté ako REQ-05: tam ide o okno medzi kontrolou **prekryvu**
a zápisom nad dvoma rôznymi rezerváciami, tu o okno medzi kontrolou
**zdrojového stavu** a zápisom nad jednou — a týka sa aj zrušenia, kde REQ-05
nefiguruje vôbec.

### Šesť rozporov v špecifikácii

Všetky vznikli tým, že oprava nálezu N-04 sa premietla len do časti dokumentu:
BR-03 a REQ-14 stále tvrdili opak chybového zoznamu OP-04; definícia blokujúcich
stavov a BR-02 nemali podmienku živosti; REQ-05 nepokrývala schvaľovanie; BR-08
sľubovalo zápis `EXPIRED` aj tam, kde sa nedeje; zdôvodnenie opakovanej
kontroly certifikátu tvrdilo niečo, čo sa plynutím času stať nemôže; OP-05
kontrolovala stav pred oprávnením a tým prezrádzala stav cudzej rezervácie.
Podrobne v časti 9 špecifikácie, tabuľka R-1 až R-6.

### Čo sa doplnilo v testoch

Revízia našla požiadavky bez pokrytia — najmä **REQ-06** (rezerváciu po
začiatku nemožno potvrdiť), ktorá nemala ani jeden test, a celú skupinu
„OP-03 voči novým stavom“. Doplnené; niektoré existujúce testy mali slabé
tvrdenia (čítali objekt, ktorý služba práve zmutovala, namiesto stavu
v databáze) a boli spevnené.

### Stav po revízii

```
86 passed, 3 xfailed
```

Tri `xfailed` sú tri doložené medzery, všetky s reprodukovateľným výstupom:
REQ-05 pri potvrdení, REQ-05 pri schvaľovaní (obe vetvy REQ-10) a REQ-16
(stratený zápis). Všetky tri sú vstup pre C03.
