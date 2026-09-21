# Diagramy — Specification Baseline v0.1

Vizuálne pohľady na to isté správanie, ktoré popisuje
[docs/specification.md](specification.md). Diagramy **nedopĺňajú** nové pravidlá:
každá podmienka v nich má zodpovedajúce `BR-xx` alebo `REQ-xx` v texte. Ak sa
niekedy rozídu, rozpor sa rieši opravou oboch, nie prekreslením obrázka.

---

## 1. Diagram prípadov užitia — aktéri a ciele

Pohľad na celý minimálny systém: kto ho používa a za akým cieľom.

```mermaid
flowchart LR
    student(("Študent"))
    supervisor(("Vedúci<br/>laboratória"))
    notif(("Notification<br/>Service"))

    subgraph SYS["Systém SWI-LAB — baseline v0.1"]
        direction TB
        op1(["OP-01<br/>Vytvoriť rezerváciu"])
        op2(["OP-02<br/>Zistiť dostupnosť"])
        op3(["OP-03<br/>Potvrdiť rezerváciu"])
        op4(["OP-04<br/>Zrušiť rezerváciu"])
    end

    student --- op1
    student --- op2
    student --- op3
    student --- op4

    supervisor --- op2
    supervisor --- op3
    supervisor --- op4

    op3 -.->|"oznámenie o potvrdení"| notif
    op4 -.->|"oznámenie o zrušení"| notif

    classDef uc fill:#eef4ff,stroke:#4a6fa5,color:#123;
    classDef actor fill:#fff,stroke:#333,color:#000;
    class op1,op2,op3,op4 uc;
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
- **Notification Service** je podporný externý aktér (hranica z C01). Prerušovaná
  šípka znamená, že systém ho volá, nie že ho niekto používa. V v0.1 je hranica
  **definovaná, nie implementovaná** — TBD-03.
- Žiadne `include` / `extend`. Operácie sú navzájom nezávislé ciele; `Confirm`
  síce vnútorne vyhodnocuje to isté pravidlo ako `Check Availability`, ale to je
  zdieľané **pravidlo BR-02**, nie zdieľaný prípad užitia.

---

## 2. Stavový diagram — životný cyklus Reservation

```mermaid
stateDiagram-v2
    direction LR
    [*] --> DRAFT : create / BR-01, BR-05, BR-06

    DRAFT --> CONFIRMED : confirm [aktívny prístroj ∧ platný certifikát ∧ bez prekryvu ∧ pred začiatkom]
    DRAFT --> CANCELLED : cancel / bez časovej podmienky
    CONFIRMED --> CANCELLED : cancel [zostáva viac ako 60 min]
    CANCELLED --> CANCELLED : cancel / bez efektu (REQ-09)

    note right of DRAFT
        Zámer používateľa.
        Prístroj NEALOKUJE.
        Prekryv ani certifikát
        sa pri create neriešia (REQ-02).
    end note

    note right of CONFIRMED
        Jediný stav, ktorý alokuje
        prístroj pre svoj interval.
        Platí BR-02 (bez prekryvu).
    end note

    note left of CANCELLED
        Koncový stav. NIE je to
        zmazanie záznamu - história
        zostáva, iba prestáva blokovať.
    end note
```

**Úplné podmienky prechodov** (skrátené v diagrame, úplné v špecifikácii):

| Prechod                 | Podmienka                                                                                                                     | Zdroj                |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------- |
| `[*] → DRAFT`           | používateľ existuje ∧ prístroj existuje ∧ `is_active` ∧ `ends_at > starts_at` ∧ `now < starts_at`                              | REQ-01, BR-01/05/06  |
| `DRAFT → CONFIRMED`     | žiadateľ oprávnený ∧ `now < starts_at` ∧ prístroj `is_active` ∧ platný certifikát na kategóriu ∧ žiadny prekryv s `CONFIRMED`  | REQ-04, BR-02/04/05/06 |
| `DRAFT → CANCELLED`     | žiadateľ oprávnený — bez časovej podmienky (nález N-03)                                                                        | REQ-07, BR-03, BR-06 |
| `CONFIRMED → CANCELLED` | žiadateľ oprávnený ∧ `now + 60 min < starts_at`                                                                                | REQ-08, BR-03, BR-06 |
| `CANCELLED → CANCELLED` | žiadateľ oprávnený — bez zmeny stavu, bez oznámenia                                                                            | REQ-09               |

**Čo v diagrame zámerne nie je:**

- **Zamietnuté pokusy nie sú prechody.** Potvrdenie bez certifikátu alebo
  zrušenie 30 minút pred začiatkom nie je prechod do iného stavu — rezervácia
  zostáva tam, kde bola. Preto v diagrame nie sú slučky pre chybové prípady.
- **Stav `REJECTED` neexistuje** (rozhodnutie z C01): zamietnutie je výsledok
  pokusu o prechod, nie stav rezervácie.
- **Žiadny automatický prechod časom.** `CONFIRMED` rezervácia po skončení
  intervalu zostáva `CONFIRMED`; v0.1 nepozná stav `COMPLETED` ani plánovač,
  ktorý by ho nastavoval. Nie je to opomenutie — nič v špecifikácii také
  správanie nepotrebuje.
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
    E -->|"áno"| F["Nájdi rezervácie prístroja<br/>v stave CONFIRMED,<br/>ktoré sa prekrývajú (BR-01)"]
    F --> G{"Našla sa aspoň jedna?"}
    G -->|"nie"| R2["AVAILABLE"]
    G -->|"áno"| R3["UNAVAILABLE<br/>+ zoznam kolidujúcich id"]
    R1 --> Z([Koniec])
    R2 --> Z
    R3 --> Z
    X1 --> Z
    X2 --> Z

    N["Operácia je čítacia:<br/>nemení žiadny stav"]
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
    F -->|"nie"| X4["Zamietni: START_IN_PAST"]
    F -->|"áno"| G{"Prístroj aktívny?<br/>BR-05"}
    G -->|"nie"| X5["Zamietni: INSTRUMENT_INACTIVE"]
    G -->|"áno"| H{"Certifikát na kategóriu<br/>platný k starts_at?<br/>BR-04"}
    H -->|"nie"| X6["Zamietni: MISSING_CERTIFICATION"]
    H -->|"áno"| I{"Prekryv s inou<br/>CONFIRMED rezerváciou?<br/>BR-02"}
    I -->|"áno"| X7["Zamietni: OVERLAP"]
    I -->|"nie"| J["state = CONFIRMED"]
    J --> K["Oznámenie do Notification Service<br/>TBD-03 - v0.1 neimplementované"]
    K --> L["Vráť aktuálny stav rezervácie"]
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
    class X1,X2,X3,X4,X5,X6,X7 rej;
    class N note;
    class K todo;
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
    F -->|"DRAFT"| G["Bez časovej podmienky<br/>BR-03, nález N-03"]
    F -->|"CONFIRMED"| H{"now + 60 min < starts_at?"}
    G --> J["state = CANCELLED"]
    H -->|"nie"| X4["Zamietni: TOO_LATE<br/>zostáva CONFIRMED"]
    H -->|"áno"| J
    J --> K["Oznámenie do Notification Service<br/>TBD-03 - v0.1 neimplementované"]
    K --> L["Vráť aktuálny stav rezervácie"]
    L --> Z([Koniec])
    R1 --> Z
    X1 --> Z
    X2 --> Z
    X4 --> Z

    N["Prístroj sa uvoľní až tu:<br/>po prechode do CANCELLED vracia<br/>OP-02 pre ten interval AVAILABLE"]
    J -.- N

    classDef rej fill:#ffecec,stroke:#c0392b,color:#300;
    classDef ok fill:#eaf7ea,stroke:#27ae60,color:#032;
    classDef note fill:#fffbe6,stroke:#c9a227,color:#332;
    classDef todo fill:#f0f0f0,stroke:#888,color:#333,stroke-dasharray: 4 3;
    class X1,X2,X4 rej;
    class R1 ok;
    class N note;
    class K todo;
```

Vetva `CONFIRMED` je prísnejšia než vetva `DRAFT` — to je BR-03 a jeho
zdôvodnenie (potvrdená rezervácia blokuje prístroj ostatným, návrh nie).
