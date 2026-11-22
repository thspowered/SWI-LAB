"""Logovanie aplikacie (C10).

Aplikacia zapisuje iba to, co sa z odpovede ani z databazy nedozvieme:
technicku poruchu (cas, operacia, typ problemu, vysledok) a start
a zastavenie (cas, verzia, cielova databaza). Uspech ani domenove
zamietnutie nezapisuje - dokladom je odpoved a stav v databaze
(rozhodnutie C10 v docs/evidence-and-evolution.md).

Cas je v UTC s milisekundami, ako vsetky casy v systeme (F-01), a za nim
cislo procesu - pri viacerych workeroch by sa zaznamy inak nedali rozlisit.
Bez hesiel: cielova databaza je iba host:port/meno. Parametre SQL skryva
engine (hide_parameters v db.py). Hodnotu, ktoru databaza sama vypise
v sprave (napr. DETAIL pri porusenej unikatnosti), vsak neskryje - dnes ju
ziadna operacia API nad osobnymi udajmi nevyvola.

Vlastna konfiguracia (uvicorn --log-config) ma definovat aj logger
'swilab' (s vlastnym handlerom a propagate: false) - configure() potom
ustupi. Inak sa pri root handleri zaznamy zapisu dvakrat.
"""

import logging
import time

LOGGER = "swilab"
FORMAT = "%(asctime)s.%(msecs)03dZ [%(process)d] %(levelname)s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def configure() -> None:
    """Handler na stderr pre logger 'swilab' - nezavisle od toho, ako si
    nastavi svoje loggery uvicorn (jeho predvoleny format cas nema, L-1)."""
    logger = logging.getLogger(LOGGER)
    if logger.handlers:
        return
    formatter = logging.Formatter(FORMAT, DATE_FORMAT)
    formatter.converter = time.gmtime
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
