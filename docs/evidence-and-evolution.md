# C01 Engineering Spike

**Varianta:** A — Persistence
**Vykonal:** <<DOPLNIT meno>>
**Dátum:** <<DOPLNIT>>

## Question / unknown
Prejde rezervácia cez reálny PostgreSQL tam a späť s neporušenými časmi?
Konkrétne: prežije timezone a mikrosekundová presnosť zápis a opätovné načítanie
v novej session — alebo sa niekde ticho oreže?

Nie je to otázka „funguje SQLAlchemy". Je to otázka, či môžeme stavať pravidlo
o prekryve intervalov na časoch, ktoré nám databáza vráti.

## What we did
Uložili sme rezerváciu s timezone-aware časmi a zámerne nekrúhlym počtom
mikrosekúnd (`123456`, resp. `654321`), commitli, načítali ju v **novej** session
(aby sme nedostali objekt z identity map pôvodnej session) a porovnali hodnoty.

```
docker compose up -d db
pip install -e ".[dev]"
pytest tests/test_persistence_spike.py -v
```

## Observed result
<<DOPLNIT — sem vlož SKUTOČNÝ výstup pytestu, nie „prešlo to".
Ak test spadol, napíš ako spadol a čo to znamená. Spadnutý spike je
plnohodnotný spike, len keď je z neho urobené rozhodnutie.>>

```
<<sem vlož výstup>>
```

## Decision / what changes because of the result
<<DOPLNIT na základe výsledku. Napríklad:
- ak časy prežili presne → potvrdzujeme timestamptz ako typ pre starts_at/ends_at
  a pravidlo o prekryve môže porovnávať časy priamo, bez zaokrúhľovania;
- ak sa presnosť orezala → musíme si zvoliť granularitu slotu (napr. celé minúty)
  a zaokrúhľovať na vstupe, nie až pri porovnaní.>>
