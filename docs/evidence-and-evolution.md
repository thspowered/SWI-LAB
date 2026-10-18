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

Vetva `feature/c02-app` (nad `feature/c02-baseline`), zlúčená ako PR #4
(merge commit `fe36498`). Spoločný tag oboch častí C02 je
`c02-baseline-v0.2` — viď koniec tohto oddielu.


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
(PR #3, #4, #5), merge commity `8065899`, `fe36498` a `38486dc`.
Tag `c02-baseline-v0.2` je nastavený na posledný z nich (`38486dc`), teda na
stav, v ktorom je baseline v0.2 celá v `main`.


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
Podrobne v časti 9 špecifikácie, tabuľka RV-1 až RV-6. (Nezamieňať s R-1 až
R-5 v analýze dopadu — to sú rozhodnutia tímu, nie nálezy revízie.)

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

Tri `xfailed` dokladajú dve medzery, všetky s reprodukovateľným výstupom:
REQ-05 pri potvrdení v oboch vetvách REQ-10 (prístroj bez schvaľovania →
dve `CONFIRMED`, so schvaľovaním → dve `PENDING_APPROVAL`) a REQ-16
(stratený zápis). Oba prípady REQ-05 volajú OP-03; súbežné **schvaľovanie**
(OP-05) test nemá. Obe medzery sú vstup pre C03.

*Opravené v C03 časti A:* táto veta pôvodne tvrdila, že jeden z prípadov
REQ-05 je „pri schvaľovaní". Test však v oboch prípadoch volá
`confirm_reservation()`.

---

## C03 — Architecture Evidence

**Baseline:** v0.2 (nezmenená — C03 mení štruktúru, nie správanie)
**Part A:** AS-IS mapovanie scenára `Confirm Reservation`,
[architecture-and-decisions.md](architecture-and-decisions.md), oddiel „C03 časť A"

**Drivers:** D-1 `BR-02` pri súbehu · D-2 stratený zápis nad jednou rezerváciou
(`REQ-16`) · D-3 živosť a dočisťovanie žiadosti (`BR-08`, `TBD-08`) ·
D-4 nenapojená notifikácia a chýbajúce čítanie (`TBD-03`, `TBD-09`).

**Decision question:** Kde sa má vynucovať `BR-02`, aby platilo aj pri súbežnom
potvrdzovaní a schvaľovaní, a kto ho vlastní?

**Alternatives:** A — jediný vlastník v aplikácii + zámok nad riadkom prístroja.
B — `EXCLUDE` constraint v databáze cez `btree_gist`.

**Scenario walkthrough:** `Confirm` s komplikáciou „dve súbežné potvrdenia
prekrývajúcich sa intervalov" (oddiel E3). Obe alternatívy správanie realizovať
vedia; B rieši D-1 silnejšie, ale D-2 nerieši a D-3 rozbíja — `BR-08` závisí od
aktuálneho času a constraint `now()` volať nesmie.

**ADR:** ADR-01 — prijatá alternatíva A.

**Views:** doménový triedny (C1) · kontext (G1) · statická architektúra (G2) ·
vlastníctvo prechodov (G3, nad statechartom z C02) · runtime/deployment (G4) ·
návrhový sekvenčný (H1) · zameraný návrhový triedny (H2).

**Cross-view issues found/resolved:** dva, oba opravené v diagramoch pred
zmenou kódu. (1) Prvý návrh G2 dával `Approval Workflow` vlastníctvo prechodu
`PENDING_APPROVAL → CONFIRMED`, čím by invariant mal dvoch vlastníkov — presne
to, čo ADR-01 ruší; opravené tak, že Approval Workflow prechod *žiada*.
(2) Statechart z C02 nerozlišuje rozhodovateľa a žiadateľa; doplnené v G3 bez
zmeny baseline.
Review PR #7 našlo ďalších šesť (R-1 až R-6, oddiel I) — až **po** zmene kódu,
čo je poradie, ktorému sa kontrola mala vyhnúť. Štyri sú opravené iba
v artefaktoch; R-3 (dvaja zapisovatelia `EXPIRED`) a R-6 (zámok ako predpoklad
volajúceho, nezapísaná úroveň izolácie) si vyžiadali aj zmenu kódu.

**AS-IS → TO-BE delta:** 8× `CHANGE`, 6× `KEEP`, 1× `VERIFY` (oddiel J).

### Implementation changes

| Zmena | Miesto |
| ----- | ------ |
| `_lock_reservation()` — zámok nad riadkom rezervácie (D-2) | `services/reservations.py` |
| `_lock_instrument()` — zámok nad riadkom prístroja (D-1), režim `FOR NO KEY UPDATE` | `services/reservations.py` |
| `enter_blocking_state()` — jediný vlastník `BR-02`; zámok nad prístrojom si berie sám | `services/reservations.py` |
| `expire_request_if_dead()` — jediné miesto zápisu `EXPIRED` *(od C05: `_write_expired()`)* | `services/reservations.py` |
| úroveň izolácie `READ COMMITTED` nastavená výslovne | `db.py`, `tests/conftest.py` |
| `confirm_reservation`, `cancel_reservation`, `decide_reservation` prepojené na zámky a na vlastníka invariantu | `services/reservations.py` |
| zrušená mŕtva `rules.intervals_overlap()`, opravený komentár, ktorý o nej klamal | `domain/rules.py`, `services/reservations.py` |
| prepísané testy súbehu — bariéra na vstupe operácie, 8 kôl, bez `xfail`; doplnený súbeh nad OP-05 | `tests/test_concurrency_req05.py` |
| nová statická kontrola architektonického pravidla | `tests/test_architecture_conformance.py` |
| opravené docstringy, ktoré po zmene tvrdili, že `REQ-05` garantované nie je | `services/reservations.py` |

Poradie zámkov je v celej službe rovnaké — **najprv rezervácia, potom
prístroj**. Opačné poradie na jedinom mieste by stačilo na deadlock.

### Nález pri implementácii: test, ktorý by po oprave prestal testovať

Toto je druhýkrát, čo nás tá istá trieda chyby skoro dostala, preto to tu
stojí celé.

Testy súbehu z C02 vešali bariéru **za** `_overlapping_blocking()`, teda
dovnútra miesta, ktoré sa práve stalo kritickou sekciou. Po zavedení zámku:

- prvé vlákno vzalo zámok, prešlo kontrolou a zastalo na bariére;
- druhé vlákno uviazlo na zámku a k bariére sa nikdy nedostalo;
- prvé po piatich sekundách vypršalo.

Nameraný výsledok sondou:

```
outcomes: ['BrokenBarrierError', 'BrokenBarrierError']   final: DRAFT
```

Do databázy nezapísal nikto, takže pôvodné tvrdenie „najviac jedna blokujúca
rezervácia" bolo pravdivé — a test by bol zelený bez toho, aby čokoľvek
dokázal. Pri `xfail(strict=True)` by sa to navyše prejavilo len ako `XPASS`,
teda ako dobrá správa.

*Vyriešené dvoma pravidlami, obe sú napísané v hlavičke testu:*

1. **Bariéra je na vstupe operácie**, pred prvým dotykom databázy. Zámok ju
   nemôže zablokovať, a pritom je zaručené, že obe operácie naozaj bežia
   súčasne.
2. **Každý test tvrdí, že výsledky sú business výsledky** — konkrétne kódy,
   nie názvy výnimiek. `assert sorted(outcomes) == ["OK", "OVERLAP"]` padne na
   `BrokenBarrierError` aj na čomkoľvek inom, čo nie je rozhodnutie systému.

Štartovacia bariéra ale nevynúti presné prekrytie, takže jedno kolo môže
kolíziu minúť — pri prvom overení prešiel jeden z dvoch parametrov aj bez
zámkov. Test preto beží **8 kôl**, každé s vlastným intervalom.

### Tvrdenie o REQ-16 a rozpor v baseline v0.2

Pôvodný test tvrdil `len(succeeded) <= 1` — pri súbehu smie uspieť najviac
jedna z operácií. Doslova to hovorí aj `REQ-16` („uspeje najviac jedno z nich")
a príklad overenia pri OP-03. OP-04 v tej istej špecifikácii však o tom istom
súbehu hovorí niečo iné: ak zvíťazí *confirm*, následný *cancel* sa posudzuje
podľa pravidla pre nový stav — a ďaleko pred začiatkom, alebo nad živou
žiadosťou, **oprávnene uspeje**. Po serializácii je teda „confirm, potom
cancel" legálna história a doslovné znenie `REQ-16` sa pri termíne vzdialenom
viac než 60 minút nedá splniť žiadnou implementáciou, ktorá spĺňa OP-04.

Je to **rozpor v baseline v0.2**, nie vlastnosť implementácie. Špecifikáciu
sme v C03 nemenili; testy pokrývajú obe čítania:

- `test_at_most_one_operation_leaves_the_source_state` — termín o 30 minút,
  teda vnútri lehoty `BR-03`. Tu „uspeje najviac jedno" platí pri oboch
  poradiach a test to tvrdí doslova, pre potvrdenie aj pre schválenie.
- `test_concurrent_cancel_and_confirm_on_same_reservation` — termín o 24 hodín.
  Tvrdí to, čo `REQ-16` zakazuje bez výhrad: zrušenie nevráti úspech nad
  rezerváciou, ktorá potom blokuje prístroj.

Prvý z nich je zároveň silnejší detektor: stratený zápis sa v ňom prejaví ako
`['OK', 'OK']` pri každom poradí zápisov, kým druhý ho vidí len vtedy, keď
potvrdenie prepíše zrušenie.

### Behaviour verification

`pytest -q` proti čistej databáze z `docker-compose.yml`:

```
102 passed in 1.37s
```

Žiadny `xfailed` — tri medzery prenesené z C02 sú zatvorené.

| Overenie | Výsledok | Doklad |
| -------- | -------- | ------ |
| success path | prejde | `test_confirm_allocates_instrument`, `test_http_confirm_reservation` |
| alternatívna vetva (prekryv → zamietnutie) | prejde | `test_confirm_rejects_overlap_and_keeps_draft`, `test_confirm_allows_adjacent_interval` |
| hranice z C02 (`60:00`, `valid_until == starts_at`, dotyk intervalov) | prejdú nezmenené | `test_cancel_confirmed_boundary`, `test_certification_boundary`, `test_availability_interval_boundaries` |
| **súbeh D-1** (`REQ-05`, obe vetvy `REQ-10`, 8 kôl) | prejde | `test_concurrent_conflicting_confirmations` |
| **súbeh D-2** (`REQ-16`, potvrdenie ‖ zrušenie, obe vetvy `REQ-10`, 8 kôl) | prejde | `test_concurrent_cancel_and_confirm_on_same_reservation` |
| **súbeh D-2** (`REQ-16` doslova — uspeje najviac jedno; potvrdenie aj schválenie ‖ zrušenie) | prejde | `test_at_most_one_operation_leaves_the_source_state` |
| **súbeh D-2 nad OP-05** (dvaja vedúci rozhodnú opačne) | prejde | `test_concurrent_approve_and_reject_on_same_request` |
| príklad overenia OP-05 (dve súbežné schválenia kolidujúcich žiadostí) | prejde | `test_concurrent_approvals_of_colliding_requests` |
| predpoklad ADR-01 (úroveň izolácie) | prejde | `test_engine_aplikacie_bezi_v_read_committed` |

Demo proti bežiacej aplikácii (`scripts/demo.py`) prebehlo po prestavbe celé —
všetkých päť operácií vrátane `DRAFT → PENDING_APPROVAL → CONFIRMED`,
zamietnutia aj vypršania. Žiadny endpoint sa zmenou nerozbil.

**Dôkaz, že testy nie sú vákuové.** Súbor `tests/test_concurrency_req05.py`
nad kópiou kódu s dočasne vypnutými zámkami (`with_for_update=False`), každá
mutácia tri behy s rovnakým výsledkom:

| Mutácia | Výsledok | Čo padá |
| ------- | -------- | ------- |
| oba zámky vypnuté | `7 failed, 3 passed` | všetko okrem troch testov uvedených nižšie |
| iba zámok nad rezerváciou | `5 failed, 5 passed` | všetkých päť testov `REQ-16` |
| iba zámok nad prístrojom | `2 failed, 8 passed` | oba testy `REQ-05` |
| bez mutácie | `10 passed` | — |

Padajú s pôvodným príznakom:

```
AssertionError: kolo 0: ocakavame jedno potvrdenie a jedno zamietnutie
pre prekryv, dostali sme ['OK', 'OK'] (dve subezne ziadosti)

AssertionError: kolo 0: zrusenie vratilo uspech, ale koncovy stav je
CONFIRMED - pouzivatel si mysli, ze zrusil rezervaciu, ktora blokuje
pristroj (confirm=OK, cancel=OK)

AssertionError: kolo 0: approve=OK, reject=OK - o ziadosti smie rozhodnut
prave jedno z dvoch rozhodnuti
```

Tri testy prechádzajú aj bez zámkov a ani nemajú padať: poistka
inštrumentácie, kontrola úrovne izolácie a
`test_concurrent_approvals_of_colliding_requests`. Posledný zámok
**nerozlišuje** — každé schválenie vidí druhú žiadosť ako blokujúcu, so
zámkom aj bez neho. Vykonáva príklad overenia zo špecifikácie a dokladá, že
dve schválenia nad jedným prístrojom skončia business výsledkom.

### Review PR #7 — pokusy, o ktoré sa opravy opierajú

1. **Zámok pri schvaľovaní nič nestrážilo.** Na pôvodnom commite `b77065c`
   stačilo v `decide_reservation` nahradiť `_lock_instrument` za
   `_require_instrument` a celá sada zostala zelená (`92 passed`), vrátane
   kontroly architektonického pravidla. Test správania to ani zachytiť nemôže:
   kým `BR-02` platí, schvaľovaná žiadosť prístroj už blokuje a konflikt k nej
   nevznikne. *Oprava:* zámok si berie `enter_blocking_state()` sám a statická
   kontrola to stráži.
2. **Riešenie mlčky stálo na úrovni izolácie.** Pôvodný kód so serverom
   prepnutým do `REPEATABLE READ`
   (`PGOPTIONS="-c default_transaction_isolation=repeatable\ read"`): oba testy
   `REQ-05` padli v kole 0 s `['OK', 'OK']`, tri behy z troch. Dotaz na prekryv
   po čakaní na zámok videl starý snapshot. *Oprava:* `db.py` nastavuje
   `READ COMMITTED` výslovne; s tým istým `PGOPTIONS` potom súbor prejde
   (`10 passed`).
3. **`FOR UPDATE` blokoval viac, než ADR tvrdilo.** Dve spojenia priamo proti
   databáze: kým jedno držalo `SELECT ... FOR UPDATE` nad riadkom prístroja,
   `INSERT` rezervácie toho istého prístroja v druhom narazil na
   `lock_timeout` — kontrola cudzieho kľúča si berie `FOR KEY SHARE`. S
   `FOR NO KEY UPDATE` prešiel hneď a dvaja držitelia tohto režimu sa stále
   vylučujú. *Oprava:* zámok nad prístrojom je `FOR NO KEY UPDATE`.

### Architecture conformance rule + result

```
Architektonické pravidlo:
  Rezervácia smie vstúpiť do blokujúceho stavu (CONFIRMED, PENDING_APPROVAL)
  iba cez enter_blocking_state(). Tá istá funkcia si sama berie zámok nad
  prístrojom a až potom vyhodnotí predikát BR-02; predikát okrem nej smie
  volať iba čítacia operácia check_availability(), ktorá BR-02 iba reportuje.

Kontrola:
  tests/test_architecture_conformance.py — statická analýza AST nad celým
  src/swilab. Nepotrebuje databázu ani bežiacu aplikáciu. Sedem testov:
    - mimo vlastníka invariantu sa do stavu zapisuje iba menovaný
      neblokujúci stav,
    - predikát BR-02 sa volá iba z povolených funkcií,
    - vlastník invariantu berie zámok nad prístrojom nepodmienene a pred
      predikátom; nikto iný ten zámok neberie,
    - rezerváciu služba načíta iba cez _lock_reservation() (REQ-16),
    - každá funkcia služby patrí práve jednému prvku z G2,
    - Approval Workflow závisí od Reservation Lifecycle, nie naopak,
    - poistka, že zoznam blokujúcich stavov v teste sedí s BLOCKING_STATES.

Výsledok:
  7 passed.

  Overené aj opačne — deväť vložených porušení, každé padne na pravidle,
  ktoré ho má chytiť, a pomenuje miesto. Napríklad zápis CONFIRMED
  v cancel_reservation:

    Porusene pravidlo z ADR-01: mimo enter_blocking_state() sa do stavu
    rezervacie zapisuje nieco ine nez menovany neblokujuci stav.
      src/swilab/services/reservations.py:442 (funkcia cancel_reservation)

  a odstránenie zámku z vlastníka invariantu:

    Porusene pravidlo z ADR-01: enter_blocking_state() nevola
    _lock_instrument() nepodmienene pred _overlapping_blocking(). Bez zamku
    je kontrola prekryvu iba odporucanie (REQ-05).
```

Vložené porušenia: zápis blokujúceho stavu literálom aj cez premennú, zámok
odstránený, zámok v podmienke, zámok vzatý na druhom mieste, predikát volaný
z `confirm_reservation`, rezervácia načítaná bez zámku, `cancel_reservation`
volajúce `decide_reservation` a nová funkcia služby bez prvku.

Táto kontrola zároveň uzatvára riadok `VERIFY` z delty. Logické rozdelenie
`Approval Workflow` / `Reservation Lifecycle` z G2 **nie je** fyzickým
rozdelením modulov — obe sú v `services/reservations.py` — takže smer
závislosti sa nedá overiť cez importy. Overuje sa cez volania medzi
funkciami: priradenie funkcia → prvok je zapísané v teste (`ELEMENTS`),
nič mimo `Approval Workflow` jeho funkciu nevolá a `decide_reservation`
prechod do blokujúceho stavu žiada cez `enter_blocking_state()`.

Čo kontrola nezachytí: zápis stavu, ktorý nie je priradením ani
konštruktorom — `setattr()` alebo hromadný `UPDATE` cez SQL.

### Remaining uncertainty / risk

1. **Invariant drží dohoda v kóde, nie schéma.** Zápis mimo aplikácie
   `BR-02` poruší a databáza to nezachytí. Prijaté v ADR-01, dôsledok č. 1.
2. **Serializácia na prístroj je nezmeraná.** Future pressure z C01 hovorí
   o desaťnásobku súbežných rezervácií; zámok nad riadkom prístroja je presne
   ten bod, kde sa to prejaví. Nemáme žiadne meranie.
3. **`TBD-08` zostáva otvorené.** `EXPIRED` sa stále zapíše až pri operácii,
   ktorá na žiadosť siahne. Zabudnutá žiadosť blokuje prístroj až do
   `starts_at`. Je to zároveň podmienka, za ktorej by sa dal znovu otvoriť
   ADR-01 v prospech alternatívy B.
4. **`TBD-03` a `TBD-09` nedotknuté.** Driver D-4 je pomenovaný, ale toto kolo
   ho nerieši — systém stále nemá čítaciu operáciu ani notifikácie, takže
   schvaľovací tok je end-to-end nepoužiteľný mimo testov.
5. **Prekryv sa už nedá testovať ako čistá funkcia.** Po zrušení
   `intervals_overlap()` ho overuje iba test proti databáze.
6. **`REQ-16` si v baseline v0.2 protirečí s OP-04.** Požiadavka hovorí
   „uspeje najviac jedno", OP-04 pripúšťa úspešné zrušenie po potvrdení.
   Implementácia aj testy sa držia OP-04 a zákazu, ktorý `REQ-16` vyslovuje
   bez výhrad. Znenie `REQ-16` a príkladu overenia pri OP-03 treba zosúladiť
   v ďalšej baseline — je to zmena špecifikácie, nie architektúry.

**Commit/tag:** vetva `feature/c03-architecture` (PR #7) — návrh `03cfaab`,
implementácia ADR-01 `611e7f4`, evidence `b77065c`, opravy po review
`39b6c00`. Tag `c03-architecture` je nastavený na merge commit `63ec3f7`.

---

## C05 — DD Verification Evidence

**Baseline:** v0.2 (nezmenená — C05 mení vnútro jednej jednotky, nie správanie)
**Jednotka:** `Reservation Lifecycle`
**Návrh:** [architecture-and-decisions.md](architecture-and-decisions.md),
oddiel „C05 — Detailed Design"

Práca prebehla v poradí **návrh** `9e066e6` → **testy** `3e095e1` →
**implementácia** `11ade77` → **evidence** `9e5fd9c` → **opravy po nezávislej
kontrole** `2fefeee`.

Commity vznikli v jednom pracovnom sedení krátko po sebe, s pomocou AI
asistenta. História teda **nedokladá časový odstup** medzi návrhom a kódom.
Dokladá stav repozitára v každom kroku, a ten sa dá overiť:

- v commite `3e095e1` je `src/` zhodné s `main` — testy existujú, kód ešte nie;
- návrh z `9e066e6` obsahuje vety, ktoré sa s výsledným kódom nezhodli a museli
  sa opraviť (tabuľka nižšie) — spätný opis hotového kódu by ich neobsahoval;
- nové testy správania prechádzajú aj nad **pôvodnou** službou z C03 (pokus
  nižšie), takže neboli písané podľa novej implementácie.

Čo z návrhu kód z C03 už spĺňal a čo nie, rozlišuje porovnanie nižšie.

### Tests derived from DD

| DD element / decision | What must hold? | Test level | Test case |
| --------------------- | --------------- | ---------- | --------- |
| **interface contract** — Transition Policy (C05.4) | pre každý stav a čas vráti `Decision` iba v jednom zo štyroch tvarov; cieľ aj zamietnutie sú z množín, ktoré kontrakt vymenúva; potvrdenie vráti vždy práve jedno; iný tvar sa nedá ani vytvoriť; všetky tri tabuľky čítajú stav rovnako | unit | `test_policy_is_total_and_returns_only_shapes_from_the_contract`, `test_decision_refuses_a_shape_the_specification_does_not_know`, `test_decision_accepts_exactly_the_four_shapes`, `test_all_three_tables_read_the_state_the_same_way` |
| **algorithm branch** — tabuľka `cancel_decision` (C05.8) | každý riadok tabuľky vrátane oboch hraníc na jeden tik (`60:00`, `starts_at`); čas rozhoduje iba tam, kde tabuľka podmienku má | unit | `test_cancel_decision_table` (16 riadkov), `test_cancel_decision_depends_on_time_only_where_the_table_says_so` |
| **state transition** — `confirm_decision` (`REQ-10`, `DD-7`) | `DRAFT → CONFIRMED` alebo `PENDING_APPROVAL` podľa prístroja; stav sa posudzuje pred časom | unit | `test_confirm_decision_table_for_draft`, `test_confirm_decision_judges_state_before_time` |
| **algorithm branch** — `expiry_decision` | hranica `starts_at` je ostrá; vyprší iba `PENDING_APPROVAL`; zrušenie preberá ten istý riadok | unit | `test_expiry_decision_boundary`, `test_only_a_pending_request_expires`, `test_cancel_takes_its_expiry_row_from_expiry_decision` |
| `DD-2` — tabuľka zrušenia bez predvoleného riadku | stav bez riadku je chyba programu | unit | `test_cancel_table_has_no_default_row` |
| `DD-3` — blokujúci cieľ iba z potvrdenia | zrušenie ani vypršanie nikdy nevrátia blokujúci stav | unit | `test_only_confirm_ever_asks_for_a_blocking_state` |
| **failure behavior** — `DD-4` | `EXPIRED` je commitnutý skôr, než operácia skončí zamietnutím — pri zrušení, schválení aj zamietnutí; živá žiadosť a iné stavy ostanú nedotknuté | component | `test_expiry_is_committed_before_the_operation_is_rejected`, `test_expire_request_if_dead_leaves_everything_else_alone` |
| **collaboration** — `DD-1`, operácia ↔ politika | čo politika rozhodne, to operácia vykoná — aj presne na hraniciach `starts_at` a `60:00`: cieľ je commitnutý, zamietnutie vyhodené, `changed` sedí | component | `test_cancel_carries_out_exactly_the_policy_decision`, `test_confirm_enters_exactly_the_state_the_policy_chose` |
| **failure behavior** — C05.8, cieľ, ktorý operácia nevie vykonať | `AssertionError` a nič zapísané — v zrušení, v potvrdení (ešte pred predpokladmi alokácie) aj vo vypršaní | component | `test_operation_refuses_a_decision_it_cannot_carry_out`, `test_expiry_refuses_a_decision_it_cannot_carry_out` |
| **interface contract** — Invariant Guard (C05.4), `DD-6` | necommituje; zámok nad prístrojom drží do commitu volajúceho, a to v režime z ADR-01; pri `OVERLAP` nič nezmení; neblokujúci cieľ odmietne skôr, než si vezme zámok | component | `test_guard_writes_the_target_but_leaves_the_commit_to_the_caller`, `test_guard_holds_the_instrument_lock_until_the_callers_commit`, `test_guard_changes_nothing_when_it_rejects_overlap`, `test_guard_refuses_a_non_blocking_target_before_taking_the_lock` |
| **collaboration** — `DD-5` | `BR-05` pred `BR-04`, obe pred `BR-02` — rovnako pri potvrdení aj pri schválení; zamietnutie na predpokladoch zámok nad prístrojom nevezme | component | `test_allocation_preconditions_keep_one_order_on_both_paths` |
| **failure behavior** — `DD-7`, poradie zamietnutí | neznáma rezervácia pred neznámym žiadateľom; neznámy žiadateľ → `UNKNOWN_USER`; oprávnenie pred stavom; rozhodnutie politiky pred predpokladmi alokácie | component | `test_unknown_reservation_wins_over_unknown_requester`, `test_unknown_requester_is_rejected_and_nothing_changes`, `test_authorization_is_judged_before_the_state`, `test_policy_rejection_comes_before_allocation_preconditions` |
| dve formy `BR-08` (politika a SQL) | na hranici `starts_at` odpovedajú rovnako | component | `test_liveness_in_the_overlap_query_agrees_with_the_policy` |
| `DD-1`, `DD-4`, `DD-5` — kde čo smie byť | import politiky nenačíta perzistenciu ani hodiny; pomenované časové pravidlá používa iba doména; operácie sa pýtajú politiky a čas neporovnávajú; politiku volá iba lifecycle; jedna implementácia predpokladov alokácie; jeden zapisovateľ `EXPIRED` | statická (AST, jeden import v čistom procese) | `test_politika_prechodov_nepozna_perzistenciu_ani_hodiny`, `test_casove_pravidla_prechodov_pouziva_iba_domena`, `test_operacie_rozhoduju_iba_cez_politiku`, `test_politiku_prechodov_vola_iba_reservation_lifecycle`, `test_predpoklady_alokacie_maju_jednu_implementaciu`, `test_expired_zapisuje_jedina_funkcia` |
| čisté predikáty `domain/rules.py` | štyri hranice (`BR-01`, `BR-03`, `BR-04`, `BR-08`) sú ostré na jeden tik | unit | `test_rule_boundaries_are_strict` |

Unit testy sú v `tests/test_transitions.py`, component testy
v `tests/test_lifecycle_contracts.py`, statické kontroly v oddiele „C05"
súboru `tests/test_architecture_conformance.py`.

**Zdôvodnenie úrovne pri troch testoch:**

```
Test:            test_cancel_decision_table
Selected level:  unit
Why this level:  Rozhodnutie je čistá funkcia troch hodnôt. Databáza by
                 k nemu nepridala nič okrem času behu: test nerobí jediný
                 zápis, prejde aj so zastavenou databázou a celý súbor
                 (92 testov vrátane hraníc na mikrosekundu) dobehne za
                 niekoľko stotín sekundy.
What design decision does this test verify?
                 DD-1 (rozhodnutie je oddelené od vykonania) a tabuľku
                 zrušenia z C05.8 riadok po riadku.
```

```
Test:            test_expiry_is_committed_before_the_operation_is_rejected
Selected level:  component — služba proti skutočnému Postgresu, stav číta
                 DRUHÉ spojenie
Why this level:  Tvrdenie je o tom, čo prežije transakciu. To vidí iba iné
                 spojenie. Unit test s falošnou session by overil falošnú
                 session; čítanie v tej istej session nerozlíši flush od
                 commitu (mutácia M1b nižšie).
What design decision does this test verify?
                 DD-4 — zápis EXPIRED je potvrdený skôr, než operácia
                 vyhodí zamietnutie.
```

```
Test:            test_guard_holds_the_instrument_lock_until_the_callers_commit
Selected level:  component — druhé spojenie skúša zámok s NOWAIT
Why this level:  Zámok je vlastnosť databázovej transakcie, v procese
                 neexistuje. Testy súbehu z C03 ukazujú jeho DÔSLEDOK
                 a iba pravdepodobnostne (8 kôl); tento ukazuje MECHANIZMUS
                 a deterministicky — vrátane režimu zámku.
What design decision does this test verify?
                 Kontrakt Invariant Guard a DD-6: kritická sekcia okolo
                 BR-02 trvá od vstupu do strážcu po commit volajúceho.
```

### Unit/component tests executed

**Pred implementáciou** (commit `3e095e1`, kód ešte z C03):

```
tests/test_transitions.py, tests/test_lifecycle_contracts.py
    2 errors during collection
    ModuleNotFoundError: No module named 'swilab.domain.transitions'
tests/test_architecture_conformance.py      6 failed, 5 passed
zvyšok sady                                 95 passed
```

„Červený" stav dvoch nových súborov je teda chyba importu: dokladá, že modul
neexistoval, nie to, že testy vedia zachytiť porušenie návrhu. To druhé
dokladajú mutácie nižšie.

**Po implementácii** (commit `11ade77`): `205 passed`.

**Po opravách z nezávislej kontroly** (commit `2fefeee`), `pytest -q` proti
čistej databáze z `docker-compose.yml`:

```
249 passed in 2.24s
```

Tri behy za sebou s rovnakým výsledkom. Z toho 102 testov spred C05 — **žiadny
z nich sa nemenil**, v `test_architecture_conformance.py` iba pribudli dve
mená do `ELEMENTS` — a 147 nových: 92 unit, 49 component, 6 statických.

| Overenie | Výsledok |
| -------- | -------- |
| unit a statické testy s **nedostupnou** databázou (`DATABASE_URL` na mŕtvy port) | `105 passed` — databázu naozaj nepotrebujú |
| component testy s nedostupnou databázou | chyba už vo fixture — databázu naozaj potrebujú |
| `scripts/demo.py` proti bežiacej aplikácii | prebehlo celé, všetkých päť operácií, žiadna odpoveď 5xx |

**Dôkaz, že testy nie sú vákuové.** Tridsaťdva vložených chýb; každá je
jedna zmena nad kópiou `src/` mimo repozitára a po nej celá sada. Dá sa
zopakovať jedným príkazom:

```
.venv/bin/python scripts/mutation_check.py
```

| # | Vložená chyba | Padne testov z C05 | Padne starších |
| - | ------------- | ------------------ | -------------- |
| M1 | `_write_expired()` necommituje | 5 | 3 |
| M1b | `_write_expired()` robí `flush()` namiesto `commit()` | 5 | **0** |
| M2 | hranica `60:00` neostrá (`<` → `<=`) | 2 | 1 |
| M3 | `enter_blocking_state()` commituje sám | 2 | **0** |
| M4 | `BR-04` pred `BR-05` | 2 | **0** |
| M5 | zrušenie vykoná rozhodnutie `EXPIRED` ako `CANCELLED` | 3 | 1 |
| M6 | `confirm_decision`: čas pred stavom | 10 | **0** |
| M7 | žiadosť žije aj v okamihu `starts_at` | 4 | 1 |
| M8 | strážca prijme neblokujúci cieľ | 1 | **0** |
| M9 | `Decision` neoveruje tvar rozhodnutia | 6 | **0** |
| M10 | `cancel_decision` s tichým predvoleným riadkom | 1 | **0** |
| M11 | potvrdenie rozhodne skôr, než overí oprávnenie | 1 | **0** |
| S1 | služba importuje časové pravidlo | 1 | **0** |
| S2 | druhý zapisovateľ `EXPIRED` | 1 | **0** |
| S3 | druhé vyhodnotenie `BR-04` v schvaľovaní | 1 | **0** |
| S4 | politika importuje `sqlalchemy` | 1 | **0** |
| S5 | politika číta hodiny namiesto `now` | 4 | **0** |
| R1 | potvrdenie rozhoduje samo, mimo politiky, s hranicou o tik inde | 5 | **0** |
| R2 | služba volá časové pravidlo cez atribút modulu | 1 | **0** |
| R3 | politika importuje perzistenciu tvarom `from swilab import db` | 1 | **0** |
| R4 | `cancel_decision`: `REJECTED` sa po termíne dá zrušiť | 2 | **0** |
| R5 | `confirm_decision` vráti „úspech bez zmeny" tesne pred začiatkom | 3 | **0** |
| R6 | druhá kópia `BR-05` v schvaľovaní | 1 | **0** |
| R7a | zrušenie neznámy cieľ ticho ignoruje | 1 | **0** |
| R7b | potvrdenie nechá neznámy cieľ na strážcu | 2 | **0** |
| R7c | vypršanie neznámy cieľ ticho ignoruje | 1 | **0** |
| R8a | zámok nad prístrojom je `FOR UPDATE` | 1 | **0** |
| R8b | zámok nad prístrojom je `FOR SHARE` | 1 | 2 |
| R9 | `expiry_decision` porovnáva stav identitou | 1 | **0** |
| R10 | `Decision` prijme zamietnutie bez dôvodu | 2 | **0** |
| R11 | `Approval Workflow` volá politiku priamo | 1 | **0** |
| R12 | zrušenie porovnáva čas samo | 1 | **0** |

`M` a `S` sú chyby, ktorými sme testy overili hneď po implementácii. `R` sú
scenáre z nezávislej kontroly — obídenia, ktoré **pôvodné testy z C05
prepustili**, a vetvy, ktoré nemali test; po opravách ich zachytia.

Všetkých tridsaťdva chýb zachytí aspoň jeden test. Dvadsaťsedem z nich
zachytia **iba** testy odvodené z návrhu. Desať z tých dvadsiatich siedmich
(M1b, M3, M4, M6, M8, M11, S2, R1, R4, R8a) je chyba v správaní alebo pravidle,
ktoré platilo už pred C05 — sada z C03 by ju prepustila. Zvyšných sedemnásť sa
týka štruktúry, ktorú zaviedol až návrh.

Pri M1 sme čakali, že staršie testy chýbajúci commit neuvidia, lebo stav
čítajú cez `session.refresh()` v tej istej transakcii. Uvideli ho: `refresh()`
nezapísanú zmenu zahodí a načíta pôvodnú hodnotu. Slepé sú až na M1b — zápis,
ktorý je flushnutý, ale necommitnutý. Presne ten rozdiel by v prevádzke
rozhodol: po zatvorení session by ho rollback zahodil.

### Design ↔ implementation mismatch

Návrh sme porovnali s kódom dvakrát: s kódom z C03 (čo z návrhu už platilo)
a s výslednou implementáciou.

**1. Návrh ↔ kód z C03.** Nové testy sme pustili proti **pôvodnej** službe
z `main`, iba s doplneným modulom politiky. S testami v stave commitu
`11ade77`:

```
5 failed, 105 passed
```

Prešlo všetkých 32 vtedajších component testov a 67 unit testov; padlo iba
päť statických kontrol. S dnešnými testami je výsledok `11 failed, 143
passed`: sedem statických kontrol a štyri testy, ktoré službe podvrhujú
politiku — tú pôvodná služba nemá. Zvyšných 45 component testov vrátane
riadkov na hraniciach nad ňou prechádza.

Znamená to dve veci. Správanie, ktoré návrh popisuje — poradie zamietnutí,
commit pred zamietnutím, kontrakt strážcu — kód z C03 **už mal**. A
rozhodovacie tabuľky z C05.8 sú zhodné s pôvodnými reťazcami `if`:
`test_cancel_carries_out_exactly_the_policy_decision` porovnáva výsledok
služby s rozhodnutím politiky a prešiel nad starou službou pre každý riadok.
Implementácia C05 je teda zmena **štruktúry**, nie správania.

| DD decision | Platilo v kóde z C03? | Doklad | Čo z toho vyplynulo |
| ----------- | --------------------- | ------ | ------------------- |
| `DD-1` rozhodnutie oddelené od vykonania | NO | rozhodnutie v reťazcoch `if` troch funkcií; statické kontroly nad starou službou padajú | zmena kódu: `domain/transitions.py` |
| `DD-2` totálna politika, zrušenie bez predvoleného riadku | NO | politika neexistovala; zrušenie malo na konci tichý predvolený zápis `CANCELLED` | zmena kódu |
| `DD-3` blokujúci cieľ iba cez `enter_blocking_state()` | YES | kód strážcu sa od C03 nemenil; statické kontroly z C03 | nič — doplnený unit test |
| `DD-4` jeden zapisovateľ `EXPIRED`, commit pred zamietnutím | YES | jedno miesto zápisu aj commit pred zamietnutím boli v `expire_request_if_dead()` | zápis sa oddelil od rozhodnutia ako dôsledok `DD-1` (`_write_expired()`) |
| `DD-5` jedna implementácia predpokladov alokácie | PARTLY | dve kópie s rovnakým poradím — test správania prešiel, statická kontrola padla | zmena kódu: `_require_allocatable()` |
| `DD-6` hranica transakcie patrí operácii | YES | kód sa nemenil; doteraz to neoveroval žiadny test | nič — doplnené testy |
| `DD-7` poradie zamietnutí | YES | poradie sa nemenilo | nič — doplnené testy, aj pre `UNKNOWN_USER` pri `OP-03` (A3 ho označila ako chýbajúci) a rovnako pri `OP-04` |

**2. Návrh ↔ výsledná implementácia.** Všetkých sedem rozhodnutí je
implementovaných. Riadky pod nimi sú miesta, kde sa návrh a kód po
implementácii rozišli — prvé tri sme našli sami, zvyšok nezávislá kontrola.

| DD decision | Implemented? | Evidence | Action |
| ----------- | ------------ | -------- | ------ |
| `DD-1` … `DD-7` | YES | testy v tabuľke „Tests derived from DD"; `249 passed`; 32 mutácií | keep |
| `DD-2`: „stav bez riadku je chyba programu" pre celú politiku | PARTLY | tabuľky potvrdenia a vypršania v tom istom návrhu riadok „každý iný" majú | update DD — veta zúžená na tabuľku zrušenia a zdôvodnená špecifikáciou |
| C05.5: „do stavu zapisujú práve dva prvky", „`commit()` volá iba `LifecycleOperations`" | NO | `decide_reservation()` zapisuje `REJECTED` a commituje; `create_reservation()` zapisuje `DRAFT` | update DD — obe vety platia vnútri jednotky, výnimky sú pomenované |
| C05.7 `confirm_reservation`: „pri každom zamietnutí zostáva `DRAFT`" | NO | pri `INVALID_STATE` rezervácia v `DRAFT` nie je | update DD |
| C05.8: cieľ, ktorý **potvrdenie** nevie vykonať → `AssertionError` | PARTLY | cieľ išiel až do strážcu (`ValueError`), no až po predpokladoch alokácie — chybu programu mohlo prekryť bežné zamietnutie | **update code** — vlastná vetva hneď pri rozhodnutí. *Najprv sme tu opravili návrh; kontrola ukázala, že správny bol návrh.* |
| C05.8: to isté pre **vypršanie** | NO | `expire_request_if_dead()` by ľubovoľný cieľ zapísal ako `EXPIRED` | update code |
| C05.4: `Decision` tvar mimo tabuľky odmietne | PARTLY | overoval sa jeden smer — `Decision(target=EXPIRED)` bez zamietnutia prešiel | update code |
| C05.4: tri tabuľky jednej politiky čítajú ten istý stav | PARTLY | `cancel_decision` porovnával hodnotou, ostatné identitou | update code |
| C05.3: politika „v prvku `Domain Rules`" | NO | rozpor s C2 a G3: vlastníkom rozhodnutia o prechode (`O-1`) je lifecycle | update DD — politika je rozhodovacia časť lifecycle, fyzicky v `domain/`; G2 dorovnané; pribudol test, že ju volá iba lifecycle |
| `DD-1` v docstringu služby: „živosť žiadosti si služba nevyhodnocuje" | PARTLY | druhá forma `BR-08` je v SQL dotaze na prekryv | update DD — docstring aj formulácie hovoria o prechode a o pomenovaných predikátoch |
| C05.1: „hranice pravidiel sa dajú overiť iba proti databáze" | NO | predikáty `60:00` a živosti boli čisté funkcie už v C03 (hovorí to aj C2) | update DD |
| C05.3: tri príliš silné bunky (L-1, L-2, L-3) | PARTLY | napr. „celá tabuľka prechodov" — schvaľovanie v nej nie je | update DD |

### Resolution

**Zmeny kódu z návrhu.** Dve rozhodnutia kód z C03 nespĺňal: `DD-1` —
rozhodovanie sa presunulo do čistej politiky (s ňou aj `DD-2`) — a `DD-5` —
dve kópie predpokladov alokácie sa spojili do jednej. Zápis `EXPIRED` sa
oddelil od rozhodnutia, či žiadosť vypršala, ako dôsledok prvého.

**Štyri zmeny kódu po kontrole.** Vo všetkých štyroch mal pravdu návrh a kód
ho nedržal: vetva pre neznámy cieľ v potvrdení a vo vypršaní, overenie tvaru
`Decision` v oboch smeroch a jednotné porovnávanie stavu. Pri prvej sme
pôvodne spravili opak — upravili sme vetu v návrhu, aby sedela s kódom.
Kontrola ukázala konkrétnu chybu tej úvahy, a tak sa oprava presunula tam, kam
patrila.

**Opravy návrhu.** Ani v jednom prípade sme návrh neprepísali len preto, že
kód je hotový: zakaždým bola nepresná **veta**, nie zámer — tvrdila viac, než
platí, alebo si protirečila s inou časťou toho istého návrhu či s C03.
Rozhodovacie tabuľky z C05.8 sa od commitu `9e066e6` nezmenili. Kontrakt
politiky v C05.4 dostal štyri doplnené vety (potvrdenie vráti práve jedno;
zamietnutie nesie dôvod; stav sa porovnáva hodnotou; tvar mimo tabuľky je
`ValueError`) a jedna sa zúžila (`AssertionError` iba pri `cancel_decision`).

**Jeden rozdiel viditeľný zvonka.** Text správy pri `MISSING_CERTIFICATION`
v potvrdení je teraz „vlastnik rezervacie nema platny certifikat…" namiesto
„pouzivatel nema…" — predtým mala každá z dvoch kópií vlastnú vetu. Kód
zamietnutia aj HTTP status sú rovnaké; text správy návrh zámerne necháva na
kód (C05.9) a žiadny test ho nestráži.

### Nezávislá kontrola

Pred review v tíme prešla vetva tromi nezávislými kontrolami. Robili ich AI
agenti s čistým kontextom — nevideli, ako práca vznikala, iba repozitár
a zadanie — a iba čítali.

| Kontrola | Čo porovnávala | Výsledok |
| -------- | -------------- | -------- |
| zadanie ↔ odovzdanie | každú položku DoD a každý predpísaný formát zo zadania | žiadna položka nechýba; 5 stredných a 9 drobných nálezov |
| návrh ↔ kód ↔ testy | každé tvrdenie návrhu a evidence proti kódu, testom a histórii | žiadne tvrdenie o správaní nie je nepravdivé; 5 stredných a 11 drobných, väčšinou príliš silné vety a neaktualizované miesta v C03 |
| kód a testy | rozdiel oproti `main`, 12 294 porovnaných volaní starej a novej služby | správanie zhodné až na priznaný text správy; 6 stredných a 5 drobných — slabiny testov, nie kódu |

Čo sa po nej zmenilo:

- **kód** — štyri zmeny vymenované vyššie;
- **testy** — hranice aj na úrovni služby; testy pre tri vetvy „cieľ, ktorý
  operácia nevie vykonať"; režim zámku; statické kontroly prepísané tak, aby
  ich neobišiel bežný zápis (import cez atribút, `from swilab import db`,
  druhá kópia `BR-05`), a doplnené o tri nové pravidlá;
- **návrh** — vlastníctvo politiky (`O-1` ostáva lifecycle), rozlíšenie nových
  rozhodnutí od zámeru, ktorý kód už realizoval, tabuľka „Kde sa čo mení",
  dve kostry vykonania namiesto jednej, anglické názvy sekcií zo zadania;
- **C03** — poznámky v G2, G3, H2 a v delte, kde C05 zmenilo, čo tam stojí;
- **evidence** — porovnanie rozdelené na dve tabuľky s hodnotami zo zadania,
  mutácie opakovateľné skriptom, presnejšie zdôvodnenie úrovne.

Čo sme **neprevzali** a prečo:

- test na texty zamietnutí — návrh ich zámerne necháva na kód;
- presun fixtures `now` a `tomorrow` do `conftest.py` — znamenal by zásah do
  testov spred C05, ktoré sme nechceli meniť;
- pomenovaný predikát pre `REQ-06` — porovnanie ostáva v politike, miesto je
  uvedené v tabuľke „Kde sa čo mení";
- `Instrument` v diagrame namiesto `Session` — dôvod je pod diagramom;
- správa commitu `11ade77` uvádza 16 vložených chýb, hoci ich vtedy bolo 17
  (M1b). Históriu neprepisujeme; správne čísla sú tu.

### Remaining design risk

1. **`BR-08` má stále dve formy.** Živosť žiadosti vyhodnocuje politika
   (`approval_request_is_alive`) a nezávisle SQL podmienka v dotaze na prekryv
   (`starts_at > now`). Je to dôsledok ADR-01 č. 4 — predikát `BR-02` žije
   v SQL. Zhodu na hranici drží jeden test, nie konštrukcia.
2. **`Approval Workflow` rozhoduje stále po starom.** Kontrola stavu
   a zápis `REJECTED` sú v `decide_reservation()`, nie v politike. Je to mimo
   vybranej jednotky; kým to tak je, časť stavového diagramu sa nedá overiť
   na úrovni unit.
3. **Statické kontroly vidia mená, nie význam.** Po kontrole vidia aj prístup
   cez atribút, celý `src/` a priame porovnanie času v operáciách. Stále
   nevidia meno poskladané z reťazca (`getattr`), pravidlo prepísané v **novej**
   funkcii, ktorú zoznamy v teste nepoznajú, ani logiku presunutú do SQL.
4. **Test spolupráce berie očakávanie z politiky.** Overuje, že služba
   vykoná, čo politika rozhodla — nie, že politika rozhodla správne. To druhé
   držia unit testy tabuliek; jeden bez druhého nestačí.
5. **Čítanie bez zámku pred kritickou sekciou.** Potvrdenie aj schválenie
   si príznaky prístroja (`requires_approval`, `is_active`) a certifikáty
   prečítajú skôr, než strážca vezme zámok. Dnes ich nemení žiadna operácia
   (`TBD-02`), takže to nemá dôsledok; so správou prístrojov a certifikátov by
   ich súbežná zmena nebola s potvrdením serializovaná. Tak to bolo už po C03.
6. **`_lock_reservation()` neobnoví objekt, ktorý session už drží.** Zámok
   vezme, ale atribúty objektu z identity mapy neprepíše. V prevádzke je
   session na request, takže rezerváciu vždy načíta čerstvú; v testoch so
   zdieľanou session vidí politika objekt z pamäte. Platilo už po C03.
7. **Tri vetvy `AssertionError` sú pre volajúceho HTTP 500.** Je to zámer —
   ide o chybu programu, nie o business výsledok — a so súčasnou politikou ich
   žiadny vstup nedosiahne. Keby sa to zmenilo, používateľ uvidí technickú
   chybu, nie kód zamietnutia.
8. **Mutačný skript je viazaný na presný text kódu.** Po zmene kódu skončí
   chybou „mutácia sa nedá aplikovať" — nahlas, nie ticho — a treba ho
   dorovnať. Je to doklad k tomuto stavu, nie trvalá súčasť testov.
9. **Riziká z C03 trvajú** — invariant drží dohoda v kóde, serializácia na
   prístroj je nezmeraná a `REQ-16` si v baseline v0.2 protirečí s OP-04.

**Commit/tag:** vetva `feature/c05-detailed-design` — návrh `9e066e6`, testy
`3e095e1`, implementácia `11ade77`, evidence `9e5fd9c`, opravy po nezávislej
kontrole `2fefeee` (kód a testy) a nasledujúci commit (dokumenty). Na vetve je
navyše `80a60b6`, drobná oprava stavu baseline v špecifikácii, ktorá s C05
nesúvisí. Tag `c05-detailed-design` je nastavený na merge commit `c7df7a0`
(PR #8).
