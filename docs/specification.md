# Špecifikácia správania — Specification Baseline v0.1

Rezervačný systém laboratórnych prístrojov (SWI-LAB). Tento dokument popisuje
**celé minimálne správanie** systému: štyri základné operácie, spoločné doménové
pravidlá a invarianty.

Nadväzuje na [docs/intent-and-change.md](intent-and-change.md) (Project Frame
z C01). Pojmy odtiaľ sa tu neopakujú, iba spresňujú tam, kde to operácie
vyžadujú.

Vizuálne pohľady na to isté správanie — diagram prípadov užitia, stavový diagram
a diagramy aktivít — sú v [docs/diagrams.md](diagrams.md).

**Stav:** návrh v0.1 — čaká na schválenie tímom (krok 3).

---

## 1. Rozsah

V baseline v0.1 systém pozná štyri operácie:

| ID    | Operácia          | Význam                                                |
| ----- | ----------------- | ----------------------------------------------------- |
| OP-01 | Create Reservation | zaznamená zámer používateľa; prístroj ešte nealokuje  |
| OP-02 | Check Availability | zistí, či je prístroj v intervale voľný                |
| OP-03 | Confirm Reservation | rezervácia sa stáva platnou alokáciou prístroja      |
| OP-04 | Cancel Reservation | rezervácia sa ruší a prestáva blokovať prístroj        |

Schvaľovanie (`Approve`) **nie je** súčasťou v0.1. Pribúda až zmenou v časti B.

### Aktéri

- **Študent** — vytvára, potvrdzuje a ruší **vlastné** rezervácie, zisťuje dostupnosť.
- **Vedúci laboratória (supervisor)** — to isté nad **ľubovoľnou** rezerváciou;
  spravuje prístroje a certifikáty (mimo rozsah v0.1).
- **Notification Service** (externý, podporný) — prijíma oznámenie po úspešnom
  potvrdení a po úspešnom zrušení. V v0.1 je hranica definovaná, **nie
  implementovaná** — viď TBD-03.

### Čo znamená „alokácia“

Prístroj je **exkluzívny**: v danom okamihu ho môže používať jeden človek.
Prístroj je pre interval alokovaný práve vtedy, keď preň existuje rezervácia
v stave `CONFIRMED`. Stavy `DRAFT` a `CANCELLED` nealokujú nič — to je
rozhodnutie tímu, nie vedľajší efekt implementácie, a platí rovnako vo všetkých
štyroch operáciách.

---

## 2. Doménové pravidlá a invarianty

Pravidlá sú definované **raz tu**; operácie sa na ne odkazujú a neopakujú ich.

### BR-01 — Význam intervalu

Rezervácia pokrýva polootvorený interval `[starts_at, ends_at)`.

- Platný interval: `ends_at > starts_at` (striktne; `starts_at == ends_at`
  je neplatný interval, nie prázdna rezervácia).
- **Prekryv:** rezervácie A a B sa prekrývajú práve vtedy, keď
  `A.starts_at < B.ends_at ∧ B.starts_at < A.ends_at`.
  Rezervácie `10:00–11:00` a `11:00–12:00` sa teda **neprekrývajú**.
- Všetky časy sú timezone-aware, v databáze `timestamptz`, teda porovnávané
  ako okamihy v UTC (overené C01 spikom).
- v0.1 **nezavádza** minimálnu ani maximálnu dĺžku rezervácie ani zarovnanie na
  sloty — viď TBD-01.

*Zdôvodnenie:* bez jednej definície prekryvu skončí každá operácia, diagram
a test pri inej chybe o jedna, a tím sa o rozdiele dozvie až z produkčného
konfliktu.

### BR-02 — Invariant exkluzívneho prístroja

V žiadnom potvrdenom stave systému nesmú existovať dve rezervácie toho istého
prístroja v stave `CONFIRMED`, ktorých intervaly sa prekrývajú podľa BR-01.

Toto je **jediný invariant systému nad viacerými záznamami**. Platí aj pri
súbežnom vykonávaní operácií (viď REQ-04).

### BR-03 — Politika rušenia *(rozhodnutie tímu)*

| Stav rezervácie | Zrušenie povolené, keď                           |
| --------------- | ------------------------------------------------ |
| `DRAFT`         | vždy — bez časovej podmienky                      |
| `CONFIRMED`     | `currentTime + 60 min < starts_at`               |
| `CANCELLED`     | vždy — operácia je idempotentná, stav sa nemení   |

- **Hranica:** podmienka pre `CONFIRMED` je striktná. Presne 60:00 min pred
  začiatkom sa `CONFIRMED` rezervácia zrušiť **nedá**; 60:01 min pred začiatkom
  áno.
- **Zdroj času:** hodiny aplikačného servera v UTC. `currentTime` sa odčíta
  **raz** na začiatku operácie a ten istý údaj sa použije pre všetky podmienky
  danej operácie. Nie je to implementačný detail: bez určeného zdroja času by
  podmienku „zostáva viac ako 60 minút“ nebolo možné overiť, lebo každá vrstva
  by sa pýtala iných hodín.

*Zdôvodnenie:* potvrdená rezervácia drží prístroj a odrádza ostatných. Ak sa
uvoľní pár minút pred začiatkom, nikto to už nestihne využiť a prístroj stojí
prázdny — čo je presne strata, ktorú má systém odstrániť. Hodina je dohodnutá
prevádzková lehota laboratória, nie odvodená veličina; ak sa prevádzka zmení,
mení sa toto číslo na jednom mieste.

Pre `DRAFT` **zámerne neplatí žiadna lehota.** Návrh nikoho neblokuje, takže
lehota na jeho zrušenie by nemala čo chrániť — chránila by len samotný záznam
pred vlastníkom. Bez tohto rozhodnutia by navyše vznikol trvalo zaseknutý stav:
`DRAFT`, ktorého začiatok nastal, sa nedá potvrdiť (REQ-06) a s časovou
podmienkou by sa nedal ani zrušiť (nález N-03, časť 8).

### BR-04 — Certifikácia *(domain-specific pravidlo z C01)*

Rezerváciu možno potvrdiť, len ak jej používateľ má certifikát na **kategóriu**
daného prístroja, platný k času `starts_at` rezervácie:

```
∃ c ∈ certifications(user) : c.category == instrument.category
                             ∧ c.valid_until > reservation.starts_at
```

**Hranica:** `valid_until == starts_at` znamená **neplatný** certifikát —
platnosť končí v ten okamih, rezervácia v ten okamih začína. Je to rovnaká
polootvorená logika ako v BR-01, zámerne, aby v systéme nežili dva rôzne významy
slova „do“.

Certifikát sa viaže na kategóriu, nie na konkrétny prístroj (C01). Chýbajúci
alebo expirovaný certifikát potvrdenie zamietne a rezervácia zostáva v `DRAFT`;
používateľ si certifikát doplní a potvrdí znova.

### BR-05 — Neaktívny prístroj

Neaktívny prístroj (`is_active = false`, napr. v servise) **nesmie získať novú
alokáciu** a nesmie prijať nový zámer:

- nedá sa preň vytvoriť rezervácia (OP-01),
- nedá sa preň potvrdiť rezervácia (OP-03),
- kontrola dostupnosti ho hlási ako nedostupný pre ľubovoľný interval (OP-02).

Deaktivácia prístroja **neruší** existujúce `CONFIRMED` rezervácie — tie sa
riešia ručne cez OP-04. Správa prístrojov nie je v rozsahu v0.1 (TBD-02).

### BR-06 — Oprávnenie *(zjednodušené v0.1)*

- Používateľ uvedený v požiadavke musí existovať.
- Operácie nad existujúcou rezerváciou (OP-03, OP-04) smie vykonať iba jej
  **vlastník** alebo používateľ s rolou `SUPERVISOR`.
- OP-02 (kontrola dostupnosti) je čítacia a oprávnenie nevyžaduje.

**Predpoklad:** identita prichádza v požiadavke ako `user_id`; autentifikácia je
mimo rozsah predmetu (C01). „Oprávnený používateľ“ v zvyšku dokumentu znamená
presne túto podmienku.

---

## 3. OP-01 — Create Reservation

**Cieľ / hodnota pre používateľa:**
Študent si zaznamená zámer použiť prístroj v konkrétnom čase. Zámer je možné
neskôr potvrdiť; do potvrdenia nikoho neblokuje.

**Spúšťacia udalosť:**
Oprávnený používateľ požiada o rezerváciu prístroja `I` na interval `[s, e)`.

**Pozorovateľné požiadavky:**

> **REQ-01:** Systém vytvorí rezerváciu v stave `DRAFT` pre existujúci **aktívny**
> prístroj, ak je interval platný podľa BR-01 a začína v budúcnosti.

> **REQ-02:** Vytvorenie rezervácie **nevyhodnocuje** prekryv s inými
> rezerváciami ani certifikáciu používateľa; dve prekrývajúce sa rezervácie
> v stave `DRAFT` môžu existovať súčasne.

**Predpoklady:**
- používateľ existuje (BR-06),
- prístroj existuje a je aktívny (BR-05),
- `e > s` (BR-01),
- `currentTime < s`.

**Stav po úspešnom vykonaní:**
- existuje práve jedna nová rezervácia,
- `state = DRAFT`,
- žiadna alokácia prístroja nevznikla — výsledok OP-02 pre rovnaký interval sa
  nezmenil,
- stav ostatných rezervácií je nezmenený.

**Zmena stavu:** `[initial] → DRAFT`

**Odkaz na pravidlá:** BR-01 (platnosť intervalu), BR-05, BR-06.
Zámerne **nie** BR-02 a **nie** BR-04 — viď REQ-02.

**Hlavný úspešný scenár:**
1. Používateľ odošle `instrument_id`, `user_id` a interval `[s, e)`.
2. Systém overí existenciu používateľa a prístroja, aktívnosť prístroja
   a platnosť intervalu.
3. Systém vytvorí rezerváciu v stave `DRAFT`.
4. Systém vráti identifikátor rezervácie a jej aktuálny stav.

**Alternatívne / chybové výsledky** (v každom prípade nevznikne žiadna rezervácia):
- neznámy používateľ → zamietnuté,
- neznámy prístroj → zamietnuté,
- neaktívny prístroj → zamietnuté (BR-05),
- `e <= s` → zamietnuté (BR-01),
- `s <= currentTime` → zamietnuté (rezervovať spätne nemá zmysel; prístroj už bol
  alebo nebol použitý a systém o tom nemá čo tvrdiť).

**Príklady overenia:**

```
aktívny prístroj + [zajtra 10:00, zajtra 11:00)      → vznikne 1× DRAFT
starts_at == ends_at                                  → zamietnuté, 0 rezervácií
ends_at < starts_at                                   → zamietnuté, 0 rezervácií
neznámy prístroj                                      → zamietnuté, 0 rezervácií
neaktívny prístroj                                    → zamietnuté, 0 rezervácií
interval v minulosti                                  → zamietnuté, 0 rezervácií
existuje CONFIRMED [10:00,11:00), žiadam [10:30,11:30) → vznikne DRAFT (REQ-02)
používateľ bez certifikátu                            → vznikne DRAFT (REQ-02)
```

**Zdôvodnenie / zdroj:**
Vytvorenie zaznamenáva zámer, nie alokáciu. Keby `Create` kontroloval prekryv,
študent by nemohol pripraviť dve alternatívy toho istého merania a nechať si
rozhodnutie na neskôr; keby kontroloval certifikát, nemohol by si rezerváciu
pripraviť skôr, než mu vedúci zapíše absolvované školenie. Obe kontroly patria
k okamihu, keď rezervácia začne prístroj skutočne blokovať — teda do OP-03.

Posledné dva príklady overenia sú tu zámerne: sú to riadky, ktoré zlyhajú, ak
niekto (človek alebo AI) presunie kontrolu prekryvu alebo certifikátu do
`Create` bez zmeny tejto špecifikácie.

---

## 4. OP-02 — Check Availability

**Cieľ / hodnota pre používateľa:**
Používateľ zistí, či má zmysel žiadať prístroj na daný čas, skôr než rezerváciu
vytvorí alebo potvrdí.

**Spúšťacia udalosť:**
Používateľ sa pýta na prístroj `I` a interval `[s, e)`.

**Pozorovateľná požiadavka:**

> **REQ-03:** Pre existujúci aktívny prístroj a platný interval systém ohlási
> prístroj ako **nedostupný**, ak sa interval prekrýva (BR-01) s ľubovoľnou
> rezerváciou toho istého prístroja v stave `CONFIRMED`; inak ho ohlási ako
> **dostupný**. Neaktívny prístroj je nedostupný pre ľubovoľný interval (BR-05).

**Predpoklady:**
- prístroj existuje,
- interval je platný podľa BR-01.

**Stav po úspešnom vykonaní:**
- vráti sa výsledok dostupnosti a zoznam identifikátorov kolidujúcich
  `CONFIRMED` rezervácií (prázdny, ak je prístroj dostupný),
- **žiadna** rezervácia nezmenila stav, žiadna nevznikla.

**Zmena stavu:** žiadna — operácia je čítacia.

**Interval v minulosti je platný dopyt.** Na rozdiel od OP-01 tu podmienka
`currentTime < starts_at` **neplatí**: otázka „bol prístroj v utorok o desiatej
obsadený a kým“ je legitímna a systém na ňu vie odpovedať z tých istých dát.
Rozdiel je zámerný — OP-01 mení stav do budúcnosti, OP-02 iba číta (nález N-02,
časť 8).

**Odkaz na pravidlá:** BR-01, BR-02, BR-05.

**Hlavný úspešný scenár:**
1. Používateľ odošle `instrument_id` a interval `[s, e)`.
2. Systém overí existenciu prístroja a platnosť intervalu.
3. Systém nájde `CONFIRMED` rezervácie toho istého prístroja prekrývajúce sa
   s intervalom.
4. Systém vráti `AVAILABLE` (žiadna kolízia) alebo `UNAVAILABLE` so zoznamom
   kolidujúcich rezervácií.

**Alternatívne / chybové výsledky:**
- neznámy prístroj → zamietnuté (nie „nedostupný“ — systém o takom prístroji
  nemá čo tvrdiť),
- neplatný interval → zamietnuté,
- neaktívny prístroj → `UNAVAILABLE` s dôvodom `INSTRUMENT_INACTIVE`
  a prázdnym zoznamom kolízií.

**Príklady overenia:**

```
existuje CONFIRMED [10:00, 11:00) na prístroji I:

  dopyt [09:00, 10:00)  → AVAILABLE      (dotyk zľava, BR-01)
  dopyt [11:00, 12:00)  → AVAILABLE      (dotyk sprava, BR-01)
  dopyt [10:30, 11:30)  → UNAVAILABLE    (čiastočný prekryv)
  dopyt [09:00, 12:00)  → UNAVAILABLE    (obsahuje celú rezerváciu)
  dopyt [10:15, 10:45)  → UNAVAILABLE    (leží vnútri rezervácie)

existuje iba DRAFT [10:00, 11:00):
  dopyt [10:00, 11:00)  → AVAILABLE      (DRAFT nealokuje)

existuje iba CANCELLED [10:00, 11:00):
  dopyt [10:00, 11:00)  → AVAILABLE      (CANCELLED nealokuje)

neaktívny prístroj:
  dopyt [10:00, 11:00)  → UNAVAILABLE, dôvod INSTRUMENT_INACTIVE
```

**Zdôvodnenie / zdroj:**
Odpoveď musí používať **presne ten istý** význam prekryvu a tú istú množinu
blokujúcich stavov ako OP-03. Inak systém sľúbi voľný termín, ktorý potvrdenie
o sekundu neskôr zamietne — a používateľ prestane odpovedi veriť.

Zoznam kolidujúcich rezervácií je súčasťou pozorovateľného výsledku zámerne:
bez neho nevieme z čiernej skrinky odlíšiť „nedostupné kvôli tejto rezervácii“
od „nedostupné kvôli chybe v dotaze“.

---

## 5. OP-03 — Confirm Reservation

**Cieľ / hodnota pre používateľa:**
Návrh rezervácie sa stáva platnou alokáciou prístroja. Od tohto okamihu má
používateľ istotu, že prístroj bude v jeho intervale jeho.

**Spúšťacia udalosť:**
Oprávnený používateľ požiada o potvrdenie rezervácie `X`.

**Pozorovateľné požiadavky:**

> **REQ-04:** Systém potvrdí rezerváciu v stave `DRAFT`, len ak je jej prístroj
> aktívny (BR-05), používateľ má platný certifikát na kategóriu prístroja
> (BR-04) a interval rezervácie sa neprekrýva so žiadnou existujúcou
> `CONFIRMED` rezerváciou toho istého prístroja (BR-02).

> **REQ-05:** Pri súbežných pokusoch o potvrdenie, ktoré sú navzájom
> v konflikte podľa BR-02, dosiahne stav `CONFIRMED` **najviac jedna**
> rezervácia. Ostatné skončia zamietnutím a zostanú v stave `DRAFT`.

> **REQ-06:** Rezerváciu, ktorej začiatok už nastal, nemožno potvrdiť.

**Predpoklady:**
- rezervácia existuje,
- `state = DRAFT`,
- žiadateľ je vlastník alebo `SUPERVISOR` (BR-06),
- `currentTime < starts_at`.

**Stav po úspešnom vykonaní:**
- `state = CONFIRMED`,
- rezervácia blokuje prístroj pre svoj interval — OP-02 pre prekrývajúci sa
  interval odteraz vracia `UNAVAILABLE` a uvádza túto rezerváciu,
- BR-02 naďalej platí,
- (po implementácii hranice) odoslané oznámenie do Notification Service — TBD-03.

**Zmena stavu:** `DRAFT → CONFIRMED`

**Odkaz na pravidlá:** BR-01, BR-02, BR-04, BR-05, BR-06.

**Hlavný úspešný scenár:**
1. Používateľ odošle `reservation_id` a svoju identitu.
2. Systém nájde rezerváciu a overí oprávnenie.
3. Systém overí, že rezervácia je v stave `DRAFT` a jej začiatok ešte nenastal.
4. Systém overí, že prístroj je aktívny.
5. Systém overí certifikát používateľa na kategóriu prístroja (BR-04).
6. Systém overí, že interval nekoliduje so žiadnou `CONFIRMED` rezerváciou toho
   istého prístroja (BR-02).
7. Systém nastaví `state = CONFIRMED` a vráti aktuálny stav rezervácie.

**Alternatívne / chybové výsledky:**
- neznáma rezervácia → zamietnuté,
- žiadateľ nie je vlastník ani supervisor → zamietnuté, stav nezmenený,
- zdrojový stav nie je `DRAFT`:
  - `CONFIRMED` → zamietnuté, stav zostáva `CONFIRMED` (nie je to chyba dát,
    ale potvrdenie nie je idempotentné — druhý pokus môže pochádzať od niekoho
    iného a tichý úspech by to zakryl),
  - `CANCELLED` → zamietnuté, stav zostáva `CANCELLED`,
- neaktívny prístroj → zamietnuté, zostáva `DRAFT`,
- chýbajúci alebo expirovaný certifikát → zamietnuté, zostáva `DRAFT`,
- prekryv s `CONFIRMED` rezerváciou → zamietnuté, zostáva `DRAFT`,
- `currentTime >= starts_at` → zamietnuté, zostáva `DRAFT`.

**Príklady overenia:**

```
DRAFT + aktívny prístroj + platný certifikát + žiadny prekryv → CONFIRMED
DRAFT + prekryv s CONFIRMED [10:00,11:00)                     → zamietnuté, zostáva DRAFT
DRAFT + prekryv iba s DRAFT                                   → CONFIRMED (DRAFT neblokuje)
DRAFT + prekryv iba s CANCELLED                               → CONFIRMED
DRAFT, susedný interval [11:00,12:00) k CONFIRMED [10:00,11:00) → CONFIRMED (BR-01)
DRAFT + bez certifikátu                                       → zamietnuté, zostáva DRAFT
DRAFT + certifikát s valid_until == starts_at                 → zamietnuté (hranica BR-04)
DRAFT + certifikát s valid_until == starts_at + 1 s           → CONFIRMED
DRAFT + certifikát na inú kategóriu                           → zamietnuté, zostáva DRAFT
DRAFT + neaktívny prístroj                                    → zamietnuté, zostáva DRAFT
už CONFIRMED                                                   → zamietnuté, zostáva CONFIRMED
už CANCELLED                                                   → zamietnuté, zostáva CANCELLED
cudzí študent potvrdzuje cudziu rezerváciu                     → zamietnuté, zostáva DRAFT
supervisor potvrdzuje cudziu rezerváciu                        → CONFIRMED
dve súbežné potvrdenia prekrývajúcich sa rezervácií            → najviac jedna CONFIRMED (REQ-05)
```

**Zdôvodnenie / zdroj:**
Potvrdenie je jediný prechod, ktorý mení obsadenosť prístroja, a preto jediné
miesto, kde sa vynucujú BR-02 a BR-04 (C01, Project Frame).

REQ-05 nie je implementačný detail: ak by pri súbehu vznikli dve potvrdené
rezervácie, systém by produkoval presne ten výsledok, kvôli ktorému vzniká —
dvoch ľudí pred jedným prístrojom. Ako sa to zabezpečí (transakčná izolácia,
zámok, databázový constraint) je otázka architektúry v C03; **pozorovateľný
výsledok** patrí sem.

**Prijatý dôsledok:** rezerváciu možno potvrdiť aj menej než 60 minút pred
začiatkom — a taká rezervácia sa už podľa BR-03 nedá zrušiť. Nie je to
opomenutie: kto potvrdzuje na poslednú chvíľu, prístroj v tom čase chce, a keby
ho aj uvoľnil, nikto iný by to už nestihol využiť. Platí to isté, čo zdôvodňuje
samotnú 60-minútovú lehotu (nález N-01, časť 8).

**Predpoklad / neznáma:** TBD-04.

---

## 6. OP-04 — Cancel Reservation

**Cieľ / hodnota pre používateľa:**
Používateľ odvolá rezerváciu, ktorú nevyužije, a prístroj sa včas uvoľní pre
ostatných.

**Spúšťacia udalosť:**
Oprávnený používateľ požiada o zrušenie rezervácie `X`.

**Pozorovateľné požiadavky:**

> **REQ-07:** Systém zruší rezerváciu v stave `DRAFT` bez časového obmedzenia
> (BR-03).

> **REQ-08:** Systém zruší rezerváciu v stave `CONFIRMED`, ak do jej začiatku
> zostáva **striktne viac ako 60 minút** (BR-03). Po zrušení prestáva blokovať
> prístroj.

> **REQ-09:** Zrušenie rezervácie, ktorá už je v stave `CANCELLED`, skončí
> úspechom a nezmení žiadny stav (idempotencia).

**Predpoklady:**
- rezervácia existuje,
- žiadateľ je vlastník alebo `SUPERVISOR` (BR-06),
- pre `CONFIRMED` platí časová podmienka BR-03 (pre `DRAFT` žiadna neplatí).

**Stav po úspešnom vykonaní:**
- `state = CANCELLED`,
- rezervácia neblokuje dostupnosť prístroja — OP-02 pre jej interval vracia
  `AVAILABLE`, ak neexistuje iná kolízia,
- (po implementácii hranice) odoslané oznámenie do Notification Service — TBD-03;
  pri idempotentnom opakovaní (REQ-09) sa oznámenie **neodosiela**.

**Zmena stavu:** `DRAFT → CANCELLED`, `CONFIRMED → CANCELLED`,
`CANCELLED → CANCELLED` (bez efektu).

**Odkaz na pravidlá:** BR-03, BR-06.

**Hlavný úspešný scenár:**
1. Používateľ odošle `reservation_id` a svoju identitu.
2. Systém nájde rezerváciu a overí oprávnenie.
3. Systém odčíta `currentTime` a vyhodnotí politiku BR-03 pre aktuálny stav
   rezervácie.
4. Systém nastaví `state = CANCELLED` a vráti aktuálny stav.

**Alternatívne / chybové výsledky:**
- neznáma rezervácia → zamietnuté,
- žiadateľ nie je vlastník ani supervisor → zamietnuté, stav nezmenený,
- `CONFIRMED` a do začiatku zostáva 60 minút alebo menej → zamietnuté, zostáva
  `CONFIRMED` (vrátane prípadu, že začiatok už nastal),
- už `CANCELLED` → **úspech bez zmeny** (REQ-09).

Pre `DRAFT` neexistuje časový dôvod zamietnutia — zamietnuť sa dá iba neznáma
rezervácia alebo neoprávnený žiadateľ.

**Súbeh s potvrdením (Cancel vs. Confirm nad tou istou rezerváciou):**
Rezervácia opustí stav `DRAFT` najviac raz; druhá operácia vidí už zmenený
zdrojový stav a vyhodnotí sa podľa neho:

- zvíťazí *cancel* → rezervácia je `CANCELLED`, následný *confirm* je zamietnutý
  (zdrojový stav nie je `DRAFT`),
- zvíťazí *confirm* → rezervácia je `CONFIRMED`, následný *cancel* sa posudzuje
  podľa prísnejšieho pravidla pre `CONFIRMED` (60 min).

Nikdy nenastane stav, v ktorom by rezervácia bola zároveň zrušená aj blokovala
prístroj.

**Príklady overenia:**

```
DRAFT, 30 min pred začiatkom          → CANCELLED
DRAFT, 1 s po začiatku                → CANCELLED (pre DRAFT neplatí lehota)
CONFIRMED, 90 min pred začiatkom      → CANCELLED + prístroj je v tom intervale AVAILABLE
CONFIRMED, presne 60:00 min pred      → zamietnuté, zostáva CONFIRMED (hranica BR-03)
CONFIRMED, 60:01 min pred             → CANCELLED
CONFIRMED, 30 min pred začiatkom      → zamietnuté, zostáva CONFIRMED
CONFIRMED, po skončení rezervácie     → zamietnuté, zostáva CONFIRMED
už CANCELLED                          → úspech, stav zostáva CANCELLED (REQ-09)
cudzí študent ruší cudziu rezerváciu  → zamietnuté, stav nezmenený
supervisor ruší cudziu rezerváciu     → CANCELLED
```

**Zdôvodnenie / zdroj:**
Zdôvodnenie lehoty je v BR-03. Idempotencia (REQ-09) je rozhodnutie tímu:
klient, ktorému vypadne spojenie, musí vedieť požiadavku bezpečne zopakovať bez
toho, aby riešil, či prvý pokus prešiel. Pri súbehu v C03 bude táto vlastnosť
vítaná, pretože opakovanie nie je novou zmenou stavu.

`CANCELLED` **nie je zmazanie dát.** Rezervácia zostáva v systéme aj s históriou;
mení sa iba to, že prestáva alokovať prístroj. Bez toho by sa nedalo spätne
zistiť, kto prístroj v danom čase držal — čo je jeden z dôvodov existencie
systému (C01).

---

## 7. Predpoklady, neznáme a otvorené otázky

| ID     | Vec                                                                                        | Stav                                         |
| ------ | ------------------------------------------------------------------------------------------ | -------------------------------------------- |
| TBD-01 | Minimálna / maximálna dĺžka rezervácie, prípadne zarovnanie na sloty.                        | nerozhodnuté; v0.1 nezavádza žiadny limit    |
| TBD-02 | Správa prístrojov a certifikátov (vytváranie, deaktivácia) — kto a cez aké rozhranie.        | mimo rozsah v0.1; dáta sa napĺňajú priamo    |
| TBD-03 | Notification Service — protokol, správanie pri výpadku, synchrónne vs. asynchrónne volanie.  | hranica definovaná (C01), neimplementovaná   |
| TBD-04 | Zdroj certifikátov: vlastná evidencia vs. študijný systém univerzity (C01 unknown).          | otvorené; ovplyvní súbeh a správanie OP-03   |
| TBD-05 | Zobrazenie lokálneho času používateľovi — databáza vracia UTC (nález C01 spiku).             | otvorené; pravdepodobne pásmo na `Instrument` |
| TBD-06 | Autentifikácia. v0.1 predpokladá dôveryhodné `user_id` v požiadavke (BR-06).                 | mimo rozsah predmetu                         |

Žiadna z týchto položiek nie je doplnená vymyslenou hodnotou. Ak sa v texte
objaví číslo (60 minút v BR-03), má zdroj — je to rozhodnutie tímu, nie odhad.

---

## 8. Kontrola konzistencie špecifikácie ako celku

Špecifikáciu sme kontrolovali ako **jeden systém tvrdení**, nie ako štyri
nezávislé texty. Kontrola prebehla po dopísaní diagramov, teda nad textom
aj obrázkami naraz.

| Kontrola                        | Výsledok                                                                                                                                             |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Create vs. Confirm              | ✅ Alokácia vzniká výhradne pri `Confirm`. OP-01 to má ako výslovnú požiadavku REQ-02 a ako postcondition („žiadna alokácia nevznikla“).                |
| Availability vs. Confirm        | ✅ Obe používajú tú istú množinu blokujúcich stavov `{CONFIRMED}` a tú istú definíciu prekryvu (BR-01). Rozdiel pri neaktívnom prístroji je zámerný.¹   |
| Cancel vs. stavový diagram      | ✅ Po oprave N-03. Text aj diagram povoľujú `DRAFT → CANCELLED`, `CONFIRMED → CANCELLED` a idempotentné opakovanie.                                     |
| Význam intervalov               | ✅ `[start, end)` v BR-01, vo všetkých operáciách aj v príkladoch overenia vrátane dotyku zľava a sprava. BR-04 používa tú istú polootvorenú logiku.    |
| Use case diagram vs. text       | ✅ Štyri ciele ↔ OP-01..OP-04. Každá čiara v diagrame má špecifikované správanie; žiadna operácia nie je bez aktéra.                                    |
| Požiadavka vs. návrhové rozhodnutie | ✅ Žiadny REQ nepredpisuje technológiu. REQ-05 popisuje pozorovateľný výsledok súbehu, nie mechanizmus (zámok/constraint je téma C03).²             |
| Neistota vs. vymyslená presnosť | ✅ Šesť explicitných TBD. Jediná číselná hodnota v celej špecifikácii (60 min) je rozhodnutie tímu so zdôvodnením.³                                    |

¹ Neaktívny prístroj je pre OP-02 **platná odpoveď** (`UNAVAILABLE` + dôvod),
pre OP-03 **chyba**. Je to zámer: „je voľný?“ má zmysluplnú odpoveď aj pri
prístroji v servise, kým „potvrď mi ho“ nemá.

² Jediné miesto, kde sa špecifikácia dotýka mechanizmu, je zdroj času v BR-03.
Bez určenia, ktoré hodiny sú smerodajné, by podmienka o 60 minútach nebola
overiteľná — ide teda o sémantiku požiadavky, nie o predpis implementácie.

³ Zvažovali sme doplniť maximálnu dĺžku rezervácie (napr. 8 hodín). Zamietli
sme to: žiadny zdroj v C01 takú hranicu neuvádza a vymyslené číslo by sa
z TBD nepozorovane stalo „pravidlom“. Ostáva ako TBD-01.

### Nájdené nesúlady a ich vyriešenie

Kontrola nebola formalita — našla tri veci a všetky si vyžiadali zmenu
špecifikácie.

**N-01 — Potvrdenie na poslednú chvíľu vyrobí nezrušiteľnú rezerváciu.**
OP-03 dovolí potvrdiť rezerváciu kedykoľvek pred jej začiatkom, ale BR-03 dovolí
zrušiť `CONFIRMED` len viac ako 60 minút pred začiatkom. Rezervácia potvrdená
10 minút pred začiatkom sa teda už nedá zrušiť.
*Vyriešené:* pravidlo sa nemení — dôsledok je v súlade so zdôvodnením BR-03
(neskoro uvoľnený prístroj už nikto nevyužije). Opravili sme **špecifikáciu**:
dôsledok je odteraz v OP-03 napísaný explicitne, aby sa naň neprišlo až pri
reklamácii používateľa.

**N-02 — Dopyt na dostupnosť v minulosti nebol rozhodnutý.**
OP-01 zamieta intervaly v minulosti, OP-02 o nich nehovorila nič. Implementácia
by si doplnila ľubovoľnú z dvoch možností a test by potvrdil práve tú.
*Vyriešené:* opravili sme **špecifikáciu** — OP-02 výslovne povoľuje dopyty do
minulosti, pretože je čítacia a odpoveď („kto prístroj vtedy držal“) je jedným
z dôvodov existencie systému podľa C01.

**N-03 — `DRAFT` po začiatku by ostal natrvalo zaseknutý.**
Pôvodné znenie BR-03 dovoľovalo zrušiť `DRAFT` len pred jeho začiatkom, pričom
REQ-06 zakazuje potvrdiť rezerváciu po začiatku. Taká rezervácia by sa nedala
ani potvrdiť, ani zrušiť — stav bez východiska, ktorý žiadny diagram nezakresľuje
a ktorý by sa ukázal až po nasadení.
*Vyriešené:* rozhodnutím tímu sme **zmenili pravidlo** BR-03 — `DRAFT` sa dá
zrušiť kedykoľvek. Lehota má chrániť ostatných používateľov pred blokovaným
prístrojom, a návrh nikoho neblokuje. Zmena sa premietla do BR-03, REQ-07,
predpokladov a príkladov overenia OP-04, do stavového diagramu aj do diagramu
aktivít OP-04.

Za povšimnutie stojí, že vo všetkých troch prípadoch bol chybný **zdroj
špecifikácie**, nie príklad overenia — v tejto fáze ešte neexistoval žiadny kód,
ktorý by sa dal obviniť.

---

## 9. Kontrola prijatia požiadaviek

Každý prijatý požiadavok prešiel kontrolou podľa deviatich otázok (význam,
potreba, pozorovateľnosť, uskutočniteľnosť, overiteľnosť, stav/čas, súbeh,
konzistencia, neistota). Nasledujúca tabuľka zhŕňa odpovede na tie otázky, pri
ktorých sa požiadavky reálne líšia; ostatné sú rozpísané v texte príslušnej
operácie.

| REQ    | Závisí od stavu, času alebo hranice?                             | Môže súbeh zmeniť business výsledok?                                  | Čím sa overí                                          |
| ------ | ---------------------------------------------------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------- |
| REQ-01 | áno — `ends_at > starts_at`, `now < starts_at`, `is_active`        | nie — vytvorenie nezávisí od iných rezervácií                           | vznik práve jedného `DRAFT`; zamietnutie pri `s == e`  |
| REQ-02 | nie                                                                | nie                                                                     | `DRAFT` vznikne aj pri prekryve a bez certifikátu      |
| REQ-03 | áno — hranice intervalu (dotyk zľava/sprava), množina stavov       | nie — čítacia operácia, ale výsledok môže zastarať hneď po odpovedi¹    | tri hraničné dopyty okolo `CONFIRMED` [10:00,11:00)    |
| REQ-04 | áno — stav `DRAFT`, `now < starts_at`, platnosť certifikátu        | **áno** — dve potvrdenia môžu súčasne prejsť kontrolou prekryvu         | potvrdenie pri prekryve zamietnuté, stav zostáva `DRAFT` |
| REQ-05 | áno — týka sa práve okamihu medzi kontrolou a zápisom              | **áno, toto je jeho jediný obsah**                                      | dve súbežné potvrdenia → najviac jedno `CONFIRMED`     |
| REQ-06 | áno — hranica `now < starts_at`                                    | hraničný prípad: potvrdenie v okamihu `starts_at` závisí od jedného odčítania času | potvrdenie po začiatku zamietnuté            |
| REQ-07 | nie — pre `DRAFT` neplatí žiadna lehota (po N-03)                  | zriedkavo: súbeh s `Confirm` — riešené v OP-04, časť „Súbeh s potvrdením“ | zrušenie `DRAFT` pred aj po začiatku prejde          |
| REQ-08 | áno — hranica presne 60:00 min                                     | ako REQ-07                                                              | 60:00 zamietnuté, 60:01 prejde                        |
| REQ-09 | nie                                                                | nie — opakovanie nie je novou zmenou stavu                              | druhé zrušenie vráti úspech a stav zostáva `CANCELLED` |

¹ Odpoveď OP-02 je platná k okamihu dopytu a systém ju **negarantuje do
budúcnosti**. Kto chce istotu, musí potvrdiť (OP-03) — a tam rozhodne REQ-04
a REQ-05, nie predošlá odpoveď o dostupnosti. Bez tejto vety by používateľ
právom očakával, že „AVAILABLE“ znamená rezervované.

**Čo revízia skutočne zmenila** (dôkaz, že nešlo o odškrtnutie):

- BR-03 zmenilo obsah: časová podmienka pre `DRAFT` bola po náleze N-03
  odstránená. S ňou sa preformuloval REQ-07, predpoklady OP-04, dva príklady
  overenia, stavový diagram aj diagram aktivít OP-04.
- OP-02 dostala explicitné stanovisko k dopytom do minulosti (nález N-02).
  Predtým to bola diera, ktorú by ticho vyplnila implementácia.
- OP-03 dostala pomenovaný prijatý dôsledok neskorého potvrdenia (nález N-01).
- Maximálnu dĺžku rezervácie sme ako požiadavku **zamietli** — nemá zdroj
  v C01; ostáva TBD-01.
- Do baseline sme vedome **nezaradili žiadny výkonnostný cieľ** (typu „dostupnosť
  sa overí do 200 ms“). Nemá zdroj a bez zdroja je to vymyslená presnosť, nie
  požiadavka.

---

## 10. Schválenie baseline v0.1

Špecifikácia je pripravená na schválenie tímom. Platí, že **schválená baseline
znamená, že obaja členovia tímu vedia každú požiadavku obhájiť** — nie že si
dokument prečítali.

| Člen          | Rola pri baseline v0.1                                  | Stav                         |
| ------------- | -------------------------------------------------------- | ---------------------------- |
| Tomáš Hrubý   | návrh špecifikácie, diagramov a kontroly konzistencie     | ✅ 2026-09-21                 |
| Tomáš Krišica | review pred integráciou (PR `feature/c02-baseline`)       | ⏳ prebieha                   |

Po schválení sa tento stav stáva **Specification Baseline v0.1** a ďalšie zmeny
(vrátane schvaľovacieho procesu z časti B zadania) sa vedú ako v0.2 s explicitnou
analýzou dopadu.
