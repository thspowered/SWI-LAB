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
| Oneskorený pokus (čakal na zámok)? | Posudzuje sa podľa `now` odčítaného pri prijatí požiadavky. Schválenie prijaté sekundu pred lehotou, ktoré čakalo na zámok, ešte prejde. | Tá istá sémantika ako dnes pri `starts_at` (BR-03: čas sa odčíta raz). BR-02 tým ohrozené nie je — rozbor v analýze dopadu. Zostáva ako riziko `RISK-C08-1`. |

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
