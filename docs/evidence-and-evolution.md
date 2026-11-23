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
   Databáza vracia UTC *(doplnené po C06, F-01: vracia ho v pásme session;
   UTC platí, lebo ho od `8b1083b` nastavujeme na každom spojení — viď C07)*.
   Ak bude UI potrebovať zobraziť „rezervoval si to na
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

---

## C07 — Vyhodnotenie nálezov z C06

**Hodnotená baseline (C06 assessed baseline):** `db9c523`, tagy `c02-baseline-v0.2`,
`c03-architecture`, `c05-detailed-design`.
**Reporting baseline:** commit s touto verziou záznamu na vetve
`feature/c07-findings-and-risks`; presný hash a dátum uvádza status report
(`docs/SWI-LAB_C08_Project_Status.pptx`, slide 01). Prvá verzia záznamu
(`12e24b3`) opisovala stav pred opravami; F-01 a F-02 sú odvtedy uzavreté
v `8b1083b` a `fe6c298`.
**Zdroj:** nezávislé review cudzieho tímu podľa L06, 2026-10-26.

Nálezy sme reprodukovali na hodnotenej baseline, nie na opise v review.
Prostredie: PostgreSQL 16 z `docker-compose.yml`, Python 3.12.

### F-01 — timezone: výsledok C01 spiku závisí od konfigurácie servera

| Položka | Obsah |
| ------- | ----- |
| **Claim** | `evidence-and-evolution.md` (C01 spike): „Postgres `timestamptz` neukladá pásmo… databáza vracia UTC." · `architecture-and-decisions.md`: „testy… rozbehnú sa proti akémukoľvek Postgresu." |
| **Expected** | `test_persistence_spike.py:68` očakáva `utcoffset() == 0` nezávisle od servera. |
| **Observed** | PostgreSQL vracia `timestamptz` prevedené do pásma **session** (`TimeZone`), nie vždy do UTC. Obraz `postgres:16` má UTC, natívna inštalácia preberá pásmo systému. |
| **Verify** | Reprodukované na `db9c523`: `PGTZ=UTC pytest` → `249 passed`; `PGTZ=Europe/Prague pytest` → **`1 failed, 248 passed`**, `AssertionError` na `test_persistence_spike.py:68`. |
| **Disposition** | **VERIFIED FINDING · ACCEPT.** Nález je technický, nie vec vkusu hodnotiteľa. |
| **Action / owner** | Fixovať pásmo na spojení a tým zo sľubu urobiť vlastnosť systému (nie preformulovať tvrdenie). Pridelené Tomášovi Krišicovi, vykonal Tomáš Hrubý. |
| **Closure evidence** | **Uzavreté: `db9c523` → `8b1083b`.** `create_db_engine()` v `src/swilab/db.py` je jediný spôsob, ako vzniká engine pre aplikáciu aj testy; pri každom novom spojení nastaví `SET TIME ZONE 'UTC'`. Overenie na `8b1083b`: `PGTZ=Europe/Prague pytest` → `253 passed`; server s `ALTER DATABASE swilab SET timezone TO 'Europe/Prague'` → `db9c523`: `1 failed, 248 passed`, `8b1083b`: `253 passed`. Nové testy `test_session_timezone_is_utc_whatever_the_server_says` a `test_engine_aplikacie_aj_testov_bezi_v_utc`; mutácia `F01` (odstránenie nastavenia) ich zhodí obe. Riziko `R-01`. |

**Návrh z review sme overili a nestačí.** `connect_args={"options": "-c timezone=UTC"}`
pri `PGTZ=Europe/Prague` prehrá: `PGTZ` sa posiela ako parameter spojenia a server
ho spracuje až po `options`, takže session ostane v `Europe/Prague`. Preto `SET`
až po pripojení, ktorý prebije server aj klienta.

Doplnenie k dopadu, ktoré review neuvádza: netýka sa to len testu. Pri serveri
mimo UTC vracia API časy s offsetom (`+01:00`) namiesto `Z`, čo je zmena
pozorovateľného rozhrania.

### F-02 — hranica OP-01 `starts_at == now` nie je overená

| Položka | Obsah |
| ------- | ----- |
| **Claim** | `specification.md`, OP-01: „`s <= currentTime` → zamietnuté" (`START_IN_PAST`). |
| **Expected** | Rezervácia začínajúca presne v okamihu `now` je zamietnutá. |
| **Observed** | Jediný test (`test_create_rejects_past_start`) skúša `now - 1h`, teda ďaleko od hranice. |
| **Verify** | Reprodukované: mutácia `if starts_at <= now:` → `if starts_at < now:` na `services/reservations.py:314` **prežila celú sadu — `249 passed`**. Náš `scripts/mutation_check.py` ju nepokrýva (hlási ďalej `32/32`), lebo mieri na jednotku z C05. |
| **Disposition** | **VERIFIED FINDING · ACCEPT.** |
| **Action / owner** | Doplniť hraničný test `starts_at == now` → `START_IN_PAST` a mutáciu pridať do `mutation_check.py`, aby ju chránila trvalo. Owner: Tomáš Hrubý. |
| **Closure evidence** | **Uzavreté: `db9c523` → `fe6c298`.** `test_create_start_boundary` overuje hranicu z oboch strán: `starts_at == now` → `START_IN_PAST`, `now + 1s` → `DRAFT`. Kód sa nemenil. Mutácia `F02` (`<=` → `<`) v `scripts/mutation_check.py` teraz padne na tomto teste — na `db9c523` prežila celú sadu. Riziko `R-02`. |

Je to jediná hranica v systéme, ktorú nestrážime. Pri `BR-01`, `BR-03`, `BR-04`,
`BR-08` a `REQ-06` to robíme dôsledne — o to viac to patrí doplniť.

### F-03 — `REQ-16` si v schválenej baseline v0.2 protirečí s OP-04

| Položka | Obsah |
| ------- | ----- |
| **Claim** | `REQ-16` a príklad overenia pri OP-03: pri súbehu zrušenia a potvrdenia „uspeje najviac jedno z nich". |
| **Expected** | Jedno konzistentné čítanie naprieč špecifikáciou. |
| **Observed** | OP-04 pripúšťa, že po víťaznom potvrdení sa zrušenie posúdi podľa nového stavu a ďaleko pred začiatkom **legitímne uspeje**. Obe operácie teda uspejú. |
| **Verify** | Potvrdené čítaním špecifikácie. Rozpor sme našli sami už v C03 (nález N-07, riziko 6) a zopakovali v C05 (riziko 9); testy pokrývajú **obe** čítania. |
| **Disposition** | **VERIFIED FINDING · ACCEPT — uzavreté v špecifikácii v0.3 (2026-11-15).** Pôvodne DEFER, s uznaním, že odklad je sám o sebe nález. |
| **Prečo pôvodne DEFER** | Je to zmena **špecifikácie**, nie architektúry ani kódu. Správanie systému je definované a otestované; mení sa znenie požiadavky, a to patrí do v0.3 s analýzou dopadu, nie do opravy na poslednú chvíľu pred obhajobou. |
| **Čo review dodalo** | Upozornenie, ktoré sme si sami nevšimli: špecifikácia je naďalej označená ako **schválená** (§11), tabuľka konzistencie v §9 rozpor neuvádza a README v C02 DoD tvrdí „text, požiadavky a oba pohľady sú vzájomne konzistentné". Dokumenty teda tvrdia konzistenciu, o ktorej vieme, že neplatí. |
| **Action / owner** | **Návrh tímu: baseline v0.3** — preformulovať `REQ-16` na to, čo OP-04 aj implementácia skutočne garantujú: „zrušenie nikdy nevráti úspech nad rezerváciou, ktorá potom blokuje prístroj". Kód ani testy sa nemenia — toto čítanie už tvrdí `test_concurrent_cancel_and_confirm_on_same_reservation`. Mení sa znenie požiadavky, preto v0.3 s analýzou dopadu a schválením, nie tichá oprava. Owner: Tomáš Krišica, do 2026-11-09. |
| **Closure evidence** | **Uzavreté.** Špecifikácia **v0.3**: `REQ-16` zúžená na záruku, ktorú `OP-04` a implementácia dávajú; kontrola konzistencie na konci §9 prepísaná z *otvoreného rozporu* na *vyriešený*; §10 a príklad overenia pri OP-03 zosúladené; §11 nesie schválenie v0.3 oboma členmi; C02 DoD v README už výnimku neuvádza. Správanie sa nezmenilo — `253 passed`; v testoch sa zmenil iba docstring a hlásenie `test_at_most_one_operation_leaves_the_source_state`, ktoré citovali odstránenú vetu `REQ-16`. Riziko `R-03` uzavreté. |

### Kandidát, ktorý review samo zamietlo

`decide_reservation` porovnáva stav cez `is not`, hoci `transitions.py` zdôvodňuje
porovnanie hodnotou. S ORM enumami je správanie správne a funkcia leží mimo
jednotky z C05 — **REJECTED CANDIDATE**, nie je to vada. Súhlasíme.

### Čo review potvrdilo

Reprodukovalo `249 passed`, `32/32` zachytených mutácií a počty testov z C05
(92 unit, 49 component). Dvanásť vlastných mutácií mimo rozsahu nášho skriptu:
jedenásť zachytených, jedna prežila — a to je presne `F-02`.

### Stav po uzavretí F-01 a F-02 (reporting baseline)

| Overenie | `db9c523` (hodnotená) | po opravách (`78b0dc4`) |
| -------- | --------------------- | ----------------------- |
| `pytest`, Postgres 16 z `docker-compose.yml` | `249 passed` | `253 passed` |
| `PGTZ=Europe/Prague pytest` | `1 failed, 248 passed` | `253 passed` |
| server s `timezone = Europe/Prague` (`ALTER DATABASE`) | `1 failed, 248 passed` | `253 passed` |
| `scripts/mutation_check.py` | `32` mutácií, `0` prežilo — `F-02` mimo skriptu prežila | `34` mutácií (`+F01`, `+F02`), `0` prežilo |
| čistý `git clone` + nový venv podľa README, druhý stroj (T. Hrubý) | — | `253 passed` |

Štyri nové testy: dva pre F-01 (`test_persistence_spike.py`), dva prípady
hranice pre F-02 (`test_create_start_boundary`). Počty testov z C05
(92 unit, 49 component) sa nemenia.

---

## Registr rizík — C07/C08

Osem rizík zo štyroch kategórií: R-01 až R-05 z C07/C08 (status report),
R-06 a R-07 pridal C08 (Controlled Change, CR-08), R-08 meranie I-02. `L × I`: 1–2 LOW, 3–4 MEDIUM, 6–9 HIGH.
**Actual residual** je `NOT VERIFIED`, kým nie je účinnosť opatrenia doložená —
plán skóre neznižuje.

### R-01 — prostredie: nefixované pásmo databázy

- **Kategória:** Technical / Quality
- **Popis rizika:** Spojenie nefixuje `TimeZone` → ak projekt pobeží proti
  Postgresu mimo UTC (iný stroj, hodnotiteľ, nasadenie) → sada padne a API vráti
  časy s offsetom namiesto `Z`; tvrdenie „rozbehnú sa proti akémukoľvek
  Postgresu" neplatí.
- **Počiatočné riziko:** L=3, I=2 → **HIGH (6)**. `L=3`, lebo sa to už raz
  stalo — hodnotiteľ na to narazil pri prvom behu mimo nášho `docker-compose`.
- **Stratégia:** MITIGATE — odstrániť závislosť od konfigurácie, nie ju opisovať.
- **Opatrenie / owner / termín:** pásmo session fixované pri každom spojení
  (`SET TIME ZONE 'UTC'` v `create_db_engine()`, `src/swilab/db.py`, spoločné
  pre aplikáciu aj testy) + dva testy. Pôvodne navrhnuté `connect_args` sa
  ukázali ako nedostatočné (F-01). Owner: Tomáš Krišica, vykonal Tomáš Hrubý,
  hotové 2026-11-01 v `8b1083b`.
- **Dôkaz účinnosti:** `PGTZ=Europe/Prague pytest` → `253 passed`; server
  nastavený na `Europe/Prague` (`ALTER DATABASE`) → `253 passed`, kým
  `db9c523` dáva `1 failed, 248 passed`. Mutácia `F01` zachytená. Stav: **overené**.
- **Očakávané reziduálne:** L=1, I=2 → LOW (2).
- **Skutočné reziduálne:** **L=1, I=2 → LOW (2)**, overené behmi vyššie.
  `L` neklesá na nulu: iná databáza než Postgres 16 overená nie je.
- **Prijatie:** Tomáš Krišica.
- **Monitoring / trigger:** spustenie proti inej databáze než z `docker-compose.yml`;
  akýkoľvek čas v odpovedi API, ktorý nekončí na `Z`.

### R-02 — overenie: neotestovaná hranica OP-01

- **Kategória:** Technical / Quality
- **Popis rizika:** Hranica `starts_at == now` nemá test → zmena `<=` na `<`
  prejde nepovšimnutá → aplikácia prijme rezerváciu začínajúcu presne teraz,
  v rozpore s OP-01.
- **Počiatočné riziko:** L=2, I=2 → **MEDIUM (4)**. `L=2`, lebo kód sa okolo
  tohto miesta pri C05 už raz menil; `I=2`, lebo ide o jednu hranicu jednej
  operácie, nie o invariant.
- **Stratégia:** MITIGATE.
- **Opatrenie / owner / termín:** hraničný test + zaradenie mutácie do
  `scripts/mutation_check.py`. Owner: Tomáš Hrubý, hotové 2026-11-01
  v `fe6c298`.
- **Dôkaz účinnosti:** mutácia `F02` (`<=` → `<`) padne na
  `test_create_start_boundary`; na `db9c523` prežila celú sadu.
  Stav: **overené**.
- **Očakávané reziduálne:** L=1, I=1 → LOW (1).
- **Skutočné reziduálne:** **L=1, I=1 → LOW (1)**, overené mutáciou.
- **Prijatie:** Tomáš Hrubý.
- **Monitoring / trigger:** každé ďalšie rozšírenie `mutation_check.py`;
  ktorákoľvek prežitá mutácia.

### R-03 — rozsah: nerozhodnutý rozpor `REQ-16` ↔ OP-04

- **Kategória:** Scope / Requirements
- **Popis rizika:** Schválená baseline v0.2 obsahuje dve čítania `REQ-16` →
  pri obhajobe alebo v CP2 sa prijme to nesprávne → prepracovanie testov, alebo
  neobhájiteľné tvrdenie o konzistencii v README a v §9.
- **Počiatočné riziko:** L=2, I=2 → **MEDIUM (4)**. `L=2`: rozpor prešiel dvoma
  checkpointmi bez dôsledku, ale pri treťom sa naň už pýtali.
- **Stratégia:** MITIGATE — rozhodnúť, nie ďalej odkladať.
- **Opatrenie / owner / termín:** **vykonané 2026-11-15** — špecifikácia v0.3
  zúžila `REQ-16` podľa OP-04 (pozri F-03). Owner: Tomáš Krišica; termín
  z C07 bol 2026-11-09, prekročený o šesť dní.
- **Dôkaz účinnosti:** v0.3 s novým znením `REQ-16`; rozbor dopadu v kontrole
  konzistencie na konci §9; §10, príklad overenia pri OP-03 a C02 DoD v README
  zosúladené; §11 nesie schválenie oboma členmi. `pytest` **`253 passed` bez
  zmeny správania testov** (upravený iba text jedného docstringu a hlásenia,
  ktoré citovali odstránenú vetu) — zmena znenia, nie správania.
  Stav: **overené**.
- **Očakávané reziduálne:** L=1, I=2 → LOW (2).
- **Skutočné reziduálne:** **LOW (2) — overené.** Rozpor v schválenej baseline
  už nie je; zostáva bežné riziko, že budúca zmena `REQ-16`, OP-03 alebo OP-04
  zavedie nový.
- **Prijatie:** rozhodnutie tímu — v0.3 schválili obaja členovia (§11).
- **Monitoring / trigger:** akákoľvek ďalšia zmena `REQ-16`, OP-03 alebo OP-04.
- **Doplnené v C08:** `CR-08` mierilo na tú istú verziu špecifikácie. Tím
  zmeny **rozdelil** (`ADR-02`): v0.3 je oprava `F-03`, `CR-08` ide do v0.4.
  Oprava chyby tým nečakala na novú požiadavku a `R-03` sa dalo uzavrieť hneď.

### R-04 — prevádzka: schéma bez migrácií

- **Kategória:** Release / Operation
- **Popis rizika:** Schéma vzniká cez `create_all()` pri štarte → pri ďalšej
  zmene schémy nad databázou s dátami → ručná prestavba databázy, strata dát,
  ohrozená živá ukážka.
- **Počiatočné riziko:** L=2, I=3 → **HIGH (6)**. `I=3`, lebo zlyhanie pri
  ukážke je nezotaviteľné v reálnom čase; `L=2`, lebo CP2 zmenu schémy
  pravdepodobne prinesie.
- **Stratégia:** ACCEPT pre CP1 s contingency, MITIGATE pred CP2.
- **Opatrenie / owner / termín:** pre CP1 pripravená a vyskúšaná postupnosť
  `docker compose down && docker compose up -d --wait db` pred ukážkou.
  **Rozhodnutie o migráciách padlo 2026-11-15 — `ADR-02`**
  ([architecture-and-decisions.md](architecture-and-decisions.md#adr-02--migrácie-schémy-pred-zmenou-v-c09)):
  migrácie sa nezavádzajú, lebo databáza nemá volume a žiadne dáta nezachováva.
  Namiesto nich aplikácia pri štarte odmietne naštartovať nad nesediacou
  schémou — implementuje sa v C09 ako súčasť delty `CR-08`. Owner: Tomáš Hrubý.
- **Dôkaz účinnosti:** prestavba databázy nás už raz zasiahla pri C02 časti B
  a postup je zapísaný v PR #5. Pre CP1 teda **overené ako contingency**,
  nie ako odstránenie príčiny.
- **Očakávané reziduálne:** L=2, I=2 → MEDIUM (4) pre CP1.
- **Skutočné reziduálne:** MEDIUM (4) pre CP1 — contingency je vyskúšaná.
  *(C09)* Kontrola schémy pri štarte je implementovaná a otestovaná
  (`tests/test_schema_check.py`, mutácie D8, D9); rollback aj návrat vpred
  `CR-08` raz vyskúšané nad databázou s dátami (evidence C09). Zlyhanie pri
  zmene schémy je teda skoré a zrozumiteľné, nie `500` pri každej
  požiadavke. Strata dát pri prestavbe ostáva (bez volume, `ADR-02`).
  Pre CP2 **MEDIUM (4) — overené**; potvrdil owner T. Hrubý v review PR #15
  (2026-11-18). Overené testami a skúškou rollbacku, nie čistým checkoutom —
  v ňom pytest schému najprv prestaví, takže kontrolu pri štarte nespustí.
- **Prijatie:** Tomáš Hrubý pre CP1.
- **Monitoring / trigger:** akákoľvek zmena v `models.py`. Po `ADR-02`
  pribúda spúšťač na prehodnotenie celého rozhodnutia: `volumes:` pre databázu
  v `docker-compose.yml`, nasadenie, odkiaľ sa dáta nedajú zahodiť, alebo druhá
  inštancia aplikácie.
- **Doplnené v C08:** trigger sa spustil — `CR-08` pridáva do `reservations`
  stĺpec s lehotou. Rozhodnutie bolo **podmienkou ACCEPT** a padlo pred kódom
  v C09 (`ADR-02`, 2026-11-15). Akcia `P4` z C07 je tým uzavretá; termín
  2026-11-09 z C07 bol prekročený o šesť dní.

### R-05 — harmonogram: pre CP2 nie je v repozitári zaznamenaný plán

- **Kategória:** Schedule / Integration
- **Popis rizika:** V repozitári nie je žiadny plán prác ani termíny pre CP2 →
  práca sa naakumuluje tesne pred checkpointom → nezostane čas na integráciu
  a opravy, tak ako to opisuje zadanie C07/C08.
- **Počiatočné riziko:** L=2, I=2 → **MEDIUM (4)**.
- **Stratégia:** MITIGATE — pomenovať planning gap a odstrániť ho.
- **Opatrenie / owner / termín:** zapísať work packages pre CP2 s vlastníkmi
  a termínmi. Owner: Tomáš Hrubý, do 2026-11-09.
- **Dôkaz účinnosti:** existujúci plán v repozitári. Stav: **neoverené**.
- **Očakávané reziduálne:** L=1, I=2 → LOW (2).
- **Skutočné reziduálne:** **NOT VERIFIED.**
- **Prijatie:** rozhodnutie tímu.
- **Monitoring / trigger:** zadanie CP2; akákoľvek zmena rozsahu.

### R-06 — technické: schválenie čakajúce na zámok prejde po lehote *(C08, CR-08)*

- **Kategória:** Technical / Quality
- **Popis rizika:** `now` sa odčíta pri prijatí požiadavky (BR-03) → operácia
  prijatá tesne pred lehotou, ktorá čaká na zámok, sa vykoná až po nej →
  schválenie, zamietnutie alebo zrušenie prejde o dĺžku čakania „po termíne"
  a potvrdenie či dopyt na dostupnosť ešte započíta vypršanú žiadosť ako
  blokujúcu (falošné `OVERLAP` / `UNAVAILABLE`). BR-02 tým ohrozené nie je
  (rozbor súbehu v CR-08, `intent-and-change.md`).
- **Počiatočné riziko:** L=1, I=1 → **LOW (1)**. `L=1`: treba súbeh v tej istej
  sekunde ako hranica; `I=1`: posun o čas čakania na zámok, invariant drží.
- **Stratégia:** ACCEPT — rovnaká sémantika dnes platí pri `starts_at` — a overiť.
- **Opatrenie / owner / termín:** test na úrovni služby s explicitným `now` —
  potvrdenie po lehote, potom schválenie so starším `now` → `OVERLAP` — v C09.
  Dnes tento súbeh nepokrýva žiadny test (C08, *Súbeh*). Owner: tím (kto
  implementuje C09).
- **Dôkaz účinnosti:** *(C09)* `test_approval_with_an_older_now_meets_a_newer_request`
  — potvrdenie `B` s `now` v termíne rozhodnutia žiadosti `A`, potom schválenie
  `A` so starším `now` o sekundu skôr → `OVERLAP`, `A` ostáva
  `PENDING_APPROVAL`, BR-02 drží. Mutácia D3 (SQL forma živosti podľa
  `starts_at`) ho zhodí. Stav: **overené**.
- **Očakávané reziduálne:** L=1, I=1 → LOW (1).
- **Skutočné reziduálne:** **LOW (1) — overené** testom vyššie. Sémantický
  posun (operácia čakajúca na zámok sa posúdi podľa času príchodu) ostáva
  vlastnosťou systému, ako ho prijalo `CA-3`; BR-02 ním ohrozené nie je.
- **Prijatie:** návrh T. Hrubý, potvrdzuje peer review CR-08 (CA-3).
- **Monitoring / trigger:** druhá inštancia aplikácie (každá s vlastnými
  hodinami); meranie čakania na zámok prístroja (I-02).

### R-07 — prevádzka: prepadnuté žiadosti, kým vedúci nemá zoznam *(C08, CR-08)*

- **Kategória:** Release / Operation
- **Popis rizika:** po CR-08 žiadosť prepadne 48 h po podaní → vedúci, ktorý sa
  o žiadosti dozvie neskoro alebo vôbec (I-01: TBD-03, TBD-09), nestihne
  rozhodnúť → žiadateľ prichádza o žiadosti, ktoré by inak dostal.
- **Počiatočné riziko:** L=2, I=2 → **MEDIUM (4)**. Dnes taká žiadosť tiež
  nikdy nedostane rozhodnutie, ale drží prístroj do `starts_at`; CR-08 mení,
  **kedy** prepadne, nie či. Pre ostatných používateľov je to zlepšenie, pre
  žiadateľa kratšia lehota.
- **Stratégia:** ACCEPT pre C09 so súhlasom stakeholdera (CA-1, CA-7); MITIGATE
  v CP2 cez TBD-09 alebo TBD-03 (issue I-01).
- **Opatrenie / owner / termín:** lehota viditeľná v odpovedi potvrdenia
  (AC-06). Owner: tím.
- **Dôkaz účinnosti:** žiadny — **PLANNED**.
- **Očakávané reziduálne:** L=2, I=1 → LOW (2) po AC-06.
- **Skutočné reziduálne:** **NOT VERIFIED.**
- **Prijatie:** rozhodnutie tímu v peer review CR-08.
- **Monitoring / trigger:** podiel žiadostí, ktoré skončia `EXPIRED` bez
  rozhodnutia; zadanie rozsahu CP2.

### R-08 — technické: dotaz na prekryv číta celú tabuľku *(I-02)*

- **Kategória:** Technical / Quality
- **Popis rizika:** tabuľka `reservations` nemá index na `instrument_id` →
  dotaz na prekryv pod zámkom prístroja číta rezervácie všetkých prístrojov
  vrátane histórie → čas pod zámkom rastie s objemom celej tabuľky → pri
  asi 1 000 000 rezervácií a 50 súbežných potvrdeniach na jeden prístroj
  prekročí p95 hranicu 1 s (meranie I-02, E-2).
- **Počiatočné riziko:** L=1, I=2 → **LOW (2)**. Pri modeli 10× (100 000
  riadkov) je rezerva desaťnásobná (na druhom stroji asi 8,5-násobná, review
  I-02); aj tempom modelu 10× by tabuľka 1 000 000 riadkov dosiahla asi za
  desať rokov.
- **Stratégia:** ACCEPT so spúšťačom — ošetrenie je pripravené a zmerané.
- **Opatrenie / owner / termín:** pri spúšťači Change Request: index na
  `reservations(instrument_id, ends_at)`. Index iba na `instrument_id`
  nestačí: rezervácia ostáva `CONFIRMED` aj po skončení, takže história
  prístroja rastie s tabuľkou a takýto index ju nevynechá (review I-02,
  RV-I02-1). CR musí vyriešiť aj ADR-02 — `create_all()`
  index do existujúcej tabuľky nepridá a `verify_schema()` indexy
  neporovnáva, takže index iba v modeli by na existujúcej databáze ticho
  chýbal. Owner: Tomáš Krišica.
- **Dôkaz účinnosti ošetrenia:** pokus A z review I-02 — horúci prístroj
  s vlastnou históriou (1/20 objemu), 1 000 000 riadkov: s indexom
  `(instrument_id, ends_at)` p95 159 ms, s indexom iba na `instrument_id`
  793 ms, bez indexu 1 153 ms. E-3 (prístroj bez histórie, index na
  `instrument_id`) 168 ms namiesto 1 004 ms. Indexy iba v izolovanej databáze
  merania.
- **Očakávané reziduálne:** LOW (2) — akceptované, bez zmeny.
- **Skutočné reziduálne:** **LOW (2)** — doložené meraním I-02 (pri 100 000
  riadkoch p95 276 ms).
- **Prijatie:** návrh T. Krišica, potvrdil T. Hrubý v review I-02
  (2026-11-22) s opraveným indexom.
- **Monitoring / trigger:** viac ako 300 000 riadkov v `reservations`;
  zavedenie migrácií (vtedy je index lacný, urobiť hneď); opakované meranie
  I-02 s p95 nad 0,5 s pri modeli 10×.

### Otvorené issues (nie riziká — už nastali)

| ID | Issue | Dopad | Owner | Ďalší krok |
| -- | ----- | ----- | ----- | ---------- |
| **I-01** | Schvaľovací tok nie je použiteľný end-to-end: Notification Service nie je napojená (`TBD-03`) a systém nemá žiadnu čítaciu operáciu (`TBD-09`). | Vedúci sa o žiadosti nedozvie a žiadateľ sa nedozvie výsledok. Tok funguje len cez testy a `scripts/demo.py`. | obaja | Rozhodnúť rozsah pre CP2; dovtedy priznávať ako obmedzenie, nie zakrývať. |
| **I-02** | Serializácia na prístroj (ADR-01) nie je zmeraná. | Future pressure z C01 hovorí o 10× viac súbežných rezervácií; nemáme žiadne číslo. | Tomáš Krišica | **Uzavreté 2026-11-22**, 13 dní po termíne 2026-11-09 — meranie, časť *I-02* nižšie: kritériá K-1 až K-3 PASS, ADR-01 sa neotvára, doplnené čísla pod ADR-01, nové riziko R-08. |
| **I-03** | `F-01` a `F-02` boli doložené nezhody, nie budúce riziká. | Viď vyššie. | Tomáš Hrubý | **Uzavreté** v `8b1083b` a `fe6c298` (closure evidence pri F-01, F-02). |
| **I-04** *(C10)* | `/health` nekontroluje databázu: počas výpadku hlásil `200 ok`, kým potvrdenie skončilo `500` (zistenie O-1 v C10). Iné operácie sme počas výpadku neskúšali, všetky však databázu potrebujú. | Monitoring nad `/health` výpadok databázy nevidí. | obaja | Rozhodnúť kontrakt: `/health` ostane „proces beží" a pribudne kontrola pripravenosti s dotazom do databázy — zmena rozhrania, teda nie v C10. Spúšťač: skôr, než sa nad aplikáciou postaví monitoring, najneskôr pri pláne CP2 (R-05). |

---

## C08 — Controlled Change: baseline a overenie dnešného správania

Zmena `CR-08` (lehota na rozhodnutie o žiadosti) je opísaná v
[intent-and-change.md, časť C08](intent-and-change.md#c08--controlled-change-cr-08).
Tu je to, o čo sa opiera: presná baseline, test cases dnešného správania a ich
skutočné vykonanie. **V C08 sa kód aplikácie nemení** — vetva
`feature/c08-controlled-change` mení iba `docs/`, `README.md` a `scripts/`.

Odkazy na riadky vedú na commit `b0974e7`, nie na `main`: po implementácii
v C09 by sa posunuli a dôkaz by prestal ukazovať na to, čo platilo pred zmenou.

### Krok 0 — východisková baseline (before)

| Položka | Hodnota |
| ------- | ------- |
| **Commit** | `b0974e7` — merge PR #9 do `main` (2026-11-08), najnovší stav `main`. Vlastný tag nemá; identifikuje ho hash. Najbližší tag je `c05-detailed-design` (`c7df7a0`). |
| **Špecifikácia** | baseline v0.2, schválená 2026-10-04 ([§11](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/specification.md?plain=1#L1145-L1173)). Pravidlo, ktoré CR-08 mení: **BR-08** ([L251–293](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/specification.md?plain=1#L251-L293)). Pravidlá, ktoré na ňom stoja: definícia alokácie ([L72–100](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/specification.md?plain=1#L72-L100)), BR-02 ([L126–144](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/specification.md?plain=1#L126-L144)), REQ-14 ([L656–662](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/specification.md?plain=1#L656-L662)), REQ-11, REQ-12, REQ-15 ([L773–788](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/specification.md?plain=1#L773-L788)). |
| **Stavový pohľad** | statechart v0.2, prechod `PENDING_APPROVAL → EXPIRED` „nastal `starts_at`" ([diagrams.md L76–141](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/diagrams.md?plain=1#L76-L141)). |
| **Architektúra** | ADR-01 — jediný vlastník BR-02 ([L538–600](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/architecture-and-decisions.md?plain=1#L538-L600)); vlastníctvo prechodu `PENDING_APPROVAL → EXPIRED` v G3 ([L745](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/architecture-and-decisions.md?plain=1#L745)); tabuľka `expiry_decision` v C05.8 ([L1451–1461](https://github.com/thspowered/SWI-LAB/blob/b0974e7/docs/architecture-and-decisions.md?plain=1#L1451-L1461)). |
| **Runtime** | Python 3.12.11 · FastAPI 0.141.1 · SQLAlchemy 2.0.52 · psycopg 3.3.5 · pydantic 2.13.5 · pydantic-settings 2.15.0 · uvicorn 0.53.0 · pytest 9.1.1 · httpx 0.28.1 (`pip list` vo `.venv`). |
| **Databáza** | PostgreSQL 16.15 z `docker-compose.yml` (obraz `postgres:16`, port 5433). Pásmo session UTC (`create_db_engine()`, F-01), izolácia `READ COMMITTED` (ADR-01). |
| **Verzia schémy** | Schéma nemá verziu ani migrácie (R-04): vzniká cez `create_all()` z `src/swilab/models.py` pri `b0974e7`. Aby sa dala porovnať na inom stroji, zapisujeme ju vypísanú z databázy — tabuľka nižšie. |
| **Testovacia konfigurácia** | `pytest` berie `DATABASE_URL` z prostredia, inak predvolenú hodnotu v `tests/conftest.py` (L10–12), zhodnú s `.env.example` — lokálny kontajner, heslo tu neopakujeme. `pyproject.toml`: `testpaths = ["tests"]`, `pythonpath = ["src"]`. Sada je izolovaná: na začiatku `drop_all` + `create_all`, po každom teste `TRUNCATE` (`tests/conftest.py`). |
| **Príprava dát** | Správa prístrojov, používateľov a certifikátov nie je v rozsahu v0.2 (TBD-02) — dáta sa zakladajú priamo v databáze. Testy cez fixtures v `conftest.py`, test cases C08 cez `INSERT` s pevnými identifikátormi (časť TC nižšie). |
| **Príkazy a výsledok** | `.venv/bin/python -m pytest -q` → **`253 passed`** · `.venv/bin/python scripts/mutation_check.py` → **34 mutácií, 0 prežilo** · oboje 2026-11-15 na `b0974e7`, rovnaké čísla ako na reporting baseline C07 (`78b0dc4`). |
| **Tajomstvá** | Záznam neobsahuje žiadne heslo. Jediné heslo v repozitári je heslo lokálneho vývojového kontajnera; stojí v `docker-compose.yml`, `.env.example`, `src/swilab/config.py` (L7), `tests/conftest.py` (L11) a `scripts/demo.py` (L26) — všetko existovalo pred C08. `scripts/c08_baseline_tc.py` ho nemá: berie `DATABASE_URL` z prostredia alebo z `.env.example` a vo výpise ho maskuje. |

**Schéma pri `b0974e7`**, vypísaná z `information_schema` a `pg_enum`:

| Tabuľka | Stĺpce (všetky `NOT NULL`) | Obmedzenia |
| ------- | -------------------------- | ---------- |
| `instruments` | `id uuid`, `name varchar`, `category instrument_category`, `location varchar`, `is_active boolean`, `requires_approval boolean` | PK |
| `users` | `id uuid`, `full_name varchar`, `email varchar`, `role user_role` | PK, `UNIQUE (email)` |
| `certifications` | `id uuid`, `user_id uuid`, `category instrument_category`, `valid_until timestamptz` | PK, FK `users` |
| `reservations` | `id uuid`, `instrument_id uuid`, `user_id uuid`, `starts_at timestamptz`, `ends_at timestamptz`, `state reservation_state`, `created_at timestamptz` | PK, FK `instruments`, FK `users` |

Enum `reservation_state`: `DRAFT, PENDING_APPROVAL, CONFIRMED, CANCELLED,
REJECTED, EXPIRED`. Žiadny stĺpec nemá predvolenú hodnotu v databáze (predvoľby
sú iba v Pythone) a schéma nemá žiadny `CHECK` ani obmedzenie pre BR-02 — to je
prijatý dôsledok č. 1 ADR-01.

**Pozorovanie, ktoré CR-08 priamo zasahuje:** v `reservations` **nie je stĺpec,
z ktorého by sa dalo zistiť, kedy bola žiadosť podaná.** `created_at` je okamih
vzniku `DRAFT` (`create_reservation`, [services L319–326](https://github.com/thspowered/SWI-LAB/blob/b0974e7/src/swilab/services/reservations.py#L319-L326));
prechod `DRAFT → PENDING_APPROVAL` zapíše iba nový stav
([L251](https://github.com/thspowered/SWI-LAB/blob/b0974e7/src/swilab/services/reservations.py#L251),
commit na [L435](https://github.com/thspowered/SWI-LAB/blob/b0974e7/src/swilab/services/reservations.py#L435)).

**Ako iný člen tímu obnoví rovnaký stav:** `git checkout b0974e7` (alebo táto
vetva — `git diff b0974e7 -- src tests pyproject.toml docker-compose.yml` je
prázdny), nový venv podľa README, `docker compose up -d --wait db`,
`pytest -q` → `253 passed`.

**Obmedzenia reprodukovateľnosti** — priznané, nie odstránené:

- `pyproject.toml` určuje iba spodné hranice verzií a projekt nemá lock súbor.
  Iný stroj môže dostať novšie knižnice; verzie v tabuľke sú tie, proti ktorým
  baseline skutočne bežala.
- `docker-compose.yml` používa obraz `postgres:16`, nie presnú verziu — iný
  stroj môže dostať inú 16.x.
- Projekt nemá CI. „Zelená baseline" znamená beh na dvoch strojoch (tabuľka
  nižšie), nie automatickú kontrolu pri každom commite.

**Obnovenie baseline druhým členom tímu — vykonané.** Zadanie to žiada ako
doklad fázy 0 („jiný člen obnoví stejný výchozí stav"). Postup podľa
[README, časť C08](../README.md#c08--controlled-change) zopakoval
**T. Krišica 2026-11-15 na vlastnom stroji**, nezávisle od behu T. Hrubého:

| Položka | T. Hrubý (pôvodný beh) | T. Krišica (obnovenie) |
| ------- | ---------------------- | ---------------------- |
| Dátum | 2026-11-15 | 2026-11-15, 21:01 UTC |
| Python | 3.12.11 | 3.12.13 |
| FastAPI | 0.141.1 | 0.142.2 |
| SQLAlchemy | 2.0.52 | **2.1.3** |
| psycopg | 3.3.5 | 3.3.6 |
| uvicorn | 0.53.0 | 0.54.0 |
| PostgreSQL (`postgres:16`) | 16.15 | **16.13** |
| `pytest -q` | `253 passed` | **`253 passed`** |
| `mutation_check.py` | 34 mutácií, 0 prežilo | **34 mutácií, 0 prežilo** |
| `c08_baseline_tc.py` | 3× `PASS`, kód `0` | **3× `PASS`, kód `0`** |
| Odtlačok schémy | `1f96b2bbf6bf8c47` | **`1f96b2bbf6bf8c47`** |

Obnovenie **posilňuje** doklad fázy 0 nad rámec toho, čo postup sľuboval:
knižnice sa nezhodujú (SQLAlchemy je o minor verziu vyššia, Postgres je naopak
o dve patch verzie staršie) a baseline napriek tomu dala tie isté čísla
**a ten istý odtlačok schémy**. Obmedzenie „bez lock súboru" teda existuje
ďalej, ale pre túto baseline sa neprejavilo.

Krišica zopakoval aj dve kontroly, že oracle nie je prázdny:

| Kontrolný beh | Výsledok |
| ------------- | -------- |
| vložená chyba — `if conflicts:` → `if False:` na riadku 245 (`enter_blocking_state`), kópia `src/` mimo repozitára, port 8001 | `TC-C08-01 PASS`, `TC-C08-02 FAIL`, `TC-C08-03 FAIL`, návratový kód `1`; nezávislý SQL dotaz našiel blokujúcu dvojicu `[(B1, B2)]` |
| aplikácia nebeží (port 8009) | `BLOCKED - infrastruktura nebezi: [Errno 61] Connection refused`, návratový kód `2` |

Poznámka k postupu, ktorú pôvodný záznam neuvádza: reťazec `    if conflicts:`
je v `services/reservations.py` **dvakrát** — na riadku 245
(`enter_blocking_state`, rozhodnutie o BR-02) a na riadku 365
(`check_availability`, iba hlásenie dostupnosti). Chybu treba vložiť na riadok
245; náhrada oboch alebo nesprávneho z nich kontrolný beh znehodnotí.

### Krok 2 — test cases dnešného správania (návrh pred behom)

Tri test cases. Prvé dva overujú to, čo CR-08 **nesmie rozbiť** (BR-02 pri
schválení a pri potvrdení), tretí zachytáva **obmedzenie, ktoré CR-08 mení** —
je to doklad, že požiadavka rieši skutočný problém, nie vymyslený.

*Expected* je odvodené zo špecifikácie v0.2 a zapísané **pred prvým behom**
(tento commit). *Actual* sa dopĺňa až po vykonaní, v ďalšom commite. Rovnaké
Expected je prepísané v `scripts/c08_baseline_tc.py`, ktorý test cases vykoná.

**Spoločné pre všetky tri:**

- **Baseline:** `b0974e7`, schéma z `models.py` (krok 0), runtime podľa kroku 0.
- **Rozhranie:** skutočné HTTP API bežiacej aplikácie (`uvicorn`, port 8000)
  nad PostgreSQL 16 z `docker-compose.yml`. Žiadny mock, žiadna testovacia
  session.
- **Oracle:** stav sa po operácii číta **novým spojením priamo z databázy**
  (SQL cez `psycopg`), nie z odpovede API ani cez ORM aplikácie. Invariant BR-02
  overuje vlastný SQL dotaz skriptu — dvojice blokujúcich rezervácií toho istého
  prístroja, ktoré sa prekrývajú podľa BR-01 (`a.start < b.end ∧ b.start < a.end`),
  pričom blokuje `CONFIRMED` a žiadosť so `starts_at` v budúcnosti. Dotaz je
  napísaný nezávisle od `_overlapping_blocking()` v aplikácii.
- **Dáta:** prístroj `R1` = *Zeiss LSM 900*, `MICROSCOPE`, aktívny,
  `requires_approval = true` (exkluzívny, BR-02). Používatelia `S1`, `S2`
  (`STUDENT`) s certifikátom `MICROSCOPE` platným do D + 30 dní, `V1`
  (`SUPERVISOR`, nie je vlastníkom žiadnej rezervácie). Pevné UUID
  `00000000-0000-0000-0000-0000000000xx`, aby sa výpis dal čítať.
- **Čas:** všetky intervaly ležia v dni **D = dátum behu + 7 dní**, v UTC.
  API číta skutočné hodiny servera a zvonku sa posunúť nedajú, takže
  termín musí byť v budúcnosti. Intervaly sú polootvorené `[start, end)`.
- **Príprava:** `TRUNCATE` všetkých tabuliek a `INSERT` presných riadkov.
  Prístroje, používatelia a certifikáty nemajú v v0.2 API (TBD-02); rezervácie
  sa vkladajú priamo v požadovanom stave rovnako ako vo fixtures testov.
- **Opakovanie:** každý test case sa vykoná dvakrát; pred druhým behom sa dáta
  znova resetujú. Verdikt platí, len ak oba behy dajú **rovnaké** pozorovania.

#### C08 / TC-C08-01 — schválenie žiadosti susediacej s potvrdenou rezerváciou (pozitívny)

| Pole | Obsah |
| ---- | ----- |
| **Test basis** | REQ-11 (schválenie), BR-01 (susedné intervaly sa neprekrývajú), BR-02 — špecifikácia v0.2 @ `b0974e7`. Riziko: N/A. Pre CR-08 je to regresný základ AC-01 a AC-07. |
| **Preconditions + roly** | Izolovaná databáza. `B1` = `CONFIRMED`, `R1`, `[D 10:00, D 11:00)`, vlastník `S2`. `B2` = `PENDING_APPROVAL`, `R1`, `[D 11:00, D 12:00)`, vlastník `S1` s platným certifikátom. Schvaľuje `V1` — `SUPERVISOR`, nie vlastník `B2` (BR-07). |
| **Test data** | Intervaly v UTC, polootvorené. Prístroj vyžaduje schválenie, takže `B2` sa môže stať alokáciou iba cez OP-05. |

| # | Akcia / vstup | Expected PRED behom | Actual PO behu | Dôkaz |
| - | ------------- | ------------------- | -------------- | ----- |
| 1 | reset + vložiť `R1`, `B1`, `B2` | presne jedna `CONFIRMED` (`B1`); `B2` čaká (`PENDING_APPROVAL`) | `{B1: CONFIRMED, B2: PENDING_APPROVAL}` | výpis r. 11–13 |
| 2 | nezávisle prečítať uložené intervaly a stavy (SQL) | `B1 [10:00, 11:00)`, `B2 [11:00, 12:00)`; žiadna blokujúca dvojica v prekryve | `B1 [10:00, 11:00)`, `B2 [11:00, 12:00)`; blokujúce prekryvy `[]` | r. 14–16 |
| 3 | `POST /reservations/{B2}/approve`, `requested_by = V1` | `200`, `state = CONFIRMED` — podmienky REQ-11 sú splnené, susedný interval nekoliduje | `200`, `state = CONFIRMED` | r. 17–21 |
| 4 | znova načítať commitnutý stav novým spojením | `B1` aj `B2` `CONFIRMED`; žiadna blokujúca dvojica v prekryve | `B1 CONFIRMED`, `B2 CONFIRMED`; blokujúce prekryvy `[]` | r. 22–24 |
| 5 | reset a celé zopakovať | ten istý výsledok — test sa dá opakovať bez zmeny vstupov | beh 2 po resete: kroky 1–4 doslova rovnaké | r. 27–43 |

**Final postcondition / oracle:** v databáze sú dve `CONFIRMED` rezervácie `R1`
a dotaz na blokujúce prekryvy vráti prázdny výsledok.
**Čo nie je pokryté:** súbeh — pozri *Súbeh* nižšie; schválenie proti súbežnému
potvrdeniu toho istého prístroja netestuje dnes nič.
**Rozhodnutie:** regresný základ pre C09 (AC-01, AC-07) — po implementácii musí
dať rovnaký výsledok.

#### C08 / TC-C08-02 — potvrdenie rezervácie prekrývajúcej sa s potvrdenou (negatívny)

| Pole | Obsah |
| ---- | ----- |
| **Test basis** | REQ-04 (potvrdenie len bez prekryvu s blokujúcim stavom), BR-02, BR-01 — v0.2 @ `b0974e7`. Kontrakt zamietnutia: kód `OVERLAP`, HTTP `409` (`main.py`, `STATUS_BY_CODE`). Riziko: N/A. Pre CR-08 regresný základ AC-07. |
| **Preconditions + roly** | `B1` = `CONFIRMED`, `R1`, `[D 10:00, D 11:00)`, vlastník `S2`. `B2` = **`DRAFT`**, `R1`, `[D 10:30, D 11:30)`, vlastník `S1` s platným certifikátom. Potvrdzuje vlastník `S1` (BR-06). |
| **Prečo `B2` je `DRAFT`, nie `PENDING_APPROVAL`** | Živá žiadosť prekrývajúca sa s `CONFIRMED` je v v0.2 stav, ktorý BR-02 zakazuje — legálne do nej nevedie žiadna operácia. Jediná skutočná cesta, ako sa `B2` dostane k rozhodnutiu o BR-02, je potvrdenie z `DRAFT`. Keby sme `B2` vložili ako žiadosť, testovali by sme poškodené dáta, nie kontrakt. |
| **Test data** | Prekryv `[10:30, 11:00)` — čiastočný, nie dotyk. Prístroj vyžaduje schválenie: bez prekryvu by potvrdenie viedlo do `PENDING_APPROVAL`, takže test pokrýva aj cestu k žiadosti. |

| # | Akcia / vstup | Expected PRED behom | Actual PO behu | Dôkaz |
| - | ------------- | ------------------- | -------------- | ----- |
| 1 | reset + vložiť `R1`, `B1`, `B2` | iba `B1` je `CONFIRMED`; `B2` `DRAFT` | `{B1: CONFIRMED, B2: DRAFT}` | výpis r. 46–48 |
| 2 | nezávisle prečítať východiskový stav (SQL) | prekryv v čase `B1`–`B2` existuje, blokujúca dvojica nie (druhé potvrdenie neexistuje) | prekryv v čase `[(B1, B2)]`; blokujúce prekryvy `[]` | r. 49–51 |
| 3 | `POST /reservations/{B2}/confirm`, `requested_by = S1` | zamietnuté: `409`, `code = OVERLAP` | `409 {"code": "OVERLAP"}` | r. 52–56 |
| 4 | prečítať uložený stav po rollbacku novým spojením | `B1` bez zmeny (celý riadok), `B2` zostáva `DRAFT`; žiadna blokujúca dvojica v prekryve | `B1 CONFIRMED`, riadok bez zmeny `True`; `B2 DRAFT`; blokujúce prekryvy `[]` | r. 57–59 |
| 5 | reset a celé zopakovať | ten istý výsledok | beh 2 po resete: kroky 1–4 doslova rovnaké | r. 62–78 |

**Final postcondition / oracle:** riadok `B1` je bajtovo rovnaký ako pred
operáciou, `B2` je `DRAFT`, dotaz na blokujúce prekryvy je prázdny.
**Čo nie je pokryté:** dve súbežné potvrdenia — `test_concurrent_conflicting_confirmations`.
**Rozhodnutie:** regresný základ pre C09 (AC-07) — po implementácii musí dať
rovnaký výsledok.

#### C08 / TC-C08-03 — zabudnutá žiadosť blokuje prístroj až do začiatku (charakterizácia)

| Pole | Obsah |
| ---- | ----- |
| **Test basis** | BR-08 (žiadosť je živá, kým `currentTime < starts_at`), REQ-03 a REQ-13 (živá žiadosť blokuje, dôvod `PENDING_APPROVAL`), REQ-04 (potvrdenie prekrývajúcej sa rezervácie je zamietnuté), REQ-11 (schválenie živej žiadosti) — v0.2 @ `b0974e7`. Je to **old accepted behavior**, ktoré CR-08 mení. Riziko: N/A (obmedzenie, nie riziko — je predmetom CR-08). |
| **Preconditions + roly** | `B1` = `PENDING_APPROVAL`, `R1`, `[D 10:00, D 11:00)`, vlastník `S1`; jej `DRAFT` vznikol pred 10 dňami (`created_at = beh − 10 dní`). `B2` = `DRAFT`, `R1`, `[D 10:00, D 11:00)`, vlastník `S2`. Schvaľuje `V1`. |
| **Test data** | Kedy bola `B1` podaná ako žiadosť, sa nastaviť nedá — systém ten okamih neukladá. `created_at` je najbližší údaj, aký baseline má. |

| # | Akcia / vstup | Expected PRED behom | Actual PO behu | Dôkaz |
| - | ------------- | ------------------- | -------------- | ----- |
| 1 | reset + vložiť `R1`, `B1`, `B2` | `B1` `PENDING_APPROVAL`, `B2` `DRAFT` | `{B1: PENDING_APPROVAL, B2: DRAFT}` | výpis r. 81–83 |
| 2 | prečítať stĺpce `reservations` (`information_schema`) | iba `id, instrument_id, user_id, starts_at, ends_at, state, created_at` — okamih podania sa neukladá | `[id, instrument_id, user_id, starts_at, ends_at, state, created_at]` | r. 84–86 |
| 3 | `GET /instruments/{R1}/availability?starts_at=D 10:00&ends_at=D 11:00` | `200`, `UNAVAILABLE`, `reason = PENDING_APPROVAL`, kolízia `[B1]` | `200`, `UNAVAILABLE`, `PENDING_APPROVAL`, `[B1]` | r. 87–91 |
| 4 | `POST /reservations/{B2}/confirm`, `requested_by = S2` | zamietnuté: `409 OVERLAP` — živá žiadosť blokuje | `409 OVERLAP` | r. 92–96 |
| 5 | `POST /reservations/{B1}/approve`, `requested_by = V1` | `200`, `CONFIRMED` — lehota na rozhodnutie neexistuje | `200`, `CONFIRMED` | r. 97–101 |
| 6 | prečítať uložený stav novým spojením | `B1` `CONFIRMED`, `B2` `DRAFT` | `B1 CONFIRMED`, `B2 DRAFT` | r. 102–104 |
| 7 | reset a celé zopakovať | ten istý výsledok | beh 2 po resete: kroky 1–6 doslova rovnaké | r. 107–133 |

**Final postcondition / oracle:** `B1` `CONFIRMED`, `B2` `DRAFT`.
**Čo test znamená:** `PASS` tu **nie je** dobrá správa o systéme — potvrdzuje,
že obmedzenie z CR-08 skutočne existuje.
**Rozhodnutie:** po implementácii CR-08 sa test stane oracle novej požiadavky.
Nové Expected musí vzniknúť pred behom a zmení sa takmer celé: v kroku 1 sa
`B1` vloží **s lehotou v minulosti** (podaná pred viac než 48 h) — bez toho by
ju AC-09 považovalo za žiadosť spred zmeny a ostala by živá; krok 2 nájde
stĺpec s lehotou; krok 3 → `AVAILABLE`; krok 4 → `B2` prejde do
`PENDING_APPROVAL`; krok 5 → `409 EXPIRED`; krok 6 → `B1 EXPIRED`,
`B2 PENDING_APPROVAL`. Nezávislý SQL oracle BR-02 v skripte treba prepísať na
pravidlo v0.4.
**Čo nie je pokryté:** hranica 48:00:00 — na baseline neexistuje, overí ju
až C09 (AC-04).

### Krok 2 — vykonanie a výsledky (Actual)

| Položka | Hodnota |
| ------- | ------- |
| **Kedy a nad čím** | 2026-11-15 11:27:42 UTC, `HEAD` = `29c3170`, kód aplikácie = `b0974e7` (skript to vypíše podľa `git diff` nad `src`, `pyproject.toml`, `docker-compose.yml`; beh tým nezastaví — v C09 sa kód zmení zámerne) |
| **Prostredie** | `uvicorn swilab.main:app --port 8000` (PYTHONPATH=src), PostgreSQL 16.15 z `docker-compose.yml`, odtlačok schémy `1f96b2bbf6bf8c47` |
| **Príkaz** | `.venv/bin/python scripts/c08_baseline_tc.py` → návratový kód `0` |
| **Deň testu D** | 2026-11-22 (UTC) |

| Test case | Verdikt | Pozorovaný základ |
| --------- | ------- | ----------------- |
| `TC-C08-01` | **PASS** | Schválenie susednej žiadosti vrátilo `200 CONFIRMED`; databáza po commite: dve `CONFIRMED`, žiadna blokujúca dvojica v prekryve. Dva behy po resete doslova rovnaké. |
| `TC-C08-02` | **PASS** | Potvrdenie prekrývajúcej sa rezervácie vrátilo `409 OVERLAP`; riadok `B1` sa nezmenil, `B2` zostal `DRAFT`, blokujúca dvojica nevznikla. Dva behy rovnaké. |
| `TC-C08-03` | **PASS** | Žiadosť, ktorej `DRAFT` vznikol pred 10 dňami, blokuje (`UNAVAILABLE`, `PENDING_APPROVAL`, `409 OVERLAP` pre iného) a vedúci ju schváli (`200 CONFIRMED`). Okamih podania v tabuľke nie je. **Obmedzenie, ktoré CR-08 rieši, je doložené.** |

*If FAIL:* žiadny. *If BLOCKED:* žiadny.

**Poradie návrhu a behu.** Expected je v `bbfbcfe` (10:55:15 UTC). Skript
bežal na čistom strome trikrát, vždy s rovnakými verdiktmi a pozorovaniami;
menil sa iba **výpis**, nikdy Expected ani kroky:

| Beh | Commit | Čas (UTC) | Čo sa zmenilo pred behom |
| --- | ------ | --------- | ------------------------ |
| 1 | `bbfbcfe` | 10:55:25 | — prvý beh, 10 s po commite s Expected |
| 2 | `f36e126` | 10:55:54 | výpis `GET` doplnený o query parametre, hlavička priznáva necommitnuté zmeny |
| 3 | `29c3170` | 11:27:42 | po nezávislej kontrole: výpis URL presne tak, ako odišla (kódovaná — skopírovaná do `curl` vráti `200`, nekódovaná dávala `422`), filter `table_schema` v kroku 2 `TC-C08-03`, `BLOCKED` aj pri výpadku počas behu |

Záznam nižšie je beh 3. Od behu 2 sa líši iba dvoma riadkami `HTTP GET`
(r. 87 a 114) — čísla riadkov, na ktoré odkazujú tabuľky TC, platia pre oba.

**Kontrola, že oracle nie je prázdny.** Zelený výsledok má cenu, len ak by
chyba skončila červeno. Prvé dva kontrolné behy prebehli na `f36e126`, tretí
na `29c3170`:

| Kontrolný beh | Očakávanie | Výsledok |
| ------------- | ---------- | -------- |
| aplikácia s vloženou chybou — v kópii `src/` v `enter_blocking_state()` `if conflicts:` → `if False:` (BR-02 sa nevyhodnocuje), port 8001 | `TC-C08-02` a `TC-C08-03` musia padnúť, `TC-C08-01` nie (susedné intervaly sa neprekrývajú) | `TC-C08-01 PASS`, `TC-C08-02 FAIL`, `TC-C08-03 FAIL`, návratový kód `1`. V `TC-C08-02` vrátilo potvrdenie `200 PENDING_APPROVAL` a **nezávislý SQL dotaz našiel blokujúcu dvojicu `[(B1, B2)]`** — porušenie BR-02 odhalila perzistencia, nie odpoveď API. |
| aplikácia nebeží (port 8009) | `BLOCKED`, nie `FAIL` ani `PASS` | `BLOCKED - infrastruktura nebezi: [Errno 61] Connection refused`, ďalší krok vypísaný, návratový kód `2` |
| výpadok počas behu — na `29c3170` tretie HTTP volanie nahradené výnimkou `httpx.ConnectError` (simulácia v samostatnom procese, repozitár bez zmeny) | `BLOCKED`, nie traceback ani `FAIL` | po kroku 2 behu 2 `TC-C08-01`: `BLOCKED - infrastruktura vypadla pocas behu`, návratový kód `2` |

Ako sa kontrolné behy spustili a čo vypísali (súhrnné riadky výpisu):

```text
# 1) vložená chyba: kópia src/ v scratch adresári, jedna náhrada textu
cp -R src $SCRATCH/mutant/
#   services/reservations.py, enter_blocking_state():  "    if conflicts:" -> "    if False:"
PYTHONPATH=$SCRATCH/mutant/src .venv/bin/python -m uvicorn swilab.main:app --port 8001 &
BASE_URL=http://127.0.0.1:8001 .venv/bin/python scripts/c08_baseline_tc.py ; echo $?
  [FAIL] krok 3: POST confirm(B2), potvrdzuje vlastnik S1
         actual:   200 PENDING_APPROVAL
  [FAIL] krok 4: precitat ulozeny stav po rollbacku (nove spojenie)
         actual:   {'stavy': {'B1': 'CONFIRMED', 'B2': 'PENDING_APPROVAL'}, 'B1_bez_zmeny': True, 'blokujuce_prekryvy': [('B1', 'B2')]}
  TC-C08-01: PASS
  TC-C08-02: FAIL
  TC-C08-03: FAIL
  1

# 2) aplikácia nebeží
BASE_URL=http://127.0.0.1:8009 .venv/bin/python scripts/c08_baseline_tc.py ; echo $?
  BLOCKED - infrastruktura nebezi: [Errno 61] Connection refused
  dalsi krok: docker compose up -d --wait db; spustit uvicorn (docstring)
  2
```

Oba kontrolné behy nezávisle zopakovala aj kontrola pred review s rovnakým
výsledkom.

Pri behu s vloženou chybou hlavička hlási `kod aplikacie: = b0974e7`, lebo
porovnáva repozitár, nie kópiu, ktorú server spustil — kontrolný beh to
neuvádza ako vlastnosť skriptu, iba ako jeho hranicu.

**Limity tohto overenia** (čo beh **nedokazuje**):

- **Čas sa posunúť nedá.** API číta skutočné hodiny servera, takže hranice
  v čase (BR-08 v `starts_at`, budúca hranica 48 h) sa cez HTTP overiť nedajú
  bez čakania. Overujú ich testy služby, ktoré dostávajú `now` ako parameter
  (`test_expiry_boundary`, `test_liveness_in_the_overlap_query_agrees_with_the_policy`).
- **Súbeh** tu nie je — beží jedna požiadavka za druhou. Pokrývajú ho testy
  súbehu nižšie.
- **`TC-C08-03` používa `created_at` ako náhradu** okamihu podania, ktorý
  baseline nepozná. Pre test to stačí: dokazuje práve to, že systém vek
  žiadosti nevie posúdiť.
- **Jeden stroj, jedna inštancia aplikácie** (G4), bez CI.
- Dáta sa vkladajú priamo do databázy, nie cez OP-01 a OP-03 — rovnako ako
  fixtures testov. Vytvorenie cez HTTP overuje `test_http_create_reservation`,
  prechod `DRAFT → PENDING_APPROVAL` cez HTTP
  `test_http_confirm_on_guarded_instrument_returns_pending` (oba cez
  `TestClient`, nie cez bežiaci server).

<details>
<summary>Úplný výpis behu 3 na <code>29c3170</code> (čísla riadkov, na ktoré odkazujú tabuľky TC)</summary>

```text
  1  cas behu (UTC):   2026-11-15T11:27:42Z
  2  HEAD:             29c3170
  3  kod aplikacie:    = b0974e7
  4  databaza:         postgresql://swilab:***@localhost:5433/swilab
  5  aplikacia:        http://127.0.0.1:8000
  6  den testu (UTC):  2026-11-22
  7  PostgreSQL:       16.15 (Debian 16.15-1.pgdg13+2)
  8  odtlacok schemy:  1f96b2bbf6bf8c47
  9  
 10  === TC-C08-01 - beh 1 ===
 11    [OK  ] krok 1: reset + R1, B1, B2
 12           expected: presne jedna CONFIRMED (B1); B2 PENDING_APPROVAL
 13           actual:   {'B1': 'CONFIRMED', 'B2': 'PENDING_APPROVAL'}
 14    [OK  ] krok 2: nezavisle precitat intervaly a stavy (SQL)
 15           expected: B1 [10:00, 11:00), B2 [11:00, 12:00); ziadna blokujuca dvojica v prekryve
 16           actual:   {'intervaly': {'B1': '[10:00, 11:00)', 'B2': '[11:00, 12:00)'}, 'blokujuce_prekryvy': []}
 17        HTTP POST /reservations/B2/approve  {"requested_by": "V1"}
 18        -> 200 {"id": "B2", "instrument_id": "R1", "user_id": "S1", "starts_at": "2026-11-22T11:00:00Z", "ends_at": "2026-11-22T12:00:00Z", "state": "CONFIRMED"}
 19    [OK  ] krok 3: POST approve(B2), schvaluje V1 (SUPERVISOR, nie vlastnik)
 20           expected: 200, state CONFIRMED (REQ-11; susedny interval nekoliduje, BR-01)
 21           actual:   200 CONFIRMED
 22    [OK  ] krok 4: znovu nacitat commitnuty stav z databazy (nove spojenie)
 23           expected: B1 CONFIRMED, B2 CONFIRMED; ziadna blokujuca dvojica v prekryve (BR-02)
 24           actual:   {'stavy': {'B1': 'CONFIRMED', 'B2': 'CONFIRMED'}, 'blokujuce_prekryvy': []}
 25    verdikt behu 1: PASS
 26  
 27  === TC-C08-01 - beh 2 ===
 28    [OK  ] krok 1: reset + R1, B1, B2
 29           expected: presne jedna CONFIRMED (B1); B2 PENDING_APPROVAL
 30           actual:   {'B1': 'CONFIRMED', 'B2': 'PENDING_APPROVAL'}
 31    [OK  ] krok 2: nezavisle precitat intervaly a stavy (SQL)
 32           expected: B1 [10:00, 11:00), B2 [11:00, 12:00); ziadna blokujuca dvojica v prekryve
 33           actual:   {'intervaly': {'B1': '[10:00, 11:00)', 'B2': '[11:00, 12:00)'}, 'blokujuce_prekryvy': []}
 34        HTTP POST /reservations/B2/approve  {"requested_by": "V1"}
 35        -> 200 {"id": "B2", "instrument_id": "R1", "user_id": "S1", "starts_at": "2026-11-22T11:00:00Z", "ends_at": "2026-11-22T12:00:00Z", "state": "CONFIRMED"}
 36    [OK  ] krok 3: POST approve(B2), schvaluje V1 (SUPERVISOR, nie vlastnik)
 37           expected: 200, state CONFIRMED (REQ-11; susedny interval nekoliduje, BR-01)
 38           actual:   200 CONFIRMED
 39    [OK  ] krok 4: znovu nacitat commitnuty stav z databazy (nove spojenie)
 40           expected: B1 CONFIRMED, B2 CONFIRMED; ziadna blokujuca dvojica v prekryve (BR-02)
 41           actual:   {'stavy': {'B1': 'CONFIRMED', 'B2': 'CONFIRMED'}, 'blokujuce_prekryvy': []}
 42    verdikt behu 2: PASS
 43    opakovanie po resete dalo ROVNAKY vysledok
 44  
 45  === TC-C08-02 - beh 1 ===
 46    [OK  ] krok 1: reset + R1, B1, B2
 47           expected: iba B1 je CONFIRMED; B2 DRAFT
 48           actual:   {'B1': 'CONFIRMED', 'B2': 'DRAFT'}
 49    [OK  ] krok 2: nezavisle precitat vychodiskovy stav (SQL)
 50           expected: prekryv v case B1-B2 existuje, blokujuca dvojica nie
 51           actual:   {'prekryvy_v_case': [('B1', 'B2')], 'blokujuce_prekryvy': []}
 52        HTTP POST /reservations/B2/confirm  {"requested_by": "S1"}
 53        -> 409 {"code": "OVERLAP", "message": "interval koliduje s inou rezervaciou toho isteho pristroja v blokujucom stave"}
 54    [OK  ] krok 3: POST confirm(B2), potvrdzuje vlastnik S1
 55           expected: zamietnute: 409, code OVERLAP (REQ-04, BR-02)
 56           actual:   409 OVERLAP
 57    [OK  ] krok 4: precitat ulozeny stav po rollbacku (nove spojenie)
 58           expected: B1 bez zmeny (cely riadok), B2 DRAFT; ziadna blokujuca dvojica v prekryve
 59           actual:   {'stavy': {'B1': 'CONFIRMED', 'B2': 'DRAFT'}, 'B1_bez_zmeny': True, 'blokujuce_prekryvy': []}
 60    verdikt behu 1: PASS
 61  
 62  === TC-C08-02 - beh 2 ===
 63    [OK  ] krok 1: reset + R1, B1, B2
 64           expected: iba B1 je CONFIRMED; B2 DRAFT
 65           actual:   {'B1': 'CONFIRMED', 'B2': 'DRAFT'}
 66    [OK  ] krok 2: nezavisle precitat vychodiskovy stav (SQL)
 67           expected: prekryv v case B1-B2 existuje, blokujuca dvojica nie
 68           actual:   {'prekryvy_v_case': [('B1', 'B2')], 'blokujuce_prekryvy': []}
 69        HTTP POST /reservations/B2/confirm  {"requested_by": "S1"}
 70        -> 409 {"code": "OVERLAP", "message": "interval koliduje s inou rezervaciou toho isteho pristroja v blokujucom stave"}
 71    [OK  ] krok 3: POST confirm(B2), potvrdzuje vlastnik S1
 72           expected: zamietnute: 409, code OVERLAP (REQ-04, BR-02)
 73           actual:   409 OVERLAP
 74    [OK  ] krok 4: precitat ulozeny stav po rollbacku (nove spojenie)
 75           expected: B1 bez zmeny (cely riadok), B2 DRAFT; ziadna blokujuca dvojica v prekryve
 76           actual:   {'stavy': {'B1': 'CONFIRMED', 'B2': 'DRAFT'}, 'B1_bez_zmeny': True, 'blokujuce_prekryvy': []}
 77    verdikt behu 2: PASS
 78    opakovanie po resete dalo ROVNAKY vysledok
 79  
 80  === TC-C08-03 - beh 1 ===
 81    [OK  ] krok 1: reset + R1, B1 (ziadost, DRAFT vznikol pred 10 dnami), B2
 82           expected: B1 PENDING_APPROVAL, B2 DRAFT
 83           actual:   {'B1': 'PENDING_APPROVAL', 'B2': 'DRAFT'}
 84    [OK  ] krok 2: precitat stlpce tabulky reservations (information_schema)
 85           expected: iba id, instrument_id, user_id, starts_at, ends_at, state, created_at - okamih podania ziadosti sa neuklada
 86           actual:   ['id', 'instrument_id', 'user_id', 'starts_at', 'ends_at', 'state', 'created_at']
 87        HTTP GET /instruments/R1/availability?starts_at=2026-11-22T10%3A00%3A00%2B00%3A00&ends_at=2026-11-22T11%3A00%3A00%2B00%3A00
 88        -> 200 {"instrument_id": "R1", "starts_at": "2026-11-22T10:00:00Z", "ends_at": "2026-11-22T11:00:00Z", "status": "UNAVAILABLE", "reason": "PENDING_APPROVAL", "conflicting_reservation_ids": ["B1"]}
 89    [OK  ] krok 3: GET availability R1 [10:00, 11:00)
 90           expected: 200, UNAVAILABLE, reason PENDING_APPROVAL, kolizia [B1] (REQ-03, REQ-13)
 91           actual:   200 UNAVAILABLE PENDING_APPROVAL ['B1']
 92        HTTP POST /reservations/B2/confirm  {"requested_by": "S2"}
 93        -> 409 {"code": "OVERLAP", "message": "interval koliduje s inou rezervaciou toho isteho pristroja v blokujucom stave"}
 94    [OK  ] krok 4: POST confirm(B2), potvrdzuje vlastnik S2
 95           expected: zamietnute: 409 OVERLAP - ziva ziadost blokuje (REQ-04, BR-08 v0.2)
 96           actual:   409 OVERLAP
 97        HTTP POST /reservations/B1/approve  {"requested_by": "V1"}
 98        -> 200 {"id": "B1", "instrument_id": "R1", "user_id": "S1", "starts_at": "2026-11-22T10:00:00Z", "ends_at": "2026-11-22T11:00:00Z", "state": "CONFIRMED"}
 99    [OK  ] krok 5: POST approve(B1), schvaluje V1
100           expected: 200, state CONFIRMED - ziadna lehota na rozhodnutie neexistuje (REQ-11)
101           actual:   200 CONFIRMED
102    [OK  ] krok 6: precitat ulozeny stav (nove spojenie)
103           expected: B1 CONFIRMED, B2 DRAFT
104           actual:   {'B1': 'CONFIRMED', 'B2': 'DRAFT'}
105    verdikt behu 1: PASS
106  
107  === TC-C08-03 - beh 2 ===
108    [OK  ] krok 1: reset + R1, B1 (ziadost, DRAFT vznikol pred 10 dnami), B2
109           expected: B1 PENDING_APPROVAL, B2 DRAFT
110           actual:   {'B1': 'PENDING_APPROVAL', 'B2': 'DRAFT'}
111    [OK  ] krok 2: precitat stlpce tabulky reservations (information_schema)
112           expected: iba id, instrument_id, user_id, starts_at, ends_at, state, created_at - okamih podania ziadosti sa neuklada
113           actual:   ['id', 'instrument_id', 'user_id', 'starts_at', 'ends_at', 'state', 'created_at']
114        HTTP GET /instruments/R1/availability?starts_at=2026-11-22T10%3A00%3A00%2B00%3A00&ends_at=2026-11-22T11%3A00%3A00%2B00%3A00
115        -> 200 {"instrument_id": "R1", "starts_at": "2026-11-22T10:00:00Z", "ends_at": "2026-11-22T11:00:00Z", "status": "UNAVAILABLE", "reason": "PENDING_APPROVAL", "conflicting_reservation_ids": ["B1"]}
116    [OK  ] krok 3: GET availability R1 [10:00, 11:00)
117           expected: 200, UNAVAILABLE, reason PENDING_APPROVAL, kolizia [B1] (REQ-03, REQ-13)
118           actual:   200 UNAVAILABLE PENDING_APPROVAL ['B1']
119        HTTP POST /reservations/B2/confirm  {"requested_by": "S2"}
120        -> 409 {"code": "OVERLAP", "message": "interval koliduje s inou rezervaciou toho isteho pristroja v blokujucom stave"}
121    [OK  ] krok 4: POST confirm(B2), potvrdzuje vlastnik S2
122           expected: zamietnute: 409 OVERLAP - ziva ziadost blokuje (REQ-04, BR-08 v0.2)
123           actual:   409 OVERLAP
124        HTTP POST /reservations/B1/approve  {"requested_by": "V1"}
125        -> 200 {"id": "B1", "instrument_id": "R1", "user_id": "S1", "starts_at": "2026-11-22T10:00:00Z", "ends_at": "2026-11-22T11:00:00Z", "state": "CONFIRMED"}
126    [OK  ] krok 5: POST approve(B1), schvaluje V1
127           expected: 200, state CONFIRMED - ziadna lehota na rozhodnutie neexistuje (REQ-11)
128           actual:   200 CONFIRMED
129    [OK  ] krok 6: precitat ulozeny stav (nove spojenie)
130           expected: B1 CONFIRMED, B2 DRAFT
131           actual:   {'B1': 'CONFIRMED', 'B2': 'DRAFT'}
132    verdikt behu 2: PASS
133    opakovanie po resete dalo ROVNAKY vysledok
134  
135  === suhrn ===
136  TC-C08-01: PASS
137  TC-C08-02: PASS
138  TC-C08-03: PASS
```

</details>

#### Súbeh

Zadanie žiada pre súbežné potvrdenia samostatný test s dvoma súperiacimi
operáciami. V projekte existuje od C03 a beží v každej sade
(`tests/test_concurrency_req05.py`): dve vlákna, každé s vlastnou session proti
Postgresu, štartujú spolu na bariére na vstupe do operácie, v ôsmich kolách;
test tvrdí aj konkrétne business výsledky, nie iba počet blokujúcich
rezervácií. Na baseline prechádzajú ako súčasť `253 passed` (krok 0).

Čo ktorý test naozaj dokazuje — presnejšie, než to uvádzala prvá verzia tohto
záznamu (opravené po nezávislej kontrole):

| Test | Súbeh | Čo dokazuje |
| ---- | ----- | ----------- |
| `test_concurrent_conflicting_confirmations` | dve potvrdenia prekrývajúcich sa `DRAFT` | **skutočný súbeh** REQ-05 — bez zámku prístroja by prešli obe |
| `test_concurrent_cancel_and_confirm_on_same_reservation`, `test_at_most_one_operation_leaves_the_source_state`, `test_concurrent_approve_and_reject_on_same_request` | dve operácie nad **jednou** rezerváciou | skutočný súbeh REQ-16 (zámok nad riadkom rezervácie) |
| `test_concurrent_approvals_of_colliding_requests` | dve schválenia | **nie súbeh BR-02.** Začína z dvoch živých prekrývajúcich sa žiadostí vložených priamo — zo stavu, ktorý BR-02 zakazuje (preto ho `TC-C08-02` odmieta testovať). Každé schválenie vidí druhú žiadosť, takže obe skončia `OVERLAP`; podľa vlastného docstringu test kód so zámkom od kódu bez neho nerozlíši. Zámok na ceste schválenia stráži iba statická kontrola (`test_vlastnik_invariantu_si_berie_zamok_sam`). |

**Súbeh schválenia s potvrdením toho istého prístroja netestuje nič** — a práve
ten je jadrom rizika `R-06` pri CR-08. C09 ho overí testom na úrovni služby
s explicitným `now` (poradie sa dá určiť priamo, bariéra netreba). Pre CR-08
sú testy vyššie regresnými testami AC-07.

---

## C09 — Integrácia, CI a reprodukovateľný release

C09 sa delí na dve časti, ktoré na sebe nezávisia:

- **vetva A** (tento oddiel) — automatizácia a identita release: zamknuté
  závislosti, jeden kontrolný skript, CI pri PR a push do `main`, reprodukovateľný
  artefakt. **Kód aplikácie sa nemení.**
- **vetva B** — samotná zmena `CR-08` podľa ohraničenej delty z C08
  ([intent-and-change.md](intent-and-change.md#c08--controlled-change-cr-08)).

Záznam C09 podľa šablóny zo zadania je v [README, časť C09](../README.md#c09--integration-ci-and-release-record).

### Vetva A — zamknuté závislosti

**Prečo.** `pyproject.toml` uvádza iba spodné hranice. Ten istý commit tak
dával na rôznych strojoch a v rôznych dňoch iný produkt — evidence C08 to
priznávala ako obmedzenie reprodukovateľnosti. Pri príprave locku sa ukázalo,
ako rýchlo sa to deje:

| Balíček | T. Hrubý, C08 (2026-11-15) | T. Krišica, obnovenie C08 (2026-11-15) | čistá inštalácia, C09 (2026-11-15) |
| ------- | -------------------------- | -------------------------------------- | ---------------------------------- |
| FastAPI | 0.141.1 | 0.142.2 | **0.163.1** |
| SQLAlchemy | 2.0.52 | 2.1.3 | **2.1.8** |
| psycopg | 3.3.5 | 3.3.6 | **3.3.9** |
| pydantic | 2.13.5 | 2.13.5 | **2.14.3** |

Tri prostredia, tri rôzne produkty — a všetky tri hlásili `253 passed`.
Zelená sada bez locku teda nehovorí, **na čom** bola zelená.

**Ako lock vznikol.** Z čistého venv (Python 3.12.13), nie z vývojárskeho
prostredia: to obsahovalo nástroje, ktoré projekt nedeklaruje (`python-pptx`,
`pillow`, `lxml`, `xlsxwriter` — z práce na prezentáciách), a `pip freeze`
by ich zapísal do identity produktu.

| Súbor | Obsah | Použitie |
| ----- | ----- | -------- |
| `requirements.lock` | 24 runtime balíčkov | identita produktu v RC manifeste |
| `requirements-dev.lock` | 35 balíčkov: runtime + `pytest`, `httpx` + `build`, `setuptools` | CI, čistý checkout, `ci_check.sh` |

Runtime lock je vygenerovaný s obmedzením na dev lock, takže verzie spoločných
balíčkov sú v oboch súboroch rovnaké (overené `comm`).

**Overenie na zamknutých verziách** — pred zamknutím, nie po ňom:

| Kontrola | Výsledok |
| -------- | -------- |
| `pytest -q` | `253 passed` |
| `PGTZ=Europe/Prague pytest -q` | `253 passed` |
| `scripts/mutation_check.py` | 34 mutácií, 0 prežilo |
| `scripts/c08_baseline_tc.py` proti bežiacej aplikácii | `TC-C08-01..03` 3× `PASS`, návratový kód `0` |

Projekt teda funguje aj na verziách o dvadsať minor verzií FastAPI novších,
než na akých vznikol — a od tohto commitu sa už nezmenia bez zmeny locku.

### Vetva A — jeden kontrolný skript

`scripts/ci_check.sh` je **jediný** popis toho, čo sa kontroluje. Spúšťa ho
CI aj človek lokálne, takže „zelené v CI" a „zelené u mňa" znamenajú to isté.

| Krok | Čo robí | Prečo |
| ---- | ------- | ----- |
| 1/6 identita behu | commit, Python, systém; **každá zamknutá verzia musí byť nainstalovaná presne** | zelená na iných verziách by nič nedokazovala |
| 2/6 `pytest` | celá sada proti PostgreSQL | jednotkové, komponentové a súbehové testy |
| 3/6 `pytest` pri `PGTZ=Europe/Prague` | tá istá sada pri inom pásme klienta | trvalá ochrana nálezu F-01 — predtým sa to overilo raz a ručne |
| 4/6 mutácie | 34 vložených chýb musí sada zachytiť | zelená sada, ktorá nič nezachytí, je bezcenná |
| 5/6 `TC-C08-01..03` | aplikácia sa spustí, test cases idú cez HTTP, stav sa číta novým spojením priamo z databázy | **skutočná integračná hranica** HTTP → aplikácia → PostgreSQL, ktorú zadanie C09 žiada |
| 6/6 artefakt | wheel + SHA256 do `dist/SHA256SUMS` | identita produktu (krok 6 zadania) |

Návratový kód `0` iba vtedy, keď prejdú všetky kroky; inak skript skončí na
prvom zlyhanom kroku a vypíše ktorý.

**Lokálny beh — prostredie nainštalované presne ako v CI.** Nový venv,
`pip install -r requirements-dev.lock`, `pip install --no-deps --no-build-isolation -e .`:

| Položka | Hodnota |
| ------- | ------- |
| Commit | `35d684f` |
| Prostredie | Python 3.12.13, Darwin arm64, PostgreSQL 16.13 z `docker-compose.yml` |
| Príkaz | `PYTHON=<venv>/bin/python scripts/ci_check.sh` |
| Výsledok | návratový kód **`0`**, `VSETKY KROKY PRESLI` |
| Kroky | lock 35/35 · `253 passed` · `253 passed` (Prague) · 34/34 · `TC-C08-01..03` PASS · wheel |
| Artefakt | `swilab-0.2.0-py3-none-any.whl`, SHA256 `a4e80878b4a7384381e6cfdc6c5313b98e4d6dc1f7aba910d1545bb161fb7592` |

**Negatívna kontrola — skript musí odmietnuť prostredie mimo locku.** Ten istý
skript spustený vo vývojárskom `.venv` (SQLAlchemy 2.1.3 namiesto 2.1.8 a
ďalších 14 rozdielov):

```text
  sqlalchemy: zamknute 2.1.8, nainstalovane 2.1.3
  starlette: zamknute 1.10.0, nainstalovane 1.7.0
  ...
prostredie nezodpoveda requirements-dev.lock

ZLYHANIE v kroku: 1/6 identita behu (navratovy kod 1)
```

Skript sa k testom nedostal. Zelená na nesprávnych verziách teda nemôže vzniknúť.

### Vetva A — reprodukovateľný artefakt

Wheel obsahuje iba balík `swilab` (15 modulov) a metadáta — žiadne testy,
skripty ani dokumenty. Zostavuje sa s dvoma nastaveniami:

- `SOURCE_DATE_EPOCH` = čas commitu — časové značky v archíve nezávisia od
  okamihu zostavenia,
- `--no-isolation` — použije sa `setuptools` z locku, nie najnovší z PyPI.

| Pokus | SHA256 |
| ----- | ------ |
| `2a6f615`, beh 1, so `SOURCE_DATE_EPOCH` | `c8e606fd…ee80f02` |
| `2a6f615`, beh 2, so `SOURCE_DATE_EPOCH` | `c8e606fd…ee80f02` — **zhodný** |
| `2a6f615`, beh 3, bez `SOURCE_DATE_EPOCH` | `f06f7dcf…ad46e6c5` — iný |

Ten istý commit dá **bajtovo ten istý wheel**. Hash je teda identita, ktorú
môže ktokoľvek overiť zostavením zo zdroja — nie iba číslo, ktoré niekto
vypísal. Metadáta wheelu (`Requires-Dist`) obsahujú spodné hranice z
`pyproject.toml`; presné verzie prostredia určuje `requirements.lock`.

### Vetva A — CI

`.github/workflows/ci.yml`, pri každom PR a push do `main`: Ubuntu, Python 3.12,
PostgreSQL 16 ako service kontajner na porte 5433 (rovnako ako
`docker-compose.yml`, takže `DATABASE_URL` je všade rovnaká), závislosti
z `requirements-dev.lock`, potom `scripts/ci_check.sh` a wheel ako artefakt
behu.

Heslo v `ci.yml` je heslo vývojového kontajnera z `docker-compose.yml`,
nie tajomstvo — rovnaké ako na miestach, ktoré vymenúva krok 0 v C08.

**Prvý beh CI** — [run 43767331765](https://github.com/thspowered/SWI-LAB/actions/runs/43767331765),
udalosť `push`, commit `35d684f` — ten istý commit ako lokálny beh vyššie,
pushnutý samostatne práve kvôli tomuto porovnaniu:

| Položka | Lokálne (T. Krišica) | CI |
| ------- | -------------------- | -- |
| Systém | Darwin arm64 | Linux x86_64 (`ubuntu-latest`) |
| Python | 3.12.13 | 3.12.16 |
| PostgreSQL | 16.13 | 16.16 |
| Lock | 35/35 v zamknutej verzii | 35/35 v zamknutej verzii |
| `pytest` / pri `PGTZ=Europe/Prague` | `253 passed` / `253 passed` | `253 passed` / `253 passed` |
| Mutácie | 34, 0 prežilo | 34, 0 prežilo |
| `TC-C08-01..03` | 3× `PASS` | 3× `PASS` |
| Výsledok | návratový kód `0` | `success` |
| **SHA256 wheelu** | `a4e80878b4a7384381e6cfdc6c5313b98e4d6dc1f7aba910d1545bb161fb7592` | **`a4e80878b4a7384381e6cfdc6c5313b98e4d6dc1f7aba910d1545bb161fb7592`** |

**Hash je zhodný naprieč operačným systémom, architektúrou aj patch verziou
Pythonu.** Reprodukovateľnosť artefaktu teda nie je vlastnosť jedného stroja:
ktokoľvek, kto zostaví commit `35d684f` podľa `ci_check.sh`, dostane ten istý
súbor — a môže to overiť porovnaním s artefaktom behu CI.

Lock pritom nedrží verziu Pythonu ani Postgresu na patch úrovni (3.12.13 vs.
3.12.16, 16.13 vs. 16.16) a výsledok sa nezmenil. Je to obmedzenie, ktoré
zostáva — pozri nižšie.

### Vetva A — čo nedokazuje

- **Kód aplikácie sa nezmenil.** CI dnes overuje baseline pred `CR-08`.
  Novú požiadavku overí až vetva B — vtedy `TC-C08-03` podľa očakávania
  zmení výsledok a skript treba upraviť (krok 3 delty z C08).
- **Python a PostgreSQL sú zamknuté iba na minor verzii** (`3.12`, `postgres:16`).
  Prvý beh CI prebehol na iných patch verziách než lokálny a dal rovnaký
  výsledok aj hash — ale to je pozorovanie, nie záruka.
- **Lock neobsahuje hashe balíčkov** a nezachováva platformové značky:
  `uvloop` a `httptools` sa neinštalujú na Windows. Platí pre Linux a macOS,
  na ktorých tím a CI bežia.
- **Verzia balíčka je stále `0.2.0`.** Či bude `0.4.0` (zhoda so špecifikáciou
  v0.4), alebo sa číslovanie rozpojí, je otvorené rozhodnutie pre vetvu B.
- **Nezávislý čistý checkout** druhým členom (krok 5 zadania) ešte neprebehol.
  Lokálny beh vyššie robil autor tejto vetvy na vlastnom stroji.

### Vetva B — implementácia `CR-08` (kroky 2 a 3 zadania C09)

**Rozhodnutie z C08:** [CR-08](intent-and-change.md#c08--controlled-change-cr-08),
`ACCEPT`, varianta A so zmenou `CA-5`, peer review PR #11. Podmienky ACCEPT:
(1)(2) review, (3) migrácie — `ADR-02`, (4) špecifikácia v0.4 — PR #14,
**schválená 2026-11-18** (review T. Hrubého). Kód vznikol súbežne so
špecifikáciou, teda pred jej schválením; podmienka (4) je dodržaná na úrovni
integrácie, nie písania. Kód preto ide v samostatnom PR, ktorý nadväzuje na
PR #14 a do `main` sa smie dostať až po jeho schválení.

**Ohraničená delta z C08 → čo vzniklo:**

| Krok delty (C08) | Kde | Commit |
| ---------------- | --- | ------ |
| 1. špecifikácia v0.4 a diagramy (vrátane RV-5) | PR #14 | `5268785` |
| 2. architektúra a detailný návrh — `DD-8`, `DD-9`, signatúry C05 | PR #14 | `5268785`, `0a8a723` |
| 3. testy, ktoré najprv padnú; nové Expected `TC-C08-03`; oracle v0.4 | PR #15 | `8ce4035` |
| 4. kód — stĺpec, obmedzenie, termín, politika, SQL, API, kontrola schémy | PR #15 | `9ede5a6` |
| 5. mutácie — upravené texty + D1–D11 | PR #15 | `9ede5a6` |
| 6. evidence, README, verzia `0.4.0`, postup rollbacku | PR #15 | tento commit a nasledujúce |

**Expected pred kódom.** Testy a nové Expected `TC-C08-03` sú v commite
`8ce4035` *(pôvodne `5beac73`; hash sa zmenil presadením na opravený návrh
v PR #14, testy sú totožné a dátum autora pôvodný)*. Beh na ňom, pred kódom:
**`218 failed, 82 passed, 10 errors`**. Po kóde (`9ede5a6`), bez zmeny
jediného testu: **`310 passed`** — 253 pôvodných a 57 nových.

**Nové správanie — test basis z C08 a skutočný výsledok** (príkaz
`scripts/ci_check.sh`, commit `fe28bed`, PostgreSQL 16 z `docker-compose.yml`):

| Test ID | Prijatý oracle | Úroveň a kde | Expected | Actual | Verdikt |
| ------- | -------------- | ------------ | -------- | ------ | ------- |
| AC-01, AC-04 | BR-08 v0.4, REQ-11 | služba: `test_approval_at_the_48h_boundary[AC-01-47:59:59]` | `CONFIRMED` | `CONFIRMED`, uložené | PASS |
| AC-02, AC-04 | BR-08 v0.4, REQ-15 | služba: `…[AC-02-48:00:00]`, `test_late_rejection_expires_and_a_repeat_is_invalid_state` | `EXPIRED` + zápis; druhý pokus `INVALID_STATE` | rovnako | PASS |
| AC-03 | BR-08, REQ-03, REQ-04 | služba: `test_request_past_its_deadline_frees_the_instrument`; HTTP: `TC-C08-03` kroky 3–4 | po termíne `AVAILABLE`, prekryv sa dá potvrdiť | rovnako | PASS |
| AC-05 | BR-08 v0.4 | služba: `test_request_submitted_shortly_before_start_expires_at_start` | termín = `starts_at` | rovnako | PASS |
| AC-06 | REQ-10 v0.4 | HTTP: `test_http_confirm_of_a_request_returns_its_decision_deadline`, `…never_was_a_request_has_no_deadline` | termín = podanie + 48 h; inak `null` | rovnako | PASS |
| AC-07 | BR-02, R-06 | služba: `test_approval_with_an_older_now_meets_a_newer_request`; súbehové testy REQ-05, REQ-16 | `OVERLAP`, BR-02 drží | rovnako | PASS |
| AC-08 | celá sada v0.2 | 253 pôvodných testov | prejdú | `253` z nich prejde | PASS |
| AC-09 | BR-08 v0.4 | služba: `test_a_request_from_before_v04_behaves_as_in_v02`; rollback skúška krok 6 | správanie v0.2 | rovnako | PASS |
| AC-10 | BR-08 v0.4 | komponent: `test_changing_the_period_does_not_move_an_existing_deadline` | termín sa nehýbe | rovnako | PASS |
| REQ-15 | oprávnenie pred vypršaním | služba: `test_authorization_wins_over_expiry[lehota|starts_at]` | `FORBIDDEN`, stav bez zmeny | rovnako | PASS |
| DD-9 | obmedzenie | komponent: `test_the_database_refuses_a_request_that_breaks_dd9` | `IntegrityError` | rovnako | PASS |
| ADR-02 | kontrola schémy | `tests/test_schema_check.py` | každý druh rozdielu nájdený, štart odmietnutý | rovnako | PASS |
| `TC-C08-01` | BR-02, REQ-11 — regresia | HTTP → aplikácia → PostgreSQL, stav novým spojením | ako na baseline | ako na baseline | PASS |
| `TC-C08-02` | BR-02, REQ-04 — regresia | tamtiež | ako na baseline | ako na baseline | PASS |
| `TC-C08-03` | **oracle CR-08** — Expected z C08, zapísané pred kódom | tamtiež | `AVAILABLE`; `B2 → PENDING_APPROVAL` s termínom; `409 EXPIRED`; `B1 EXPIRED` | rovnako | PASS |

**Nález počas prvého behu — chyba v návrhu test casu, nie v aplikácii.**
Prvý beh `ci_check.sh` na `9ede5a6`: `TC-C08-03` prešiel v oboch behoch
všetkými šiestimi krokmi, ale verdikt bol FAIL — porovnanie dvoch behov po
resete hlásilo rozdiel. Krok 4 zapisoval do pozorovania aj hodnotu termínu
z odpovede API, ktorá závisí od skutočných hodín servera
(`22:51:31.100326Z` vs. `22:51:31.133303Z`). Oprava `fe28bed` mení iba
**záznam** pozorovania (termín *je / nie je* v odpovedi); Expected ani
kritérium kroku sa nemenia. Presnú hodnotu overuje pytest test pre AC-06.

**Kontrolný beh — nový oracle nie je prázdny.** Aplikácia s vloženou chybou
D3 (SQL forma živosti podľa `starts_at`), port 8001:
`TC-C08-01 PASS`, `TC-C08-02 PASS`, **`TC-C08-03 FAIL`** — krok 3
`UNAVAILABLE PENDING_APPROVAL ['B1']`, krok 4 `409 OVERLAP`, krok 6
`B2 DRAFT`. Návratový kód `1`. Regresné test cases od termínu nezávisia,
preto prešli — správne.

**Mutácie: 45, žiadna neprežila.** Nové D1–D11, každá zachytená testami,
pre ktoré vznikla:

| Mutácia | Zachytené |
| ------- | --------- |
| D1 lehota 47 h | 11 testov, vrátane `test_decision_period_is_48_hours` |
| D2 termín bez `min` | 10 — obmedzenie DD-9 navyše odmietne zápis |
| D3 SQL živosť podľa `starts_at` | 3, vrátane zhody oboch foriem na hranici 48 h |
| D4 politika podľa `starts_at` | 9 |
| D5 strážca termín nezapíše | 21 — obmedzenie DD-9 odmietne každé podanie |
| D6 termín aj pri `CONFIRMED` | 3 |
| D7 DD-9 bez `<= starts_at` | 1 — `test_the_database_refuses_a_request_that_breaks_dd9` |
| D8 kontrola schémy bez obmedzení | 1 |
| D9 kontrola schémy sa nevolá | 1 |
| D10 vypršanie pred oprávnením | 2 — `test_authorization_wins_over_expiry` |
| D11 API bez termínu | 1 |

D7 až D11 zachytáva jediný test (D10 dva). Sú to jednoznačné kontrakty,
nie hranice — ale ak ktorýkoľvek z týchto testov niekto zmaže, mutácia
prežije a skript to ohlási.

### Vetva B — rollback a návrat vpred, vyskúšané nad dátami

C08 žiadalo „rollback aj návrat vpred raz vyskúšané nad databázou s dátami";
zadanie C09 pripomína, že rollback kódu sám nevráti dáta. Postup je
v `scripts/cr08_rollback.sql` a `scripts/cr08_forward.sql`; skúška prebehla
2026-11-15 nad PostgreSQL 16 z `docker-compose.yml`, kód v0.3 = `b0974e7`,
kód v0.4 = `9ede5a6`.

| # | Krok | Výsledok |
| - | ---- | -------- |
| 0 | dáta, ktoré vo v0.4 legálne vzniknú: žiadosť **A** po termíne rozhodnutia (začiatok o 5 dní), **B** `CONFIRMED` na tom istom čase, **C** živá žiadosť | BR-02 podľa v0.4: žiadna blokujúca dvojica |
| 1 | **pasca:** tie isté dáta posúdené pravidlom v0.2 | **blokujúca dvojica `(A, B)`** — podľa v0.2 porušenie BR-02. Rollback kódu bez rollbacku dát by ho vyrobil. |
| 2 | `cr08_rollback.sql` | `UPDATE 1` (A → `EXPIRED`), obmedzenie DD-9 zrušené, C ostáva `PENDING_APPROVAL`; BR-02 podľa v0.2 platí |
| 3 | **kód v0.4 nad schémou po rollbacku** | **štart odmietnutý** — „chýba obmedzenie `reservations.ck_reservations_request_has_deadline`". Kontrola z `ADR-02` zachytila aj neúplný návrat. |
| 4 | kód v0.3 nad dátami po rollbacku | naštartuje; dostupnosť A/B: `UNAVAILABLE`, `CONFIRMED`, `['B']`; nová žiadosť D vznikne bez termínu (`NULL`); C sa dá schváliť |
| 5 | `cr08_forward.sql` | `UPDATE 1` (D: termín = `starts_at`, AC-09), obmedzenie DD-9 späť |
| 6 | kód v0.4 po návrate vpred | naštartuje; D sa schváli — správa sa ako vo v0.2 (AC-09) |
| 7 | BR-02 na konci podľa v0.4 | žiadna blokujúca dvojica; A `EXPIRED`, B, C, D `CONFIRMED` |

**Obmedzenie postupu:** aplikácia musí pri rollbacku stáť. Žiadosť, ktorej
termín uplynie medzi krokom (1) a nasadením kódu v0.3, by inak ostala
zapísaná ako živá. Dáta sa pri rollbacku nestrácajú; pri prestavbe podľa
`ADR-02` áno.

### Vetva B — identita artefaktu

`ci_check.sh` odvodzuje `SOURCE_DATE_EPOCH` od posledného commitu, ktorý
menil **kód** (`src/`, `pyproject.toml`), nie od posledného commitu vôbec.
Ten istý kód tak dá bajtovo ten istý wheel, aj keď medzitým pribudli commity
iba s dokumentáciou — identita produktu sa nemení zmenou README. Pre vetvu A
to bol commit `35d684f` (vtedy posledný vôbec, teda rovnaký výsledok).

**Nález na PR #15 — CI dalo iný hash než lokálny beh.** Po zavedení tohto
pravidla zostavilo CI z PR #15 wheel `9ec78c06…`, lokálne `2d3adcc3…`, a ako
posledný commit s kódom hlásilo `3ce38a4` — syntetický merge commit PR, nie
náš commit. Príčina: `actions/checkout` predvolene sťahuje iba posledný
commit (`fetch-depth: 1`). V plytkom klone je ten commit koreňom histórie
a `git log -- src` ho hlási ako zmenu všetkého. Reprodukované lokálne na
`951982c`: plytký klon → `951982c` (commit iba s README), úplný klon →
`9ede5a6`. Pri vetve A sa chyba neprejavila, lebo vtedy sa bral čas
posledného commitu vôbec. Oprava: `fetch-depth: 0` v `ci.yml`.

### Vetva B — review a nezávislá reprodukcia (T. Hrubý, 2026-11-18)

**Review.** PR #13, #14 a #15 prešiel T. Hrubý proti zadaniu C09 a proti
odovzdaniu z C08. `DD-8` a `DD-9` sú potvrdené, špecifikácia v0.4 je
schválená (§11). Overené vlastným behom, nie iba čítaním:

- testy pred kódom: `8ce4035` → **`218 failed, 82 passed, 10 errors`**, presne
  ako vyššie. Testy sú podľa dátumu autora o 7 minút staršie než kód
  (`8ce4035` 23:41, `9ede5a6` 23:48); commit testov bol neskôr presadený, preto
  je jeho dátum commitu blízko kódu,
- kód zodpovedá `DD-8`, `DD-9` a `ADR-02`; testy čítajú uložený stav novým
  spojením; rollback skripty zodpovedajú postupu z C08.

**Nálezy a opravy** (cielene na vetvách autora, bez rebase — `9ede5a6` ostal
nedotknutý):

| # | Nález | Oprava |
| - | ----- | ------ |
| RV-C09-1 | Čistý checkout podľa README zlyhal: `docker compose up` → *container name "/swilab-db" is already in use* — pevné `container_name` a kontajner z pôvodného checkoutu na tom istom stroji. | README, *Ako to spustiť*: postup `docker rm -f swilab-db` (`eaf10e0`, #13) |
| RV-C09-2 | Položky z matice dopadu C08, ktoré ostali: komentáre `errors.py` a `states.py` („nastal `starts_at`"), docstring `check_availability`, „baseline v0.2" v hlavičkách modulov aj v README, opis vypršania v README. | `8200655` (komentáre a docstringy, bez zmeny logiky — `310 passed`) a README v nasledujúcom commite (#15) |
| RV-C09-3 | Podmienka ACCEPT (4) hovorila „v0.4 schválená pred implementáciou"; kód vznikol skôr. | priznané v §11 špecifikácie (`13c3c32`) a v úvode vetvy B vyššie |
| RV-C09-4 | Identita artefaktu závisí od času commitu s kódom — squash alebo rebase pri merge by ju zmenili. | v README: merge iba cez *Create a merge commit*, poradie #13 → #14 → #15 |
| RV-C09-5 | CI pri push do vetvy s otvoreným PR bežalo dvakrát. Drobnosti: BR-08 o žiadostiach spred v0.4 hovorilo o „okamihu podania" (ukladá sa termín); komentár o autoflush v `enter_blocking_state()` zdôvodňoval niečo, čo nenastane. | `push` iba do `main` (`eaf10e0`); BR-08 (`13c3c32`); komentár (`8200655`) |

**Nezávislá reprodukcia — čistý checkout druhým členom (krok 5 zadania C09).**

| | Beh 1 | Beh 2 |
| - | ----- | ----- |
| **Kedy** | 2026-11-18 09:31 UTC | 2026-11-18 09:41 UTC |
| **Commit** | `0769da8` (hlava PR #15 pred review) | `8200655` (po oprave komentárov) |
| **Prostredie** | macOS 26.6.2 arm64, Python 3.12.11, Docker 28.3.2 — stroj T. Hrubého; nový adresár, `git clone` z GitHubu, žiadny súbor z autorovho prostredia | rovnako |
| **Príkazy** | README, *Ako to spustiť*: `python3.12 -m venv .venv`, `pip install -r requirements-dev.lock`, `pip install --no-deps --no-build-isolation -e .`, `cp .env.example .env`, `docker compose up -d --wait db`, potom `scripts/ci_check.sh` | rovnako, a pri konflikte kontajnera postup z RV-C09-1 |
| **Skrytá oprava / odchýlka** | **áno:** `docker compose up` zlyhal na konflikte názvu kontajnera; použitý existujúci kontajner `swilab-db` z pôvodného checkoutu (rovnaký obraz `postgres:16`, port, prihlasovacie údaje). Nález RV-C09-1. | **žiadna** — kontajner odstránený a vytvorený z nového adresára podľa opraveného postupu |
| **Výsledok** | krok 1: 35 balíčkov presne podľa locku; `310 passed`; pri `PGTZ=Europe/Prague` `310 passed`; 45 mutácií, 0 prežilo; `TC-C08-01..03` PASS; `VSETKY KROKY PRESLI`, návratový kód `0` | rovnako |
| **Wheel** | kód `9ede5a6`: `2d3adcc3e317eccb001021ccfa743b2e2e6dbd6d97751da62067fee531788e7d` — **bajtovo rovnaký ako zostavenie T. Krišicu** (macOS, Python 3.12.13) | kód `8200655`: `cf090f0b6615c13ec80c49afc8e7d0813d27f033945ee4ea2f45743f905626ef` — **rovnaký ako v CI** ([run 44278224079](https://github.com/thspowered/SWI-LAB/actions/runs/44278224079), Linux x86_64, Python 3.12.16; aj v behu nad merge commitom PR, run 44278233074) |

Pre kód `9ede5a6` dali dva stroje s rôznymi patch verziami Pythonu (3.12.11
a 3.12.13) ten istý wheel; pre finálny kód `8200655` macOS arm64 s Pythonom
3.12.11 a Linux x86_64 s Pythonom 3.12.16 v CI. Reprodukovateľnosť artefaktu
je tým doložená nezávisle od autora.

## C10 — Prevádzka: čo sa v systéme stalo a ako to poznáme

Štyri prevádzkové situácie zo zadania C10 — úspešné potvrdenie, zamietnutá
kolízia, technická porucha s obnovou a reštart aplikácie — na **izolovanej
testovacej inštancii**: vlastný kontajner `swilab-c10-db` (PostgreSQL 16,
port 5434) a aplikácia na porte 8010. Vývojová databáza `swilab-db` (port
5433), nad ktorou beží `pytest`, sa nezastavuje ani nemení. Dáta sú iba
testovacie, založí ich skript.

Všetky situácie vykoná jeden príkaz, `scripts/c10_provoz.py`. Rovnako ako
skript z C08 neimportuje balík `swilab`: operácie volá cez HTTP, stav číta
SQL dotazom **novým spojením priamo z databázy** a log aplikácie číta zo
súboru, kam aplikácia píše `stdout` aj `stderr`. Log ukazuje, ako aplikácia
reagovala. O stave dát rozhoduje databáza.

### C10 — záznam

Odovzdávaný záznam zo zadania. Podklady k nemu — návrh pred behom, oba behy,
zistenia a rozhodnutia — sú v ďalších častiach.

**Testovaný commit:** pred zmenou kód
[`8200655`](https://github.com/thspowered/SWI-LAB/commit/8200655) — posledný
commit, ktorý menil `src/`, kód RC 0.4.0 (tag `c09-release` je na `fd8a16f`,
kód je rovnaký). Po zmene [`ea55a6f`](https://github.com/thspowered/SWI-LAB/commit/ea55a6f)
(0.4.1, po review; prvá verzia zmeny `57e63fa`). Situácie vykonal
`scripts/c10_provoz.py`. Žiadny pokus nebol `BLOCKED`.

| Situácia | Očakávaný a skutočný výsledok | Ako sme správnosť overili | Čo ukazujú logy / je log potrebný? |
| -------- | ----------------------------- | ------------------------- | ---------------------------------- |
| **Úspešné potvrdenie** | `201 DRAFT` → `200 CONFIRMED`, skutočne rovnako. **PASS** | SQL novým spojením: `B1` `CONFIRMED`, interval bez zmeny, žiadny blokujúci prekryv (BR-02) | Iba prístupové riadky. **Netreba** — dôkazom je odpoveď a stav. |
| **Zamietnutá kolízia** | `409 OVERLAP`, `B2` ostáva `DRAFT`, skutočne rovnako. **PASS** | SQL: `B1` bez zmeny, `B2` `DRAFT`, žiadny blokujúci prekryv | Prístupový riadok `409`, bez kódu a bez času. **Zámerne nelogujeme** — špecifikovaný výsledok, nie porucha. |
| **Technická porucha a obnova** | Zastavená DB (P3a): `500`, nič sa nezapíše, po obnove jedno opakovanie uspeje — skutočne rovnako. **PASS**. Reštart DB (P3b): prvá požiadavka má uspieť — pred zmenou `500` (**FAIL**, F-04), po zmene `200`. **PASS** | SQL **pred** opakovaním: `B3` `DRAFT`, `B1` `CONFIRMED`. Až potom jedno opakovanie, po ňom `B3` `CONFIRMED` | Pred zmenou dva záznamy bez času (prístupový riadok a traceback). Po zmene jeden záznam ERROR s časom, operáciou, typom a `500` (ukážka nižšie). **Potrebný** — jediný zdroj času a príčiny. Stav `B3` z neho nevyčítame. |
| **Reštart aplikácie** | `SIGINT` → kód `0`, dáta ostanú, ďalšia operácia uspeje — skutočne rovnako. **PASS** | SQL: `B1`, `B3`, `B4` `CONFIRMED`; dostupnosť cez API po štarte | Po zmene `stop:` a `start:` s časom, verziou a cieľovou databázou. Riadky uvicornu ostávajú bez času. |

Ukážky logu po zmene (beh 5 na `ea55a6f`, UUID nahradené menom, v hranatých
zátvorkách číslo procesu):

```
2026-11-22T17:30:03.528Z [32203] ERROR swilab.main: POST /reservations/B3/confirm -> 500: chyba databazy OperationalError (psycopg.OperationalError: connection failed: connection to server at "127.0.0.1", port 5434 failed: could not receive data from server: Connection refused)
Traceback (most recent call last):
  ... spolu 151 riadkov tracebacku
```

```
2026-11-22T17:30:05.271Z [32203] INFO swilab.main: stop: swilab 0.4.1
2026-11-22T17:30:05.731Z [32209] INFO swilab.main: start: swilab 0.4.1, databaza 127.0.0.1:5434/swilab, schema sedi s modelmi
```

**Ktoré udalosti má zmysel logovať?** Technickú poruchu — jeden záznam
ERROR s časom, operáciou, typom problému a výsledkom. Štart a zastavenie
s verziou a cieľovou databázou. Úspech ani doménové zamietnutie nie:
výsledok je v odpovedi a stav v databáze. Do logu nepatria heslá ani
parametre SQL. Pozor: aj samotná správa databázy môže obsahovať hodnotu
(napr. pri porušení unikátnosti). Dnes ju žiadna operácia API nad osobnými
údajmi nevyvolá.

**Čo sme z logov skutočne zistili:**

- po zmene kedy porucha nastala a ktorá operácia nad ktorou rezerváciou
  zlyhala,
- typ príčiny zo správy databázy. `AdminShutdown` znamená, že aplikácia
  použila spojenie z poolu, ktoré server ukončil pri vypínaní. O tom, či
  databáza beží teraz, nehovorí nič. `Connection refused` znamená, že pri
  novom spojení nikto nepočúva. Pred zmenou bol v P3a aj P3b iba
  `AdminShutdown`. `Connection refused` sa ukázal až po zmene, lebo
  `pool_pre_ping` mŕtve spojenie zahodí a skúsi nové,
- v P3b sa `AdminShutdown` objavil v čase, keď databáza už znova prijímala
  spojenia (skript to overil), a druhá požiadavka prešla. Odtiaľ nález
  **F-04**: chyba je v poole aplikácie, nie v databáze,
- kedy sa aplikácia zastavila a znova naštartovala, v akej verzii a nad akou
  databázou. Zastavenie bolo čisté (`Application shutdown complete`).

**Čo sme museli overiť inak:**

- **stav dát:** log pri P3a hovorí iba `500`. Či sa `B3` zapísala, ukázal až
  SQL dotaz (`DRAFT`) a až potom bolo opakovanie bezpečné. Rovnako to, že
  `B1` prežila výpadok aj reštart, a invariant BR-02,
- **že aplikácia po reštarte vidí dáta:** dostupnosť cez API spolu so SQL,
- **že `/health` počas výpadku klame:** hlásil `200 ok`, kým potvrdenie
  skončilo `500` (issue I-04),
- **návratový kód procesu** pri zastavení: z procesu, nie z logu.

### Návrh pred behom (Expected)

*Expected* je zapísané **pred prvým behom** (tento commit). Výsledok a stav
vychádzajú zo špecifikácie v0.4. Kde špecifikácia mlčí (technická porucha,
reštart), je Expected **prevádzkové očakávanie tímu** a tak je aj označené.

**Spoločné pre všetky situácie:**

- **Rozhranie:** skutočné HTTP API (`uvicorn`) nad skutočným PostgreSQL 16,
  žiadny mock. Aplikácia sa spúšťa bez vlastnej konfigurácie logovania, tak
  ako v README.
- **Oracle stavu:** SQL novým spojením. BR-02 overuje vlastný dotaz skriptu
  (dvojice blokujúcich rezervácií, ktoré sa prekrývajú podľa BR-01), rovnaký
  ako v C08.
- **Dáta:** prístroj `R1` je aktívny a **bez** schvaľovania, takže potvrdenie
  vedie priamo do `CONFIRMED` (REQ-10). Používatelia `S1` a `S2` sú `STUDENT`
  s platným certifikátom. Prístroj, používatelia a certifikáty sa vkladajú
  SQL (TBD-02). Rezervácie zakladá API (OP-01), nie SQL.
- **Čas:** deň **D = dátum behu + 7 dní**, UTC, intervaly `[start, end)`.

| ID | Situácia a kroky | Expected — výsledok | Expected — stav (SQL, nové spojenie) | Čo má ukázať log |
| -- | ---------------- | ------------------- | ------------------------------------ | ---------------- |
| **P1** | **Úspešné potvrdenie.** `S1` vytvorí `B1` `R1 [D 10:00, D 11:00)` a potvrdí ju. | `201 DRAFT`, potom `200 CONFIRMED` (REQ-01, REQ-04, REQ-10) | `B1` `CONFIRMED`, interval bez zmeny; žiadna blokujúca dvojica (BR-02) | nič navyše — úspech dokladá odpoveď a stav, log nie je povinný |
| **P2** | **Zamietnutá kolízia.** `S2` vytvorí `B2` `R1 [D 10:30, D 11:30)` — vytvorenie prekryv nevyhodnocuje (REQ-02) — a potvrdí ju. | `201 DRAFT`, potom `409 OVERLAP` (REQ-04) | `B1` `CONFIRMED` bez zmeny, `B2` ostáva `DRAFT` (REQ-05), žiadna blokujúca dvojica | rozhodnúť, či treba záznam — doménové zamietnutie **nie je** technická chyba |
| **P3a** | **Výpadok databázy počas operácie.** `S1` vytvorí `B3` `R1 [D 12:00, D 13:00)`; `docker stop` databázy; potvrdenie `B3`; `GET /health`; `docker start`. | potvrdenie skončí **technickou chybou 5xx**, nie úspechom ani doménovým `409`. Klient z odpovede nevie, či sa zápis vykonal. `/health` špecifikácia nedefinuje — iba zaznamenáme. | po obnove, **pred akýmkoľvek opakovaním**: `B3` `DRAFT`, `B1` `CONFIRMED` (dáta výpadok prežili). Až potom jedno opakovanie potvrdenia → `200 CONFIRMED` **bez reštartu aplikácie**, žiadna blokujúca dvojica. | **jeden záznam** s časom, operáciou (metóda + cesta), typom problému a výsledkom (5xx) |
| **P3b** | **Reštart databázy, keď aplikácia nič nerobí.** `docker restart` databázy; potom dve čítania dostupnosti `R1 [D 10:00, D 11:00)`. | *prevádzkové očakávanie:* už **prvá** požiadavka po obnove uspeje — `200 UNAVAILABLE`, konflikt `B1` | `B1` `CONFIRMED` | ak požiadavka zlyhá, rovnaký záznam ako pri P3a |
| **P4** | **Reštart aplikácie.** `SIGINT` (ako Ctrl+C), nový štart; `GET /health`; dostupnosť `R1 [D 10:00, D 11:00)`; `S2` vytvorí a potvrdí `B4` `R1 [D 14:00, D 15:00)`. | ukončenie s kódom `0` do 10 s; po štarte `/health` `200`; dostupnosť `UNAVAILABLE` s `B1`; `B4` `201` → `200 CONFIRMED` | `B1`, `B3` `CONFIRMED` — stav je v databáze, nie v aplikácii; `B4` `CONFIRMED`; žiadna blokujúca dvojica | kedy sa aplikácia zastavila a kedy znova bežala, ideálne aj ktorá verzia |

**Pravidlá behu:**

- **Neopakovať zápis naslepo.** Pri P3a klient dostane 5xx a nevie, či sa
  potvrdenie zapísalo. Skript preto najprv prečíta stav `B3` z databázy
  a potvrdenie zopakuje **iba vtedy**, ak je `B3` stále `DRAFT`. Inak ho
  neopakuje a situácia je `FAIL`.
- **Verdikt `PASS` / `FAIL` sa týka výsledku a stavu.** Užitočnosť logu sa
  posudzuje zvlášť (riadky `LOG` vo výpise), lebo log nie je dôkaz stavu.
- **`BLOCKED`**, ak nebeží Docker, ak kontajner s rovnakým menom nepatrí
  skriptu alebo ak aplikácia nenaštartuje. Vtedy skript vypíše príčinu
  a ďalší krok.

**Hypotéza o logoch pred behom.** Aplikácia nemá vlastné logovanie
(`grep logging src/` nič nenájde). Vypisuje teda iba `uvicorn` so svojou
predvolenou konfiguráciou. Očakávame prístupové riadky a traceback chyby,
**bez času**. Ak sa to potvrdí, chýba „kedy" — doplníme minimálny záznam
a pokus zopakujeme.

*Doplnené po review — návrh vyššie je od `3bd6670` bez zmeny
(`git diff 3bd6670 -- docs/evidence-and-evolution.md` mení iba iné časti).*
Prevádzkovým očakávaním tímu sú aj P3a (5xx) a P4 (kód `0` do 10 s).
Špecifikácia ich nedefinuje, v návrhu bolo takto označené iba P3b. Že
Expected nebolo prispôsobené výsledku, dokladá aj to, že P3b bolo
predpovedané zle (beh 3: `FAIL`). Názov P3a hovorí „počas operácie", ale
databáza bola zastavená tesne **pred** potvrdením.

### Vykonanie na pôvodnom kóde (Actual, beh 3)

**Testovaný kód:** `8200655` — kód `c09-release` (RC 0.4.0), skript z commitu
`e603b38`. 2026-11-22 17:00 UTC, macOS 26.6.2 arm64, Python 3.12.11 (venv
z `requirements-dev.lock`), Docker 28.3.2, PostgreSQL 16.15.
Príkaz: `python scripts/c10_provoz.py` → návratový kód `1`.

| Beh | Commit skriptu | Čo sa stalo |
| --- | -------------- | ----------- |
| 1 | `ab3dc68` | skript spadol pred P1: `fetchall()` po `INSERT` — chyba skriptu |
| 2 | `ab3dc68` + necommitnutá oprava `fetchall()` | P1 – P3a prebehli, v P3b skript spadol na vlastnej chybe (`has()` — kolízia mena `status`). **Pozorovanie:** `GET /health` hneď po `500` skončil `Connection reset by peer`. Uvicorn po neošetrenej výnimke spojenie zatvorí a klient poslal ďalšiu požiadavku tým istým keep-alive spojením. Odvtedy skript posiela každú požiadavku novým spojením (`e603b38`). |
| 3 | `e603b38` | úplný beh — výsledky nižšie |

| ID | Actual — výsledok | Actual — stav (SQL) | Verdikt |
| -- | ----------------- | ------------------- | ------- |
| P1 | `201 DRAFT` → `200 CONFIRMED` | `B1` `CONFIRMED`, interval bez zmeny, žiadna blokujúca dvojica | **PASS** |
| P2 | `201 DRAFT` → `409 {"code":"OVERLAP", …}` | `B1` bez zmeny, `B2` `DRAFT`, žiadna blokujúca dvojica | **PASS** |
| P3a | počas výpadku `500 Internal Server Error` (text, bez detailov), `/health` počas výpadku `200 {"status":"ok"}`; po `docker start` jedno opakovanie → `200 CONFIRMED` bez reštartu aplikácie | pred opakovaním `B3` `DRAFT`, `B1` `CONFIRMED`; po opakovaní `B3` `CONFIRMED`, žiadna blokujúca dvojica | **PASS** |
| P3b | po `docker restart` **prvá** požiadavka `500`, druhá `200 UNAVAILABLE [B1]` | `B1` `CONFIRMED` | **FAIL** |
| P4 | `SIGINT` → kód `0` do 0,35 s (rozdiel časov krokov vo výpise); po štarte `/health` `200`, dostupnosť `UNAVAILABLE [B1]`, `B4` `201` → `200 CONFIRMED` | `B1`, `B3`, `B4` `CONFIRMED`, žiadna blokujúca dvojica | **PASS** |

**Čo ukázal log** (skript nahrádza UUID menami, rámce tracebacku iba počíta).
Pri P3a:

```
INFO:     127.0.0.1:60528 - "POST /reservations/B3/confirm HTTP/1.1" 500 Internal Server Error
ERROR:    Exception in ASGI application
Traceback (most recent call last):
  ... 6 riadkov rámcov tracebacku
psycopg.errors.AdminShutdown: terminating connection due to administrator command

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ... 113 riadkov rámcov tracebacku
sqlalchemy.exc.OperationalError: (psycopg.errors.AdminShutdown) terminating connection due to administrator command
[SQL: SELECT reservations.id, … FROM reservations WHERE reservations.id = %(pk_1)s::UUID FOR UPDATE]
[parameters: {'pk_1': UUID('B3')}]
INFO:     127.0.0.1:60529 - "GET /health HTTP/1.1" 200 OK
```

Pri P4 (zastavenie a nový štart):

```
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [29749]
INFO:     Started server process [29754]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8010 (Press CTRL+C to quit)
```

Pri P1 a P2 sú v logu iba prístupové riadky (`… /confirm HTTP/1.1" 200 OK`,
`… 409 Conflict`). Kód `OVERLAP` v logu nie je.

**Zistenia:**

| # | Zistenie | Dôsledok |
| - | -------- | -------- |
| L-1 | **Žiadny riadok nemá čas.** „Kedy" sa dalo určiť iba z hodín skriptu, nie z logu. | Hypotéza sa potvrdila — chýba podstatná informácia. |
| L-2 | Operácia (cesta v prístupovom riadku) a typ problému (traceback) sú **dva samostatné záznamy**. Spája ich iba poradie. Pri súbežných požiadavkách by sa nedalo povedať, ktorá chyba patrí ku ktorej požiadavke. Jedna porucha = 132 riadkov. | Chýba jeden záznam s časom, operáciou, typom a výsledkom. |
| L-3 | Traceback obsahuje **parametre SQL**. Tu je to UUID rezervácie, pri iných príkazoch aj UUID používateľa (`requested_by`), teda identifikátor osoby. | Do pridaného záznamu nesmie ísť. |
| L-4 | Zastavenie a štart sú viditeľné, ale **bez času, bez verzie aplikácie** a bez cieľovej databázy. | Z logu sa nedá povedať, ktorá verzia kedy bežala. |
| F-04 | **P3b: po reštarte databázy prvá požiadavka skončí `500`.** Pool vydal spojenie, ktoré databáza pri vypnutí ukončila (`AdminShutdown`). SQLAlchemy to zistí až pri použití, vtedy celý pool zneplatní a druhá požiadavka už prejde. Aj `500` v P3a bolo také mŕtve spojenie, nie `Connection refused`. | Po každom reštarte databázy dostane prvý používateľ chybu. Pri potvrdení nevie, či sa zapísalo — zlyhal síce prvý príkaz (`SELECT … FOR UPDATE`), ale to vidí iba log. |
| O-1 | `/health` počas výpadku databázy hlási `200 ok`. Hovorí iba „proces beží", nie „vie obslúžiť požiadavku". | Monitoring nad `/health` by výpadok nevidel. Zmena kontraktu `/health` nie je v rozsahu C10 — otvorené. |
| O-2 | Po neošetrenej výnimke uvicorn zatvorí spojenie (beh 2). Odpoveď `500` už bola odoslaná, preto ho zatvorí bez `Connection: close`. | Klient, ktorý spojenie použije znova, dostane reset. *Doplnené po review:* po zmene sa to týka iba iných neošetrených výnimiek — chyba databázy je ošetrená. Skript posiela každú požiadavku novým spojením, takže tento jav ďalej nemeria. |

**Rozhodnutie o logovaní doménového zamietnutia (P2): nelogovať.**
`409 OVERLAP` je bežný, špecifikovaný výsledok (REQ-04), nie porucha. Klient
dostane kód aj dôvod v odpovedi a stav je v databáze. Záznam by teda nič
nedokazoval a ničomu by nepomohol pri diagnostike. Prístupový riadok so
`409` existuje, ale je to iba hrubý obraz: `409` vracia sedem rôznych kódov
a riadok nemá čas. Na počty podľa kódu nestačí, dnes ich však nikto
nepotrebuje. Audit („kto sa kedy pokúsil") špecifikácia nepožaduje. Keby ho
bolo treba, patrí do databázy, nie do logu. Zmenili by sme to, keby podpora
potrebovala odpovedať „prečo mi to neprešlo" alebo by sa merala vyťaženosť
prístrojov. Vtedy INFO s časom, ID rezervácie a kódom, bez osoby.

**Rozhodnutie o zmene (pred kódom):**

1. **Jeden záznam pre technickú poruchu databázy:** ERROR s časom v UTC,
   operáciou (metóda + cesta), triedou chyby so správou databázy
   a s výsledkom `500`. Traceback ostane pripojený, ale **bez parametrov SQL**
   (`hide_parameters`, L-3). Odpoveď klientovi sa nemení — `500`, text
   `Internal Server Error`, bez detailov.
2. **Štart a zastavenie:** INFO s časom, verziou a cieľovou databázou
   (`host:port/meno`, bez používateľa a hesla). Zadanie hovorí o jednom
   minimálnom zázname. Druhý druh pribudol, lebo pri reštarte chýbal čas aj
   verzia (L-4).
3. **F-04 — mimo mandátu C10, zaradené vedome.** Zadanie žiada doplniť
   záznam, nie opravovať správanie. Pool spojenie pred vydaním overí
   (`pool_pre_ping`) a mŕtve nahradí. **Je to nové správanie:** stojí jeden
   krátky dotaz pri každom vydaní spojenia a mení aj to, ako zlyhá
   požiadavka počas výpadku (po zmene `Connection refused` namiesto
   `AdminShutdown`). Zaradili sme ho preto, že Expected P3b bolo zapísané
   pred behom, oprava je jeden parameter bez zmeny rozhrania a overí ju test
   aj mutácia. Na rozdiel od F-01 a F-02 z C07 nejde o nezhodu so
   špecifikáciou, ale s prevádzkovým očakávaním tímu. Obhájiteľná bola aj
   alternatíva zapísať F-04 iba ako issue. Ak ju review uprednostní, oprava
   sa vráti odstránením jedného riadku v `db.py` — presne to robí mutácia
   `F04`. Kontrakt `/health` (I-04) naopak bez rozhodnutia nemeníme, lebo
   to je zmena rozhrania.
4. **Verzia 0.4.1:** kód sa mení, takže wheel už nie je RC 0.4.0
   z `c09-release`. Patch verzia zabezpečí, aby názov artefaktu neklamal.
   O vydaní sa v C10 nerozhoduje.

### Vykonanie po zmene (Actual, beh 4)

**Testovaný kód:** `57e63fa` (verzia 0.4.1), skript bez zmeny od `e603b38`,
2026-11-22 17:06 UTC, rovnaké prostredie ako beh 3. Návratový kód `0`.
Celý `scripts/ci_check.sh` na `57e63fa`: `315 passed` (aj pri
`PGTZ=Europe/Prague`), 49 mutácií, žiadna neprežila (nové `F04`, `L1` až
`L3` zachytí vždy práve test, ktorý ich má chrániť; po review premenované
a doplnené, pozri nižšie), `TC-C08-01..03` PASS,
wheel `swilab-0.4.1-py3-none-any.whl` `951a5e997e59ade9ac7c617543b1203aac0d395aa61790c2d502b116b9be8b10`
(macOS arm64 — Linux v CI sa overí v PR).

Skrytie parametrov SQL beh 4 nedokladá: porucha v P3a nastala pri
pripájaní a v tracebacku žiadny príkaz SQL nie je. Dokladá ho test
`test_a_database_error_does_not_carry_sql_parameters` a mutácia `L3` (pred
premenovaním po review `L1`).
Traceback po zmene je dlhší (152 riadkov namiesto 132), lebo `pool_pre_ping`
pridal reťazenú výnimku. Súhrnný riadok však stojí sám. Ak by šum vadil,
traceback sa dá presunúť na úroveň DEBUG.

Testy pred kódom: `5a9f882` → `4 failed, 1 passed`. Každý padol z dôvodu,
ktorý mal zachytiť: neošetrená `OperationalError`, parameter v texte chyby,
chýbajúci záznam o štarte a pri F-04 presne ten `AdminShutdown` ako v P3b.

### Nezávislá kontrola a beh 5

Dve nezávislé kontroly (subagenti): jedna proti zadaniu a podkladom
(iba čítanie), druhá nad kódom, testami a skriptom (behy a vložené chyby
v kópiách mimo repozitára). Nálezy a opravy:

| # | Nález | Oprava |
| - | ----- | ------ |
| RV-C10-1 | Výklad `AdminShutdown` v odpovedi bol chybný — tvrdil, že databáza bežala ďalej. V P3a bola zastavená. | prepísané v zázname; rozlíšenie oboch príčin ukázal až beh 4 |
| RV-C10-2 | „Nelogovať OVERLAP" stálo aj na argumente o počtoch. `409` však vracia sedem kódov a riadok nemá čas. | zdôvodnenie prepísané |
| RV-C10-3 | F-04 je nové správanie mimo mandátu C10. Text to zakrýval („nie nové správanie"). | priznané, alternatíva (iba issue) uvedená |
| RV-C10-4 | Záznam nebol „krátky" — tabuľka až za 170 riadkami podkladov. | záznam presunutý na začiatok sekcie, bunky skrátené |
| RV-C10-5 | Skript pri `BLOCKED` kvôli cudziemu kontajneru ten kontajner vo `finally` zmazal (overené behom). | maže iba kontajner, ktorý sám vytvoril (`fed795d`). Overené: `BLOCKED`, kód `2`, cudzí kontajner ostal. |
| RV-C10-6 | Handler chytal iba `DBAPIError`. `TimeoutError` vyčerpaného poolu a `PendingRollbackError` ostali bez záznamu — práve pod záťažou. | `SQLAlchemyError`, test pre päť druhov poruchy, mutácia `L2b` |
| RV-C10-7 | Čas v UTC nestrážil žiadny test. Bez prevodu bolo pri `TZ=Europe/Prague` všetko zelené a čas o hodinu zlý — rovnaký druh chyby ako F-01. | test s `TZ=Europe/Prague`, mutácia `L1` |
| RV-C10-8 | Drobné: bez čísla procesu sú záznamy pri `--workers` nerozlíšiteľné; regex verzie v skripte zachytil `127.0.0.1`; skript pri chybe padol bez súhrnu a `BLOCKED` v P4 vypísal `PASS`; slabé `"500" in`; riadiaci znak z cesty by prešiel do záznamu; mutácie pomenované inak než zistenia; 120 namiesto 132 riadkov; telo `500` sa nekontrolovalo; identita behu nesledovala `scripts/`. | `[%(process)d]` vo formáte; verzia hľadaná za menom `swilab`; `NEDOKONCENA` a kód `3`; `-> 500:`; cesta cez `quote()` (nový riadok odstráni už Starlette); mutácie `L1`–`L4` podľa zistení; čísla opravené; kontrola tela; `scripts/` v identite |
| ponechané | `hide_parameters` neskryje hodnotu v `DETAIL`; duplicity pri `--log-config` s root handlerom. | zapísané v `logs.py` a v „Čo C10 nepokrýva" |

**Testy pred kódom:** `962eabd` → `4 failed, 8 passed`. Padli `TimeoutError`,
`PendingRollbackError`, číslo procesu a riadiaci znak. Test času v UTC prešiel
už pred zmenou — stráži správanie, ktoré kód mal. Jeho silu dokladá mutácia
`L1`.

**Beh 5** — kód [`ea55a6f`](https://github.com/thspowered/SWI-LAB/commit/ea55a6f),
2026-11-22 17:30 UTC, Docker 28.3.2 (skript ho od review vypisuje). P1 – P4
**PASS**, návratový kód `0`. Telo `500` je `Internal Server Error` bez
detailov. Záznam poruchy má 152 riadkov s tracebackom, v `app.log` nie je
ani jeden výskyt `parameters`. `SIGINT` → nový štart do 0,34 s.

**`scripts/ci_check.sh` na `ea55a6f`:** `322 passed` (aj pri
`PGTZ=Europe/Prague`), **51 mutácií, žiadna neprežila** (C10: `F04`, `L1`,
`L2`, `L2b`, `L3`, `L4` — každú zachytí test, ktorý ju má chrániť),
`TC-C08-01..03` PASS, wheel `swilab-0.4.1-py3-none-any.whl`
`71239070d792617b1fb4f88064956bad88c09d3dc392b1b74b786f8b7bdf345b` (macOS
arm64). **Rovnaký hash v CI** PR #17
([run 45050383758](https://github.com/thspowered/SWI-LAB/actions/runs/45050383758),
Linux x86_64, Python 3.12.16, `322 passed`, 51 mutácií, 0 prežilo,
`VSETKY KROKY PRESLI`).

### Čo C10 nepokrýva

- **databáza, ktorá neodpovedá** (zaseknutá, sieť zahadzuje pakety) — aplikácia
  nemá `connect_timeout` ani `statement_timeout`, požiadavka by čakala na
  časový limit TCP. Netestované,
- **databáza nedostupná pri štarte aplikácie** — aplikácia nenaštartuje
  (`create_all` zlyhá), záznam `start:` chýba a ostane iba traceback
  uvicornu. Netestované,
- **zlyhanie uprostred transakcie** — vyvolané bolo iba zlyhanie prvého
  príkazu (`SELECT … FOR UPDATE`, P3a pred zmenou) a pripojenia (po zmene).
  Výpadok medzi `COMMIT` a odpoveďou dáva skutočne neznámy výsledok
  a deterministicky sa vyvolať nedá. Platí pravidlo „najprv prečítať stav,
  až potom opakovať",
- **iné neošetrené výnimky** (chyba v kóde) — stále po starom: dva záznamy
  bez času a uvicorn zatvorí spojenie (O-2). Poruchy SQLAlchemy vrátane
  `TimeoutError` vyčerpaného poolu handler od review zachytí, vyvolaná bola
  ale iba podvrhnutím v teste, nie skutočnou záťažou,
- **hodnoty v správe databázy** — `hide_parameters` skryje parametre SQL,
  nie hodnotu, ktorú databáza sama vypíše (`DETAIL: Key (email)=(…) already
  exists`, `Failing row contains (…)`, overené v review). Cez dnešné API
  sa to dá vyvolať iba chybou v kóde,
- **vlastná konfigurácia logovania** (`uvicorn --log-config` s handlerom na
  root) — záznamy by boli dvakrát. Postup je v `logs.py`,
- **nečisté zastavenie** (`SIGKILL`, pád procesu) — testovaný bol iba
  `SIGINT`. Záznam `stop:` by chýbal, čo je samo osebe stopa,
- **súbežné poruchy** — tvrdenie L-2 o priraďovaní záznamov pri súbehu je
  úvaha, netestovaná,
- **`uvicorn --reload`** z README — skript spúšťa aplikáciu bez neho,
- uchovávanie a rotácia logov — v tejto fáze idú na `stderr` procesu, N/A.

### Identita artefaktu

Wheel 0.4.1 nie je kandidát na vydanie. RC 0.4.0 ostáva identifikovaný tagom
`c09-release` a hashom `cf090f0b…626ef` (README, C09).

## I-02 — Meranie serializácie na prístroj (ADR-01)

Issue **I-02** (C07): „Serializácia na prístroj (ADR-01) nie je zmeraná."
Opatrenie znelo: do 2026-11-09 rozhodnúť a zapísať pri ADR-01 — buď merateľné
kritérium a meranie, alebo tvrdenie o škálovaní stiahnuť. **Termín sme
nedodržali**, meranie je z 2026-11-22, 13 dní po termíne.

Volíme meranie. Časť vzniká v troch commitoch: návrh a kritériá **pred**
prvým meraním (tento commit), skript a výsledky s rozhodnutím. **Kód
aplikácie sa v I-02 nemení.** Ak meranie ukáže potrebu zmeny, je to podklad
pre Change Request, nie súčasť merania.

### Čo ADR-01 tvrdí a čo treba overiť

ADR-01, prijatý negatívny dôsledok 2: *zámok nad riadkom prístroja
serializuje všetky potvrdenia a schválenia pre ten istý prístroj; pri
desaťnásobku súbežných rezervácií (future pressure z C01) je to bod, ktorý
treba merať.* E2 k tomu dodáva, že rôzne prístroje bežia súbežne ďalej
a vytváranie `DRAFT` zámok neblokuje. Druhé tvrdenie už stráži test (zámok
`FOR KEY SHARE` počas držaného zámku strážcu, `test_lifecycle_contracts.py`).
Prvé nemá žiadne číslo. ADR-01 sa má znovu otvoriť, keď *meranie ukáže, že
serializácia na prístroj je úzke hrdlo*.

Meriame tri veci:

1. či BR-02 drží aj pri desaťnásobnom súbehu — testy ho overujú pri dvoch
   súčasných operáciách,
2. koľko stojí čakanie na zámok pri desaťnásobnom súbehu na jeden prístroj,
3. či horúci prístroj spomalí potvrdenia na iných prístrojoch.

### Hypotézy z čítania kódu (pred meraním)

- **H-1 — kritická sekcia môže rásť s objemom celej tabuľky.** Zámok sa drží
  od `_lock_instrument()` po commit: dotaz na prekryv, `UPDATE` a `COMMIT`.
  Tabuľka `reservations` nemá index na `instrument_id`, iba primárny kľúč.
  Dotaz na prekryv teda pravdepodobne číta celú tabuľku vrátane histórie
  všetkých prístrojov a čas držania zámku by rástol s počtom všetkých
  rezervácií, nie s vyťažením prístroja. Plán dotazu skript zaznamená
  (`EXPLAIN`).
- **H-2 — čakanie na zámok drží spojenie z poolu.** Pool má predvolených
  5 + 10 spojení a na voľné čaká najviac 30 s. Synchrónne endpointy bežia
  v threadpoole s predvolenými 40 vláknami. Požiadavka čakajúca na zámok
  prístroja drží spojenie, takže pri viac ako 15 súbežných požiadavkách na
  jeden prístroj by mohli čakať aj požiadavky na iné prístroje — nie na
  zámok, ale na spojenie.
- **H-3 — pri dnešnom objeme je kritická sekcia krátka** a serializácia nie
  je úzke hrdlo. Toto má meranie potvrdiť alebo vyvrátiť číslom.

### Model záťaže (predpoklad tímu)

Systém nemá prevádzku, z ktorej by sa dala záťaž odčítať. Hodnoty sú
**predpoklad tímu** — rovnako ako 48 h v CR-08 — a dajú sa napadnúť. Preto
meriame aj krivky E-1 a E-2, z ktorých sa výsledok dá odčítať pre iný
predpoklad.

| Veličina | dnes | 10× (future pressure C01) |
| -------- | ---- | ------------------------- |
| súbežné potvrdenia na jeden prístroj v špičke (zverejnenie termínov) | 5 | 50 |
| riadkov v tabuľke `reservations` | 10 000 | 100 000 |

10 000 riadkov ≈ rok prevádzky: 20 prístrojov × 2 rezervácie denne × 250
pracovných dní. Future pressure hovorí o súbežnosti. Objem násobíme tiež,
lebo viac súbežných rezervácií znamená aj viac uložených.

### Kritériá — rozhodujú o I-02

Všetky pri modeli 10×: 50 súbežných požiadaviek, 100 000 riadkov.

| ID | Scenár | PASS, ak |
| -- | ------ | -------- |
| **K-1** | **Kolízia pod záťažou.** 50 používateľov naraz potvrdí 50 `DRAFT` s **rovnakým** intervalom na jednom prístroji. | v každom opakovaní presne 1× `200 CONFIRMED` a 49× `409 OVERLAP`; žiadna iná odpoveď (5xx, timeout, bez odpovede); SQL: jedna blokujúca rezervácia, BR-02 drží |
| **K-2** | **Serializácia na jeden prístroj.** 50 používateľov naraz potvrdí 50 `DRAFT` s **rôznymi**, nekolidujúcimi intervalmi na jednom prístroji. Každé potvrdenie uspeje a commitne pod zámkom — najhorší prípad držania zámku. | všetky `200 CONFIRMED`; **p95 času odozvy ≤ 1 s** |
| **K-3** | **Sused horúceho prístroja.** Spolu s K-2 (50 potvrdení na prístroji A) potvrdí 5 ďalších používateľov po jednej rezervácii na 5 iných prístrojoch, uvoľnení tou istou bariérou. | všetky `200`; **p95 času odozvy týchto 5 požiadaviek ≤ 1 s** |

**Prečo 1 s:** hranica, do ktorej používateľ vníma odozvu interaktívnej
operácie ako plynulú a nečaká vedome (Nielsen, *Usability Engineering*,
1993: hranice 0,1 s / 1 s / 10 s). Potvrdenie rezervácie je interaktívna
operácia.

**Prečo p95, nie priemer:** pri serializácii čaká posledná požiadavka na
všetky pred ňou. Priemer by ju skryl.

**Rozhodovacie pravidlo** (zapísané pred meraním):

- **K-1 FAIL** → chyba správnosti pod záťažou: nový nález s vyššou prioritou
  než I-02.
- **K-1, K-2 aj K-3 PASS** → I-02 sa uzavrie: pri modeli 10× serializácia nie
  je úzke hrdlo. ADR-01 sa neotvára, dôsledok 2 dostane čísla a spúšťač
  „meranie ukáže úzke hrdlo" sa nahradí konkrétnou hranicou z E-1 a E-2.
- **K-2 alebo K-3 FAIL** → rozhodne porovnanie s rovnakým počtom požiadaviek
  na **rôzne** prístroje (E-1). Ak je p95 na jednom prístroji aspoň 2× horšie,
  úzke hrdlo je zámok a spúšťač ADR-01 je naplnený. Ak sú podobné, úzke hrdlo
  je inde (pool, threadpool, databáza): ADR-01 sa to netýka a vznikne issue
  pre nasadenie.

### Prieskum — bez PASS/FAIL

| ID | Čo | Prečo |
| -- | -- | ----- |
| **E-0** | model „dnes": scenáre K-1 a K-2 pri 5 súbežných požiadavkách a 10 000 riadkoch | porovnanie s 10× |
| **E-1** | krivka podľa súbehu: 1, 5, 10, 25, 50, 100, 200 požiadaviek na **jeden** prístroj (ako K-2) a na **rôzne** prístroje (každá na inom), 100 000 riadkov | kde sa p95 dostane nad 1 s a či rastie rýchlejšie pre jeden prístroj (zámok) alebo pre oba rovnako (pool, H-2) |
| **E-2** | krivka podľa objemu: 0, 10 000, 100 000 a 1 000 000 riadkov, 50 požiadaviek na jeden prístroj | H-1: rastie čas s objemom celej tabuľky? |
| **E-3** | ako E-2 pri 100 000 a 1 000 000 riadkoch, ale s indexom na `reservations(instrument_id)`, vytvoreným **iba v izolovanej databáze merania** | ak H-1 platí, či ho index odstráni — podklad pre prípadný CR, nie zmena kódu |

### Metóda

- **Inštancia:** ako v C10 — vlastný kontajner `swilab-i02-db` (PostgreSQL 16,
  port 5435) a aplikácia `uvicorn` na porte 8020. Jeden proces, bez vlastnej
  konfigurácie, predvolený pool aj threadpool. Vývojová databáza `swilab-db`
  sa nemení.
- **Meraná operácia:** iba potvrdenie (OP-03) cez HTTP. Prístroje sú bez
  schvaľovania, potvrdenie vedie do `CONFIRMED`. Prístroje, používatelia,
  certifikáty, `DRAFT` rezervácie aj objem na pozadí vkladá SQL — ich vznik
  sa nemeria.
- **Objem na pozadí:** rezervácie `CONFIRMED` na 20 ďalších prístrojoch,
  v minulosti, bez prekryvov (každá v inej hodine). Po každom vložení
  `ANALYZE`.
- **Súbeh:** N vlákien klienta, každé pošle jednu požiadavku novým spojením
  (ako C10). Všetky uvoľní jedna bariéra naraz. Dávka v jednom okamihu je
  najhorší prípad.
- **Čas odozvy:** od odoslania požiadavky po prijatie celej odpovede, meraný
  klientom.
- **Opakovania:** každý bod má 1 zahrievací beh, ktorý sa zahodí, a 5
  meraných behov. p50, p95 a max sa počítajú zo všetkých požiadaviek piatich
  behov. Medzi behmi sa rezervácie merania zmažú (`DELETE` a `VACUUM`), objem
  na pozadí ostáva.
- **Stav:** po každom behu SQL novým spojením — počty stavov a BR-02
  rovnakým dotazom ako v C08 a C10.
- **Zaznamená sa:** commit, verzie Pythonu, Dockera a PostgreSQL, procesor;
  pri každom objeme plán dotazu na prekryv (`EXPLAIN`).

### Známe hrozby platnosti

- klient aj server bežia na tom istom stroji a delia sa o procesor; pri 200
  vláknach môže čas pridávať aj klient — bod N = 1 ukazuje réžiu,
- databáza beží v Docker Desktop, teda vo virtuálnom stroji na macOS; `fsync`
  pri commite sa správa inak než na serveri,
- jeden proces uvicornu s predvolenými limitmi — nasadenie s `--workers` by
  malo viac spojení aj vlákien,
- dávka v jednom okamihu je umelý najhorší prípad, skutočné požiadavky
  prichádzajú rozložene,
- schválenie (`approve`) prechádza tým istým zámkom, meriame však iba
  potvrdenie.

### Vykonanie

**Testovaný kód:** [`ea55a6f`](https://github.com/thspowered/SWI-LAB/commit/ea55a6f)
— aplikácia 0.4.1, kód `main` po PR #17. Skript `scripts/i02_meranie.py`.
2026-11-22, macOS 26.6.2 arm64 (Apple M5 Pro, 15 jadier), Python 3.12.13
(čisté prostredie z `requirements-dev.lock`), Docker 29.5.3, PostgreSQL 16.13.
Jeden úplný beh trvá asi minútu.

| Beh | Commit skriptu | Čo sa stalo |
| --- | -------------- | ----------- |
| 1 | `e4132a8` | úplný beh, návratový kód `0`. Pri 100 000 a 1 000 000 riadkoch vypísal prázdny názov plánu — pod uzlom `Gather` je riadok `-> Parallel Seq Scan`, ktorý výpis nerozpoznal. Celý plán bol v `results.json`, čísla to neovplyvnilo. |
| 2 | `2dcbcb4` | opravený výpis, úplný beh, návratový kód `0`. **Hlavný beh** — čísla nižšie, beh 1 v zátvorke. |

**Kritériá** (model 10×: 50 požiadaviek, 100 000 riadkov):

| ID | Výsledok | Verdikt |
| -- | -------- | ------- |
| **K-1** | Všetkých 6 behov (zahrievací aj merané), v oboch úplných behoch: 1× `200 CONFIRMED`, 49× `409 OVERLAP`, žiadna iná odpoveď. SQL: 1× `CONFIRMED`, 49× `DRAFT`, 0 blokujúcich dvojíc. | **PASS** |
| **K-2** | 250 potvrdení, všetky `200 CONFIRMED`. p50 165 ms, **p95 276 ms** (275), max 296 ms. | **PASS** |
| **K-3** | 25 susedných potvrdení, všetky `200`. **p95 223 ms** (228). | **PASS** |

**E-0 — dnes** (5 požiadaviek, 10 000 riadkov): kolízia 1× `200` a 4× `409
OVERLAP`, p95 29 ms; jeden prístroj p95 27 ms.

**E-1 — krivka podľa súbehu** (100 000 riadkov), p95 v ms:

| Súbežných požiadaviek | 1 | 5 | 10 | 25 | 50 | 100 | 200 |
| --------------------- | - | - | -- | -- | -- | --- | --- |
| jeden prístroj | 18 | 39 | 67 | 148 | 276 | 523 | 1 416 (1 227) |
| rôzne prístroje | 21 | 24 | 38 | 73 | 138 | 259 | 478 |
| pomer | 0,9 | 1,6 | 1,8 | 2,0 | 2,0 | 2,0 | 3,0 |

**E-2 a E-3 — krivka podľa objemu** (50 požiadaviek na jeden prístroj).
„V rade" je trvanie dávky delené 50 — koľko pridá každá ďalšia požiadavka
v rade na zámok.

| Riadkov | Plán dotazu na prekryv | Dotaz | p95 | V rade | S indexom: p95 | S indexom: v rade |
| ------- | ---------------------- | ----- | --- | ------ | -------------- | ----------------- |
| 0 | `Seq Scan` | 0,01 ms | 144 ms | 2,9 ms | — | — |
| 10 000 | `Seq Scan` | 0,43 ms | 154 ms | 3,2 ms | — | — |
| 100 000 | `Parallel Seq Scan` | 3,4 ms | 276 ms | 5,7 ms | 135 ms (`Index Scan`, 0,02 ms) | 2,8 ms |
| 1 000 000 | `Parallel Seq Scan` | 16,7 ms | 1 004 ms (954) | 21,1 ms | 168 ms (`Index Scan`, 0,02 ms) | 3,1 ms |

V logu aplikácie nie je ani jeden záznam ERROR, iba štart a zastavenie.

### Čo meranie ukázalo

1. **BR-02 drží aj pri 50 súčasných kolidujúcich potvrdeniach.** Dvanásť
   dávok, zakaždým presne jedno potvrdenie a ostatné `OVERLAP`, žiadna
   technická chyba. Testy to doteraz overovali pri dvoch súčasných
   operáciách.
2. **Serializácia pri modeli 10× stojí dvojnásobok času, úzke hrdlo to nie
   je.** 50 potvrdení na jeden prístroj má p95 276 ms, rovnaký počet na rôzne
   prístroje 138 ms. Každá požiadavka v rade pridá asi 5,7 ms, prístroj teda
   zvládne asi 175 potvrdení za sekundu. Posledná z 50 čaká na zámok najviac
   asi 0,3 s. Pre R-06 to znamená, že rozhodnutie čakajúce na zámok sa môže
   posunúť o desatiny sekundy, pri lehote 48 h zanedbateľne.
3. **H-1 platí: čas pod zámkom rastie s objemom celej tabuľky, nie
   s vyťažením prístroja.** Dotaz na prekryv číta všetky rezervácie všetkých
   prístrojov (`Seq Scan`, od 100 000 riadkov paralelne). Pri prázdnej
   tabuľke trvá 0,01 ms, pri 100 000 riadkoch 3,4 ms, pri 1 000 000 riadkoch
   16,7 ms. Čas požiadavky v rade narastá zhruba o toľko isto: 2,9 → 5,7 →
   21,1 ms. Pri 1 000 000 riadkoch je p95 na hranici 1 s. **Index na
   `reservations(instrument_id)` túto závislosť odstráni** (E-3): pri
   1 000 000 riadkoch s indexom je p95 168 ms, takmer ako pri prázdnej
   tabuľke. Nie je to vlastnosť zámku z ADR-01, ale dotazu, ktorý sa pod ním
   vykonáva. *Doplnené v review (RV-I02-1):* platí, kým horúci prístroj nemá
   vlastnú históriu — ako v E-3. S históriou závislosť odstráni až index
   `(instrument_id, ends_at)`, pozri pokus A v časti *Review I-02*.
4. **Pri malom objeme zámok nie je to, čo obmedzuje.** Pri prázdnej tabuľke
   pridá požiadavka v rade 2,9 ms; 50 požiadaviek na rôzne prístroje, teda
   bez spoločného zámku, prejde tempom 2,6 ms na požiadavku. S indexom je
   jeden prístroj pri 100 000 riadkoch takmer taký rýchly ako rôzne prístroje
   (p95 135 vs. 138 ms). Strop tu dáva jeden proces aplikácie, nie zámok.
5. **H-2 čiastočne: sused horúceho prístroja na zámok nečaká, ale spomalí
   sa.** Päť potvrdení na iných prístrojoch má samostatne p95 24 ms, počas
   dávky na prístroj A 223 ms — viac než dávka 50 požiadaviek na rôzne
   prístroje (138 ms). Zodpovedá to H-2 (požiadavky čakajúce na zámok držia
   spojenia z poolu), meranie však nerozlíši, či čakali na spojenie alebo na
   procesor. Tvrdenie z E2 „rôzne prístroje bežia súbežne ďalej" platí pre
   zámok, nie pre celú aplikáciu. *Doplnené v review (RV-I02-2):* pokus B to
   oddelil — sused čaká na spojenie z poolu, nie na procesor ani na
   threadpool.
6. **Kde p95 prekročí 1 s:** pri 100 000 riadkoch medzi 100 a 200 súbežnými
   potvrdeniami na jeden prístroj (pri 5,7 ms v rade okolo 175). Pri 50
   súbežných okolo 1 000 000 riadkov. Rezerva voči modelu 10× je teda asi
   3,5× v súbehu a 10× v objeme.

### Rozhodnutie

Podľa pravidla zapísaného pred meraním: **K-1, K-2 aj K-3 PASS, I-02 sa
uzatvára.**

- **ADR-01 sa neotvára.** Pri modeli 10× serializácia na prístroj nie je úzke
  hrdlo. Dôsledok 2 dostal čísla a spúšťač „meranie ukáže úzke hrdlo"
  konkrétnu hranicu (doplnenie pod ADR-01 v `architecture-and-decisions.md`).
  Text ADR sa nemení.
- **Tvrdenie o škálovaní platí s rozsahom:** jeden proces aplikácie, stroj
  z merania, do asi 175 súbežných potvrdení na jeden prístroj pri 100 000
  riadkoch a do asi 1 000 000 riadkov pri 50 súbežných.
- **Nové riziko R-08** — dotaz na prekryv bez indexu (bod 3). Dnes ho
  neriešime, rezerva v objeme je desaťnásobná. Ošetrenie je pripravené
  a zmerané (E-3; *v review opravené na index `(instrument_id, ends_at)`,
  RV-I02-1*), ide však o zmenu schémy, teda Change Request. ADR-02 ho
  sťažuje: `create_all()` index do existujúcej tabuľky nepridá
  a `verify_schema()` indexy neporovnáva. Index pridaný iba do modelu by na
  existujúcej databáze ticho chýbal.

### Hrozby platnosti po meraní

- Pri 200 súbežných sa behy líšia o 15 % (p95 1 227 vs. 1 416 ms), pri
  1 000 000 riadkoch o 5 % (954 vs. 1 004 ms). Hranice v bode 6 sú odhad, nie
  presná hodnota. Pri modeli 10× sa behy líšia o menej ako 3 %.
- Meralo sa na jednom stroji (macOS, Docker Desktop). Iný stroj ani Linux
  sme nemerali; opakovanie je jeden príkaz, `python scripts/i02_meranie.py`.
  *Doplnené v review (RV-I02-3):* druhý stroj dal rovnaký verdikt, hranice
  okolo 140 súbežných a 850 000 riadkov; Linux ostáva nezmeraný.
- Tabuľka bola po `VACUUM` bez mŕtvych riadkov. V prevádzke by ich dotaz bez
  indexu čítal tiež.
- Meranie nie je súčasťou `ci_check.sh`: trvá minútu, potrebuje Docker a čas
  odozvy na zdieľanom stroji CI by nebol porovnateľný.

### Review I-02 — druhý stroj a dva doplnkové pokusy (T. Hrubý, 2026-11-22)

**Review.** PR #18 prešiel T. Hrubý proti issue I-02 a piatim bodom v popise
PR. Text plánu je od `4df292e` bez zmeny — `git diff 4df292e 1e81df4` mení
v pláne iba hlavičku registra a riadok I-02. Skript bol prečítaný pred
spustením: vytvára iba vlastný kontajner a odstraňuje iba ten, ktorý sám
vytvoril. Overené vlastným behom, nie iba čítaním.

**Druhý stroj — `python scripts/i02_meranie.py`** na `1e81df4` (kód
aplikácie `ea55a6f`), macOS 26.6.2 arm64 (Apple M4 Pro, 14 jadier), Python
3.12.11 (prostredie z `requirements-dev.lock`), Docker 28.3.2, PostgreSQL
16.15. Na stroji počas behov bežali aj iné, nesúvisiace kontajnery. Dva úplné
behy, 2026-11-22 20:18 a 20:19 UTC, oba s návratovým kódom `0`.

| | Stroj autora (beh 2) | Druhý stroj (beh 1 / beh 2) |
| - | -------------------- | --------------------------- |
| **K-1** | 1× `200`, 49× `409 OVERLAP` | rovnako vo všetkých 12 dávkach, BR-02 0 dvojíc — **PASS** |
| **K-2** p95 | 276 ms | 333 / 374 ms — **PASS** |
| **K-3** p95 suseda | 223 ms | 282 / 279 ms — **PASS** |
| E-1, 50 súbežných: jeden / rôzne prístroje | 276 / 138 ms (2,0) | 333 / 155 (2,2), 374 / 159 (2,3) |
| E-1, 200 súbežných na jeden prístroj | 1 416 ms | 1 606 / 1 584 ms |
| E-2, 1 000 000 riadkov bez indexu | 1 004 ms | 1 149 / 1 121 ms |
| E-3, 1 000 000 riadkov s indexom | 168 ms | 169 / 164 ms |

Verdikt je rovnaký, druhý stroj je o 10 – 35 % pomalší. Hranica 1 s tam leží
nižšie: okolo **140** súbežných potvrdení na jeden prístroj (lineárne medzi
100 a 200, priemer behov 634 → 1 595 ms) a okolo **850 000** riadkov pri 50
súbežných (lineárne medzi 100 000 a 1 000 000, 353 → 1 135 ms). Rezerva voči
modelu 10× je tam asi 2,8× v súbehu a 8,5× v objeme.

**Pokusy — `python scripts/i02_review.py`** na `67018ac`, ten istý stroj,
2026-11-22 20:27 UTC, návratový kód `0`. Skript používa dáta, dávky
a štatistiku zo `scripts/i02_meranie.py`. Vo všetkých dávkach boli všetky
potvrdenia `200 CONFIRMED` a BR-02 držalo.

*Pokus B — na čo čaká sused horúceho prístroja* (bod 5, otázka 4 v PR). K-3
pri 100 000 riadkoch. Aplikácia je tá istá, iba s iným poolom spojení
a threadpoolom (`scripts/i02_review_app.py`):

| Pool spojení / threadpool | p95 suseda | p95 prístroja A |
| ------------------------- | ---------- | --------------- |
| 5 + 10 / 40 (predvolené) | 258 ms (pri opakovaní 299) | 334 (362) ms |
| 5 + 10 / **200** | 268 ms | 332 ms |
| **70** / 40 | 141 ms | 331 ms |
| **70** / **200** | 117 ms | 371 ms |

Pre porovnanie: 50 požiadaviek na rôzne prístroje (E-1) tu má p95 154 –
159 ms. **H-2 platí.** Sused čaká na spojenie z poolu, ktoré držia
požiadavky čakajúce na zámok prístroja A. Väčší threadpool nepomôže, väčší
pool áno — sused potom klesne na úroveň dávky na rôzne prístroje. Zvyšok je
réžia jedného procesu, nie zámok. ADR-01 sa to netýka, je to parameter
nasadenia: pri predvolenom poole s 15 spojeniami sa prístroje začnú
ovplyvňovať, keď na jeden čaká viac ako asi 15 požiadaviek. Pri modeli 10× je
sused stále viac ako 3× pod hranicou, nová akcia z toho nevzniká.

*Pokus A — horúci prístroj s vlastnou históriou* (R-08, otázka 3 v PR).
V meraní leží celé pozadie na 20 iných prístrojoch a prístroj A históriu
nemá. Rezervácia však ostáva `CONFIRMED` aj po skončení — stav pre dokončenú
rezerváciu systém nemá — takže história prístroja rastie spolu s tabuľkou.
Tu je A jedným z 20 prístrojov pozadia, má teda 1/20 objemu. 50 súbežných
potvrdení na A ako K-2:

| Riadkov (z toho A) | Index | Plán dotazu na prekryv | Dotaz | p95 |
| ------------------ | ----- | ---------------------- | ----- | --- |
| 100 000 (5 000) | žiadny | `Parallel Seq Scan` | 4,0 ms | 315 ms |
| | `(instrument_id)` | `Bitmap Heap Scan` | 1,3 ms | 187 ms |
| | `(instrument_id, ends_at)` | `Index Scan` | 0,03 ms | 154 ms |
| 1 000 000 (50 000) | žiadny | `Parallel Seq Scan` | 20,1 ms | 1 153 ms |
| | `(instrument_id)` | `Bitmap Heap Scan` | 13,0 ms | **793 ms** |
| | `(instrument_id, ends_at)` | `Index Scan` | 0,03 ms | **159 ms** |

Index na `instrument_id` závislosť od objemu neodstráni, iba ju zmenší
o podiel ostatných prístrojov: pri 1 000 000 riadkoch číta 50 000 riadkov
histórie A a p95 je 793 ms, bližšie k hranici než k 168 ms z E-3. Index
`(instrument_id, ends_at)` históriu vynechá, lebo podmienka prekryvu
`ends_at > starts_at` nového intervalu je rozsah nad druhým stĺpcom. Dotaz
potom trvá 0,03 ms pri oboch objemoch a p95 ostáva okolo 155 ms.

**Nálezy a opravy** — cielene na vetve PR, text plánu nedotknutý:

| # | Nález | Oprava |
| - | ----- | ------ |
| RV-I02-1 | E-3 meria index pri prístroji bez histórie. R-08 z neho vyvodzuje, že ošetrenie je „pripravené a zmerané". S vlastnou históriou (pokus A) dáva index na `instrument_id` pri 1 000 000 riadkoch p95 793 ms. | R-08: ošetrením je index `(instrument_id, ends_at)`, dôkazom pokus A; poznámky pri bode 3 a v *Rozhodnutí* |
| RV-I02-2 | Bod 5: vysvetlenie poolom bola hypotéza, meranie ju neoddelilo. | pokus B; poznámka pri bode 5 |
| RV-I02-3 | Meralo sa iba na jednom stroji. | druhý stroj vyššie; hranice doplnené pod ADR-01, pri R-08 a v hrozbách platnosti |
| RV-I02-4 | Drobnosť: úvod I-02 hovorí o troch commitoch a „(tento commit)". Vetva ich má štyri a text je už v neskoršom commite. | ponechané — text plánu sa od `4df292e` nemení, zapísané iba tu |

**K bodom v popise PR:**

1. **Model záťaže — súhlas.** Pri súbehu je konzervatívny: future pressure
   z C01 hovorí o 10× viac súbežných rezervácií v celom systéme, meranie dáva
   všetkých 50 na jeden prístroj. Objem rastie s časom, nie so záťažou —
   história sa nemaže. 100 000 riadkov je teda rok prevádzky pri 10× alebo
   desať rokov pri dnešnom tempe. Jediná medzera modelu je rozloženie
   histórie (pokus A). Pre `Seq Scan` je jedno, pri indexe rozhoduje.
2. **Hranica p95 ≤ 1 s — súhlas.** Potvrdenie je interaktívna operácia
   a p95 nad všetkými požiadavkami dávky zachytí poslednú v rade. Čas je
   meraný klientom na tom istom stroji, v nasadení pribudne sieť. Rezerva pri
   K-2 je aj na pomalšom stroji 2,7×.
3. **R-08 — ACCEPT so spúšťačom, index teraz nie.** Rezerva v objeme je
   8,5 – 10×, databáza nemá volume ani trvalé dáta (ADR-02) a zmena schémy
   ide cez CR, ktorý musí rozšíriť aj `verify_schema()` o indexy. Ošetrenie
   je opravené na `(instrument_id, ends_at)` (RV-I02-1). Do plánu CP2 Change
   Request nepribúda.
4. **Spomalenie suseda — vysvetlené poolom** (pokus B). Akcia nevzniká.
5. **Druhý stroj — K-1 až K-3 PASS**, hranice nižšie (140 a 850 000).
   ADR-01 sa neotvára.
