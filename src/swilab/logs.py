"""Logovanie aplikacie (C10).

Aplikacia zapisuje iba to, co sa z odpovede ani z databazy nedozvieme:
technicku poruchu (cas, operacia, typ problemu, vysledok) a start
a zastavenie (cas, verzia, cielova databaza). Uspech ani domenove
zamietnutie nezapisuje - dokladom je odpoved a stav v databaze
(rozhodnutie C10 v docs/evidence-and-evolution.md).

Cas je v UTC s milisekundami, ako vsetky casy v systeme (F-01). Bez hesiel
a osobnych udajov: parametre SQL skryva engine (hide_parameters v db.py),
cielova databaza je iba host:port/meno.
"""

import logging
import time

LOGGER = "swilab"
FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s"
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
