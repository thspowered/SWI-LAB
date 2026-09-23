# Špecifikácia správania — Specification Baseline v0.2

Rezervačný systém laboratórnych prístrojov (SWI-LAB). Tento dokument popisuje
**celé minimálne správanie** systému: päť operácií, spoločné doménové pravidlá
a invarianty.

Nadväzuje na [docs/intent-and-change.md](intent-and-change.md) (Project Frame
z C01). Pojmy odtiaľ sa tu neopakujú, iba spresňujú tam, kde to operácie
vyžadujú.

Vizuálne pohľady na to isté správanie — diagram prípadov užitia, stavový diagram
a diagramy aktivít — sú v [docs/diagrams.md](diagrams.md).

**Stav:** návrh v0.2 — čaká na schválenie tímom.

**História verzií**

| Verzia | Obsah |
| ------ | ----- |
| v0.1 | Štyri základné operácie, pravidlá BR-01..BR-06, požiadavky REQ-01..REQ-09. Schválená tímom. |
| v0.2 | Schvaľovací proces: nová operácia OP-05, stavy `PENDING_APPROVAL`, `REJECTED`, `EXPIRED`, pravidlá BR-07 a BR-08, požiadavky REQ-10..REQ-16. |

Čo presne zmena zasiahla a čo výslovne nie, je v
[docs/change-impact-c02.md](change-impact-c02.md). Analýza dopadu vznikla
**pred** touto úpravou; požiadavky, ktorých sa zmena nedotkla, majú nezmenené
znenie aj číslo.

Nové požiadavky sú číslované v poradí, v akom vznikli, nie podľa sekcií —
`REQ-13` preto leží pri OP-02, hoci má vyššie číslo než požiadavky v OP-05.
Číslo je identifikátor, nie poradie v texte; prečíslovanie by rozbilo odkazy
v testoch, diagramoch aj v review.

---

## 1. Rozsah

Systém pozná päť operácií:

| ID    | Operácia          | Význam                                                |
| ----- | ----------------- | ----------------------------------------------------- |
| OP-01 | Create Reservation | zaznamená zámer používateľa; prístroj ešte nealokuje  |
| OP-02 | Check Availability | zistí, či je prístroj v intervale voľný                |
| OP-03 | Confirm Reservation | rezervácia sa stáva alokáciou prístroja — buď hneď, alebo po schválení |
| OP-04 | Cancel Reservation | rezervácia sa ruší a prestáva blokovať prístroj        |
| OP-05 | Approve Reservation | vedúci rozhodne o žiadosti: schváli alebo zamietne *(v0.2)* |

**Schvaľovanie sa zapína na konkrétnom prístroji** príznakom
`Instrument.requires_approval`. Rezervácie ostatných prístrojov idú pôvodným
tokom `DRAFT → CONFIRMED` a OP-05 sa ich netýka.

### Aktéri

- **Študent** — vytvára, potvrdzuje a ruší **vlastné** rezervácie, zisťuje dostupnosť.
- **Vedúci laboratória (supervisor)** — to isté nad **ľubovoľnou** rezerváciou;
  navyše **rozhoduje o žiadostiach** o schválenie (OP-05, BR-07). Spravuje
  prístroje a certifikáty (mimo rozsah v0.2).
- **Notification Service** (externý, podporný) — prijíma oznámenie po úspešnom
  potvrdení, zrušení, **po podaní žiadosti o schválenie** (adresát je vedúci
  laboratória) a **po rozhodnutí o žiadosti** (adresát je žiadateľ). Hranica je
  definovaná, **nie implementovaná** — viď TBD-03.

Oznámenie pri podaní žiadosti nie je kozmetika: celý schvaľovací tok stojí na
tom, že sa vedúci o žiadosti dozvie. Systém nemá operáciu „zoznam čakajúcich
žiadostí“ (TBD-09), takže bez oznámenia by žiadosť čakala, kým na ňu niekto
náhodou nenarazí.

Zmena v0.2 **nepridáva nového aktéra.** Vedúci laboratória aktérom už bol;
pribudol mu nový cieľ.

### Čo znamená „alokácia“

Prístroj je **exkluzívny**: v danom okamihu ho môže používať jeden človek.

**Blokujúce stavy** sú `CONFIRMED` a **živá** žiadosť `PENDING_APPROVAL`
(živá = `currentTime < starts_at`, BR-08). Prístroj je pre interval blokovaný
práve vtedy, keď preň existuje rezervácia v jednom z týchto stavov. Stavy
`DRAFT`, `CANCELLED`, `REJECTED`, `EXPIRED` ani **vypršaná** žiadosť
`PENDING_APPROVAL` neblokujú nič.

Podmienka živosti je súčasťou definície, nie výnimka z nej. Bez nej by
invariant BR-02 hovoril, že vypršaná žiadosť bráni novej rezervácii — čo je
presný opak toho, čo BR-08 sľubuje, a systém by si protirečil v okamihu, keď
by termín žiadosti nastal.

Rozdiel medzi nimi je podstatný a musí byť viditeľný vo výsledku OP-02:

- `CONFIRMED` — prístroj **je** pridelený, čakanie nemá zmysel,
- `PENDING_APPROVAL` — o prístroj **niekto požiadal** a čaká sa na rozhodnutie
  vedúceho; ak žiadosť skončí zamietnutím, vypršaním alebo zrušením, interval
  sa uvoľní.

*Zdôvodnenie blokovania žiadosti (rozhodnutie tímu, v0.2):* keby žiadosť
neblokovala, vedúci by mohol dostať na stôl dve žiadosti na ten istý čas
a jednu z nich by musel zamietnuť len preto, že druhú vybavil skôr. Cena za
toto rozhodnutie je, že zabudnutá žiadosť drží prístroj — proti tomu stojí
expirácia (BR-08).

Toto rozdelenie platí rovnako vo všetkých piatich operáciách.

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
- Špecifikácia **nezavádza** minimálnu ani maximálnu dĺžku rezervácie ani
  zarovnanie na sloty — viď TBD-01.

*Zdôvodnenie:* bez jednej definície prekryvu skončí každá operácia, diagram
a test pri inej chybe o jedna, a tím sa o rozdiele dozvie až z produkčného
konfliktu.

### BR-02 — Invariant exkluzívneho prístroja *(zmenené v v0.2)*

V žiadnom potvrdenom stave systému nesmú existovať dve rezervácie toho istého
prístroja v **blokujúcom stave** (`CONFIRMED` alebo **živá** žiadosť
`PENDING_APPROVAL` podľa BR-08), ktorých intervaly sa prekrývajú podľa BR-01.

Vypršaná žiadosť do invariantu nevstupuje. Znamená to, že v databáze môže
zostať záznam v stave `PENDING_APPROVAL`, ktorý sa prekrýva s potvrdenou
rezerváciou — nie je to porušenie BR-02, pretože taká žiadosť už prístroj
neblokuje a nikdy sa nestane alokáciou (REQ-15).

Toto je **jediný invariant systému nad viacerými záznamami**. Platí aj pri
súbežnom vykonávaní operácií (viď REQ-05).

*Čo sa zmenilo oproti v0.1:* invariant sa pôvodne týkal iba stavu `CONFIRMED`.
Rozšírenie na `PENDING_APPROVAL` je priamy dôsledok rozhodnutia, že žiadosť
blokuje prístroj — bez neho by dve žiadosti na ten istý čas mohli existovať
súčasne a invariant by sa porušil až pri druhom schválení, teda v okamihu,
keď to už nikto nečaká.

### BR-03 — Politika rušenia *(rozhodnutie tímu)*

| Stav rezervácie      | Zrušenie povolené, keď                           |
| -------------------- | ------------------------------------------------ |
| `DRAFT`              | vždy — bez časovej podmienky                      |
| `PENDING_APPROVAL`   | kým žiadosť nevypršala, t. j. `currentTime < starts_at` (BR-08) — inak sa zruší nie zrušenie, ale žiadosť: stav sa zapíše ako `EXPIRED` a operácia sa zamietne *(v0.2)* |
| `CONFIRMED`          | `currentTime + 60 min < starts_at`               |
| `CANCELLED`          | vždy — operácia je idempotentná, stav sa nemení   |
| `REJECTED`, `EXPIRED` | **nikdy** — koncové stavy, zamietnuté ako `INVALID_STATE` *(v0.2)* |

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
podmienkou by sa nedal ani zrušiť (nález N-03, časť 9).

**Pre `PENDING_APPROVAL` neplatí 60-minútová lehota** *(rozhodnutie tímu,
v0.2)*, hoci tento stav prístroj blokuje. Nie je to nedôslednosť: lehota
nechráni prístroj pred uvoľnením, ale chráni **záväzok** — a voči žiadateľovi,
ktorého žiadosť ešte nikto neschválil, žiadny záväzok nevznikol. Nútiť ho
držať žiadosť, ktorú mu vedúci môže kedykoľvek schváliť, by bolo horšie než
neskoré uvoľnenie prístroja.

Platí preň však **hranica expirácie** (BR-08): žiadosť, ktorej termín už začal,
sa nedá zrušiť, pretože v tom okamihu už nie je žiadosťou — je vypršaná. Nejde
o lehotu na zrušenie, ale o to, že predmet zrušenia zanikol (nález N-04).

`REJECTED` a `EXPIRED` sa zrušiť nedajú a zrušenie na nich **nie je
idempotentné** (REQ-09 sa na ne nevzťahuje). Tiché „úspešné“ zrušenie by
zakrylo, že rezerváciu v skutočnosti zamietol človek alebo že prepadla.

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
- nedá sa preň potvrdiť rezervácia ani podať žiadosť o schválenie (OP-03),
- nedá sa schváliť čakajúca žiadosť (OP-05) *(v0.2)*,
- kontrola dostupnosti ho hlási ako nedostupný pre ľubovoľný interval (OP-02).

Deaktivácia prístroja **neruší** existujúce `CONFIRMED` rezervácie ani živé
žiadosti — tie sa riešia ručne cez OP-04, prípadne zamietnutím (REQ-12).
Správa prístrojov nie je v rozsahu tejto baseline (TBD-02).

### BR-06 — Oprávnenie *(zjednodušené v0.1)*

- Používateľ uvedený v požiadavke musí existovať.
- Operácie nad existujúcou rezerváciou (OP-03, OP-04) smie vykonať iba jej
  **vlastník** alebo používateľ s rolou `SUPERVISOR`. Pre OP-05 platí prísnejšie
  pravidlo BR-07, ktoré toto nenahrádza, ale dopĺňa.
- OP-02 (kontrola dostupnosti) je čítacia a oprávnenie nevyžaduje.

**Predpoklad:** identita prichádza v požiadavke ako `user_id`; autentifikácia je
mimo rozsah predmetu (C01). „Oprávnený používateľ“ v zvyšku dokumentu znamená
presne túto podmienku.

### BR-07 — Oprávnenie schvaľovať *(nové v v0.2)*

O žiadosti o schválenie smie rozhodnúť **iba používateľ s rolou `SUPERVISOR`**,
a **nie nad vlastnou rezerváciou**.

Vedúci laboratória si smie prístroj rezervovať ako ktokoľvek iný, ale rozhodnúť
o svojej vlastnej žiadosti nesmie. Bez druhej podmienky by schvaľovanie pre
vedúceho fakticky neexistovalo a pravidlo by platilo len pre študentov — čo je
presne opak toho, prečo schvaľovanie zavádzame.

Dôsledok, ktorý treba uniesť: ak je v laboratóriu jediný vedúci, jeho vlastné
žiadosti nemá kto schváliť a prepadnú podľa BR-08. Je to **vedomý** dôsledok,
nie prehliadnutie — viď TBD-07.

### BR-08 — Expirácia žiadosti *(nové v v0.2)*

Žiadosť v stave `PENDING_APPROVAL` platí, kým nenastane `starts_at` rezervácie:

- `currentTime < starts_at` → žiadosť je živá, blokuje prístroj, dá sa
  schváliť alebo zamietnuť,
- `currentTime >= starts_at` → žiadosť je **vypršaná**: neblokuje prístroj
  a nemožno ju schváliť, zamietnuť **ani zrušiť**. Pozorovateľný stav je
  `EXPIRED` (nález N-04, časť 9).

Expirácia sa týka **iba** stavu `PENDING_APPROVAL`. `DRAFT` nevyprší, pretože
nikoho neblokuje — zostáva zrušiteľný bez lehoty (BR-03) a nepotvrditeľný po
svojom začiatku (REQ-06).

**Hranica** je rovnako ostrá ako všade inde: v okamihu `starts_at` už žiadosť
neplatí.

*Zdôvodnenie voľby hranice:* lehota je odvodená z údajov, ktoré už máme, takže
do špecifikácie nevstupuje žiadne vymyslené číslo. Žiadosť, ktorej termín už
začal, nemá čo schvaľovať — prístroj v tom čase buď niekto použil, alebo stál
prázdny, a ani jedno sa rozhodnutím vedúceho spätne nezmení.

**Ako prechod vzniká.** `EXPIRED` je jediný prechod v systéme, ktorý nespúšťa
človek. Špecifikácia ho popisuje ako **pozorovateľný stav, nie ako úlohu
plánovača**, a rozlišuje dve veci:

- **Vyhodnotenie:** žiadosť so `starts_at` v minulosti sa považuje za vypršanú
  v **každej** operácii, ktorá ju číta. OP-02 ju preto nezapočíta do blokovania
  a OP-03 jej prekryv ignoruje — bez ohľadu na to, aký stav má zapísaný.
- **Zápis:** stav `EXPIRED` zapíšu iba operácie, ktoré na tú **konkrétnu**
  žiadosť smerujú — OP-04 a OP-05. Čítacie operácie (OP-02) ani operácie nad
  inou rezerváciou (OP-03) cudzí záznam neprepisujú.

Dôsledok, ktorý treba priznať: žiadosť môže mať v databáze zapísané
`PENDING_APPROVAL`, hoci sa podľa pravidiel už správa ako `EXPIRED`, a to až
dovtedy, kým o ňu niekto nepožiada. Pre správanie systému to nič nemení, pre
čitateľa databázy áno. Dávkové dočisťovanie je rozhodnutie architektúry
v C03 — TBD-08.

**Ako sa o vypršaní dozvie žiadateľ.** Nijako — a to je diera, ktorú
priznávame: systém v tejto fáze nemá operáciu „prečítaj rezerváciu“ ani
oznámenie pri vypršaní. Žiadateľ vypršanie zistí len tak, že sa pokúsi
o operáciu, ktorá skončí `EXPIRED`. Viď TBD-09.

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

> **REQ-03** *(zmenené v v0.2)***:** Pre existujúci aktívny prístroj a platný
> interval systém ohlási prístroj ako **nedostupný**, ak sa interval prekrýva
> (BR-01) s ľubovoľnou rezerváciou toho istého prístroja v **blokujúcom stave**
> — `CONFIRMED` alebo živá žiadosť `PENDING_APPROVAL` (BR-08); inak ho ohlási
> ako **dostupný**. Neaktívny prístroj je nedostupný pre ľubovoľný interval
> (BR-05).

> **REQ-13** *(nové v v0.2)***:** Vo výsledku musí byť rozoznateľné, či je
> prístroj nedostupný kvôli potvrdenej rezervácii, alebo kvôli žiadosti
> čakajúcej na schválenie.

**Predpoklady:**
- prístroj existuje,
- interval je platný podľa BR-01.

**Stav po úspešnom vykonaní:**
- vráti sa výsledok dostupnosti, dôvod nedostupnosti a zoznam identifikátorov
  kolidujúcich rezervácií (prázdny, ak je prístroj dostupný),
- dôvod je jeden z `CONFIRMED`, `PENDING_APPROVAL`, `INSTRUMENT_INACTIVE`
  (REQ-13); ak kolidujú oba stavy naraz, dôvodom je `CONFIRMED`, pretože ten
  je definitívny,
- **žiadna** rezervácia nezmenila stav, žiadna nevznikla.

**Zmena stavu:** žiadna — operácia je čítacia. Platí to aj pre vypršané
žiadosti: OP-02 ich do blokovania nezapočíta (BR-08), ale sama ich stav
neprepisuje.

**Interval v minulosti je platný dopyt.** Na rozdiel od OP-01 tu podmienka
`currentTime < starts_at` **neplatí**: otázka „bol prístroj v utorok o desiatej
obsadený a kým“ je legitímna a systém na ňu vie odpovedať z tých istých dát.
Rozdiel je zámerný — OP-01 mení stav do budúcnosti, OP-02 iba číta (nález N-02,
časť 9).

**Odkaz na pravidlá:** BR-01, BR-02, BR-05, BR-08.

**Hlavný úspešný scenár:**
1. Používateľ odošle `instrument_id` a interval `[s, e)`.
2. Systém overí existenciu prístroja a platnosť intervalu.
3. Systém odčíta `currentTime` (potrebný na posúdenie živosti žiadostí podľa
   BR-08).
4. Systém nájde rezervácie toho istého prístroja v blokujúcom stave, ktoré sa
   prekrývajú s intervalom.
5. Systém vráti `AVAILABLE` (žiadna kolízia) alebo `UNAVAILABLE` s dôvodom
   a zoznamom kolidujúcich rezervácií.

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

--- pridané v v0.2 ---

existuje živá PENDING_APPROVAL [10:00, 11:00), teraz je 08:00:
  dopyt [10:30, 11:30)  → UNAVAILABLE, dôvod PENDING_APPROVAL
  dopyt [11:00, 12:00)  → AVAILABLE                     (BR-01 platí rovnako)

existuje PENDING_APPROVAL [10:00, 11:00), teraz je 10:00:
  dopyt [10:00, 11:00)  → AVAILABLE      (žiadosť vypršala, BR-08)

existuje CONFIRMED aj PENDING_APPROVAL na prekrývajúcom sa intervale:
  dopyt [10:30, 11:30)  → UNAVAILABLE, dôvod CONFIRMED  (definitívny stav má prednosť)

existuje REJECTED [10:00, 11:00):
  dopyt [10:00, 11:00)  → AVAILABLE      (zamietnutá žiadosť neblokuje)
```

**Zdôvodnenie / zdroj:**
Odpoveď musí používať **presne ten istý** význam prekryvu a tú istú množinu
blokujúcich stavov ako OP-03. Inak systém sľúbi voľný termín, ktorý potvrdenie
o sekundu neskôr zamietne — a používateľ prestane odpovedi veriť.

Zoznam kolidujúcich rezervácií je súčasťou pozorovateľného výsledku zámerne:
bez neho nevieme z čiernej skrinky odlíšiť „nedostupné kvôli tejto rezervácii“
od „nedostupné kvôli chybe v dotaze“.

*Čo zmena v0.2 urobila s povahou tejto operácie:* do vyhodnotenia vstúpil čas.
V v0.1 bola odpoveď čistou funkciou uložených dát — tie isté dáta dali tú istú
odpoveď kedykoľvek. Od v0.2 sa tá istá žiadosť v ten istý deň ráno počíta ako
blokujúca a po `starts_at` už nie. Je to cena za expiráciu bez plánovača
a treba ju priznať: dva dopyty s rovnakým vstupom môžu vrátiť rôzny výsledok.

---

## 5. OP-03 — Confirm Reservation

**Cieľ / hodnota pre používateľa:**
Návrh rezervácie prestáva byť návrhom. Pri bežnom prístroji sa stáva platnou
alokáciou hneď; pri prístroji, ktorý vyžaduje schválenie, sa stáva **žiadosťou**,
ktorá prístroj rezervuje pre žiadateľa až do rozhodnutia vedúceho.

**Spúšťacia udalosť:**
Oprávnený používateľ požiada o potvrdenie rezervácie `X`.

**Pozorovateľné požiadavky:**

> **REQ-04** *(zmenené v v0.2)***:** Systém prijme potvrdenie rezervácie v stave
> `DRAFT`, len ak je jej prístroj aktívny (BR-05), **vlastník rezervácie** má
> platný certifikát na kategóriu prístroja (BR-04) a interval rezervácie sa
> neprekrýva so žiadnou existujúcou rezerváciou toho istého prístroja
> v blokujúcom stave, teda ani so **živou** žiadosťou (BR-02, BR-08).

> **REQ-10** *(nové v v0.2)***:** Výsledný stav závisí od prístroja: ak
> `Instrument.requires_approval` neplatí, rezervácia prejde do `CONFIRMED`; ak
> platí, prejde do `PENDING_APPROVAL`. Systém v odpovedi vráti dosiahnutý stav,
> aby používateľ vedel, či má prístroj pridelený, alebo naň čaká.

> **REQ-05** *(zmenené v v0.2)***:** Pri súbežných pokusoch **potvrdiť alebo
> schváliť** rezervácie, ktoré sú navzájom v konflikte podľa BR-02, dosiahne
> blokujúci stav (`CONFIRMED` alebo `PENDING_APPROVAL`) **najviac jedna**
> z nich. Ostatné skončia zamietnutím a zostanú v pôvodnom stave — pri
> potvrdení `DRAFT`, pri schvaľovaní `PENDING_APPROVAL`.

> **REQ-16** *(nové v v0.2)***:** Rezervácia opustí daný stav **najviac raz**,
> aj keď o ňu súbežne žiadajú dve rôzne operácie. Ak sa stretne zrušenie
> s potvrdením alebo so schválením tej istej rezervácie, uspeje najviac jedno
> z nich; druhé skončí zamietnutím podľa už zmeneného zdrojového stavu.
> Nikdy nenastane stav, v ktorom by používateľ dostal na zrušenie úspech
> a rezervácia pritom blokovala prístroj.

> **REQ-06:** Rezerváciu, ktorej začiatok už nastal, nemožno potvrdiť.

**Predpoklady:**
- rezervácia existuje,
- `state = DRAFT`,
- žiadateľ je vlastník alebo `SUPERVISOR` (BR-06),
- `currentTime < starts_at`.

**Stav po úspešnom vykonaní:**
- `state = CONFIRMED` (prístroj bez `requires_approval`), alebo
  `state = PENDING_APPROVAL` (prístroj s `requires_approval`) — REQ-10,
- rezervácia v oboch prípadoch **blokuje** prístroj pre svoj interval — OP-02
  pre prekrývajúci sa interval odteraz vracia `UNAVAILABLE` a uvádza túto
  rezerváciu; líši sa iba uvedený dôvod,
- BR-02 naďalej platí,
- (po implementácii hranice) odoslané oznámenie do Notification Service — TBD-03.

**Zmena stavu:** `DRAFT → CONFIRMED`, alebo `DRAFT → PENDING_APPROVAL`

**Odkaz na pravidlá:** BR-01, BR-02, BR-04, BR-05, BR-06.

**Hlavný úspešný scenár:**
1. Používateľ odošle `reservation_id` a svoju identitu.
2. Systém nájde rezerváciu a overí oprávnenie.
3. Systém overí, že rezervácia je v stave `DRAFT` a jej začiatok ešte nenastal.
4. Systém overí, že prístroj je aktívny.
5. Systém overí certifikát **vlastníka rezervácie** na kategóriu prístroja
   (BR-04) — nie žiadateľa, ktorý môže byť vedúci (BR-06).
6. Systém overí, že interval nekoliduje so žiadnou rezerváciou toho istého
   prístroja v blokujúcom stave (BR-02, BR-08).
7. Systém nastaví `state = PENDING_APPROVAL`, ak prístroj vyžaduje schválenie,
   inak `state = CONFIRMED` (REQ-10), a vráti dosiahnutý stav rezervácie.

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
- prekryv s rezerváciou v blokujúcom stave → zamietnuté, zostáva `DRAFT`
  (vrátane prípadu, keď blokuje cudzia **živá žiadosť** — pre žiadateľa je
  výsledok rovnaký ako pri potvrdenej rezervácii, iba dôvod je iný),
- `currentTime >= starts_at` → zamietnuté `ALREADY_STARTED`, zostáva `DRAFT`.
  Je to iný kód než `START_IN_PAST` pri OP-01: tam ide o chybný **vstup**,
  tu o uložený stav a čas.

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
dve súbežné potvrdenia prekrývajúcich sa rezervácií            → najviac jedna v blokujúcom stave (REQ-05)
súbežné zrušenie a potvrdenie tej istej rezervácie             → uspeje najviac jedno (REQ-16)

--- pridané v v0.2 ---

DRAFT + prístroj s requires_approval + všetko ostatné v poriadku → PENDING_APPROVAL (REQ-10)
DRAFT + prekryv so živou PENDING_APPROVAL                        → zamietnuté, zostáva DRAFT
DRAFT + prekryv s vypršanou PENDING_APPROVAL                     → CONFIRMED / PENDING_APPROVAL (BR-08)
DRAFT + prekryv s REJECTED                                       → prejde (REJECTED neblokuje)
už PENDING_APPROVAL                                              → zamietnuté, zostáva PENDING_APPROVAL
už REJECTED / EXPIRED                                            → zamietnuté, stav nezmenený
```

**Zdôvodnenie / zdroj:**
Potvrdenie je prechod, ktorý mení obsadenosť prístroja, a preto miesto, kde sa
vynucujú BR-02 a BR-04 (C01, Project Frame). Od v0.2 to už nie je prechod
**jediný** — rovnaké pravidlá musí znova vyhodnotiť aj schválenie (OP-05),
pretože medzi žiadosťou a rozhodnutím ubehne čas.

*Prečo `Confirm` nedostal nové meno.* Zvažovali sme rozdeliť operáciu na
„požiadať o schválenie“ a „potvrdiť“. Zamietli sme to: z pohľadu študenta ide
o ten istý cieľ — „chcem tento prístroj na tento čas naozaj“ — a to, či za tým
nasleduje rozhodnutie vedúceho, je vlastnosť prístroja, nie iný zámer
používateľa. Preto je rozdiel viditeľný vo **výsledku** (REQ-10), nie v dvoch
tlačidlách, medzi ktorými by si musel vyberať.

REQ-05 nie je implementačný detail: ak by pri súbehu vznikli dve potvrdené
rezervácie, systém by produkoval presne ten výsledok, kvôli ktorému vzniká —
dvoch ľudí pred jedným prístrojom. Ako sa to zabezpečí (transakčná izolácia,
zámok, databázový constraint) je otázka architektúry v C03; **pozorovateľný
výsledok** patrí sem.

**Prijatý dôsledok:** rezerváciu možno potvrdiť aj menej než 60 minút pred
začiatkom — a taká rezervácia sa už podľa BR-03 nedá zrušiť. Nie je to
opomenutie: kto potvrdzuje na poslednú chvíľu, prístroj v tom čase chce, a keby
ho aj uvoľnil, nikto iný by to už nestihol využiť. Platí to isté, čo zdôvodňuje
samotnú 60-minútovú lehotu (nález N-01, časť 9).

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

> **REQ-14** *(nové v v0.2)***:** Systém zruší **živú** žiadosť v stave
> `PENDING_APPROVAL` (BR-08) bez časového obmedzenia; po zrušení prestáva
> blokovať prístroj a nemožno o nej ďalej rozhodnúť (OP-05). Pokus zrušiť
> žiadosť, ktorej `starts_at` už nastal, skončí zamietnutím s dôvodom
> `EXPIRED` a systém jej stav zapíše ako `EXPIRED`. Rezerváciu v stave
> `REJECTED` alebo `EXPIRED` zrušiť **nemožno** — pokus skončí zamietnutím
> `INVALID_STATE` a idempotencia podľa REQ-09 sa na tieto stavy nevzťahuje.

**Predpoklady:**
- rezervácia existuje,
- žiadateľ je vlastník alebo `SUPERVISOR` (BR-06),
- stav je `DRAFT`, `PENDING_APPROVAL`, `CONFIRMED` alebo `CANCELLED`,
- pre `CONFIRMED` platí 60-minútová podmienka BR-03; pre `PENDING_APPROVAL`
  platí podmienka živosti `currentTime < starts_at` (BR-08); pre `DRAFT`
  neplatí žiadna časová podmienka.

**Stav po úspešnom vykonaní:**
- `state = CANCELLED`,
- rezervácia neblokuje dostupnosť prístroja — OP-02 pre jej interval vracia
  `AVAILABLE`, ak neexistuje iná kolízia,
- (po implementácii hranice) odoslané oznámenie do Notification Service — TBD-03;
  pri idempotentnom opakovaní (REQ-09) sa oznámenie **neodosiela**.

**Zmena stavu:** `DRAFT → CANCELLED`, `PENDING_APPROVAL → CANCELLED`,
`CONFIRMED → CANCELLED`, `CANCELLED → CANCELLED` (bez efektu).

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
- už `CANCELLED` → **úspech bez zmeny** (REQ-09),
- `REJECTED` alebo `EXPIRED` → zamietnuté `INVALID_STATE`, stav nezmenený (REQ-14),
- `PENDING_APPROVAL`, ktorej `starts_at` už nastal → žiadosť je vypršaná:
  systém zapíše `state = EXPIRED` a zrušenie **zamietne** s dôvodom `EXPIRED`
  (BR-08, nález N-04). Je to jediné miesto v celej špecifikácii, kde operácia
  zmení stav a napriek tomu skončí zamietnutím.

Pre `DRAFT` neexistuje časový dôvod zamietnutia — zamietnuť sa dá iba neznáma
rezervácia alebo neoprávnený žiadateľ. Pre `PENDING_APPROVAL` je jediným
časovým dôvodom expirácia podľa BR-08, nie lehota podľa BR-03.

**Súbeh s potvrdením alebo schválením nad tou istou rezerváciou** (REQ-16):
Rezervácia opustí daný stav najviac raz; druhá operácia vidí už zmenený
zdrojový stav a vyhodnotí sa podľa neho:

- zvíťazí *cancel* → rezervácia je `CANCELLED`, následný *confirm* aj *approve*
  sú zamietnuté (zdrojový stav nesedí),
- zvíťazí *confirm* na bežnom prístroji → rezervácia je `CONFIRMED`, následný
  *cancel* sa posudzuje podľa prísnejšieho pravidla pre `CONFIRMED` (60 min),
- zvíťazí *confirm* na prístroji so schvaľovaním → rezervácia je
  `PENDING_APPROVAL` a následný *cancel* sa posudzuje podľa BR-08, nie podľa
  60-minútovej lehoty,
- zvíťazí *approve* → rezervácia je `CONFIRMED` a platí druhý bod.

Nikdy nenastane stav, v ktorom by rezervácia bola zároveň zrušená aj blokovala
prístroj — ani opačne, že by používateľ dostal na zrušenie úspech a rezervácia
pritom prešla do blokujúceho stavu.

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

--- pridané v v0.2 ---

PENDING_APPROVAL, 10 min pred začiatkom → CANCELLED + prístroj je AVAILABLE
PENDING_APPROVAL, po starts_at           → zamietnuté, stav sa mení na EXPIRED (N-04)
PENDING_APPROVAL zrušená, potom pokus o schválenie → zamietnuté (OP-05)
REJECTED                                → zamietnuté INVALID_STATE
EXPIRED                                 → zamietnuté INVALID_STATE
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

## 7. OP-05 — Approve Reservation *(nové v v0.2)*

**Cieľ / hodnota pre používateľa:**
Vedúci laboratória rozhodne o žiadosti o prístroj, ktorý vyžaduje schválenie.
Pre žiadateľa je to okamih, keď sa dozvie, či prístroj dostane; pre vedúceho
miesto, kde má kontrolu nad drahým alebo rizikovým vybavením.

**Spúšťacia udalosť:**
Vedúci laboratória rozhodne o rezervácii `X` v stave `PENDING_APPROVAL` —
schváli ju, alebo zamietne.

**Pozorovateľné požiadavky:**

> **REQ-11:** Systém schváli žiadosť v stave `PENDING_APPROVAL` (prechod do
> `CONFIRMED`), len ak rozhoduje oprávnený schvaľovateľ (BR-07), žiadosť ešte
> nevypršala (BR-08), prístroj je aktívny (BR-05), vlastník rezervácie má
> stále platný certifikát (BR-04) a interval nekoliduje s inou rezerváciou
> v blokujúcom stave (BR-02).

> **REQ-12:** Systém zamietne žiadosť v stave `PENDING_APPROVAL` (prechod do
> `REJECTED`) na pokyn oprávneného schvaľovateľa. Zamietnutie nevyžaduje
> splnenie podmienok z REQ-11 okrem oprávnenia a živosti žiadosti; po ňom
> rezervácia prestáva blokovať prístroj.

> **REQ-15:** Pokus **oprávneného schvaľovateľa** rozhodnúť — schváliť alebo
> zamietnuť — o žiadosti, ktorej `starts_at` už nastal, skončí zamietnutím
> s dôvodom `EXPIRED`. Systém pritom stav rezervácie zapíše ako `EXPIRED`
> (BR-08). Pokus neoprávneného používateľa skončí skôr, na kontrole
> oprávnenia, a stav nemení.

**Predpoklady:**
- rezervácia existuje,
- rozhoduje používateľ s rolou `SUPERVISOR`, ktorý nie je vlastníkom
  rezervácie (BR-07),
- `state = PENDING_APPROVAL`,
- `currentTime < starts_at` (BR-08).

Oprávnenie sa overuje **pred** stavom, rovnako ako v OP-03 a OP-04: inak by sa
neoprávnený používateľ z kódu chyby dozvedel stav cudzej rezervácie.

**Stav po úspešnom vykonaní:**

*Schválenie:*
- `state = CONFIRMED`,
- rezervácia naďalej blokuje prístroj, ale už ako definitívna alokácia — OP-02
  odteraz uvádza dôvod `CONFIRMED` namiesto `PENDING_APPROVAL`,
- BR-02 naďalej platí,
- (po implementácii hranice) odoslané oznámenie žiadateľovi — TBD-03.

*Zamietnutie:*
- `state = REJECTED`,
- rezervácia **prestáva blokovať** prístroj — OP-02 pre jej interval vracia
  `AVAILABLE`, ak neexistuje iná kolízia,
- rezervácia je v koncovom stave: nedá sa zrušiť ani znova potvrdiť (REQ-14),
- (po implementácii hranice) odoslané oznámenie žiadateľovi — TBD-03.

**Zmena stavu:** `PENDING_APPROVAL → CONFIRMED`, `PENDING_APPROVAL → REJECTED`,
alebo `PENDING_APPROVAL → EXPIRED` pri pokuse o rozhodnutie po `starts_at`
(REQ-15).

**Odkaz na pravidlá:** BR-02, BR-04, BR-05, BR-07, BR-08.

**Hlavný úspešný scenár (schválenie):**
1. Vedúci odošle `reservation_id`, svoju identitu a rozhodnutie *schváliť*.
2. Systém nájde rezerváciu a overí oprávnenie schvaľovateľa (BR-07).
3. Systém overí, že rezervácia je v stave `PENDING_APPROVAL`.
4. Systém odčíta `currentTime` a overí, že žiadosť nevypršala (BR-08).
5. Systém **znova** overí prístroj (BR-05), certifikát vlastníka (BR-04)
   a prekryv s blokujúcimi rezerváciami (BR-02).
6. Systém nastaví `state = CONFIRMED` a vráti aktuálny stav rezervácie.

**Alternatívny scenár (zamietnutie):**
1.–4. rovnako ako vyššie, s rozhodnutím *zamietnuť*.
5. Systém nastaví `state = REJECTED` a vráti aktuálny stav rezervácie.

**Alternatívne / chybové výsledky:**
- neznáma rezervácia → zamietnuté,
- rozhoduje používateľ bez role `SUPERVISOR` → zamietnuté `FORBIDDEN`, stav
  nezmenený,
- vedúci rozhoduje o **vlastnej** žiadosti → zamietnuté `FORBIDDEN`, stav
  nezmenený (BR-07),
- stav nie je `PENDING_APPROVAL` (`DRAFT`, `CONFIRMED`, `CANCELLED`,
  `REJECTED` aj už zapísaný `EXPIRED`) → zamietnuté `INVALID_STATE`, stav
  nezmenený,
- žiadosť vypršala → zamietnuté `EXPIRED`, stav sa mení na `EXPIRED` (REQ-15),
- prístroj je medzičasom neaktívny → schválenie zamietnuté, žiadosť zostáva
  `PENDING_APPROVAL`,
- certifikát vlastníka medzičasom stratil platnosť (vedúci ho odobral alebo
  skrátil) → schválenie zamietnuté, žiadosť zostáva `PENDING_APPROVAL`,
- interval medzičasom obsadila iná potvrdená rezervácia → schválenie zamietnuté
  `OVERLAP`, žiadosť zostáva `PENDING_APPROVAL`.

Posledné tri prípady zostávajú v `PENDING_APPROVAL` zámerne: prekážka môže
zmiznúť (prístroj sa vráti zo servisu, študent si obnoví certifikát) a vedúci
môže rozhodnúť znova. Zamietnuť žiadosť môže kedykoľvek explicitne (REQ-12).

**Príklady overenia:**

```
PENDING_APPROVAL + supervisor + všetko platí        → CONFIRMED
PENDING_APPROVAL + supervisor zamieta               → REJECTED + prístroj je AVAILABLE
PENDING_APPROVAL + rozhoduje študent                → zamietnuté FORBIDDEN, zostáva PENDING_APPROVAL
PENDING_APPROVAL vlastnená supervisorom + ten istý supervisor → zamietnuté FORBIDDEN (BR-07)
PENDING_APPROVAL vlastnená supervisorom + iný supervisor      → CONFIRMED
PENDING_APPROVAL, currentTime == starts_at          → zamietnuté EXPIRED, stav = EXPIRED (hranica BR-08)
PENDING_APPROVAL, currentTime == starts_at - 1 s    → CONFIRMED
PENDING_APPROVAL + certifikát medzitým odobratý    → zamietnuté, zostáva PENDING_APPROVAL
PENDING_APPROVAL + prístroj medzitým deaktivovaný   → zamietnuté, zostáva PENDING_APPROVAL
PENDING_APPROVAL + interval medzitým potvrdený inde → zamietnuté OVERLAP, zostáva PENDING_APPROVAL
už CONFIRMED / CANCELLED / REJECTED                 → zamietnuté INVALID_STATE
dve súbežné schválenia kolidujúcich žiadostí        → najviac jedna CONFIRMED (REQ-05)
```

**Zdôvodnenie / zdroj:**
Zmenová karta C02 hovorí, že schválenie môže byť **oneskorené, zamietnuté alebo
môže vypršať**. Práve to je dôvod, prečo sa kontroly z OP-03 musia vykonať
znova: medzi žiadosťou a rozhodnutím ubehne ľubovoľne dlhý čas a svet sa
medzitým zmení. Keby sa neopakovali, vedúci by jedným kliknutím potvrdil
rezerváciu, ktorá porušuje BR-04, BR-05 alebo BR-02.

**Pozor na presnú formuláciu pri certifikáte.** BR-04 porovnáva `valid_until`
so `starts_at` rezervácie — dva uložené údaje. Samotným plynutím času sa teda
výsledok tejto kontroly zmeniť **nemôže**; zmeniť sa musí **záznam**
certifikátu, a to sa stane, keď vedúci certifikát odoberie alebo skráti jeho
platnosť (certifikáty eviduje ručne, C01). Opakovaná kontrola BR-04 pri
schválení chráni presne pred týmto, nie pred „vypršaním počas čakania“.
U BR-05 a BR-02 je to inak — tam stačí, že sa medzičasom zmení stav prístroja
alebo pribudne iná rezervácia.

Schválenie a zamietnutie sú **jedna operácia s dvoma výsledkami**, nie dve
operácie: je to jeden cieľ aktéra — rozhodnúť o žiadosti — s rovnakými
predpokladmi. `Cancel` je oproti `Confirm` samostatná operácia práve preto, že
je to iný cieľ iného človeka v inom okamihu.

**Prijatý dôsledok (nález N-06):** vedúci smie schváliť žiadosť kedykoľvek do
`starts_at` — teda aj desať minút pred začiatkom. Taká rezervácia prejde do
`CONFIRMED` a podľa BR-03 ju už **nikto nezruší**, ani žiadateľ.

Nie je to ten istý prípad, ktorý obhájil nález N-01: tam neskorý prechod
vykonal sám žiadateľ, takže mu nemohol byť proti vôli. Tu ho vykoná niekto iný.
Prijímame to napriek tomu, lebo žiadateľ má celý čas čakania možnosť žiadosť
zrušiť bez akejkoľvek lehoty (REQ-14) — kto prístroj už nechce, nemusí čakať
na rozhodnutie. Kto žiadosť nechá žiť, dáva najavo, že prístroj stále chce.

**Predpoklad / neznáma:** TBD-07.

---

## 8. Predpoklady, neznáme a otvorené otázky

| ID     | Vec                                                                                        | Stav                                         |
| ------ | ------------------------------------------------------------------------------------------ | -------------------------------------------- |
| TBD-01 | Minimálna / maximálna dĺžka rezervácie, prípadne zarovnanie na sloty.                        | nerozhodnuté; špecifikácia nezavádza žiadny limit |
| TBD-02 | Správa prístrojov a certifikátov (vytváranie, deaktivácia, prepínanie `requires_approval`) — kto a cez aké rozhranie. | mimo rozsah; dáta sa napĺňajú priamo |
| TBD-03 | Notification Service — protokol, správanie pri výpadku, synchrónne vs. asynchrónne volanie.  | hranica definovaná (C01), neimplementovaná; od v0.2 **chýbajúca časť toku**, nie ozdoba |
| TBD-04 | Zdroj certifikátov: vlastná evidencia vs. študijný systém univerzity (C01 unknown).          | otvorené; ovplyvní súbeh a správanie OP-03 aj OP-05 |
| TBD-05 | Zobrazenie lokálneho času používateľovi — databáza vracia UTC (nález C01 spiku).             | otvorené; pravdepodobne pásmo na `Instrument` |
| TBD-06 | Autentifikácia. Špecifikácia predpokladá dôveryhodné `user_id` v požiadavke (BR-06).         | mimo rozsah predmetu                         |
| TBD-07 | Žiadosť vedúceho laboratória, ak je v laboratóriu jediný — podľa BR-07 ju nemá kto schváliť a prepadne. | otvorené; rieši sa personálne (druhý schvaľovateľ), nie zmenou pravidla *(v0.2)* |
| TBD-08 | Dočisťovanie vypršaných žiadostí: stav `EXPIRED` sa dnes zapíše až pri operácii, ktorá na žiadosť smeruje. | otvorené; dávka alebo plánovač je rozhodnutie C03 *(v0.2)* |
| TBD-09 | Systém nemá operáciu na **čítanie** rezervácií ani zoznam čakajúcich žiadostí. Žiadateľ sa o vypršaní a vedúci o novej žiadosti nedozvedia inak než cez Notification Service (TBD-03). | otvorené; ovplyvní použiteľnosť celého schvaľovacieho toku *(v0.2)* |

Žiadna z týchto položiek nie je doplnená vymyslenou hodnotou. Ak sa v texte
objaví číslo (60 minút v BR-03), má zdroj — je to rozhodnutie tímu, nie odhad.
Expirácia žiadosti (BR-08) zámerne **žiadne nové číslo nezavádza**: viaže sa na
`starts_at`, ktorý v dátach už je.

---

## 9. Kontrola konzistencie špecifikácie ako celku

Špecifikáciu sme kontrolovali ako **jeden systém tvrdení**, nie ako izolované
texty. Kontrola prebehla po dopísaní diagramov, teda nad textom aj obrázkami
naraz.

> **Nasledujúca tabuľka je záznam kontroly pri baseline v0.1** a hovorí o štyroch
> operáciách a o blokujúcom stave `{CONFIRMED}`. Nechávame ju tak, ako vznikla —
> je to doklad o tom, čo sme kontrolovali vtedy. Stav po zmene je v tabuľke
> „Kontrola konzistencie po zmene v0.2“ nižšie.

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

### Kontrola konzistencie po zmene v0.2

Po zavedení schvaľovania sme kontrolu zopakovali — zmena sa dotkla pojmu,
na ktorom stojí celý model (okamih vzniku alokácie), takže staré závery
neplatili automaticky.

| Kontrola | Výsledok |
| -------- | -------- |
| Blokujúce stavy vs. všetky operácie | ✅ `{CONFIRMED, PENDING_APPROVAL}` platí rovnako v BR-02, OP-02, OP-03 aj OP-05. Množina je definovaná raz, v časti „Čo znamená alokácia“. |
| Confirm vs. Approve | ✅ Obe vyhodnocujú tie isté pravidlá (BR-02, BR-04, BR-05). Approve ich vyhodnocuje **znova**, pretože medzitým ubehol čas — to je v OP-05 napísané, nie ponechané na implementáciu. |
| Availability vs. expirácia | ✅ Vypršaná žiadosť neblokuje (BR-08) a OP-02 ju do kolízií nezapočítava. |
| Cancel vs. nové stavy | ⚠️ Našiel sa rozpor — viď N-04 nižšie. Po oprave ✅. |
| Idempotencia zrušenia | ✅ REQ-09 zostal v pôvodnom rozsahu (`CANCELLED`). `REJECTED` a `EXPIRED` sú iné koncové stavy a zrušenie na nich zamieta. |
| Use case diagram vs. text | ✅ Nový cieľ „rozhodnúť o žiadosti“ má špecifikované chovanie (OP-05); nový aktér nevznikol. |
| Stavový diagram vs. text | ✅ Šesť stavov, všetky prechody v texte aj v diagrame vrátane `PENDING_APPROVAL → EXPIRED`. |
| Požiadavka vs. návrhové rozhodnutie | ✅ REQ-15 popisuje pozorovateľný výsledok pokusu o rozhodnutie, nie plánovač. Ako sa `EXPIRED` dočistí, je otázka C03 (TBD-08). |
| Neistota vs. vymyslená presnosť | ✅ Expirácia nezaviedla žiadne nové číslo — viaže sa na `starts_at`. Alternatíva „48 hodín“ bola zamietnutá práve preto, že by číslo nemalo zdroj. |

**N-04 — Zrušenie vypršanej žiadosti si odporovalo s expiráciou.**
BR-03 dovoľuje zrušiť `PENDING_APPROVAL` kedykoľvek. BR-08 hovorí, že žiadosť
po `starts_at` je vypršaná a `EXPIRED` sa zrušiť nedá. Pre žiadosť, ktorej
termín už začal, dávali obe pravidlá **opačnú odpoveď** — a implementácia by
si vybrala podľa poradia podmienok v kóde.
*Vyriešené:* opravená **špecifikácia**. Expirácia má prednosť, pretože BR-08 je
formulované ako vlastnosť žiadosti, nie ako správanie jednej operácie:
vypršaná žiadosť sa v každej operácii, ktorá ju číta, považuje za `EXPIRED`.
OP-04 teda stav zapíše a zrušenie zamietne. Premietnuté do BR-08, OP-04
a príkladov overenia.

**N-05 — Nebolo povedané, či vypršať môže aj `DRAFT`.**
Text hovoril o expirácii žiadosti, ale `DRAFT` po začiatku je podobne „mŕtvy“
záznam. Bez explicitného stanoviska by si to niekto domyslel.
*Vyriešené:* doplnené do BR-08 — expirácia sa týka **iba** `PENDING_APPROVAL`,
pretože iba ten blokuje prístroj. `DRAFT` ostáva presne taký, aký bol po
náleze N-03.

### Kontrola po nezávislej revízii v0.2

Baseline v0.2 sme po dopísaní dali skontrolovať ešte raz, nezávisle od toho,
kto ju písal. Revízia našla **šesť skutočných rozporov** — všetky vznikli tým,
že oprava nálezu N-04 sa premietla len do časti dokumentu. Uvádzame ich, lebo
sú to presne tie chyby, ktoré by inak prežili do C03:

| # | Čo bolo zle | Ako je to vyriešené |
| - | ----------- | ------------------- |
| R-1 | BR-03 a REQ-14 stále tvrdili, že `PENDING_APPROVAL` sa ruší „vždy, bez časovej podmienky“, kým chybový zoznam OP-04 hovoril opak (nález N-04). Kto implementuje podľa pravidiel — čo je správny postup — dostane opačné správanie. | BR-03, REQ-14 aj predpoklady OP-04 prepísané. |
| R-2 | Definícia blokujúcich stavov a BR-02 neobsahovali podmienku živosti, hoci REQ-03 áno. Podľa doslovného znenia BR-02 bolo možné invariant porušiť vypršanou žiadosťou. | Živosť je odteraz súčasťou definície aj invariantu; dôsledok (v databáze môže ležať vypršaná žiadosť prekrývajúca sa s potvrdenou rezerváciou) je pomenovaný. |
| R-3 | REQ-05 hovorila len o potvrdení a o návrate do `DRAFT`, hoci OP-05 sa na ňu odvolávala pri súbežných schváleniach. | REQ-05 rozšírená na potvrdenie aj schválenie. |
| R-4 | BR-08 sľubovalo, že `EXPIRED` sa zapíše „pri najbližšej operácii, ktorá záznam číta“ — OP-02 ho však výslovne nezapisuje a OP-03 o tom mlčal. | BR-08 rozlišuje **vyhodnotenie** (každá operácia) od **zápisu** (iba OP-04 a OP-05). |
| R-5 | Zdôvodnenie opakovanej kontroly pri schválení tvrdilo, že „certifikát mohol medzitým vypršať“. BR-04 porovnáva dva uložené údaje, takže plynutím času sa jeho výsledok zmeniť nemôže. | Zdôvodnenie opravené: chráni pred **zmenou záznamu** certifikátu, nie pred plynutím času. Kontrola zostáva. |
| R-6 | OP-05 kontrolovala stav pred oprávnením, kým OP-03 a OP-04 naopak. Neoprávnený používateľ sa z kódu chyby dozvedel stav cudzej rezervácie. | Poradie zjednotené: oprávnenie najprv. |

Okrem toho revízia odkryla dve veci o správaní systému:

**N-06 — neskoré schválenie vyrobí nezrušiteľnú rezerváciu.** Vedúci smie
schváliť žiadosť aj desať minút pred začiatkom; taká rezervácia sa už podľa
BR-03 nedá zrušiť. Nie je to ten istý prípad, ktorý obhájil N-01 (tam neskorý
prechod urobil sám žiadateľ). *Vyriešené:* prijaté a pomenované v OP-05 —
žiadateľ má po celý čas čakania možnosť žiadosť bez lehoty zrušiť.

**N-07 — stratený zápis nad tou istou rezerváciou.** Špecifikácia v OP-04
tvrdila, že „rezervácia opustí stav `DRAFT` najviac raz“, ale toto tvrdenie
nebolo požiadavkou a nikto ho neoveroval. Súbežné zrušenie a potvrdenie tej
istej rezervácie **obe uspejú**: používateľ dostane na zrušenie úspech a
rezervácia pritom blokuje prístroj. Je to iné okno než REQ-05 — medzi
kontrolou **zdrojového stavu** a zápisom — a týka sa aj zrušenia, kde REQ-05
nefiguruje.
*Vyriešené:* tvrdenie sa stalo požiadavkou **REQ-16** a dostalo spustiteľný
dôkaz (`tests/test_concurrency_req05.py`, xfail). Implementácia ju zatiaľ
nespĺňa — je to druhý architektonický driver pre C03.

---

## 10. Kontrola prijatia požiadaviek

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
| REQ-05 | áno — týka sa práve okamihu medzi kontrolou prekryvu a zápisom      | **áno, toto je jeho jediný obsah**                                      | dve súbežné potvrdenia aj dve súbežné schválenia → najviac jedna rezervácia v blokujúcom stave |
| REQ-16 | áno — okamih medzi kontrolou zdrojového stavu a zápisom             | **áno, toto je jeho jediný obsah**                                      | súbežné zrušenie a potvrdenie tej istej rezervácie → uspeje najviac jedno |
| REQ-06 | áno — hranica `now < starts_at`                                    | hraničný prípad: potvrdenie v okamihu `starts_at` závisí od jedného odčítania času | potvrdenie po začiatku zamietnuté            |
| REQ-07 | nie — pre `DRAFT` neplatí žiadna lehota (po N-03)                  | zriedkavo: súbeh s `Confirm` — riešené v OP-04, časť „Súbeh s potvrdením“ | zrušenie `DRAFT` pred aj po začiatku prejde          |
| REQ-08 | áno — hranica presne 60:00 min                                     | ako REQ-07                                                              | 60:00 zamietnuté, 60:01 prejde                        |
| REQ-09 | nie                                                                | nie — opakovanie nie je novou zmenou stavu                              | druhé zrušenie vráti úspech a stav zostáva `CANCELLED` |
| REQ-10 | nie — závisí od príznaku prístroja, nie od času                     | nie — výsledný stav nezávisí od iných rezervácií                        | prístroj s `requires_approval` → `PENDING_APPROVAL`, bez neho → `CONFIRMED` |
| REQ-11 | áno — živosť žiadosti; certifikát a stav prístroja sa čítajú **v čase rozhodnutia**, hoci samotná podmienka BR-04 od času nezávisí | **áno** — dve schválenia kolidujúcich žiadostí, rovnaké okno ako REQ-05 | schválenie po vypršaní certifikátu zamietnuté, žiadosť zostáva `PENDING_APPROVAL` |
| REQ-12 | áno — iba pre živú žiadosť                                          | nie — zamietnutie nikdy nevytvára alokáciu                              | zamietnutie → `REJECTED`, prístroj je `AVAILABLE` |
| REQ-13 | nie                                                                | nie — čítacia operácia                                                  | dôvod `PENDING_APPROVAL` vs. `CONFIRMED` v odpovedi OP-02 |
| REQ-14 | áno — pre `PENDING_APPROVAL` platí BR-08, nie lehota                | súbeh so schválením: rezervácia opustí `PENDING_APPROVAL` najviac raz   | zrušenie žiadosti prejde; zrušenie `REJECTED` skončí `INVALID_STATE` |
| REQ-15 | áno — hranica `currentTime == starts_at`                            | nie                                                                     | rozhodnutie v okamihu `starts_at` → `EXPIRED`; o sekundu skôr → prejde |

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

Pri v0.2 revízia zmenila toto:

- BR-08 dostalo prednosť pred BR-03 pri vypršanej žiadosti (nález N-04) a bolo
  doplnené o stanovisko k `DRAFT` (nález N-05).
- REQ-12 bola zúžená: zamietnutie **nevyžaduje** splnenie podmienok pre
  schválenie. Pôvodná formulácia ich vyžadovala pri oboch rozhodnutiach, čo by
  znamenalo, že žiadosť na prístroj v servise nemôže vedúci ani zamietnuť.
- Zamietli sme lehotu na rozhodnutie vedúceho („48 hodín“) — nemá zdroj
  a expirácia viazaná na `starts_at` rieši ten istý problém bez čísla.

---

## 11. Schválenie baseline

Špecifikácia je pripravená na schválenie tímom. Platí, že **schválená baseline
znamená, že obaja členovia tímu vedia každú požiadavku obhájiť** — nie že si
dokument prečítali.

**Baseline v0.1**

| Člen          | Rola pri baseline v0.1                                  | Stav                         |
| ------------- | -------------------------------------------------------- | ---------------------------- |
| Tomáš Hrubý   | návrh špecifikácie, diagramov a kontroly konzistencie     | ✅ 2026-09-21                 |
| Tomáš Krišica | review pred integráciou (PR `feature/c02-baseline`)       | ⏳ prebieha                   |

**Baseline v0.2 — schvaľovací proces**

| Člen          | Rola pri baseline v0.2                                                  | Stav          |
| ------------- | ------------------------------------------------------------------------ | ------------- |
| Tomáš Hrubý   | analýza dopadu, úprava špecifikácie a diagramov, implementácia zmeny      | ✅ 2026-09-23  |
| Tomáš Krišica | review pred integráciou (PR `feature/c02-approval`)                       | ⏳ prebieha    |

Rozhodnutia, ktoré musí tím vedieť obhájiť pri v0.2, sú vymenované
v [docs/change-impact-c02.md](change-impact-c02.md) (tabuľka R-1 až R-5).
Ďalšie zmeny sa povedú ako v0.3, opäť s analýzou dopadu pred prepisom.
