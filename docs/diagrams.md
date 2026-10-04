# Diagramy — Specification Baseline v0.2

Vizuálne pohľady na to isté správanie, ktoré popisuje
[docs/specification.md](specification.md). Diagramy **nedopĺňajú** nové pravidlá:
každá podmienka v nich má zodpovedajúce `BR-xx` alebo `REQ-xx` v texte. Ak sa
niekedy rozídu, rozpor sa rieši opravou oboch, nie prekreslením obrázka.

Prvky pridané zmenou v0.2 (schvaľovací proces) sú v texte pod diagramami
označené *(v0.2)*.

---

## 1. Diagram prípadov užitia — aktéri a ciele

Pohľad na celý minimálny systém: kto ho používa a za akým cieľom.

```mermaid
flowchart LR
    student(("Študent"))
    supervisor(("Vedúci<br/>laboratória"))
    notif(("Notification<br/>Service"))

    subgraph SYS["Systém SWI-LAB — baseline v0.2"]
        direction TB
        op1(["OP-01<br/>Vytvoriť rezerváciu"])
        op2(["OP-02<br/>Zistiť dostupnosť"])
        op3(["OP-03<br/>Potvrdiť rezerváciu"])
        op4(["OP-04<br/>Zrušiť rezerváciu"])
        op5(["OP-05<br/>Rozhodnúť o žiadosti<br/>(schváliť / zamietnuť)"])
    end

    student --- op1
    student --- op2
    student --- op3
    student --- op4

    supervisor --- op2
    supervisor --- op3
    supervisor --- op4
    supervisor --- op5

    op3 -.->|"oznámenie o potvrdení"| notif
    op4 -.->|"oznámenie o zrušení"| notif
    op5 -.->|"oznámenie o rozhodnutí"| notif

    classDef uc fill:#eef4ff,stroke:#4a6fa5,color:#123;
    classDef new fill:#e8f5e9,stroke:#2e7d32,color:#032;
    classDef actor fill:#fff,stroke:#333,color:#000;
    class op1,op2,op3,op4 uc;
    class op5 new;
    class student,supervisor,notif actor;
```

**Čo diagram hovorí a čo nie:**

- **Hranica systému** je obdĺžnik `SYS`. Vnútri sú iba ciele aktérov, nie
  databáza, triedy ani komponenty.
- **Študent** má všetky štyri ciele, ale podľa BR-06 vždy nad **vlastnou**
  rezerváciou.
- **Vedúci laboratória** má tie isté ciele nad **ľubovoľnou** rezerváciou
  (BR-06). Nevytvára rezervácie za iných — preto k `OP-01` nevedie čiara; ak by
  si rezervoval prístroj pre seba, vystupuje v role študenta.
- **OP-05 je jediný cieľ, ktorý patrí výhradne vedúcemu** *(v0.2)*. Študent
  k nemu čiaru nemá a mať nesmie (BR-07). Zmena **nepridala nového aktéra** —
  existujúcemu pribudol cieľ.
- **Notification Service** je podporný externý aktér (hranica z C01). Prerušovaná
  šípka znamená, že systém ho volá, nie že ho niekto používa. Hranica je
  **definovaná, nie implementovaná** — TBD-03. Od v0.2 na nej stojí použiteľnosť
  schvaľovania: bez oznámenia sa vedúci o žiadosti nedozvie (TBD-09).
- Žiadne `include` / `extend`. Operácie sú navzájom nezávislé ciele; `Confirm`
  síce vnútorne vyhodnocuje to isté pravidlo ako `Check Availability`, ale to je
  zdieľané **pravidlo BR-02**, nie zdieľaný prípad užitia.

---

## 2. Stavový diagram — životný cyklus Reservation

```mermaid
stateDiagram-v2
    [*] --> DRAFT : create / BR-01, BR-05, BR-06

    DRAFT --> CONFIRMED : confirm [prístroj NEvyžaduje schválenie ∧ podmienky REQ-04]
    DRAFT --> PENDING_APPROVAL : confirm [prístroj vyžaduje schválenie ∧ podmienky REQ-04]
    DRAFT --> CANCELLED : cancel / bez časovej podmienky

    PENDING_APPROVAL --> CONFIRMED : approve [supervisor ∧ podmienky stále platia]
    PENDING_APPROVAL --> REJECTED : reject [supervisor]
    PENDING_APPROVAL --> EXPIRED : expire [nastal starts_at]
    PENDING_APPROVAL --> CANCELLED : cancel [žiadosť ešte nevypršala]

    CONFIRMED --> CANCELLED : cancel [zostáva viac ako 60 min]
    CANCELLED --> CANCELLED : cancel / bez efektu (REQ-09)

    note right of DRAFT
        Zámer používateľa.
        Prístroj NEBLOKUJE.
        Prekryv ani certifikát
        sa pri create neriešia (REQ-02).
    end note

    note right of PENDING_APPROVAL
        v0.2. Žiadosť čaká na
        rozhodnutie vedúceho.
        Kým nevyprší, BLOKUJE
        prístroj ako CONFIRMED.
    end note

    note left of REJECTED
        v0.2. Rozhodnutie človeka.
        Koncový stav, neblokuje.
        Zrušiť sa nedá (REQ-14).
    end note

    note left of EXPIRED
        v0.2. Jediný prechod, ktorý
        nespúšťa človek - nastal
        starts_at žiadosti (BR-08).
        Zapíšu ho OP-04 a OP-05.
    end note

    note left of CANCELLED
        Koncový stav. NIE je to
        zmazanie záznamu - história
        zostáva, iba prestáva blokovať.
    end note
```

**Úplné podmienky prechodov** (skrátené v diagrame, úplné v špecifikácii):

| Prechod                          | Podmienka                                                                                                                                     | Zdroj                  |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `[*] → DRAFT`                    | používateľ existuje ∧ prístroj existuje ∧ `is_active` ∧ `ends_at > starts_at` ∧ `now < starts_at`                                              | REQ-01, BR-01/05/06    |
| `DRAFT → CONFIRMED`              | žiadateľ oprávnený ∧ `now < starts_at` ∧ `is_active` ∧ platný certifikát ∧ žiadny prekryv s blokujúcim stavom ∧ **prístroj nevyžaduje schválenie** | REQ-04, REQ-10, BR-02/04/05/06 |
| `DRAFT → PENDING_APPROVAL`       | tie isté podmienky ∧ **prístroj vyžaduje schválenie** *(v0.2)*                                                                                | REQ-10, BR-02/04/05/06 |
| `DRAFT → CANCELLED`              | žiadateľ oprávnený — bez časovej podmienky (nález N-03)                                                                                        | REQ-07, BR-03, BR-06   |
| `PENDING_APPROVAL → CONFIRMED`   | rozhoduje `SUPERVISOR` ≠ vlastník ∧ `now < starts_at` ∧ `is_active` ∧ certifikát vlastníka stále platí ∧ žiadny prekryv *(v0.2)*               | REQ-11, BR-02/04/05/07/08 |
| `PENDING_APPROVAL → REJECTED`    | rozhoduje `SUPERVISOR` ≠ vlastník ∧ `now < starts_at` *(v0.2)*                                                                                | REQ-12, BR-07, BR-08   |
| `PENDING_APPROVAL → EXPIRED`     | `now >= starts_at`; zapíše sa pri operácii, ktorá záznam číta *(v0.2)*                                                                         | REQ-15, BR-08          |
| `PENDING_APPROVAL → CANCELLED`   | žiadateľ oprávnený ∧ žiadosť ešte nevypršala (BR-08); 60-minútová lehota tu neplatí *(v0.2)*                                                   | REQ-14, BR-03, BR-06, BR-08 |
| `CONFIRMED → CANCELLED`          | žiadateľ oprávnený ∧ `now + 60 min < starts_at`                                                                                               | REQ-08, BR-03, BR-06   |
| `CANCELLED → CANCELLED`          | žiadateľ oprávnený — bez zmeny stavu, bez oznámenia                                                                                           | REQ-09                 |

**Čo v diagrame zámerne nie je:**

- **Zamietnuté pokusy nie sú prechody.** Potvrdenie bez certifikátu alebo
  zrušenie 30 minút pred začiatkom nie je prechod do iného stavu — rezervácia
  zostáva tam, kde bola. Preto v diagrame nie sú slučky pre chybové prípady.
- **`REJECTED` nie je zamietnutý pokus o prechod** *(v0.2)*. Rozhodnutie z C01
  („žiadny stav `REJECTED`“) stále platí pre neúspešné potvrdenie — tam
  rezervácia zostáva `DRAFT`. Stav `REJECTED` v0.2 znamená niečo iné:
  **rozhodnutie človeka**, ktoré má zostať v histórii viditeľné.
- **Z `REJECTED`, `EXPIRED` ani `CANCELLED` nevedie cesta späť.** Kto chce
  termín znova, vytvorí novú rezerváciu.
- **Jediný prechod bez človeka je `PENDING_APPROVAL → EXPIRED`** *(v0.2)*.
  `CONFIRMED` rezervácia po skončení intervalu zostáva `CONFIRMED`; stav
  `COMPLETED` neexistuje, pretože ho nič v špecifikácii nepotrebuje.
- Prechod `CANCELLED → CANCELLED` je v diagrame len preto, že ide
  o **pozorovateľný úspešný výsledok** (REQ-09), nie o chybu.

---

## 3. Diagramy aktivít

### 3.1 OP-01 — Create Reservation

```mermaid
flowchart TD
    A([Štart]) --> B["Prijmi instrument_id, user_id, interval"]
    B --> C{"Používateľ existuje?"}
    C -->|"nie"| X1["Zamietni: UNKNOWN_USER"]
    C -->|"áno"| D{"Prístroj existuje?"}
    D -->|"nie"| X2["Zamietni: UNKNOWN_INSTRUMENT"]
    D -->|"áno"| E{"Prístroj aktívny?<br/>BR-05"}
    E -->|"nie"| X3["Zamietni: INSTRUMENT_INACTIVE"]
    E -->|"áno"| F{"ends_at > starts_at?<br/>BR-01"}
    F -->|"nie"| X4["Zamietni: INVALID_INTERVAL"]
    F -->|"áno"| G{"now < starts_at?"}
    G -->|"nie"| X5["Zamietni: START_IN_PAST"]
    G -->|"áno"| H["Vytvor rezerváciu<br/>state = DRAFT"]
    H --> I["Vráť id + stav"]
    I --> Z([Koniec])
    X1 --> Z
    X2 --> Z
    X3 --> Z
    X4 --> Z
    X5 --> Z

    N["REQ-02: prekryv (BR-02) ani certifikát (BR-04)<br/>sa tu NEVYHODNOCUJÚ - DRAFT nealokuje prístroj"]
    H -.- N

    classDef rej fill:#ffecec,stroke:#c0392b,color:#300;
    classDef note fill:#fffbe6,stroke:#c9a227,color:#332;
    class X1,X2,X3,X4,X5 rej;
    class N note;
```

Pri každom zamietnutí platí: **nevznikne žiadna rezervácia** a žiadny iný záznam
sa nezmení.

### 3.2 OP-02 — Check Availability

```mermaid
flowchart TD
    A([Štart]) --> B["Prijmi instrument_id, interval"]
    B --> C{"Prístroj existuje?"}
    C -->|"nie"| X1["Zamietni: UNKNOWN_INSTRUMENT"]
    C -->|"áno"| D{"ends_at > starts_at?<br/>BR-01"}
    D -->|"nie"| X2["Zamietni: INVALID_INTERVAL"]
    D -->|"áno"| E{"Prístroj aktívny?<br/>BR-05"}
    E -->|"nie"| R1["UNAVAILABLE<br/>dôvod INSTRUMENT_INACTIVE"]
    E -->|"áno"| F["Nájdi prekrývajúce sa rezervácie<br/>v blokujúcom stave (BR-01, BR-02):<br/>CONFIRMED, alebo PENDING_APPROVAL<br/>so starts_at v budúcnosti (BR-08)"]
    F --> G{"Našla sa aspoň jedna?"}
    G -->|"nie"| R2["AVAILABLE"]
    G -->|"áno"| R3["UNAVAILABLE<br/>dôvod CONFIRMED alebo PENDING_APPROVAL<br/>+ zoznam kolidujúcich id"]
    R1 --> Z([Koniec])
    R2 --> Z
    R3 --> Z
    X1 --> Z
    X2 --> Z

    N["Operácia je čítacia: nemení žiadny stav.<br/>Vypršané žiadosti sa do kolízií nepočítajú,<br/>ale OP-02 im stav neprepisuje - zapisujú ho<br/>iba OP-04 a OP-05 (BR-08)."]
    F -.- N

    classDef rej fill:#ffecec,stroke:#c0392b,color:#300;
    classDef ok fill:#eaf7ea,stroke:#27ae60,color:#032;
    classDef note fill:#fffbe6,stroke:#c9a227,color:#332;
    class X1,X2 rej;
    class R1,R2,R3 ok;
    class N note;
```

### 3.3 OP-03 — Confirm Reservation

```mermaid
flowchart TD
    A([Štart]) --> B["Prijmi reservation_id + identitu žiadateľa"]
    B --> C{"Rezervácia existuje?"}
    C -->|"nie"| X1["Zamietni: NOT_FOUND"]
    C -->|"áno"| D{"Vlastník alebo SUPERVISOR?<br/>BR-06"}
    D -->|"nie"| X2["Zamietni: FORBIDDEN"]
    D -->|"áno"| E{"state = DRAFT?"}
    E -->|"nie"| X3["Zamietni: INVALID_STATE<br/>stav sa nemení"]
    E -->|"áno"| F{"now < starts_at?<br/>REQ-06"}
    F -->|"nie"| X4["Zamietni: ALREADY_STARTED"]
    F -->|"áno"| G{"Prístroj aktívny?<br/>BR-05"}
    G -->|"nie"| X5["Zamietni: INSTRUMENT_INACTIVE"]
    G -->|"áno"| H{"Certifikát VLASTNÍKA<br/>na kategóriu<br/>platný k starts_at?<br/>BR-04"}
    H -->|"nie"| X6["Zamietni: MISSING_CERTIFICATION"]
    H -->|"áno"| I{"Prekryv s inou rezerváciou<br/>v blokujúcom stave?<br/>BR-02, BR-08"}
    I -->|"áno"| X7["Zamietni: OVERLAP"]
    I -->|"nie"| P{"Prístroj vyžaduje schválenie?<br/>requires_approval, REQ-10"}
    P -->|"áno"| J2["state = PENDING_APPROVAL<br/>blokuje prístroj, čaká na vedúceho"]
    P -->|"nie"| J["state = CONFIRMED"]
    J --> K["Oznámenie do Notification Service<br/>TBD-03 - neimplementované"]
    J2 --> K
    K --> L["Vráť DOSIAHNUTÝ stav rezervácie<br/>REQ-10"]
    L --> Z([Koniec])
    X1 --> Z
    X2 --> Z
    X3 --> Z
    X4 --> Z
    X5 --> Z
    X6 --> Z
    X7 --> Z

    N["REQ-05: kontrola prekryvu a zápis stavu musia<br/>prebehnúť ako jeden nedeliteľný krok,<br/>inak môžu dve súbežné potvrdenia prejsť obe.<br/>AKO sa to zabezpečí = architektúra, C03"]
    I -.- N
    J -.- N

    classDef rej fill:#ffecec,stroke:#c0392b,color:#300;
    classDef note fill:#fffbe6,stroke:#c9a227,color:#332;
    classDef todo fill:#f0f0f0,stroke:#888,color:#333,stroke-dasharray: 4 3;
    classDef new fill:#e8f5e9,stroke:#2e7d32,color:#032;
    class X1,X2,X3,X4,X5,X6,X7 rej;
    class N note;
    class K todo;
    class P,J2 new;
```

Pri každom zamietnutí zostáva rezervácia v **pôvodnom** stave — `DRAFT` ostáva
`DRAFT`, už potvrdená ostáva `CONFIRMED`, zrušená ostáva `CANCELLED`.

### 3.4 OP-04 — Cancel Reservation

```mermaid
flowchart TD
    A([Štart]) --> B["Prijmi reservation_id + identitu žiadateľa"]
    B --> C{"Rezervácia existuje?"}
    C -->|"nie"| X1["Zamietni: NOT_FOUND"]
    C -->|"áno"| D{"Vlastník alebo SUPERVISOR?<br/>BR-06"}
    D -->|"nie"| X2["Zamietni: FORBIDDEN"]
    D -->|"áno"| E["Odčítaj now JEDENKRÁT<br/>BR-03, zdroj času"]
    E --> F{"Aktuálny stav?"}
    F -->|"CANCELLED"| R1["Úspech bez zmeny stavu<br/>bez oznámenia - REQ-09"]
    F -->|"REJECTED alebo EXPIRED"| X5["Zamietni: INVALID_STATE<br/>koncový stav - REQ-14"]
    F -->|"DRAFT"| G["Bez časovej podmienky<br/>BR-03, nález N-03"]
    F -->|"PENDING_APPROVAL"| Q{"now < starts_at?<br/>BR-08"}
    F -->|"CONFIRMED"| H{"now + 60 min < starts_at?"}
    Q -->|"nie"| X6["state = EXPIRED<br/>a zamietni - nález N-04"]
    Q -->|"áno"| J["state = CANCELLED"]
    G --> J
    H -->|"nie"| X4["Zamietni: TOO_LATE<br/>zostáva CONFIRMED"]
    H -->|"áno"| J
    J --> K["Oznámenie do Notification Service<br/>TBD-03 - neimplementované"]
    K --> L["Vráť aktuálny stav rezervácie"]
    L --> Z([Koniec])
    R1 --> Z
    X1 --> Z
    X2 --> Z
    X4 --> Z
    X5 --> Z
    X6 --> Z

    N["Prístroj sa uvoľní až tu:<br/>po prechode do CANCELLED vracia<br/>OP-02 pre ten interval AVAILABLE"]
    J -.- N

    classDef rej fill:#ffecec,stroke:#c0392b,color:#300;
    classDef ok fill:#eaf7ea,stroke:#27ae60,color:#032;
    classDef note fill:#fffbe6,stroke:#c9a227,color:#332;
    classDef todo fill:#f0f0f0,stroke:#888,color:#333,stroke-dasharray: 4 3;
    classDef new fill:#e8f5e9,stroke:#2e7d32,color:#032;
    class X1,X2,X4,X5,X6 rej;
    class R1 ok;
    class N note;
    class K todo;
    class Q new;
```

Vetva `CONFIRMED` je prísnejšia než vetva `DRAFT` — to je BR-03 a jeho
zdôvodnenie (potvrdená rezervácia blokuje prístroj ostatným, návrh nie).

Vetva `PENDING_APPROVAL` *(v0.2)* nemá lehotu, ale má kontrolu expirácie: keď
žiadosti už začal termín, zrušiť sa nedá a systém namiesto toho zapíše
`EXPIRED` (nález N-04). Je to jediné miesto v OP-04, kde operácia zmení stav
**a napriek tomu skončí zamietnutím**.

### 3.5 OP-05 — Approve Reservation *(v0.2)*

```mermaid
flowchart TD
    A([Štart]) --> B["Prijmi reservation_id, identitu, rozhodnutie<br/>(schváliť / zamietnuť)"]
    B --> C{"Rezervácia existuje?"}
    C -->|"nie"| X1["Zamietni: NOT_FOUND"]
    C -->|"áno"| E{"Rozhoduje SUPERVISOR,<br/>ktorý NIE je vlastník?<br/>BR-07"}
    E -->|"nie"| X3["Zamietni: FORBIDDEN"]
    E -->|"áno"| D{"state = PENDING_APPROVAL?"}
    D -->|"nie"| X2["Zamietni: INVALID_STATE<br/>stav sa nemení"]
    D -->|"áno"| F{"now < starts_at?<br/>BR-08"}
    F -->|"nie"| X4["state = EXPIRED<br/>a zamietni - REQ-15"]
    F -->|"áno"| G{"Rozhodnutie?"}
    G -->|"zamietnuť"| R2["state = REJECTED<br/>prístroj sa uvoľní - REQ-12"]
    G -->|"schváliť"| H{"Prístroj aktívny?<br/>BR-05"}
    H -->|"nie"| X5["Zamietni: INSTRUMENT_INACTIVE<br/>zostáva PENDING_APPROVAL"]
    H -->|"áno"| I{"Certifikát VLASTNÍKA<br/>stále platný?<br/>BR-04"}
    I -->|"nie"| X6["Zamietni: MISSING_CERTIFICATION<br/>zostáva PENDING_APPROVAL"]
    I -->|"áno"| J{"Prekryv s inou rezerváciou<br/>v blokujúcom stave?<br/>BR-02"}
    J -->|"áno"| X7["Zamietni: OVERLAP<br/>zostáva PENDING_APPROVAL"]
    J -->|"nie"| R1["state = CONFIRMED - REQ-11"]
    R1 --> K["Oznámenie žiadateľovi<br/>TBD-03 - neimplementované"]
    R2 --> K
    K --> L["Vráť aktuálny stav rezervácie"]
    L --> Z([Koniec])
    X1 --> Z
    X2 --> Z
    X3 --> Z
    X4 --> Z
    X5 --> Z
    X6 --> Z
    X7 --> Z

    N["Kontroly BR-04, BR-05 a BR-02 sa opakujú,<br/>hoci prebehli už pri podaní žiadosti (OP-03).<br/>Medzitým ubehol čas: certifikát mohol vypršať,<br/>prístroj ísť do servisu, termín obsadiť niekto iný."]
    I -.- N
    J -.- N

    classDef rej fill:#ffecec,stroke:#c0392b,color:#300;
    classDef ok fill:#eaf7ea,stroke:#27ae60,color:#032;
    classDef note fill:#fffbe6,stroke:#c9a227,color:#332;
    classDef todo fill:#f0f0f0,stroke:#888,color:#333,stroke-dasharray: 4 3;
    class X1,X2,X3,X4,X5,X6,X7 rej;
    class R1,R2 ok;
    class N note;
    class K todo;
```

Tri veci, ktoré diagram hovorí a stojí za to si ich všimnúť:

1. **Zamietnutie obchádza kontroly** (REQ-12). Vedúci musí vedieť zamietnuť aj
   žiadosť na prístroj, ktorý je medzitým v servise — inak by taká žiadosť
   visela až do vypršania.
2. **Certifikát sa overuje vlastníkovi rezervácie**, nie schvaľovateľovi.
   Vedúci rozhoduje, ale prístroj bude obsluhovať žiadateľ.
3. **Neúspešné schválenie nechá žiadosť žiť.** Prekážka môže zmiznúť
   a rozhodnutie sa dá zopakovať; jediný spôsob, ako žiadosť ukončiť, je
   explicitné zamietnutie, zrušenie žiadateľom alebo vypršanie.
