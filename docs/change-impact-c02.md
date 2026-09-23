# Dopad zmeny C02 — schvaľovací proces

Analýza dopadu **pred** úpravou špecifikácie. V tomto dokumente sa nič
neprepisuje; rozhoduje sa, čo sa prepísať musí a čo výslovne nie.

Východiskom je [Specification Baseline v0.1](specification.md).

---

## Zmenená podmienka

> Niektoré prístroje vyžadujú schválenie oprávnenou osobou skôr, než sa
> rezervácia môže stať `CONFIRMED`. Schválenie môže byť oneskorené, zamietnuté
> alebo môže vypršať.

### Rozhodnutia tímu k zmene

| # | Otázka | Rozhodnutie | Zdôvodnenie |
| - | ------ | ----------- | ----------- |
| R-1 | Ktoré prístroje vyžadujú schválenie? | Príznak `Instrument.requires_approval` | Zmena hovorí „niektoré Resources“. Vedúci ho zapne na konkrétnom drahom prístroji; ostatné idú pôvodným tokom. |
| R-2 | Blokuje `PENDING_APPROVAL` prístroj? | **Áno** | Vedúci nikdy nedostane na stôl dve žiadosti na ten istý čas a študent nedostane sľub, ktorý sa nedá splniť. Cena: zabudnutá žiadosť drží prístroj — preto expirácia. |
| R-3 | Kedy žiadosť vyprší? | Pri `starts_at` rezervácie | Hranica je odvodená z dát, ktoré už máme. Žiadne vymyslené číslo (napr. „48 h“), ktoré by nemalo zdroj. |
| R-4 | Čo so zamietnutou žiadosťou? | Koncový stav `REJECTED` | Rozhodnutie človeka je záznam, ktorý má zostať viditeľný. Neodporuje rozhodnutiu z C01 („žiadny REJECTED“) — tam išlo o neúspešný pokus o prechod, tu o rozhodnutie vedúceho. |
| R-5 | Dá sa zrušiť `PENDING_APPROVAL`? | **Áno, bez lehoty** | Žiadosť ešte nie je prísľub. Nútiť žiadateľa držať žiadosť, ktorú mu vedúci môže kedykoľvek schváliť, je horšie než neskoré uvoľnenie prístroja — a práve neskoré uvoľnenie je jediné, čo lehota v BR-03 rieši. |

R-5 sme neodvodili zo symetrie „blokujúci stav ⇒ lehota“, hoci by to bolo
lákavé. BR-03 nechráni prístroj pred uvoľnením, ale chráni **záväzok** — a pri
žiadosti čakajúcej na schválenie žiadny záväzok voči používateľovi ešte
nevznikol.

---

## Oblasti dopadu

### Create — mení sa?

**Nie.** `Create` naďalej vytvára `DRAFT` a nevyhodnocuje prekryv ani
certifikát (REQ-02). Príznak `requires_approval` sa uplatní až pri potvrdení.

Dôsledok, ktorý treba napísať explicitne: v okamihu vytvorenia sa ešte
nerozhoduje, ktorou cestou rezervácia pôjde. Keby sa `requires_approval`
vyhodnocoval už tu, zmena príznaku medzi vytvorením a potvrdením by viedla
k dvom rôznym odpovediam podľa toho, kedy sa používateľ opýtal.

### Availability — blokuje `PENDING_APPROVAL`?

**Áno, mení sa.** Blokujúca množina stavov sa rozširuje z `{CONFIRMED}` na
`{CONFIRMED, PENDING_APPROVAL}`. Dôsledky:

- BR-02 sa musí preformulovať — invariant už nie je len o `CONFIRMED`,
- OP-02 musí vedieť odlíšiť dôvod: `CONFIRMED` (obsadené) vs. `PENDING_APPROVAL`
  (čaká sa na rozhodnutie). Bez toho študent nevie, či má zmysel čakať.
- Žiadosť, ktorej `starts_at` už nastal, **neblokuje** (R-3) — do vyhodnotenia
  dostupnosti vstupuje čas, čo v v0.1 neplatilo. Je to skutočná zmena povahy
  operácie, nie kozmetika: OP-02 prestáva byť čisto dátový dopyt.

### Confirm — zostáva okamžitou operáciou?

**Mení sa, a to podstatne.** `Confirm` prestáva mať jeden možný výsledok:

- prístroj **bez** `requires_approval`: `DRAFT → CONFIRMED` (ako v v0.1),
- prístroj **s** `requires_approval`: `DRAFT → PENDING_APPROVAL`.

Používateľ vykonáva tú istú operáciu a dostane rôzny stav — to musí byť
v odpovedi viditeľné, inak si bude myslieť, že prístroj má istý.

Druhá zmena: kontroly (certifikát, aktívnosť prístroja, prekryv) sa vykonávajú
**dvakrát** — pri podaní žiadosti a znova pri schválení. Medzi nimi ubehne čas
a stav sa môže zmeniť: certifikát vyprší, prístroj ide do servisu. Bez
opakovanej kontroly by schválenie potvrdilo rezerváciu, ktorá už pravidlá
nespĺňa.

### Approve — vzniká nová operácia?

**Áno.** Nová operácia OP-05 s dvoma pozorovateľnými výsledkami:
schválenie (`PENDING_APPROVAL → CONFIRMED`) a zamietnutie
(`PENDING_APPROVAL → REJECTED`).

Modelujeme ich ako **jednu operáciu s dvoma výsledkami**, nie ako dve operácie:
je to jeden cieľ aktéra („rozhodnúť o žiadosti“) a rovnaké predpoklady;
líši sa len rozhodnutie. Cancel je oproti Confirm samostatná operácia práve
preto, že je to iný cieľ iného človeka.

Kto ju smie vykonať: **iba `SUPERVISOR`**, a **nie nad vlastnou žiadosťou**.
Vedúci laboratória si môže prístroj rezervovať ako ktokoľvek iný, ale rozhodnúť
o svojej vlastnej žiadosti nesmie — inak by schvaľovanie pre neho neexistovalo
a pravidlo by platilo len pre študentov. Nové pravidlo BR-07.

### Cancel — dá sa zrušiť `PENDING_APPROVAL`?

**Áno, mení sa** (R-5). BR-03 dostane tretí riadok:

| Stav | Zrušenie povolené |
| ---- | ----------------- |
| `DRAFT` | vždy |
| `PENDING_APPROVAL` | vždy (nové) |
| `CONFIRMED` | ak zostáva > 60 min |
| `CANCELLED` | idempotentný úspech |
| `REJECTED`, `EXPIRED` | **nie** — koncové stavy, zamietnuté ako `INVALID_STATE` (nové) |

Idempotencia (REQ-09) sa **nerozširuje** na `REJECTED` ani `EXPIRED`: to nie sú
výsledky zrušenia, ale iné koncové stavy. Tiché „úspešné“ zrušenie by zakrylo,
že rezerváciu v skutočnosti zamietol človek.

### Stavový diagram — aké stavy pribudnú?

Tri: `PENDING_APPROVAL`, `REJECTED`, `EXPIRED`. Nové prechody:

```
DRAFT              --confirm [requires_approval]--> PENDING_APPROVAL
PENDING_APPROVAL   --approve [podmienky stále platia]--> CONFIRMED
PENDING_APPROVAL   --reject--> REJECTED
PENDING_APPROVAL   --expire [nastal starts_at]--> EXPIRED
PENDING_APPROVAL   --cancel--> CANCELLED
```

`EXPIRED` je jediný prechod v celom systéme, ktorý **nespúšťa človek**.
Špecifikácia ho musí popísať ako pozorovateľný stav, nie ako úlohu plánovača:
žiadosť, ktorej `starts_at` nastal, sa považuje za vypršanú a neblokuje
prístroj; systém stav materializuje pri najbližšej operácii, ktorá záznam
číta. Dávkové dočisťovanie je otázka architektúry (C03), nie správania.

### Diagram prípadov užitia — nový aktér?

**Nový aktér nevzniká.** Vedúci laboratória už aktérom je; pribúda mu **nový
cieľ** „rozhodnúť o žiadosti o rezerváciu“. Študent nezískava žiadny nový cieľ.

Notification Service dostane dva nové dôvody na oznámenie (schválené,
zamietnuté) — zostáva TBD-03.

### Overenie — ako sa overí oneskorenie, zamietnutie a vypršanie?

| Jav | Ako sa overí |
| --- | ------------ |
| oneskorenie | rezervácia zostáva v `PENDING_APPROVAL` ľubovoľne dlho a celý ten čas blokuje prístroj (OP-02 vracia `UNAVAILABLE` s dôvodom `PENDING_APPROVAL`) |
| zamietnutie | `PENDING_APPROVAL → REJECTED`; prístroj je v tom intervale znova `AVAILABLE`; ďalší pokus o zrušenie je `INVALID_STATE` |
| vypršanie | žiadosť so `starts_at` v minulosti neblokuje; pokus o jej schválenie skončí `EXPIRED`, nie `CONFIRMED` |
| dvojitá kontrola | certifikát platný pri podaní a expirovaný pri schválení → schválenie zamietnuté |

Vypršanie sa dá overiť bez čakania, pretože zdrojom času je `clock.now()`
odovzdávaný do služieb — test posunie `starts_at` do minulosti namiesto toho,
aby čakal.

### Architektúra — nové drivery pre C03?

1. **Perzistentný asynchrónny proces.** Rezervácia teraz žije v stave, ktorý
   nikto neuzavrie automaticky. Systém potrebuje mechanizmus, ktorý stavy
   dočistí (plánovač, dávka alebo lazy vyhodnotenie) — a rozhodnutie, ktorý
   z nich, patrí do C03.
2. **Čas ako vstup pravidla, nie len podmienka zrušenia.** Expirácia zavádza
   čas do vyhodnotenia dostupnosti. Zdroj času prestáva byť detail a stáva sa
   závislosťou, ktorú treba vedieť riadiť v testoch aj v behu.
3. **REQ-05 sa rozširuje.** Do blokujúcej množiny pribudol druhý stav, takže
   súbežne môžu kolidovať aj dve žiadosti alebo žiadosť so schválením. Okno
   medzi kontrolou a zápisom je teraz na dvoch miestach (`confirm` aj
   `approve`), nie na jednom.
4. **Notifikácie prestávajú byť ozdoba.** Pri okamžitom potvrdení sa
   používateľ výsledok dozvedel z odpovede. Pri schvaľovaní sa ho inak
   nedozvie vôbec — TBD-03 sa mení z „pekné mať“ na chýbajúcu časť toku.

---

## Čo sa výslovne NEMENÍ

| Časť | Prečo zostáva |
| ---- | ------------- |
| OP-01 Create (REQ-01, REQ-02) | Zámer sa zaznamenáva rovnako; rozhodnutie o ceste padá až pri potvrdení. |
| BR-01 Význam intervalu | Zmena sa netýka intervalov ani prekryvu; polootvorená semantika platí pre nové stavy rovnako. |
| BR-04 Certifikácia | Pravidlo je nezmenené. Mení sa iba to, **koľkokrát** sa vyhodnocuje (pri podaní aj pri schválení). |
| BR-05 Neaktívny prístroj | Neaktívny prístroj neprijme žiadosť ani schválenie — rovnaké pravidlo, o jeden stav širšie. |
| BR-06 Oprávnenie (vlastník / supervisor) | Zostáva. Schvaľovanie pridáva **nové** pravidlo BR-07, staré neruší. |
| REQ-09 Idempotencia zrušenia | Zostáva presne v pôvodnom rozsahu — `CANCELLED`, nie nové koncové stavy. |
| BR-03 pre `CONFIRMED` (60 min) | Lehota ani jej zdôvodnenie sa nemenia. Pribúda riadok pre `PENDING_APPROVAL`. |
| Nálezy N-01, N-02, N-03 | Ich riešenia platia ďalej; zmena sa ich nedotýka. |
| Zdroj času (BR-03) | Rovnaký mechanizmus, širšie použitie. |

---

## Zhrnutie rozsahu prepisu

**Zasiahnuté:** BR-02, BR-03, nové BR-07, REQ-03 (dostupnosť), REQ-04
(potvrdenie), nové REQ pre OP-05, OP-02, OP-03, OP-04, nová OP-05, stavový
diagram, diagram prípadov užitia, diagram aktivít OP-03 a OP-04, nový diagram
aktivít OP-05.

**Nezasiahnuté:** OP-01, BR-01, BR-04, BR-05, BR-06, REQ-01, REQ-02, REQ-09.

Odhad: zmena sa dotkne zhruba polovice špecifikácie. To nie je známka zlého
návrhu v0.1 — schvaľovanie mení samotný okamih, v ktorom vzniká alokácia
prístroja, a to je pojem, na ktorom stojí celý model.
