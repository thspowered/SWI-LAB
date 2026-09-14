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
