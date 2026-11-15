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
| **Zdroj** | Vedúci laboratória — stakeholder z Project Frame (vyššie), ktorý zodpovedá za drahé prístroje so schvaľovaním. Požiadavku nevzniesol nikto mimo tímu: tím ju zapisuje za túto rolu a opiera ju o obmedzenie, ktoré je v repozitári doložené od C02 (riadok *Doklad*). Hodnotu **48 h** zapisuje tím ako požiadavku stakeholdera — rozhodnutie T. Hrubého z 2026-11-15, ktoré preverí peer review. |
| **Potreba** | Žiadosť, o ktorej vedúci nerozhodne, nemá držať prístroj až do začiatku termínu. Prístroj sa má uvoľniť včas — tak, aby ho ešte mohol použiť niekto iný. |
| **Problém dnes** | Žiadosť `PENDING_APPROVAL` blokuje prístroj, kým o nej vedúci nerozhodne alebo kým nenastane jej `starts_at` (BR-08). Žiadosť podaná dva týždne vopred, o ktorej nikto nerozhodne, drží prístroj dva týždne — a keď v `starts_at` vyprší, termín už nikto iný nevyužije. |
| **Doklad** | C02, rozhodnutie R-2 ([change-impact-c02.md](change-impact-c02.md)): „Cena: zabudnutá žiadosť drží prístroj — preto expirácia." Expirácia pri `starts_at` však uvoľní prístroj v okamihu, keď termín začína. To je presne strata, ktorou BR-03 zdôvodňuje 60-minútovú lehotu („nikto to už nestihne využiť a prístroj stojí prázdny"). Navyše issue I-01 (C07): vedúci sa o žiadosti nedozvie — Notification Service nie je napojená (TBD-03) a systém nemá zoznam žiadostí (TBD-09). Zabudnutá žiadosť teda nie je okrajový prípad. Správanie je doložené behom `TC-C08-03` v evidence. |
| **Prínos** | Prístroj, o ktorom vedúci nerozhodol, sa uvoľní najneskôr 48 h po podaní žiadosti. Žiadateľ vie, dokedy sa o žiadosti rozhodne. |
| **Prečo sa otvára rozhodnutie z C02** | C02 R-3 a špecifikácia §10 lehotu „48 h" zamietli, lebo „nemá zdroj a expirácia viazaná na `starts_at` rieši ten istý problém bez čísla". Prvá časť dnes neplatí — zdrojom je tento CR. Druhá neplatila ani vtedy: expirácia v `starts_at` prístroj pre nikoho iného neuvoľní, len ukončí žiadosť. |

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

**V rozsahu:** BR-08 a všetko, čo na ňom stojí (OP-02 až OP-05); zistiteľný
okamih podania žiadosti; lehota viditeľná pre žiadateľa.

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
| Podľa akého času sa lehota počíta? | Podľa `now` tej operácie potvrdenia, ktorá žiadosť podala: hodiny aplikačného servera v UTC, odčítané raz na operáciu (BR-03, `clock.now()`). Okamih podania musí byť uložený ako `timestamptz`. | Ten istý zdroj času ako pri všetkých ostatných pravidlách. Pásmo session je fixované na UTC (F-01), takže uložený okamih nezávisí od servera. |
| Od čoho sa lehota počíta? | Od **podania** žiadosti, nie od vzniku `DRAFT` (`created_at`). | `DRAFT` môže ležať dni a nikoho neblokuje. Lehota má merať čas, počas ktorého žiadosť **blokuje** prístroj. |
| Čo presne v hranici 48:00:00? | V okamihu `podanie + 48 h` je žiadosť **vypršaná**; o sekundu skôr je živá. | Ostrá polootvorená hranica ako v BR-01, BR-04 a BR-08 — jeden význam slova „do" v celom systéme. |
| Kalendárne, alebo pracovné hodiny? | Kalendárne. | Systém pracovný kalendár nemá a zaviesť ho je samostatná zmena. Dôsledok (žiadosť podaná v piatok poobede vyprší v nedeľu) je riziko, ktoré musí stakeholder prijať — preto je v peer review. |
| Nový stav, alebo `EXPIRED`? | Existujúci `EXPIRED`, žiadny nový stav. | Význam je ten istý: žiadosť prepadla bez rozhodnutia. Všetky pravidlá pre `EXPIRED` (koncový stav, `INVALID_STATE` pri zrušení, REQ-14) platia bez zmeny; enum v databáze sa nemení. Či vypršala kvôli lehote, alebo kvôli `starts_at`, sa dá odvodiť z uložených časov. |
| Čo uvidí žiadateľ? | V odpovedi potvrdenia, ktoré žiadosť vytvorí, okamih, dokedy sa musí rozhodnúť. Po ňom OP-02 vráti `AVAILABLE` a pokus o schválenie, zamietnutie alebo zrušenie skončí zamietnutím `EXPIRED`. | Systém nemá čítaciu operáciu (TBD-09) ani oznámenia (TBD-03). Odpoveď potvrdenia je jediné miesto, kde sa žiadateľ lehotu dozvie. |
| Čo so žiadosťami podanými pred zmenou? | Platí pre ne doterajšie pravidlo: lehota = ich `starts_at`. | Žiadna žiadosť nesmie prepadnúť spätne podľa pravidla, ktoré pri jej podaní neplatilo — a ich okamih podania systém ani nepozná. Dnes také riadky trvalo neexistujú (kontajner bez volume, R-04); pravidlo platí pre každé ďalšie nasadenie s dátami. |
| Opakovaný pokus? | Prvý pokus po lehote zapíše `EXPIRED` a vráti zamietnutie `EXPIRED`; každý ďalší dostane `INVALID_STATE`. | Je to dnešné správanie OP-04 a OP-05 pre už zapísaný `EXPIRED` — CR ho nemení. |
| Oneskorený pokus (čakal na zámok)? | Posudzuje sa podľa `now` odčítaného pri prijatí požiadavky. Schválenie prijaté sekundu pred lehotou, ktoré čakalo na zámok, ešte prejde. | Tá istá sémantika ako dnes pri `starts_at` (BR-03: čas sa odčíta raz). BR-02 tým ohrozené nie je — rozbor v analýze dopadu. Zostáva ako riziko `R-06` (register rizík v evidence). |

### Acceptance conditions

Podmienky sú pozorovateľné zvonku — cez API a cez uložený stav — a nepredpisujú
mechanizmus.

| ID | Druh | Podmienka |
| -- | ---- | --------- |
| **AC-01** | pozitívna | Oprávnený vedúci schváli živú žiadosť skôr než 48 h po jej podaní a pred `starts_at`, pri splnení ostatných podmienok REQ-11 → `CONFIRMED`. *(Dnešné správanie, ktoré musí zostať.)* |
| **AC-02** | negatívna | Po uplynutí 48 h od podania žiadosť nemôže dosiahnuť `CONFIRMED` ani `REJECTED`. Pokus o schválenie, zamietnutie alebo zrušenie skončí zamietnutím `EXPIRED` a stav sa zapíše ako `EXPIRED`. |
| **AC-03** | negatívna | Po uplynutí lehoty žiadosť **neblokuje** prístroj: OP-02 ju nezapočíta do kolízií a OP-03 prekrývajúcej sa rezervácie ju ignoruje — bez ohľadu na to, aký stav má zapísaný. |
| **AC-04** | hranica | Podanie + 47:59:59 → žiadosť je živá (schválenie prejde, blokuje). Presne podanie + 48:00:00 → vypršaná. Blokovanie (OP-02, OP-03) a rozhodovanie (OP-04, OP-05) dávajú na hranici **tú istú** odpoveď. |
| **AC-05** | hranica | Ak `starts_at` nastane skôr než koniec lehoty, žiadosť vyprší v `starts_at` — dnešné BR-08 ostáva v platnosti. |
| **AC-06** | pozorovateľnosť | Odpoveď potvrdenia, ktoré vytvorí žiadosť, uvádza okamih, do ktorého musí vedúci rozhodnúť. |
| **AC-07** | regresia | BR-02: dve blokujúce rezervácie toho istého prístroja sa neprekrývajú. `TC-C08-01` a `TC-C08-02` dajú rovnaký výsledok ako na baseline; súbežné potvrdenia a schválenia (REQ-05) aj súbeh nad jednou rezerváciou (REQ-16) sa správajú ako doteraz. |
| **AC-08** | regresia | Create, Cancel (60-minútová lehota BR-03), certifikácia (BR-04), neaktívny prístroj (BR-05), oprávnenia (BR-06, BR-07) a prístroje bez schvaľovania sa nemenia. Sada z baseline prejde bez zmeny očakávaní okrem riadkov, ktoré CR-08 výslovne mení. |
| **AC-09** | dáta | Žiadosť podaná pred nasadením zmeny sa správa ako doteraz — vyprší v `starts_at`. |

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
   je v matici nižšie ako riadok alebo ako zdôvodnené NO IMPACT.
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
| priamy zápis do databázy | **áno** — `tests/conftest.py` (`make_reservation`, L136–153), `scripts/demo.py` (L114 vkladá `PENDING_APPROVAL`), `scripts/c08_baseline_tc.py`, ručné SQL podľa TBD-02 | CHECK — žiadosť vložená bez údaja o podaní by bola „živá do `starts_at`" aj po zmene. API-only kontrola ju nezachytí; databázové obmedzenie áno (riadok *Priamy SQL zápis* nižšie). |

Všetky akcie v matici sú **PLANNED** — v C08 nie je implementované nič.
Za každý riadok zodpovedá, kým tím nerozdelí prácu v C09, **vlastník CR-08
(T. Hrubý)**.

### Impact Matrix

Odkazy `L…` sú riadky súborov pri `b0974e7`.

**Požiadavky a pravidlá — `docs/specification.md`**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| BR-08 (L251–293) | Pribúda druhá hranica živosti. Rozdelenie *vyhodnotenie* (každá operácia) / *zápis* (iba OP-04, OP-05) zostáva. | **CHANGE** | Nové znenie: živá, kým `now < min(podanie + T, starts_at)`; ostrá hranica; T = 48 h a jej zdroj (CR-08); pravidlo pre žiadosti spred zmeny (AC-09). | Review v0.3; príklady overenia sú test basis AC-02 až AC-05, AC-09 |
| Definícia alokácie (L72–100) | Píše „živá = `currentTime < starts_at`". | **CHANGE** | Odkázať na BR-08 namiesto vzorca, aby vzorec žil na jednom mieste. | Review v0.3 |
| BR-02 (L126–144) | Odkazuje na „živá podľa BR-08", takže znenie sa nemení samo. Pomenovaný dôsledok (v databáze môže ležať `PENDING_APPROVAL` prekrývajúca sa s `CONFIRMED`) teraz nastane aj pri žiadosti po lehote. | **CHECK** | Overiť, že text platí bez zmeny; dôsledok rozšíriť o lehotu. | Review v0.3 |
| BR-03, riadok `PENDING_APPROVAL` (L151) a odsek L184–186 | Opakuje vzorec `currentTime < starts_at`. | **CHANGE** | Odkaz na BR-08. | Review v0.3 |
| REQ-03, REQ-13 (L385–394) | Odkazujú na BR-08 — znenie platí. | **CHECK** | — | Test OP-02 po lehote |
| Príklady OP-02 (L457–470), odsek L482–486 | Príklady pozná iba hranicu `starts_at`. | **CHANGE** | Pridať žiadosť po lehote → `AVAILABLE`. | Príklady = test basis |
| REQ-04 (L502–506) | Odkazuje na živú žiadosť (BR-08). | **CHECK** | — | `TC-C08-03` krok 4 s novým Expected |
| REQ-10 (L508–511), stav po OP-03 (L534–541) | AC-06: odpoveď pri vzniku žiadosti uvádza lehotu; pri vstupe do `PENDING_APPROVAL` sa zaznamená okamih podania. | **CHANGE** | Doplniť do REQ-10 a do stavu po úspešnom vykonaní. | Test API (AC-06) |
| REQ-11, REQ-12 (L773–782) | Odkazujú na „nevypršala (BR-08)" a na „živosť". | **CHECK** | — | Testy OP-05 |
| REQ-14 (L656–662), REQ-15 (L784–788) | Doslova „ktorej `starts_at` už nastal". | **CHANGE** | „ktorá vypršala (BR-08)". | Review v0.3 + testy |
| OP-04 chyby a príklady (L698–701, L740–744); OP-05 predpoklady, chyby, príklady (L790–795, L844, L864–865) | Vzorec so `starts_at`. | **CHANGE** | Prepísať a doplniť príklady 47:59:59 / 48:00:00. | Príklady = test basis |
| TBD-07 (L918) | Jediný vedúci: jeho žiadosti prepadnú po 48 h, nie až v `starts_at`. Vedomý dôsledok BR-07 sa zhoršuje. | **CHECK** | Doplniť dôsledok. | Review v0.3 |
| TBD-08 (L919) | Varianta A dočisťovanie nerieši — ostáva otvorené. | **CHECK** | — | — |
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
| ADR-01 (L538–600) | Varianta A nepridáva zapisovateľa ani proces → ADR-01 platí. Varianta B by spustila „znovu otvoríme, keď… pribudne druhý zapisovateľ… dávka". | **CHECK** | Pri A nič; pri B nové ADR. | Review rozhodnutia |
| Odkiaľ prichádza T — závislosti G2 (L672–689) | `Domain Rules` ani politika prechodov nesmú čítať konfiguráciu (G2: „`Domain Rules` ani `Clock` nezávisia od ničoho"; stráži `test_politika_prechodov_nepozna_perzistenciu_ani_hodiny`). T musí prísť zvonku ako hodnota — rovnako ako `now`. | **CHECK** | Určiť, kto T číta (API alebo lifecycle cez `Reservation Store`), a ak pribudne závislosť, zapísať ju do tabuľky G2. | `test_architecture_conformance.py` zelený |
| Priamy SQL zápis | Existuje (inventár vyššie). Aplikácia BR-02 ani lehotu v databáze nevynucuje (ADR-01, dôsledok 1). | **CHECK** | Rozhodnúť databázové obmedzenie „`PENDING_APPROVAL` ⇒ okamih podania je vyplnený" — chráni aj priamy zápis. Nezávisí od `now()`, takže námietka ADR-01 proti constraintu naň neplatí. | Test: vloženie žiadosti bez údaja zlyhá |
| `Clock` (`clock.py`) | Zdroj času sa nemení. | **NO IMPACT** | — | — |
| Notification Integration | Neexistuje (TBD-03). | **NO IMPACT** | — | — |

**Detailný návrh a kód (C05) — `src/swilab/`**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `rules.approval_request_is_alive()` (L33–43) | Jediný predikát živosti; dnes `now < starts_at`. | **CHANGE** | Druhá hranica. | `test_rule_boundaries_are_strict` rozšírený o 47:59:59 / 48:00:00 |
| `expiry_decision()` (L60–80), `cancel_decision()` (L96–102); kontrakt C05.4 (L1104–1145); tabuľky C05.8 (L1430–1461) | Podpis `(state, starts_at, now)` nestačí — chýba údaj o podaní. Tvary `Decision` sa nemenia. | **CHANGE** | Nový vstup politiky; riadky tabuliek `now >= starts_at` → „žiadosť nežije". | `test_expiry_decision_boundary`, `test_cancel_decision_table` |
| `confirm_decision()` (L124–156) | Rozhoduje cieľ `PENDING_APPROVAL`, lehotu nepočíta. | **CHECK** | Rozhodnúť, kde vznikne okamih podania — vo vykonávateľovi (`confirm_reservation` alebo `enter_blocking_state`). Politika ostáva čistá. | Contract test |
| `_overlapping_blocking()`, SQL (L191–201) | Druhá forma živosti (`starts_at > now`). | **CHANGE** | Doplniť lehotu. | `test_liveness_in_the_overlap_query_agrees_with_the_policy` rozšírený o hranicu 48 h |
| `confirm_reservation()` (L382–436), `enter_blocking_state()` (L207–251) | Vstup do `PENDING_APPROVAL` musí zaznamenať okamih podania. | **CHANGE** | Miesto zápisu podľa C05 „Kde sa čo mení". | Contract test na zápis |
| `expire_request_if_dead()` (L268–288), `decide_reservation()` (L485–557), `cancel_reservation()` (L439–482) | Odovzdajú politike nový údaj; logika sa nemení. | **CHANGE** (podpis) | — | Existujúce testy OP-04, OP-05 |
| `check_availability()` (L332–379) | Kód bez zmeny; výsledok sa mení cez `_overlapping_blocking()`. | **CHECK** | — | Test OP-02 po lehote (AC-03) |
| `_write_expired()`, tvary `Decision`, poradie zamietnutí DD-7 | Nemenia sa — vypršanie po lehote je ten istý tvar `EXPIRED`/`EXPIRED`. | **NO IMPACT** | — | `test_policy_is_total_and_returns_only_shapes_from_the_contract`, `test_decision_accepts_exactly_the_four_shapes` |
| OP-01 `create_reservation()`, BR-04, BR-05, BR-03 pre `CONFIRMED`, REQ-09 | S lehotou žiadosti nesúvisia. | **NO IMPACT** | — | Regresia celej sady (AC-08) |

**Perzistencia a dáta**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `models.Reservation` (L73–101) | Chýba údaj o podaní žiadosti. | **CHANGE** | Nový `timestamptz` stĺpec (alebo priamo uložená lehota — detail C09). | Výpis schémy a nový odtlačok |
| `create_all()` pri štarte (`main.py` L35–40) | `create_all()` existujúcu tabuľku nezmení. Stará databáza by nemala nový stĺpec a aplikácia by padla na prvom `SELECT`. | **CHANGE** | Rozhodnúť migráciu (R-04) alebo zapísanú prestavbu databázy. | Release záznam s presným postupom |
| Žiadosti `PENDING_APPROVAL` spred zmeny | AC-09: lehota = ich `starts_at`. | **CHECK** | Pravidlo pri migrácii (backfill), nie v kóde. | Test na riadku vloženom pred migráciou |
| Typ a pásmo času | `timestamptz` + session v UTC (F-01). | **NO IMPACT** | — | `test_session_timezone_is_utc_whatever_the_server_says` |
| Priami zapisovatelia `PENDING_APPROVAL`: `conftest.make_reservation` (L136–153), `demo.py` (L114), `c08_baseline_tc.py` | Po zmene musia vyplniť údaj o podaní, inak ich dáta nebudú zodpovedať pravidlu. | **CHANGE** | Doplniť. | Sada zelená |

**API a oprávnenia**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `ReservationResponse` (`api/reservations.py` L42–48), `_to_response()` (L67–75) | AC-06. | **CHANGE** | Aditívne pole s lehotou — existujúci klienti sa nerozbijú. | Test API |
| `STATUS_BY_CODE` (`main.py` L17–31), `ErrorCode.EXPIRED` | `EXPIRED` → `409` už existuje. | **NO IMPACT** | — | — |
| Endpointy | Žiadny nový. | **NO IMPACT** | — | — |
| BR-06, BR-07 | Oprávnenie sa overuje pred stavom a časom (DD-7) — nemení sa. | **CHECK** | — | `test_student_cannot_decide`, `test_supervisor_cannot_decide_own_request`, `test_authorization_is_judged_before_the_state` |

**Konfigurácia a nasadenie**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `config.Settings` (`config.py` L4–12), `.env.example` | T je konfigurovateľná s predvolenou hodnotou 48 h. | **CHANGE** | Jednotka v názve parametra (hodiny), validácia `> 0`, hodnota v `.env.example` a README. | Config smoke: aplikácia s T = 1 h → odpoveď potvrdenia uvádza podanie + 1 h |
| Pásmo, `docker-compose.yml` | Bez zmeny. | **NO IMPACT** | — | — |

**Testy a evidence**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| `test_transitions.py`, `test_approval.py` (`test_expiry_boundary`, `test_cancel_expired_request_expires_it`, `test_reject_of_expired_request_expires_it`, `test_expired_request_does_not_block`, `test_confirm_ignores_expired_request`), `test_lifecycle_contracts.py` | Overujú iba hranicu `starts_at`. | **CHANGE** | Druhá hranica; nový oracle **pred** implementáciou (poradie z C05: testy červené pred kódom). | Červené pred kódom, zelené po |
| `scripts/mutation_check.py` | Mutácie viazané na presný text, ktorý CR-08 mení — napr. M7 (L188–189, `return now < starts_at`) — sa po zmene nenájdu a skript skončí chybou. To je zámer skriptu, nie porucha. | **CHANGE** | Upraviť texty; nové mutácie: hranica lehoty `<` → `<=`, SQL bez lehoty, vynechaný zápis podania. | Všetky zachytené |
| `TC-C08-01`, `TC-C08-02` | Regresia BR-02. | **CHECK** | Znova vykonať. | `PASS` |
| `TC-C08-03` | Očakávanie sa obracia (pozri návrh TC v evidence). | **CHANGE** | Nové Expected **pred** behom. | `PASS` proti novému Expected |
| `test_architecture_conformance.py` | Ak pribudne funkcia v `services/reservations.py`, musí dostať prvok v `ELEMENTS`. | **CHECK** | — | Zelený |
| Testy súbehu (REQ-05, REQ-16) | Zámky sa nemenia. | **CHECK** | — | Zelené |

**Release a obnova**

| Artefakt | Dopad a prečo | Rozhodnutie | Nutná akcia | Dôkaz dokončenia |
| -------- | ------------- | ----------- | ----------- | ---------------- |
| Verzia (`pyproject.toml` L3, `main.py` L43 — `0.2.0`) | Mení sa správanie API. | **CHANGE** | `0.3.0`. | Release záznam |
| Baseline špecifikácie | v0.2 → v0.3 so schválením oboch členov. | **CHANGE** | §11. | Schválenie + tag |
| Rollback | Starý kód nový stĺpec ignoruje, kým je `NULL`-ovateľný. Ak však pribudne databázové obmedzenie „`PENDING_APPROVAL` ⇒ podanie vyplnené", starý kód pri potvrdení na prístroji so schvaľovaním zlyhá. Rollback teda znamená odstrániť obmedzenie, stĺpec môže ostať. | **CHECK** | Zapísať presný postup do release záznamu. | Rollback raz vyskúšaný |
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
žiadosti `A` príde sekundu pred lehotou (`now₁`), potvrdenie prekrývajúcej sa
rezervácie `B` sekundu po nej (`now₂`).

- Ak zámok prístroja získa prvé **potvrdenie `B`**: dotaz na prekryv s `now₂`
  žiadosť `A` nepočíta (vypršala), `B` sa zapíše a commitne — na prístroji so
  schvaľovaním ako nová živá žiadosť `PENDING_APPROVAL`. Schválenie `A` potom
  s `now₁` považuje `A` ešte za živú, ale dotaz na prekryv nájde
  **commitnutú** `B`, ktorá blokuje aj pri `now₁` → `OVERLAP`, `A` ostáva
  `PENDING_APPROVAL`.
- Ak ho získa prvé **schválenie `A`**: `A` je `CONFIRMED`, potvrdenie `B` nájde
  `A` → `OVERLAP`.

BR-02 teda platí v oboch poradiach — `READ COMMITTED` a zámok prístroja
(ADR-01) to zaručujú bez ohľadu na to, ktoré `now` operácia nesie. Jediný
dôsledok zastaraného `now` je sémantický: schválenie, ktoré dorazilo pred
lehotou a čakalo na zámok, ešte prejde. To je riziko **R-06** — zatiaľ
**NOT VERIFIED**; overí ho test súbehu s bariérou pri hranici lehoty v C09.

### Alternatives and comparable criteria

Päť reakcií, z toho dve bez nového mechanizmu (C, E) a jedna, ktorá mení
rozhodnutie z C02 (D). Porovnávame ich tými istými kritériami: splnenie AC,
rozsah zmeny, dáta a nasadenie, failure modes, reverzibilita a overenie.
Číselné skóre zámerne nedávame — nemali by sme preň podklad.

| Varianta | Ako rieši potrebu | Splní AC? | Prínos | Náklady, failure modes, rollback | Ako overíme |
| -------- | ----------------- | --------- | ------ | -------------------------------- | ----------- |
| **A — lehota vyhodnocovaná pri čítaní** | Okamih podania sa zaznamená pri vstupe do `PENDING_APPROVAL`. Lehotu vyhodnotia obe formy živosti, ktoré už existujú (predikát v politike a SQL v dotaze na prekryv). `EXPIRED` zapíšu OP-04 a OP-05 ako dnes. | **áno**, AC-01 až AC-09 | Ten istý mechanizmus, ktorý v0.2 už prijala pre BR-08 — bez nového procesu a bez nového zapisovateľa, ADR-01 ostáva v platnosti. Malá, ohraničená delta. | Zmena schémy bez migrácií (R-04). V databáze ostáva po lehote zapísané `PENDING_APPROVAL`, kým na žiadosť niekto nesiahne (dnes to isté po `starts_at`, TBD-08). Dve formy živosti sa musia zhodovať — zabudnutá jedna z nich znamená žiadosť, ktorá sa nedá schváliť, a predsa blokuje (alebo naopak). Rollback: vrátiť kód, stĺpec môže ostať; databázové obmedzenie treba odstrániť. | Hranice 47:59:59 / 48:00:00 v politike, v SQL aj cez API; test zhody oboch foriem; `TC-C08-01/02` regresia; `TC-C08-03` s novým Expected; nové mutácie. |
| **B — plánovač + strážca** | Periodický proces zapisuje `EXPIRED` žiadostiam po lehote. Strážca z A ostáva nutný: medzi lehotou a ďalším behom procesu sa žiadosť nesmie dať schváliť ani blokovať. | **áno**, ale iba **spolu so strážcom** z A. Samotný plánovač AC-02 až AC-04 nesplní — hranica by závisela od periódy behu. | Databáza ukazuje pravdivý stav (rieši TBD-08). Dáva miesto, odkiaľ by sa dalo pri vypršaní oznamovať (TBD-03). | **B obsahuje celé A** a navyše: nový runtime prvok (G4 je dnes jeden proces), výpadok a reštart plánovača, opakovanie, poradie zámkov proti schváleniu, pozorovateľnosť. Druhý zapisovateľ spúšťa „znovu otvoríme, keď…" ADR-01. Rollback: zastaviť proces; zapísané `EXPIRED` ostanú platné. | Všetko z A + test procesu: reštart, oneskorený beh, súbeh zápisu `EXPIRED` so schválením. |
| **C — lehota pred začiatkom** | Vedúci musí rozhodnúť najneskôr X hodín pred `starts_at`. Netreba nový údaj — `starts_at` v dátach je. | **nie** — žiadosť podaná dva týždne vopred blokuje takmer dva týždne. AC-02 ani AC-04 (lehota od podania) nesplní. | Bez zmeny schémy, najmenšia delta (predikát + SQL). Termín sa uvoľní aspoň X pred začiatkom. | Nerieši jadro potreby — dlhé blokovanie. Zavádza ďalšie číslo X bez zdroja. | Hranica `starts_at − X`. |
| **D — žiadosť neblokuje** | Vrátiť rozhodnutie C02 R-2: `PENDING_APPROVAL` prestane blokovať, o kolízii rozhodne až schválenie. | **nie** — AC-02 (pozdné schválenie ostáva možné až do `starts_at`), AC-07 (mení prijaté BR-02). | Bez čísla a bez nového údaja; zabudnutá žiadosť neblokuje nikdy. | Vracia problém, ktorý R-2 vyriešil: dve žiadosti na ten istý čas, schválenie zlyhá na poslednú chvíľu. Mení BR-02, REQ-03, REQ-05, REQ-13, rozsah ADR-01 a význam `TC-C08-01/02`. | Veľká regresia celej schvaľovacej časti. |
| **E — bez zmeny kódu** | Vedúci zabudnuté žiadosti ručne zamieta (REQ-12); obmedzenie sa prizná. | **nie** — AC-02 až AC-04 nič negarantuje. | Žiadna zmena kódu ani dát. | **Dnes ani neuskutočniteľné:** vedúci sa o žiadostiach nedozvie (TBD-03) a nemá ich zoznam (TBD-09) — musel by sa pýtať priamo databázy. | Doložený ručný postup — v súčasnom systéme ho nemáme ako vykonať. |

### Consequence of inaction / feasible compensation

Doložené `TC-C08-03`: žiadosť, o ktorej vedúci nerozhodne, blokuje prístroj až
do `starts_at` a v tom okamihu termín prepadne pre všetkých — žiadateľa
aj ostatných. Kompenzácia bez kódu existuje iba na papieri: vedúci môže
každú žiadosť zamietnuť (REQ-12), ale nemá ako sa o nej dozvedieť (issue I-01).
Nečinnosť teda nie je neutrálna — ponecháva stratu, ktorú systém podľa
Project Frame odstraňuje.

### Peer reviewer and challenged assumption

**Peer reviewer: Tomáš Krišica** — review prebehne v pull requeste tejto
vetvy, rovnako ako pri C03 a C05. **Stav: čaká na review.** Výsledok sem
zapíše reviewer; kým sa to nestane, rozhodnutie nižšie je **návrh**, nie
peer-reviewed rozhodnutie.

Predpoklady, ktoré má review výslovne napadnúť:

| # | Predpoklad | Prečo ho treba napadnúť | Výsledok review |
| - | ---------- | ----------------------- | --------------- |
| CA-1 | 48 h **kalendárnych** od **podania** | Žiadosť z piatka poobede prepadne v nedeľu. Lehota od podania (nie od vzniku `DRAFT`, nie pred začiatkom) je podstatou CR. | *vyplní reviewer* |
| CA-2 | Žiadosti spred zmeny majú lehotu = `starts_at` (AC-09) | Alternatíva — počítať lehotu od nasadenia — by uvoľnila staré žiadosti skôr, ale spätne by zmenila pravidlo. | |
| CA-3 | Schválenie, ktoré dorazilo pred lehotou a čakalo na zámok, ešte prejde (R-06) | Inak by sa čas musel odčítať až pod zámkom — zmena BR-03 („čas sa odčíta raz") pre všetky operácie. | |
| CA-4 | Varianta A namiesto B: po lehote ostáva v databáze `PENDING_APPROVAL` | Kto číta databázu priamo, vidí nepravdivý stav — dnes rovnako po `starts_at` (TBD-08). | |
| CA-5 | T konfigurovateľná (predvolená 48 h), nie konštanta | 60-minútová lehota BR-03 je konštanta v kóde. Konfigurácia pridáva config smoke test a riziko zlej jednotky. | |
| CA-6 | Databázové obmedzenie „žiadosť ⇒ okamih podania" | Chráni priamy SQL zápis, ale komplikuje rollback (riadok *Rollback* v matici). | |

### Decision ACCEPT / REJECT / DEFER / NO_CHANGE, owner, date, rationale

| Položka | Obsah |
| ------- | ----- |
| **Rozhodnutie** | **ACCEPT — varianta A** *(návrh; platné po peer review)* |
| **Owner rozhodnutia** | Tomáš Hrubý |
| **Dátum návrhu** | 2026-11-15 |
| **Peer review** | Tomáš Krišica — čaká (vyššie) |
| **Zdôvodnenie** | A splní všetky AC s najmenšou deltou a tým istým mechanizmom, ktorý baseline v0.2 už prijala pre expiráciu v `starts_at`. Nepridáva zapisovateľa ani proces, takže ADR-01 platí ďalej. B obsahuje celé A — plánovač navyše prináša iba pravdivý stav v databáze (TBD-08), ktorý žiadne AC nežiada, za cenu nového runtime prvku a otvorenia ADR-01. C nerieši jadro potreby, D rozbíja prijaté BR-02 a vracia problém z C02 R-2, E nesplní AC a dnes sa nedá ani vykonať. |
| **Podmienky ACCEPT** | (1) Peer review potvrdí CA-1 až CA-6 alebo ich zmení — zmena CA-1 alebo CA-2 mení AC a znamená nové review. (2) Stakeholder prijme, že vypršanie sa prejaví pri **ďalšej operácii**, ktorá žiadosť číta (OP-02 až OP-05): hneď po lehote neblokuje a nedá sa schváliť, ale v databáze ostane zapísané `PENDING_APPROVAL`, kým na ňu niekto nesiahne, a nikto nedostane oznámenie. Proaktívne ukončenie žiadosti sa nesľubuje — ten istý kontrakt dnes platí po `starts_at`. (3) Rozhodnutie o migráciách (R-04) padne **pred** kódom v C09. (4) Špecifikácia v0.3 je schválená oboma členmi pred implementáciou. |
| **Kedy prehodnotiť v prospech B** | Keď tím rozhodne TBD-08 (dočisťovanie v databáze), alebo keď pribudne požiadavka oznámiť vypršanie v okamihu lehoty (TBD-03) — to potrebuje proces spúšťaný časom, nie čítaním. |

### If ACCEPT: bounded C09 delta and new/regression test oracles

| Odovzdanie | Obsah |
| ---------- | ----- |
| **Pred zmenou** | Commit `b0974e7`, BR-08 v0.2, `TC-C08-01/02/03` vykonané s výsledkom `PASS` ([evidence, krok 2](evidence-and-evolution.md#krok-2--vykonanie-a-výsledky-actual)), `253 passed`, 34 mutácií / 0 prežilo. |
| **Prijaté** | CR-08: žiadosť je živá, kým `now < min(podanie + 48 h, starts_at)`; po lehote neblokuje a pokus o rozhodnutie alebo zrušenie ju zapíše ako `EXPIRED` a zamietne. |
| **Ohraničená delta** — v tomto poradí | 1. **Špecifikácia v0.3 a diagramy** (riadky CHANGE v matici, vrátane nálezu RV-5 v diagrame 3.5) — pred kódom. 2. **Architektúra**: text G3, závislosť pre T v G2, kontrakt C05.4 a tabuľky C05.8. 3. **Testy, ktoré najprv padnú**: hranice predikátu, tabuľky politiky, zhoda oboch foriem živosti pri 48 h, AC-06 v API, config smoke, databázové obmedzenie, riadok spred zmeny; nové Expected `TC-C08-03`. 4. **Kód**: stĺpec a obmedzenie v `models.py`, zápis okamihu podania pri vstupe do `PENDING_APPROVAL`, predikát + politika + SQL, `Settings` + `.env.example`, pole v odpovedi, postup migrácie alebo prestavby databázy. 5. `scripts/mutation_check.py`: upravené texty + nové mutácie. 6. Evidence, README, verzia `0.3.0`. |
| **Nové test basis / oracle** | podanie + 47:59:59 → schválenie `200 CONFIRMED` · podanie + 48:00:00 → `409 EXPIRED`, uložený stav `EXPIRED` · podanie + 48:00:01 → `409 EXPIRED` · po lehote OP-02 → `AVAILABLE` a potvrdenie prekrývajúcej sa rezervácie prejde · `starts_at` skôr než lehota → vyprší v `starts_at` (AC-05) · odpoveď potvrdenia uvádza lehotu = podanie + T (AC-06) · T = 1 h z konfigurácie → lehota = podanie + 1 h · žiadosť spred zmeny sa správa ako v0.2 (AC-09) · priame vloženie žiadosti bez okamihu podania → odmietne databáza (ak sa obmedzenie prijme, CA-6). |
| **Regresia** | `TC-C08-01`, `TC-C08-02` s rovnakým výsledkom ako na baseline; celá sada — meniť sa smú iba očakávania riadkov, ktoré matica označuje CHANGE; testy súbehu REQ-05 a REQ-16; oprávnenia BR-06, BR-07; zrušenie `CONFIRMED` (60 min); `scripts/mutation_check.py` bez prežitej mutácie. |
| **Technické riziko** | `R-06` — schválenie čakajúce na zámok prejde po lehote; owner tím, **NOT VERIFIED**. `R-07` — viac prepadnutých žiadostí, kým vedúci nemá zoznam (I-01). `R-04` — zmena schémy bez migrácií. Register rizík v evidence. |
| **Práca v C09** | PR so zmenou; integračný beh (`pytest` + `scripts/c08_baseline_tc.py` proti bežiacej aplikácii); čistý checkout na stroji druhého člena; build/CI — projekt CI nemá: buď ho C09 zavedie, alebo zapíše ručný beh druhým členom ako náhradu; rozhodnutie o vydaní (špecifikácia v0.3, verzia `0.3.0`, tag). |
| **Stav** | Všetko **PLANNED**. Nič nie je IMPLEMENTED ani VERIFIED — preto ani `R-06` nemá overené reziduálne riziko. |

### If NO_CHANGE / REJECT: independent current-baseline check and response

Neuplatňuje sa — rozhodnutie je ACCEPT. Nezávislý doklad dnešnej baseline,
o ktorý by sa NO_CHANGE musel opierať, aj tak existuje: `TC-C08-01/02`
(BR-02 drží) a `TC-C08-03` (obmedzenie trvá).

### Open uncertainty / monitoring or next decision trigger

- **Peer review** (CA-1 až CA-6) — kým neprebehne, rozhodnutie je návrh.
- **Migrácie (R-04)** — podmienka ACCEPT; bez rozhodnutia sa C09 nemá začať kódom.
- **v0.3 a F-03** — `REQ-16` (R-03) a CR-08 mieria na tú istú verziu
  špecifikácie; tím rozhodne, či pôjdu spolu.
- **Stakeholder** — 48 h a kalendárne hodiny sú zapísané tímom za stakeholdera.
  Ak by skutočný vedúci laboratória povedal iné číslo, mení sa iba hodnota T,
  nie mechanizmus — preto je T konfigurovateľná.
- **Trigger na variantu B:** rozhodnutie TBD-08 alebo požiadavka na oznámenie
  pri vypršaní (TBD-03).
