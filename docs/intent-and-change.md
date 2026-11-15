# Project Frame

## Reservation domain
Rezervácia laboratórnych prístrojov na univerzitnom pracovisku. Rezervovaný
resource je konkrétny prístroj (mikroskop, spektrometer, 3D tlačiareň,
centrifúga) na časový interval.

## Purpose
Systém slúži študentom a doktorandom, ktorí potrebujú obmedzený laboratórny
prístroj na konkrétny čas, a vedúcim laboratórií, ktorí zodpovedajú za to, že
prístroj obsluhuje iba vyškolený človek. Bez systému sa prístroje rezervujú cez
papierový hárok na dverách, čo vedie k dvojitým rezerváciám a k tomu, že nikto
spätne nevie, kto prístroj v danom čase používal.

## Users / Stakeholders
- **Študent** — vytvára a ruší vlastné rezervácie.
- **Vedúci laboratória (supervisor)** — spravuje prístroje a certifikáty.

## Core concepts
- **Instrument** — rezervovaný prístroj; má kategóriu, umiestnenie a príznak aktívny.
- **Reservation** — rezervácia prístroja na interval, v jednom z troch stavov.
- **User** — kto rezerváciu vytvára; má rolu.
- **Certification** — oprávnenie používateľa na *kategóriu* prístroja, platné do dátumu.

Certifikát sa viaže na kategóriu, nie na konkrétny prístroj. Inak by pri každom
novom mikroskope museli všetci absolvovať školenie odznova.

## Core operations
- **Create reservation** — vznikne v stave `DRAFT`; kontroluje sa iba existencia
  a aktívnosť prístroja a podmienka `ends_at > starts_at`.
- **Confirm / approve reservation** — `DRAFT` → `CONFIRMED`; tu sa vynucujú obe
  business rules.
- **Cancel reservation** — z ľubovoľného nezrušeného stavu → `CANCELLED`.
- **Check availability** — pre prístroj a interval vráti voľné/obsadené; do
  obsadenosti sa počítajú iba rezervácie v stave `CONFIRMED`.

## Persistent state
- **Instrument:** id, názov, kategória, umiestnenie, aktívny.
- **User:** id, meno, e-mail, rola.
- **Certification:** id, používateľ, kategória, platnosť do.
- **Reservation:** id, prístroj, používateľ, začiatok, koniec, stav, vytvorené.

Všetky časové údaje sú uložené ako `timestamptz`.

## State-changing operation
`DRAFT → CONFIRMED` pri operácii *confirm*. Je to jediný prechod, ktorý mení
obsadenosť prístroja, a teda jediný, ktorý musí vyhodnotiť obe pravidlá.

## Common business rule
Dve rezervácie toho istého prístroja v stave `CONFIRMED` sa nesmú prekrývať.

Intervaly sú polootvorené `[starts_at, ends_at)`. Rezervácie 10:00–11:00 a
11:00–12:00 teda **nekolidujú**. Rezervácie v stavoch `DRAFT` a `CANCELLED` sa
do prekryvu nezapočítavajú.

## Domain-specific business rule
Rezerváciu možno potvrdiť, len ak používateľ má certifikát na kategóriu daného
prístroja, platný k času `starts_at` rezervácie.

Ak certifikát chýba alebo je expirovaný, potvrdenie zlyhá a rezervácia zostáva
v stave `DRAFT`. Používateľ si môže certifikát doplniť a potvrdiť znova.

## External / system boundary
**Notification Service.** Volaný po úspešnom potvrdení rezervácie a po jej
zrušení; doručuje správu používateľovi a vedúcemu laboratória. Je mimo náš
systém, môže byť pomalý alebo nedostupný. V C01 je iba definovaný, nie
implementovaný.

## Assumption
Katalóg prístrojov aj záznamy o certifikátoch spravujeme vo vlastnom systéme
a napĺňa ich vedúci laboratória ručne.

## Unknown
Či certifikáty nemajú v skutočnosti prichádzať zo študijného systému univerzity.
Ak áno, certifikácia sa stáva druhou externou hranicou a operácia *confirm*
dostane sieťové volanie — čo výrazne zmení obraz súbežnosti aj správanie pri
výpadku.

## Selected future pressure
**Category:** Q (Quality / Scale)

**Concrete pressure:** 10× viac súbežných rezervácií. Dvaja používatelia potvrdia
prekrývajúce sa intervaly na tom istom prístroji v tom istom okamihu.

**Why it is relevant to our reservation system:** Pravidlo o neprekrývaní je
jediný skutočný invariant systému a jediné pravidlo, ktoré závisí od *iných*
záznamov, nie len od potvrdzovanej rezervácie. Naivná kontrola typu
„najprv sa spýtaj, potom zapíš" ho pri súbehu neudrží: obe transakcie prečítajú
stav bez konfliktu, obe zapíšu a obe prejdú. Čím viac študentov systém používa,
tým je toto okno pravdepodobnejšie — a dôsledkom je práve to, čo mal systém
odstrániť: dvaja ľudia stoja pred jedným prístrojom.

**V C01 túto pressure neimplementujeme.**

---

# C08 — Controlled Change: CR-08

Zmena sa vedie postupom zadania C08: najprv zmraziť východiskovú baseline,
pomenovať požiadavku a podmienky prijatia, overiť dnešné správanie, analyzovať
dopad, porovnať varianty — a až potom rozhodnúť. **V C08 sa kód nemení.**
Výsledkom je rozhodnutie a plán overenia; implementácia patrí do C09.

Kde čo leží:

- tento oddiel — Change Request, analýza dopadu, varianty, rozhodnutie, odovzdanie do C09,
- [evidence-and-evolution.md, časť C08](evidence-and-evolution.md#c08--controlled-change-baseline-a-overenie-dnešného-správania)
  — baseline (krok 0), test cases a ich skutočné vykonanie,
- [README.md, časť C08](../README.md#c08--controlled-change) — príkazy na zopakovanie a Definition of Done.

## C08 / CR-08 — Controlled Change Card

### Request source and concrete project need

| Položka | Obsah |
| ------- | ----- |
| **Zdroj** | Vedúci laboratória — stakeholder z Project Frame (vyššie), ktorý zodpovedá za drahé prístroje so schvaľovaním. Požiadavku nevzniesol nikto mimo tímu: tím ju zapisuje za túto rolu a opiera ju o obmedzenie, ktoré je v repozitári doložené od C02 (riadok *Doklad*). Hodnota **48 h** je **predpoklad tímu za túto rolu**, nie odvodená veličina ani výrok skutočného vedúceho — rozhodnutie T. Hrubého z 2026-11-15. Platí, až keď ju potvrdí ten, kto rolu vedúceho pri review zastupuje (CA-1, CA-7). |
| **Potreba** | Žiadosť, o ktorej vedúci nerozhodne, nemá držať prístroj až do začiatku termínu. Prístroj sa má uvoľniť včas — tak, aby ho ešte mohol použiť niekto iný. |
| **Problém dnes** | Žiadosť `PENDING_APPROVAL` blokuje prístroj, kým o nej vedúci nerozhodne alebo kým nenastane jej `starts_at` (BR-08). Žiadosť podaná dva týždne vopred, o ktorej nikto nerozhodne, drží prístroj dva týždne — a keď v `starts_at` vyprší, termín už nikto iný nevyužije. |
| **Doklad** | C02, rozhodnutie R-2 ([change-impact-c02.md](change-impact-c02.md)): „Cena: zabudnutá žiadosť drží prístroj — preto expirácia." Expirácia pri `starts_at` však uvoľní prístroj v okamihu, keď termín začína. To je presne strata, ktorou BR-03 zdôvodňuje 60-minútovú lehotu („nikto to už nestihne využiť a prístroj stojí prázdny"). Navyše issue I-01 (C07): vedúci sa o žiadosti nedozvie — Notification Service nie je napojená (TBD-03) a systém nemá zoznam žiadostí (TBD-09). Zabudnutá žiadosť teda nie je okrajový prípad. Správanie je doložené behom `TC-C08-03` v evidence. |
| **Prínos** | Prístroj, o ktorom vedúci nerozhodol, sa uvoľní najneskôr 48 h po podaní žiadosti. Žiadateľ vie, dokedy sa o žiadosti rozhodne. |
| **Prečo práve 48 h** | Kompromis dvoch tlakov: kratšia lehota uvoľní prístroj skôr, dlhšia dá vedúcemu čas, aj keď jeden pracovný deň chýba. 48 kalendárnych hodín pokrýva pri podaní v pondelok až štvrtok vždy aspoň jeden celý nasledujúci pracovný deň; 24 h by pri podaní večer dávali len zvyšok jedného dňa. Číslo nemá iný zdroj — preto je T konfigurovateľná a hodnotu potvrdzuje review. |
| **Vzťah k príkladu zo zadania** | Zadanie ilustruje C08 práve vypršaním čakajúceho schválenia — a náš systém má presne tú baseline, ktorú príklad opisuje (schvaľovanie z C02, žiadosť bez lehoty na rozhodnutie). Prevzatý je len typ zmeny. Lehota, jej začiatok, hranica, staré záznamy, dopady aj varianty sú odvodené z nášho kódu a špecifikácie; 30 minút z príkladu by pri rezerváciách na dni dopredu nedávalo zmysel. |
| **Prečo sa otvára rozhodnutie z C02** | C02 R-3 a špecifikácia §10 lehotu „48 h" zamietli, lebo „nemá zdroj a expirácia viazaná na `starts_at` rieši ten istý problém bez čísla". Prvá námietka platí čiastočne aj dnes: CR dáva zdroj **potrebe** (doložený problém), samotné číslo je predpoklad, ktorý musí potvrdiť stakeholder (CA-1). Druhá neplatila ani vtedy: expirácia v `starts_at` prístroj pre nikoho iného neuvoľní, len ukončí žiadosť (`TC-C08-03`). |

### Before commit/tag + spec/BR/ADR + schema/config

Commit `b0974e7` (`main`), špecifikácia baseline v0.2 — pravidlo **BR-08**
a všetko, čo naň odkazuje; ADR-01; schéma z `models.py` bez migrácií;
konfigurácia iba `DATABASE_URL`. Úplný záznam s odkazmi na presné riadky je
v [evidence, krok 0](evidence-and-evolution.md#krok-0--východisková-baseline-before).

### Old accepted behavior / observable limitation

- Žiadosť je živá, kým `currentTime < starts_at` (BR-08). Žiadna iná lehota
  neexistuje — ani v špecifikácii, ani v kóde.
- Kým je živá, blokuje prístroj rovnako ako `CONFIRMED` (BR-02, REQ-03, REQ-04)
  a vedúci ju môže schváliť (REQ-11) — nech bola podaná kedykoľvek.
- Systém neukladá, **kedy** bola žiadosť podaná. `created_at` je vznik `DRAFT`;
  žiadosť podanú pred minútou a pred desiatimi dňami nevie rozlíšiť.

### New intended behavior, scope and explicit out-of-scope

**Nové správanie.** Žiadosť je živá, kým nenastane **skorší** z dvoch
okamihov: `starts_at`, alebo uplynutie **lehoty na rozhodnutie T = 48 h od
podania žiadosti** (okamih, keď potvrdenie zmenilo `DRAFT` na
`PENDING_APPROVAL`). Po uplynutí lehoty je žiadosť vypršaná v tom istom zmysle
ako dnes po `starts_at`: neblokuje prístroj, nedá sa schváliť, zamietnuť ani
zrušiť a jej pozorovateľný stav je `EXPIRED`.

Tento opis hovorí, **čo** má platiť, nie **ako** sa to dosiahne. Plánovač,
nový stĺpec alebo dávka sú varianty riešenia (krok 4), nie súčasť požiadavky.

**V rozsahu:** BR-08 a všetko, čo na ňom stojí (OP-02 až OP-05); lehota určená pri
podaní a zistiteľná z uložených dát; lehota viditeľná pre žiadateľa.

**Mimo rozsahu** (nerozhodnuté alebo vedome vynechané):

- povinný plánovač alebo worker — je to varianta, nie požiadavka,
- oznámenie o vypršaní žiadateľovi a vedúcemu (TBD-03),
- zoznam čakajúcich žiadostí (TBD-09),
- rôzna lehota pre rôzne prístroje,
- pracovné hodiny alebo kalendár sviatkov,
- používateľské rozhranie.

### Rozhodnutia, ktoré zadanie žiada urobiť výslovne

| Otázka | Rozhodnutie | Prečo |
| ------ | ----------- | ----- |
| Podľa akého času sa lehota počíta? | Podľa `now` tej operácie potvrdenia, ktorá žiadosť podala: hodiny aplikačného servera v UTC, odčítané raz na operáciu (BR-03, `clock.now()`). Lehota sa určí **pri podaní** z tohto `now` a musí byť zistiteľná z uložených dát. | Ten istý zdroj času ako pri všetkých ostatných pravidlách. `timestamptz` ukladá okamih nezávisle od pásma; F-01 zaručuje, že ho API vracia v UTC. |
| Od čoho sa lehota počíta? | Od **podania** žiadosti, nie od vzniku `DRAFT` (`created_at`). | `DRAFT` môže ležať dni a nikoho neblokuje. Lehota má merať čas, počas ktorého žiadosť **blokuje** prístroj. |
| Čo presne v hranici 48:00:00? | V okamihu `podanie + 48 h` je žiadosť **vypršaná**; o sekundu skôr je živá. | Ostrá polootvorená hranica ako v BR-01, BR-04 a BR-08 — jeden význam slova „do" v celom systéme. |
| Čo keď sa T neskôr zmení? | Zmena sa týka iba žiadostí podaných **po** nej. Lehota sa určí raz, pri podaní — je to tá, ktorú žiadateľ dostal v odpovedi (AC-06). | Ten istý princíp ako pri žiadostiach spred zmeny: pravidlo sa nemení spätne. Žiadateľ, ktorému systém povedal „do stredy 17:00“, nesmie prísť o žiadosť v utorok. |
| Kalendárne, alebo pracovné hodiny? | Kalendárne. | Systém pracovný kalendár nemá a zaviesť ho je samostatná zmena. Dôsledok (žiadosť podaná v piatok poobede vyprší v nedeľu) je riziko, ktoré musí stakeholder prijať — preto je v peer review. |
| Nový stav, alebo `EXPIRED`? | Existujúci `EXPIRED`, žiadny nový stav. | Význam je ten istý: žiadosť prepadla bez rozhodnutia. Všetky pravidlá pre `EXPIRED` (koncový stav, `INVALID_STATE` pri zrušení, REQ-14) platia bez zmeny; enum v databáze sa nemení. Či vypršala kvôli lehote, alebo kvôli `starts_at`, sa dá odvodiť z uloženej lehoty a `starts_at`. |
| Čo uvidí žiadateľ? | V odpovedi potvrdenia, ktoré žiadosť vytvorí, okamih, dokedy sa musí rozhodnúť. Po ňom OP-02 vráti `AVAILABLE` a pokus o schválenie, zamietnutie alebo zrušenie skončí zamietnutím `EXPIRED`. | Systém nemá čítaciu operáciu (TBD-09) ani oznámenia (TBD-03). Odpoveď potvrdenia je jediné miesto, kde sa žiadateľ lehotu dozvie. |
| Čo so žiadosťami podanými pred zmenou? | Platí pre ne doterajšie pravidlo: lehota = ich `starts_at`. | Žiadna žiadosť nesmie prepadnúť spätne podľa pravidla, ktoré pri jej podaní neplatilo — a ich okamih podania systém ani nepozná. Dnes také riadky trvalo neexistujú (kontajner bez volume, R-04); pravidlo platí pre každé ďalšie nasadenie s dátami. |
| Opakovaný pokus? | Prvý pokus po lehote zapíše `EXPIRED` a vráti zamietnutie `EXPIRED`; každý ďalší dostane `INVALID_STATE`. | Je to dnešné správanie OP-04 a OP-05 pre už zapísaný `EXPIRED` — CR ho nemení. |
| Oneskorený pokus (čakal na zámok)? | Posudzuje sa podľa `now` odčítaného pri prijatí požiadavky. Schválenie, zamietnutie aj zrušenie prijaté sekundu pred lehotou, ktoré čakalo na zámok, ešte prejde. Naopak potvrdenie alebo dopyt na dostupnosť so starým `now` ešte započíta žiadosť, ktorá medzitým vypršala, ako blokujúcu. | Tá istá sémantika ako dnes pri `starts_at` (BR-03: čas sa odčíta raz). BR-02 tým ohrozené nie je — rozbor v analýze dopadu. Zostáva ako riziko `R-06` (register rizík v evidence). |

### Acceptance conditions

Podmienky sú pozorovateľné zvonku — cez API a cez uložený stav — a nepredpisujú
mechanizmus.

| ID | Druh | Podmienka |
| -- | ---- | --------- |
| **AC-01** | pozitívna | Oprávnený vedúci schváli živú žiadosť skôr než 48 h po jej podaní a pred `starts_at`, pri splnení ostatných podmienok REQ-11 → `CONFIRMED`. *(Dnešné správanie, ktoré musí zostať.)* |
| **AC-02** | negatívna | Po uplynutí 48 h od podania žiadosť nemôže dosiahnuť `CONFIRMED` ani `REJECTED`. **Prvý** pokus o schválenie, zamietnutie alebo zrušenie skončí zamietnutím `EXPIRED` a stav sa zapíše ako `EXPIRED`; každý ďalší pokus nad už zapísaným `EXPIRED` skončí `INVALID_STATE` (dnešné pravidlo). |
| **AC-03** | negatívna | Po uplynutí lehoty žiadosť **neblokuje** prístroj: OP-02 ju nezapočíta do kolízií a OP-03 prekrývajúcej sa rezervácie ju ignoruje — bez ohľadu na to, aký stav má zapísaný. |
| **AC-04** | hranica | Podanie + 47:59:59 → žiadosť je živá (schválenie prejde, blokuje). Presne podanie + 48:00:00 → vypršaná. Blokovanie (OP-02, OP-03) a rozhodovanie (OP-04, OP-05) dávajú na hranici **tú istú** odpoveď. |
| **AC-05** | hranica | Ak `starts_at` nastane skôr než koniec lehoty, žiadosť vyprší v `starts_at` — dnešné BR-08 ostáva v platnosti. |
| **AC-06** | pozorovateľnosť | Odpoveď potvrdenia, ktoré vytvorí žiadosť, uvádza okamih, do ktorého musí vedúci rozhodnúť. |
| **AC-07** | regresia | BR-02: dve blokujúce rezervácie toho istého prístroja sa neprekrývajú. `TC-C08-01` a `TC-C08-02` dajú rovnaký výsledok ako na baseline; testy súbehu z baseline (REQ-05, REQ-16) prejdú. Súbeh schválenia s potvrdením pri hranici lehoty, ktorý baseline netestuje, má nový test (C09). |
| **AC-08** | regresia | Create, Cancel (60-minútová lehota BR-03), certifikácia (BR-04), neaktívny prístroj (BR-05), oprávnenia (BR-06, BR-07) a prístroje bez schvaľovania sa nemenia. Sada z baseline prejde bez zmeny očakávaní okrem riadkov, ktoré CR-08 výslovne mení. |
| **AC-09** | dáta | Žiadosť podaná pred nasadením zmeny sa správa ako doteraz — vyprší v `starts_at`. |
| **AC-10** | konfigurácia | Lehota sa určí pri podaní. Zmena T sa týka iba žiadostí podaných po zmene; už podaná žiadosť si ponechá lehotu, ktorú žiadateľ dostal v odpovedi. |

Pozorovateľný kontrakt a mechanizmus sú oddelené: „po 48 h žiadosť nemožno
schváliť a neblokuje" je požiadavka; „periodicky prepisovať stav na `EXPIRED`"
je jedna z variant.

### Impact analysis method and exact inspected artifacts

Analýza nie je zoznam súborov, ktoré sa zmenia. Postupovali sme od
pozorovateľného tvrdenia k tomu, kto ho vynucuje, ukladá, používa, konfiguruje,
overuje a vydáva — podľa metódy zo zadania:

1. **Východiskové tvrdenie:** AC-02 a AC-03 — po lehote sa žiadosť nedá
   schváliť a neblokuje.
2. **Vlastník rozhodnutia v dnešnej architektúre:** `Reservation Lifecycle`
   (G2, odpovednosť O-1 „prechody rezervácie vrátane vypršania"; G3, riadok
   `PENDING_APPROVAL → EXPIRED`). **Nie** `Approval Workflow` — ten o vypršanie
   iba žiada cez `expire_request_if_dead()`.
3. **Volania a dáta až po uloženie.** Živosť žiadosti existuje v kóde
   v **dvoch formách** (C05.8, „Kde sa čo mení"):
   - *predikát* `rules.approval_request_is_alive()` ← `expiry_decision()` ←
     `cancel_decision()` (OP-04) a `expire_request_if_dead()` ←
     `decide_reservation()` (OP-05); zápis `EXPIRED` iba `_write_expired()`,
   - *SQL podmienka* v `_overlapping_blocking()` ← `enter_blocking_state()`
     (OP-03 a schválenie v OP-05) a `check_availability()` (OP-02).

   Údaj, z ktorého by sa lehota dala vypočítať, v tabuľke `reservations`
   neexistuje (krok 0, `TC-C08-03` krok 2).
4. **Rozhrania, závislé jednotky a testy** — z výsledku
   `grep -rn "approval_request_is_alive\|expiry_decision\|expire_request_if_dead\|_overlapping_blocking\|starts_at > now" src tests scripts`
   a z `grep -n "starts_at\|EXPIRED\|vypr" docs/*.md README.md`. Každý nález
   je v matici nižšie ako riadok alebo ako zdôvodnené NO IMPACT. Prvá verzia
   matice tu niekoľko nálezov vynechala (signatúry v pohľadoch C05, texty
   zamietnutí, závery §8); doplnila ich nezávislá kontrola pred review.
5. **Zmena kontraktu vs. iba regresná kontrola** — stĺpec CHANGE / CHECK.
   Nezmenený súbor môže byť dotknutý zmenou kontraktu (napr. `check_availability()`
   sa nezmení ani o znak, ale jeho výsledok áno).
6. **Konfigurácia, migrácia, prevádzka, obnova** — samostatné časti matice.
7. **Dôkaz** — každý riadok CHANGE a CHECK má dôkaz dokončenia; NO IMPACT má dôvod.

**Inventár zapisovateľov do `reservations`** — zadanie výslovne žiada
neignorovať existujúce cesty mimo API:

| Cesta | Existuje? | Čo znamená pre CR-08 |
| ----- | --------- | ---------------- |
| Reservation API (OP-01, OP-03, OP-04, OP-05) | áno — jediný zapisovateľ v behu aplikácie | lehotu musí zaznamenať a vyhodnocovať |
| worker, plánovač, dávka | **nie** (G4: jeden proces) | NO IMPACT — táto cesta neexistuje; vznikla by iba pri variante B |
| import, administrátorské API | **nie** (TBD-02: správa dát nie je v rozsahu) | NO IMPACT — táto cesta neexistuje |
| priamy zápis do databázy | **áno** — `tests/conftest.py` (`make_reservation`, L136–153), `scripts/demo.py` (L114 vkladá `PENDING_APPROVAL`), `scripts/c08_baseline_tc.py`, ručné SQL podľa TBD-02 | CHECK — žiadosť vložená bez lehoty by bola „živá do `starts_at`" aj po zmene. API-only kontrola ju nezachytí; databázové obmedzenie áno (riadok *Priamy SQL zápis* nižšie). |

Všetky akcie v matici sú **PLANNED** — v C08 nie je implementované nič.
Za každý riadok zodpovedá, kým tím nerozdelí prácu v C09, **vlastník CR-08
(T. Hrubý)**.

### Impact Matrix

Odkazy `L…` sú riadky súborov pri `b0974e7`.

**Požiadavky a pravidlá — `docs/specification.md`**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| BR-08 (L251–293) | Pribúda druhá hranica živosti. Rozdelenie *vyhodnotenie* (každá operácia) / *zápis* (iba OP-04, OP-05) zostáva. | **CHANGE** | Nové znenie: živá, kým `now < min(lehota, starts_at)`, lehota = podanie + T určená pri podaní; ostrá hranica; T = 48 h a jej zdroj (CR-08, predpoklad za rolu); pravidlo pre žiadosti spred zmeny (AC-09) a pre zmenu T (AC-10). | Review v0.3; príklady overenia sú test basis AC-02 až AC-05, AC-09, AC-10 |
| Definícia alokácie (L72–100) | Píše „živá = `currentTime < starts_at`". | **CHANGE** | Odkázať na BR-08 namiesto vzorca, aby vzorec žil na jednom mieste. | Review v0.3 |
| BR-02 (L126–144) | Odkazuje na „živá podľa BR-08", takže znenie sa nemení samo. Pomenovaný dôsledok (v databáze môže ležať `PENDING_APPROVAL` prekrývajúca sa s `CONFIRMED`) teraz nastane aj pri žiadosti po lehote. | **CHECK** | Overiť, že text platí bez zmeny; dôsledok rozšíriť o lehotu. | Review v0.3 |
| BR-03, riadok `PENDING_APPROVAL` (L151) a odsek L184–186 | Opakuje vzorec `currentTime < starts_at`. | **CHANGE** | Odkaz na BR-08. | Review v0.3 |
| REQ-03, REQ-13 (L385–394) | Odkazujú na BR-08 — znenie platí. | **CHECK** | — | Test OP-02 po lehote |
| Príklady OP-02 (L457–470), odsek L482–486 | Príklady pozná iba hranicu `starts_at`. | **CHANGE** | Pridať žiadosť po lehote → `AVAILABLE`. | Príklady = test basis |
| REQ-04 (L502–506) | Odkazuje na živú žiadosť (BR-08). | **CHECK** | — | `TC-C08-03` krok 4 s novým Expected |
| REQ-10 (L508–511), stav po OP-03 (L534–541) | AC-06: odpoveď pri vzniku žiadosti uvádza lehotu; pri vstupe do `PENDING_APPROVAL` sa zaznamená lehota. | **CHANGE** | Doplniť do REQ-10 a do stavu po úspešnom vykonaní. | Test API (AC-06) |
| REQ-11, REQ-12 (L773–782) | Odkazujú na „nevypršala (BR-08)" a na „živosť". | **CHECK** | — | Testy OP-05 |
| REQ-14 (L656–662), REQ-15 (L784–788) | Doslova „ktorej `starts_at` už nastal". | **CHANGE** | „ktorá vypršala (BR-08)". | Review v0.3 + testy |
| OP-04 chyby a príklady (L698–701, L740–744); OP-05 predpoklady, chyby, príklady (L790–795, L844, L864–865) | Vzorec so `starts_at`. | **CHANGE** | Prepísať a doplniť príklady 47:59:59 / 48:00:00. | Príklady = test basis |
| OP-04 predpoklady (L668–670), OP-05 „Zmena stavu" (L816–818) | Opakujú vzorec `currentTime < starts_at` a „pri pokuse o rozhodnutie po `starts_at`". | **CHANGE** | Odkaz na BR-08. | Review v0.3 |
| §8, záver (L922–925) | Tvrdí, že „expirácia žiadosti (BR-08) zámerne **žiadne nové číslo nezavádza**" — CR-08 ho zavádza. | **CHANGE** | Prepísať: T je predpoklad za rolu vedúceho so zdrojom v CR-08, konfigurovateľná. | Review v0.3 |
| TBD-07 (L918) | Jediný vedúci: jeho žiadosti prepadnú po 48 h, nie až v `starts_at`. Vedomý dôsledok BR-07 sa zhoršuje. | **CHECK** | Doplniť dôsledok. | Review v0.3 |
| TBD-08 (L919) | Varianta A dočisťovanie nerieši — ostáva otvorené, ale pribúda dôvod ho rozhodnúť (rollback, riadok *Rollback*). | **CHECK** | Doplniť do TBD-08 odkaz na CR-08 a trigger na variantu B. | Review v0.3 |
| TBD-03 (L914), TBD-09 (L920) | O vypršaní sa žiadateľ dozvie iba pokusom o operáciu. AC-06 to kompenzuje len čiastočne. | **CHECK** | Pridať „vypršanie" medzi udalosti TBD-03. | Review v0.3 |
| §9, §10 (L1014, L1112, L1140–1141), §11 | Zápis „lehotu 48 h sme zamietli" sa reviduje. História ostáva, pribúda nový riadok. | **CHANGE** | Riadok kontroly konzistencie v0.3, riadky REQ-14/REQ-15 v §10, schválenie v0.3 v §11. | Schválená v0.3 |
| Súbeh s R-03 / F-03 (REQ-16 v0.3) | Obe zmeny cielia na v0.3. | **CHECK** | Tím rozhodne: jedna v0.3 s oboma, alebo v0.3 a v0.4. | Záznam v §11 |
| `docs/change-impact-c02.md`, R-3 (L22) | Historický záznam rozhodnutia z C02. | **NO IMPACT** | Neprepisuje sa — revíziu zapisuje tento CR. | — |

**Stavový model — `docs/diagrams.md`**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| Statechart (L88, poznámka L114–119), tabuľka podmienok (L136–139) | Guard `[nastal starts_at]`; podmienky approve, reject a cancel obsahujú `now < starts_at`. | **CHANGE** | `[nastal starts_at ∨ uplynula lehota]` a odkaz na BR-08. | Cross-view kontrola v0.3 |
| Diagramy aktivít 3.2 (L211), 3.4 (L297–299), 3.5 (L348–349) | Rozhodovací uzol `now < starts_at?`. | **CHANGE** | Uzol „žiadosť živá? BR-08". | Cross-view kontrola |
| Diagram 3.5, poznámka L371 | **Nález mimo CR:** „certifikát mohol vypršať" — presne zdôvodnenie, ktoré revízia v0.2 opravila (RV-5). V diagrame oprava chýba. | **CHANGE** | Opraviť v tej istej úprave 3.5. | Cross-view kontrola |
| Stavy (`domain/states.py`, enum `reservation_state`) | Žiadny nový stav — `EXPIRED` ostáva. | **NO IMPACT** | — | `test_zoznam_blokujucich_stavov_sedi_s_kodom` bez zmeny |

**Architektúra — `docs/architecture-and-decisions.md`**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| Vlastník vypršania — G2, O-1; G3 (L745) | Vlastník sa nemení (lifecycle), mení sa príčina prechodu. | **CHECK**, text G3 **CHANGE** | Doplniť príčinu do G3. | Review |
| Signatúry v pohľadoch: H2 (L866–869), C05.3 riadok L-3 (L1069), C05.5 (L1227–1229), C05.6 (L1321–1324), C05.7 (L1356), pseudokód C05.8 (L1473) | Všade `(state, starts_at, now)` a `approval_request_is_alive(starts_at, now)`. | **CHANGE** | Zosúladiť s novým vstupom politiky. | Cross-view kontrola (ako v C03 časť I) |
| ADR-01 (L538–600) | Varianta A nepridáva zapisovateľa ani proces → ADR-01 platí. Varianta B by spustila „znovu otvoríme, keď… pribudne druhý zapisovateľ… dávka". | **CHECK** | Pri A nič; pri B nové ADR. | Review rozhodnutia |
| Odkiaľ prichádza T — závislosti G2 (L672–689) | `Domain Rules` ani politika prechodov nesmú čítať konfiguráciu: G2 hovorí „`Domain Rules` ani `Clock` nezávisia od ničoho" a `config.py` patrí prvku `Reservation Store`. T musí prísť zvonku ako hodnota — rovnako ako `now`. **Dnes to žiadny test nestráži:** `FORBIDDEN_FOR_POLICY` v `test_architecture_conformance.py` (L343–353) `swilab.config` neobsahuje a import `swilab.config` nenačíta nič zo zoznamu (overené importom v čistom procese) — politika by si T mohla prečítať a sada by ostala zelená. | **CHANGE** | Určiť, kto T číta (API alebo lifecycle cez `Reservation Store`), zapísať závislosť do tabuľky G2 a doplniť `swilab.config` do `FORBIDDEN_FOR_POLICY`. | Mutácia „politika importuje `swilab.config`" padne na `test_politika_prechodov_nepozna_perzistenciu_ani_hodiny` |
| Priamy SQL zápis | Existuje (inventár vyššie). Aplikácia BR-02 ani lehotu v databáze nevynucuje (ADR-01, dôsledok 1). | **CHECK** | Rozhodnúť databázové obmedzenie „`PENDING_APPROVAL` ⇒ lehota je vyplnená" — chráni aj priamy zápis. Nezávisí od `now()`, takže námietka ADR-01 proti constraintu naň neplatí. | Test: vloženie žiadosti bez údaja zlyhá |
| `Clock` (`clock.py`) | Zdroj času sa nemení. | **NO IMPACT** | — | — |
| Notification Integration | Neexistuje (TBD-03). | **NO IMPACT** | — | — |

**Detailný návrh a kód (C05) — `src/swilab/`**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `rules.approval_request_is_alive()` (L33–43) | Jediný predikát živosti; dnes `now < starts_at`. | **CHANGE** | Druhá hranica. | `test_rule_boundaries_are_strict` rozšírený o 47:59:59 / 48:00:00 |
| `expiry_decision()` (L60–80), `cancel_decision()` (L83–121, vetva `PENDING_APPROVAL` L96–102); kontrakt C05.4 (L1104–1145); tabuľky C05.8 (L1430–1461) | Podpis `(state, starts_at, now)` nestačí — chýba lehota. Tvary `Decision` sa nemenia. | **CHANGE** | Nový vstup politiky — lehota; riadky tabuliek `now >= starts_at` → „žiadosť nežije". | `test_expiry_decision_boundary`, `test_cancel_decision_table` |
| `confirm_decision()` (L124–156) | Rozhoduje cieľ `PENDING_APPROVAL`, lehotu nepočíta. | **CHECK** | Rozhodnúť, kde sa určí lehota (`now + T`) — vo vykonávateľovi (`confirm_reservation` alebo `enter_blocking_state`). Politika ostáva čistá. | Contract test |
| `_overlapping_blocking()`, SQL (L191–201) | Druhá forma živosti (`starts_at > now`). | **CHANGE** | Doplniť lehotu. | `test_liveness_in_the_overlap_query_agrees_with_the_policy` rozšírený o hranicu 48 h |
| Texty a komentáre: `transitions.py` L78 (dôvod zamietnutia „jej termin uz zacal"), `errors.py` L19, `states.py` L19 | Pri vypršaní lehoty je text, ktorý dostane klient, nepravdivý — termín ešte nezačal. | **CHANGE** | Text podľa príčiny vypršania. Text je podľa C05.9 vec kódu, ale nesmie klamať. | Test na dôvod zamietnutia pri oboch príčinách |
| `confirm_reservation()` (L382–436), `enter_blocking_state()` (L207–251) | Vstup do `PENDING_APPROVAL` musí zaznamenať lehotu. | **CHANGE** | Miesto zápisu podľa C05 „Kde sa čo mení". | Contract test na zápis |
| `expire_request_if_dead()` (L268–288), `decide_reservation()` (L485–557), `cancel_reservation()` (L439–482) | Odovzdajú politike nový údaj; logika sa nemení. | **CHANGE** (podpis) | — | Existujúce testy OP-04, OP-05 |
| `check_availability()` (L332–379) | Kód bez zmeny; výsledok sa mení cez `_overlapping_blocking()`. | **CHECK** | — | Test OP-02 po lehote (AC-03) |
| `_write_expired()`, tvary `Decision`, poradie zamietnutí DD-7 | Nemenia sa — vypršanie po lehote je ten istý tvar `EXPIRED`/`EXPIRED`. | **NO IMPACT** | — | `test_policy_is_total_and_returns_only_shapes_from_the_contract`, `test_decision_accepts_exactly_the_four_shapes` |
| OP-01 `create_reservation()`, BR-04, BR-05, BR-03 pre `CONFIRMED`, REQ-09 | S lehotou žiadosti nesúvisia. | **NO IMPACT** | — | Regresia celej sady (AC-08) |

**Perzistencia a dáta**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `models.Reservation` (L73–101) | Z uložených dát sa lehota nedá zistiť. | **CHANGE** | Nový `timestamptz` stĺpec. **Odporúčanie pre C09: ukladať priamo lehotu** určenú pri podaní, nie okamih podania — zmena T potom nie je retroaktívna (AC-10), žiadosti spred zmeny dostanú lehotu = `starts_at` (AC-09) a obmedzenie z CA-6 môže pre žiadosť znamenať jednoducho `NOT NULL`. Okamih podania tým zaniká ako samostatný údaj; ak ho bude treba na audit, je to ďalší stĺpec. | Výpis schémy a nový odtlačok |
| `create_all()` pri štarte (`main.py` L35–40) | `create_all()` existujúcu tabuľku nezmení. Aplikácia nad starou databázou naštartuje, ale každá požiadavka, ktorá siahne na `reservations` — aj `INSERT` v OP-01 — skončí chybou `500` (chýbajúci stĺpec). | **CHANGE** | Rozhodnúť migráciu (R-04) alebo zapísanú prestavbu databázy. | Release záznam s presným postupom |
| Žiadosti `PENDING_APPROVAL` spred zmeny | AC-09: lehota = ich `starts_at`. | **CHECK** | Pravidlo pri migrácii (backfill), nie v kóde. | Test na riadku vloženom pred migráciou |
| Typ a pásmo času | `timestamptz` + session v UTC (F-01). | **NO IMPACT** | — | `test_session_timezone_is_utc_whatever_the_server_says` |
| Priami zapisovatelia `PENDING_APPROVAL`: `conftest.make_reservation` (L136–153), `demo.py` (L114), `c08_baseline_tc.py` | Po zmene musia vyplniť lehotu, inak ich dáta nezodpovedajú pravidlu. Nezávislý SQL oracle BR-02 v `c08_baseline_tc.py` (L98–108) má **natvrdo pravidlo v0.2** — po zmene by žiadosť po lehote, ktorá legálne leží v databáze, ohlásil ako porušenie. | **CHANGE** | Doplniť lehotu do všetkých troch; oracle prepísať na pravidlo v0.3 (blokuje `CONFIRMED` a žiadosť s `starts_at > now()` **a** lehotou `> now()`). | Sada zelená; `TC-C08-01/02` `PASS` s novým oracle |

**API a oprávnenia**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `ReservationResponse` (`api/reservations.py` L42–48), `_to_response()` (L67–75) | AC-06. `ReservationResponse` je aj základ `CancelResponse` a vracajú ho všetky štyri endpointy nad rezerváciou, takže nové pole sa objaví v create, confirm, cancel, approve aj reject. | **CHANGE** | Aditívne pole s lehotou — existujúci klienti sa nerozbijú. Rozhodnúť hodnotu pre rezerváciu, ktorá žiadosťou nikdy nebola (`null`). | Test API na všetkých endpointoch |
| `STATUS_BY_CODE` (`main.py` L17–31), `ErrorCode.EXPIRED` | `EXPIRED` → `409` už existuje. | **NO IMPACT** | — | — |
| Endpointy | Žiadny nový. | **NO IMPACT** | — | — |
| BR-06, BR-07 | Oprávnenie sa overuje pred stavom a časom (DD-7) — nemení sa. **Dôkaz však chýba pre vypršanú žiadosť:** `test_authorization_is_judged_before_the_state` pokrýva iba potvrdenie a zrušenie a `test_student_cannot_decide`, `test_supervisor_cannot_decide_own_request` používajú živé žiadosti. Že `FORBIDDEN` má prednosť pred `EXPIRED` (REQ-15, druhá veta), netestuje na baseline nič. | **CHANGE** (test) | Doplniť test: neoprávnený pokus o rozhodnutie vypršanej žiadosti → `FORBIDDEN`, stav sa nezmení — pre obe príčiny vypršania. | Nový test zelený, mutácia poradia zachytená |

**Konfigurácia a nasadenie**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `config.Settings` (`config.py` L4–12), `.env.example` | T je konfigurovateľná s predvolenou hodnotou 48 h. | **CHANGE** | Jednotka v názve parametra (hodiny), validácia `> 0`, hodnota v `.env.example` a README. | Config smoke: aplikácia s T = 1 h → odpoveď potvrdenia uvádza podanie + 1 h |
| Pásmo, `docker-compose.yml` | Bez zmeny. | **NO IMPACT** | — | — |
| CI | Projekt CI nemá (krok 0). | **NO IMPACT** na kód | C09 rozhodne, či ho zavedie, alebo zapíše ručný beh druhým členom (odovzdanie). | Záznam v C09 |

**Testy a evidence**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `test_transitions.py`, `test_approval.py` (`test_expiry_boundary`, `test_cancel_expired_request_expires_it`, `test_reject_of_expired_request_expires_it`, `test_expired_request_does_not_block`, `test_confirm_ignores_expired_request`), `test_lifecycle_contracts.py` | Overujú iba hranicu `starts_at`. | **CHANGE** | Druhá hranica; nový oracle **pred** implementáciou (poradie z C05: testy červené pred kódom). | Červené pred kódom, zelené po |
| `scripts/mutation_check.py` | Mutácie viazané na presný text, ktorý CR-08 mení — napr. M7 (L188–189, `return now < starts_at`) — sa po zmene nenájdu a skript skončí chybou. To je zámer skriptu, nie porucha. | **CHANGE** | Upraviť texty; nové mutácie: hranica lehoty `<` → `<=`, SQL bez lehoty, vynechaný zápis lehoty. | Všetky zachytené |
| `TC-C08-01`, `TC-C08-02` | Regresia BR-02. | **CHECK** | Znova vykonať. | `PASS` |
| `TC-C08-03` | Očakávanie sa obracia (pozri návrh TC v evidence). | **CHANGE** | Nové Expected **pred** behom. | `PASS` proti novému Expected |
| `test_architecture_conformance.py` | (1) Ak pribudne funkcia v `services/reservations.py`, musí dostať prvok v `ELEMENTS`. (2) `FORBIDDEN_FOR_POLICY` nepozná `swilab.config` (riadok *Odkiaľ prichádza T*). (3) `_mentions_time()` (L419–424) pozná ako čas iba `now`, `starts_at`, `ends_at` — porovnanie s novým stĺpcom lehoty v operácii by `test_operacie_rozhoduju_iba_cez_politiku` nevidel. `TRANSITION_TIME_RULES` (L366–368) SQL formu živosti zámerne nestráži. | **CHANGE** | Doplniť `swilab.config` do zoznamu a nový stĺpec do `_mentions_time()`. | Zelený + mutácie „politika importuje konfiguráciu" a „operácia sama porovná lehotu" zachytené |
| Testy súbehu (REQ-05, REQ-16) | Zámky sa nemenia. **Súbeh schválenia s potvrdením toho istého prístroja dnes netestuje nič** — `test_concurrent_approvals_of_colliding_requests` začína z dvoch živých prekrývajúcich sa žiadostí a podľa vlastného docstringu kód so zámkom od kódu bez neho nerozlíši. Práve tento súbeh je jadrom R-06. | **CHECK** + nový test | Test na úrovni služby s explicitným `now`: potvrdenie `B` s `now` po lehote, potom schválenie `A` so starším `now` pred lehotou → `OVERLAP`, BR-02 drží. Bariéra netreba — poradie sa dá určiť priamo. | Nový test zelený; existujúce zelené |

**Release a obnova**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| Verzia (`pyproject.toml` L3, `main.py` L43 — `0.2.0`) | Mení sa správanie API. | **CHANGE** | `0.3.0`. | Release záznam |
| Baseline špecifikácie | v0.2 → v0.3 so schválením oboch členov. | **CHANGE** | §11. | Schválenie + tag |
| Rollback | **Pri variante A nie je návrat na v0.2 bezpečný bez prípravy.** Žiadosť po lehote ostáva zapísaná ako `PENDING_APPROVAL`; starý kód ju znova považuje za živú až do `starts_at` — blokuje a dá sa schváliť. Ak medzitým niekto na uvoľnený termín potvrdil rezerváciu, databáza drží živú žiadosť prekrývajúcu sa s `CONFIRMED`, čo je podľa v0.2 porušenie BR-02. Návrat späť má druhú pascu: žiadosti, ktoré podal starý kód, nemajú lehotu (`NULL`). V SQL je `NULL > now` neznáme, teda „neblokuje", kým politika by takú žiadosť mohla brať ako živú — dve formy živosti by sa rozišli a obmedzenie z CA-6 by sa nedalo znova zapnúť. | **CHANGE** | Postup rollbacku: (1) jedným `UPDATE` zapísať `EXPIRED` všetkým žiadostiam s lehotou `<= now()`, (2) odstrániť obmedzenie, (3) nasadiť starý kód. Postup návratu vpred: doplniť lehotu = `starts_at` riadkom s `NULL` (AC-09), až potom zapnúť obmedzenie. Zapísať do release záznamu. | Rollback aj návrat vpred raz vyskúšané nad databázou s dátami |
| R-04 v registri rizík | Trigger „akákoľvek zmena v `models.py`" sa touto zmenou spúšťa. | **CHECK** | Rozhodnúť o migráciách pred implementáciou v C09. | Rozhodnutie zapísané pri R-04 |

**Používateľ a prevádzka**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| Žiadateľ | Žiadosť môže prepadnúť bez upozornenia. Lehotu uvidí v odpovedi potvrdenia, inak nič (TBD-09, TBD-03). | **CHECK** | Stakeholder prijme ako obmedzenie, alebo sa rozsah rozšíri o oznámenie. | Akceptačný scenár / prijaté obmedzenie |
| Vedúci | Musí rozhodnúť do 48 h, ale nemá zoznam žiadostí (I-01). Môže prepadnúť viac žiadostí, než dnes. | **CHECK** | Stakeholder prijme; I-01 ostáva issue pre CP2. | Peer review |
| Víkendy a sviatky | Kalendárne hodiny — žiadosť z piatka poobede prepadne v nedeľu. | **CHECK** | Prijatie stakeholderom. | Peer review |
| Prístroje bez schvaľovania | Žiadosť nevzniká, nič sa nemení. | **NO IMPACT** | — | Regresia (AC-08) |

**Súbeh** — rozbor, nie iba riadok matice:

Čas sa odčíta **raz pri prijatí požiadavky** (BR-03) a zámky sa berú v poradí
rezervácia → prístroj (ADR-01). Najhorší prípad pri hranici lehoty: schválenie
žiadosti `A` príde sekundu pred jej lehotou (`now₁`), potvrdenie prekrývajúcej
sa rezervácie `B` sekundu po nej (`now₂`).

- Ak zámok prístroja získa prvé **potvrdenie `B`**: dotaz na prekryv s `now₂`
  žiadosť `A` nepočíta (vypršala), `B` sa zapíše a commitne — na prístroji so
  schvaľovaním ako nová živá žiadosť `PENDING_APPROVAL`. Schválenie `A` potom
  s `now₁` považuje `A` ešte za živú, ale dotaz na prekryv nájde
  **commitnutú** `B`. Tá blokuje aj pri `now₁`: jej `starts_at` je v budúcnosti
  a jej lehota vznikla z `now₂ > now₁`. Výsledok `OVERLAP`, `A` ostáva
  `PENDING_APPROVAL`.
- Ak ho získa prvé **schválenie `A`**: `A` je `CONFIRMED`, potvrdenie `B` nájde
  `A` → `OVERLAP`.

BR-02 teda platí v oboch poradiach — `READ COMMITTED` a zámok prístroja
(ADR-01) to zaručujú bez ohľadu na to, ktoré `now` operácia nesie: staršie
`now` môže len **pridať** živé žiadosti, nikdy ubrať blokujúcu rezerváciu.
Dôsledky zastaraného `now` sú sémantické a sú dva: (1) schválenie, zamietnutie
aj zrušenie, ktoré dorazilo pred lehotou a čakalo na zámok, ešte prejde;
(2) potvrdenie alebo dopyt na dostupnosť so starým `now` ešte započíta žiadosť,
ktorá medzitým vypršala, ako blokujúcu — falošné `OVERLAP` alebo `UNAVAILABLE`
o dĺžku čakania. To je riziko **R-06** — zatiaľ **NOT VERIFIED**. Žiadny test
z baseline tento súbeh nepokrýva (riadok *Testy súbehu*); C09 ho overí testom
na úrovni služby s explicitným `now`.

### Alternatives and comparable criteria

Päť reakcií, z toho dve bez nového mechanizmu (C, E) a jedna, ktorá mení
rozhodnutie z C02 (D). Porovnávame ich tými istými kritériami: splnenie AC,
rozsah a zdroje, dáta a nasadenie, failure modes, reverzibilita a overenie.
Číselné skóre ani odhad hodín zámerne nedávame — nemali by sme preň podklad;
rozsah je vyjadrený tým, čo pribudne.

| Varianta | Ako rieši potrebu | Splní AC? | Prínos | Rozsah a zdroje | Failure modes | Reverzibilita | Ako overíme |
| -------- | ----------------- | --------- | ------ | --------------- | ------------- | ------------- | ----------- |
| **A — lehota vyhodnocovaná pri čítaní** | Lehota sa určí a uloží pri vstupe do `PENDING_APPROVAL`. Vyhodnotia ju obe formy živosti, ktoré už existujú (predikát v politike a SQL v dotaze na prekryv). `EXPIRED` zapíšu OP-04 a OP-05 ako dnes. | **áno**, AC-01 až AC-10 | Ten istý mechanizmus, ktorý v0.2 už prijala pre BR-08 — bez nového procesu a bez nového zapisovateľa, ADR-01 ostáva v platnosti. | Jeden PR, jeden stĺpec + obmedzenie, zmeny v existujúcich funkciách (matica), žiadny nový runtime prvok. Zmena schémy bez migrácií (R-04). | Dve formy živosti sa musia zhodovať — zabudnutá jedna znamená žiadosť, ktorá sa nedá schváliť, a predsa blokuje (alebo naopak). V databáze ostáva po lehote zapísané `PENDING_APPROVAL`, kým na žiadosť niekto nesiahne (dnes to isté po `starts_at`, TBD-08). | **Horšia než pri B.** Návrat na v0.2 by oživil žiadosti po lehote; bezpečný je až po jednom `UPDATE`, ktorý im zapíše `EXPIRED` (riadok *Rollback* v matici). | Hranice 47:59:59 / 48:00:00 v predikáte, v politike, v SQL a v službe s explicitným `now`; cez API len žiadosť s lehotou v minulosti — hodiny API sa posunúť nedajú (limit z evidence). Test zhody oboch foriem; `TC-C08-01/02` regresia; `TC-C08-03` s novým Expected; nové mutácie. |
| **B — plánovač + strážca** | Periodický proces zapisuje `EXPIRED` žiadostiam po lehote. Strážca z A ostáva nutný: medzi lehotou a ďalším behom procesu sa žiadosť nesmie dať schváliť ani blokovať. | **áno**, ale iba **spolu so strážcom** z A. Samotný plánovač AC-02 až AC-04 nesplní — hranica by závisela od periódy behu. | Databáza ukazuje pravdivý stav (rieši TBD-08). Dáva miesto, odkiaľ by sa dalo pri vypršaní oznamovať (TBD-03). | **Celé A** a navyše nový runtime prvok (G4 je dnes jeden proces), jeho spúšťanie, konfigurácia a testy; nové ADR, lebo druhý zapisovateľ spúšťa „znovu otvoríme, keď…" ADR-01. | Výpadok a reštart plánovača, opakovanie, poradie zámkov proti schváleniu, pozorovateľnosť. | Lepšia než pri A: zastaviť proces; zapísané `EXPIRED` ostanú platné aj pre v0.2. Neisté ostávajú iba žiadosti medzi lehotou a posledným behom — tie treba dočistiť rovnako ako pri A. | Všetko z A + test procesu: reštart, oneskorený beh, súbeh zápisu `EXPIRED` so schválením. |
| **C — lehota pred začiatkom** | Vedúci musí rozhodnúť najneskôr X hodín pred `starts_at`. Netreba nový údaj — `starts_at` v dátach je. | **nie** — žiadosť podaná dva týždne vopred blokuje takmer dva týždne. AC-02 ani AC-04 (lehota od podania) nesplní. | Termín sa uvoľní aspoň X pred začiatkom. | Najmenší: predikát + SQL, bez zmeny schémy. | Nerieši jadro potreby — dlhé blokovanie. Zavádza ďalšie číslo X bez zdroja. | Ľahká: návrat kódu; žiadne nové dáta. Žiadosti, ktoré v0.3 už zapísala ako `EXPIRED`, ostanú vypršané — iba skôr, než by v0.2 dovolila. | Hranica `starts_at − X`. |
| **D — žiadosť neblokuje** | Vrátiť rozhodnutie C02 R-2: `PENDING_APPROVAL` prestane blokovať, o kolízii rozhodne až schválenie. | **nie** — AC-02 (pozdné schválenie ostáva možné až do `starts_at`), AC-07 (mení prijaté BR-02). | Bez čísla a bez nového údaja; zabudnutá žiadosť neblokuje nikdy. | Veľký: BR-02, REQ-03, REQ-05, REQ-13, rozsah ADR-01, význam `TC-C08-01/02`, diagramy a testy celej schvaľovacej časti. | Vracia problém, ktorý R-2 vyriešil: dve žiadosti na ten istý čas, schválenie zlyhá na poslednú chvíľu. | **Najťažšia:** pod v0.3 môžu vzniknúť prekrývajúce sa žiadosti; návrat na v0.2 by ich zmenil na porušenie BR-02, ktoré treba ručne rozriešiť zamietnutím. | Veľká regresia celej schvaľovacej časti. |
| **E — bez zmeny kódu** | Vedúci zabudnuté žiadosti ručne zamieta (REQ-12); obmedzenie sa prizná. | **nie** — AC-02 až AC-04 nič negarantuje. | Žiadna zmena kódu ani dát. | Žiadny kód; ručná práca vedúceho. | **Dnes ani neuskutočniteľné:** vedúci sa o žiadostiach nedozvie (TBD-03) a nemá ich zoznam (TBD-09) — musel by sa pýtať priamo databázy. | Netýka sa — nič sa nemení. | Doložený ručný postup — v súčasnom systéme ho nemáme ako vykonať. |

### Consequence of inaction / feasible compensation

Doložené `TC-C08-03`: žiadosť, o ktorej vedúci nerozhodne, blokuje prístroj až
do `starts_at` a v tom okamihu termín prepadne pre všetkých — žiadateľa
aj ostatných. Kompenzácia bez kódu existuje iba na papieri: vedúci môže
každú žiadosť zamietnuť (REQ-12), ale nemá ako sa o nej dozvedieť (issue I-01).
Nečinnosť teda nie je neutrálna — ponecháva stratu, ktorú systém podľa
Project Frame odstraňuje.

### Peer reviewer and challenged assumption

**Peer reviewer: Tomáš Krišica** — review prebehne v pull requeste tejto
vetvy, rovnako ako pri C03 a C05. **Stav: čaká.** Vetva zatiaľ nie je
zverejnená a PR neexistuje; vznikne, keď ho tím otvorí. Výsledok review sem
zapíše reviewer — kým sa to nestane, rozhodnutie nižšie je **návrh**, nie
peer-reviewed rozhodnutie.

**Stakeholder pri review.** Skutočného vedúceho laboratória projekt nemá —
rolu zapisuje tím. Navrhujeme, aby ju pri review zastúpil reviewer a za ňu
výslovne odpovedal na CA-1 a CA-7. Kým to neurobí, podmienka ACCEPT (2) nie je
splnená.

Predpoklady, ktoré má review výslovne napadnúť:

| # | Predpoklad | Prečo ho treba napadnúť | Výsledok review |
| - | ---------- | ----------------------- | --------------- |
| CA-1 | Hodnota **48 h**, **kalendárnych**, počítaná od **podania** | Číslo je predpoklad tímu, nie výrok vedúceho (riadok *Prečo práve 48 h*). Žiadosť z piatka poobede prepadne v nedeľu. Lehota od podania (nie od vzniku `DRAFT`, nie pred začiatkom) je podstatou CR. | *vyplní reviewer* |
| CA-2 | Žiadosti spred zmeny majú lehotu = `starts_at` (AC-09) | Alternatíva — počítať lehotu od nasadenia — by uvoľnila staré žiadosti skôr, ale spätne by zmenila pravidlo. | |
| CA-3 | Operácia, ktorá dorazila pred lehotou a čakala na zámok, sa posúdi podľa času príchodu (R-06) | Inak by sa čas musel odčítať až pod zámkom — zmena BR-03 („čas sa odčíta raz“) pre všetky operácie. | |
| CA-4 | Varianta A namiesto B | Po lehote ostáva v databáze `PENDING_APPROVAL` (dnes rovnako po `starts_at`, TBD-08) a rollback je ťažší než pri B. Výhodou je, že nepribudne proces ani zapisovateľ. | |
| CA-5 | T konfigurovateľná (predvolená 48 h), nie konštanta | 60-minútová lehota BR-03 je konštanta v kóde. Konfigurácia pridáva config smoke test a riziko zlej jednotky. | |
| CA-6 | Databázové obmedzenie „žiadosť ⇒ lehota vyplnená“ | Chráni priamy SQL zápis, ale predlžuje postup rollbacku (riadok *Rollback* v matici). | |
| CA-7 | Stakeholder prijíma kontrakt varianty A | Vypršanie sa prejaví až pri ďalšej operácii, nikto nedostane oznámenie a proaktívne ukončenie žiadosti sa nesľubuje (podmienka ACCEPT 2). | |

**Úloha pre reviewera mimo predpokladov:** obnoviť baseline na vlastnom stroji
podľa README (časť C08) a zapísať do evidence (krok 0) dátum, prostredie
a výsledok `pytest` aj `scripts/c08_baseline_tc.py`. Zadanie to uvádza ako
doklad fázy 0 („jiný člen obnoví stejný výchozí stav“); zatiaľ beh prebehol
iba na stroji T. Hrubého.

### Decision ACCEPT / REJECT / DEFER / NO_CHANGE, owner, date, rationale

| Položka | Obsah |
| ------- | ----- |
| **Rozhodnutie** | **ACCEPT — varianta A** *(návrh; platné po peer review)* |
| **Owner rozhodnutia** | Tomáš Hrubý |
| **Dátum návrhu** | 2026-11-15 |
| **Peer review** | Tomáš Krišica — čaká (vyššie) |
| **Zdôvodnenie** | A splní všetky AC s najmenšou deltou a tým istým mechanizmom, ktorý baseline v0.2 už prijala pre expiráciu v `starts_at`. Nepridáva zapisovateľa ani proces, takže ADR-01 platí ďalej. B obsahuje celé A — plánovač navyše prináša pravdivý stav v databáze (TBD-08) a ľahší rollback, ale žiadne AC ich nežiada a cenou je nový runtime prvok a otvorenie ADR-01. Ťažší rollback A preto riešime zapísaným postupom (jeden `UPDATE` pred návratom), nie plánovačom. C nerieši jadro potreby, D rozbíja prijaté BR-02 a vracia problém z C02 R-2, E nesplní AC a dnes sa nedá ani vykonať. |
| **Podmienky ACCEPT** | (1) Peer review potvrdí CA-1 až CA-7 alebo ich zmení — zmena CA-1, CA-2 alebo CA-7 mení AC a znamená nové review. (2) Stakeholder (CA-7) prijme, že vypršanie sa prejaví pri **ďalšej operácii**, ktorá žiadosť číta (OP-02 až OP-05): hneď po lehote neblokuje a nedá sa schváliť, ale v databáze ostane zapísané `PENDING_APPROVAL`, kým na ňu niekto nesiahne, a nikto nedostane oznámenie. Proaktívne ukončenie žiadosti sa nesľubuje — ten istý kontrakt dnes platí po `starts_at`. (3) Rozhodnutie o migráciách (R-04) padne **pred** kódom v C09. (4) Špecifikácia v0.3 je schválená oboma členmi pred implementáciou. |
| **Kedy prehodnotiť v prospech B** | Keď tím rozhodne TBD-08 (dočisťovanie v databáze), alebo keď pribudne požiadavka oznámiť vypršanie v okamihu lehoty (TBD-03) — to potrebuje proces spúšťaný časom, nie čítaním. |

### If ACCEPT: bounded C09 delta and new/regression test oracles

Platí až po peer review a po splnení podmienok ACCEPT.

| Odovzdanie | Obsah |
| ---------- | ----- |
| **Pred zmenou** | Commit `b0974e7`, BR-08 v0.2, `TC-C08-01/02/03` vykonané s výsledkom `PASS` ([evidence, krok 2](evidence-and-evolution.md#krok-2--vykonanie-a-výsledky-actual)), `253 passed`, 34 mutácií / 0 prežilo. |
| **Navrhnuté na prijatie** | CR-08: žiadosť je živá, kým `now < min(lehota, starts_at)`, kde lehota = podanie + T (48 h) určená pri podaní; po lehote neblokuje a prvý pokus o rozhodnutie alebo zrušenie ju zapíše ako `EXPIRED` a zamietne. |
| **Ohraničená delta** — v tomto poradí | 1. **Špecifikácia v0.3 a diagramy** (riadky CHANGE v matici, vrátane nálezu RV-5 v diagrame 3.5) — pred kódom. 2. **Architektúra**: text G3, závislosť pre T v G2, signatúry v H2 a C05.3–C05.8, kontrakt C05.4 a tabuľky C05.8. 3. **Testy, ktoré najprv padnú**: hranice predikátu, tabuľky politiky, zhoda oboch foriem živosti pri lehote, AC-06 na všetkých endpointoch, AC-10, config smoke, databázové obmedzenie, riadok spred zmeny, `FORBIDDEN` pred `EXPIRED`, súbeh schválenia s potvrdením pri lehote; `swilab.config` do `FORBIDDEN_FOR_POLICY` a lehota do `_mentions_time()`; nové Expected `TC-C08-03` a oracle v0.3 v skripte. 4. **Kód**: stĺpec s lehotou a obmedzenie v `models.py`, zápis lehoty pri vstupe do `PENDING_APPROVAL`, predikát + politika + SQL, texty zamietnutí, `Settings` + `.env.example`, pole v odpovedi, postup migrácie alebo prestavby databázy, postup rollbacku. 5. `scripts/mutation_check.py`: upravené texty + nové mutácie. 6. Evidence, README, verzia `0.3.0`. |
| **Nové test basis / oracle** | Každý riadok je **samostatná požiadavka nad čerstvou žiadosťou**, na úrovni služby s explicitným `now`: lehota − 1 s → schválenie `CONFIRMED` · presne lehota → zamietnuté `EXPIRED`, uložený stav `EXPIRED` · lehota + 1 s → `EXPIRED` · druhý pokus nad už zapísaným `EXPIRED` → `INVALID_STATE` · po lehote OP-02 → `AVAILABLE` a potvrdenie prekrývajúcej sa rezervácie prejde · `starts_at` skôr než lehota → vyprší v `starts_at` (AC-05) · odpoveď potvrdenia uvádza lehotu = podanie + T (AC-06) · T = 1 h z konfigurácie → lehota = podanie + 1 h; zmena T nemení lehotu už podanej žiadosti (AC-10) · žiadosť spred zmeny sa správa ako v0.2 (AC-09) · neoprávnený pokus nad vypršanou žiadosťou → `FORBIDDEN`, stav bez zmeny · priame vloženie žiadosti bez lehoty → odmietne databáza (ak sa obmedzenie prijme, CA-6). Cez HTTP: `409 EXPIRED` pre žiadosť vloženú s lehotou v minulosti (hodiny API sa posunúť nedajú). |
| **Regresia** | `TC-C08-01`, `TC-C08-02` s rovnakým výsledkom ako na baseline; celá sada — meniť sa smú iba očakávania riadkov, ktoré matica označuje CHANGE; testy súbehu REQ-05 a REQ-16; oprávnenia BR-06, BR-07; zrušenie `CONFIRMED` (60 min); `scripts/mutation_check.py` bez prežitej mutácie. |
| **Technické riziko** | `R-06` — operácie čakajúce na zámok sa posúdia podľa času príchodu; owner tím, **NOT VERIFIED**. `R-07` — viac prepadnutých žiadostí, kým vedúci nemá zoznam (I-01). `R-04` — zmena schémy bez migrácií. Register rizík v evidence. |
| **Práca v C09** | PR so zmenou; integračný beh (`pytest` + `scripts/c08_baseline_tc.py` proti bežiacej aplikácii); čistý checkout na stroji druhého člena; build/CI — projekt CI nemá: buď ho C09 zavedie, alebo zapíše ručný beh druhým členom ako náhradu; rollback aj návrat vpred raz vyskúšané nad databázou s dátami; rozhodnutie o vydaní (špecifikácia v0.3, verzia `0.3.0`, tag). |
| **Stav** | Všetko **PLANNED**. Nič nie je IMPLEMENTED ani VERIFIED — preto ani `R-06` nemá overené reziduálne riziko. |

### If NO_CHANGE / REJECT: independent current-baseline check and response

Neuplatňuje sa — rozhodnutie je ACCEPT. Nezávislý doklad dnešnej baseline,
o ktorý by sa NO_CHANGE musel opierať, aj tak existuje: `TC-C08-01/02`
(BR-02 drží) a `TC-C08-03` (obmedzenie trvá).

### Open uncertainty / monitoring or next decision trigger

- **Peer review** (CA-1 až CA-7) a obnovenie baseline reviewerom — kým
  neprebehnú, rozhodnutie je návrh.
- **Migrácie (R-04)** — termín 2026-11-09 z C07 uplynul a plán v repozitári
  nie je. Owner T. Hrubý; nový termín: pred prvým commitom kódu v C09 —
  je to podmienka ACCEPT.
- **v0.3 a F-03** — `REQ-16` (R-03) a CR-08 mieria na tú istú verziu
  špecifikácie; tím rozhodne, či pôjdu spolu.
- **Stakeholder** — 48 h a kalendárne hodiny sú zapísané tímom za stakeholdera.
  Ak by skutočný vedúci laboratória povedal iné číslo, mení sa iba hodnota T,
  nie mechanizmus — preto je T konfigurovateľná.
- **Trigger na variantu B:** rozhodnutie TBD-08 alebo požiadavka na oznámenie
  pri vypršaní (TBD-03).
