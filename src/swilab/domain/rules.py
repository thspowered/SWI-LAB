"""Domenove pravidla zo specifikacie baseline v0.4 (BR-01, BR-03, BR-04, BR-08).

Funkcie su ciste: nepoznaju HTTP ani databazu, pracuju iba s hodnotami.
Kazda nesie v docstringu pravidlo, ktore vynucuje, aby sa dalo prejst od
kodu k docs/specification.md a spat.
"""

from datetime import datetime, timedelta

#: BR-03 - prevadzkova lehota laboratoria na zrusenie potvrdenej rezervacie.
#: Rozhodnutie timu, nie odvodena velicina.
CANCELLATION_LEAD_TIME = timedelta(minutes=60)

#: BR-08 (v0.4) - lehota na rozhodnutie o ziadosti od jej podania.
#: Predpoklad timu za rolu veduceho laboratoria (CR-08, CA-1), nie odvodena
#: velicina - rovnako ako CANCELLATION_LEAD_TIME. Pevna hodnota systemu,
#: nie nastavenie prostredia (CA-5).
DECISION_PERIOD = timedelta(hours=48)


def is_interval_valid(starts_at: datetime, ends_at: datetime) -> bool:
    """BR-01: interval je [starts_at, ends_at), takze ends_at > starts_at.

    starts_at == ends_at je neplatny interval, nie prazdna rezervacia.
    """
    return ends_at > starts_at


def certification_covers(valid_until: datetime, starts_at: datetime) -> bool:
    """BR-04: certifikat plati k zaciatku rezervacie.

    valid_until == starts_at je NEPLATNY certifikat - platnost konci v ten
    okamih, rezervacia v ten okamih zacina. Je to ta ista polootvorena
    logika ako pri intervale rezervacie, zamerne.
    """
    return valid_until > starts_at


def decision_deadline(submitted_at: datetime, starts_at: datetime) -> datetime:
    """BR-08 (v0.4): termin rozhodnutia o ziadosti - skorsi z okamihov
    podanie + DECISION_PERIOD a starts_at.

    Urci sa raz, pri podani, a ulozi sa (DD-8). starts_at sa po vytvoreni
    rezervacie nemeni, takze skorsi z oboch okamihov neskor nezastara -
    a zivost je potom jedine porovnanie s ulozenym terminom.
    """
    return min(submitted_at + DECISION_PERIOD, starts_at)


def approval_request_is_alive(decision_deadline: datetime, now: datetime) -> bool:
    """BR-08: ziadost o schvalenie plati, kym neuplynie jej termin rozhodnutia.

    Hranica je ostra: v okamihu terminu uz ziadost neplati. Termin je
    skorsi z okamihov podanie + 48 h a starts_at (decision_deadline()),
    takze tato podmienka zahrna aj hranicu starts_at z v0.2.

    Tyka sa IBA stavu PENDING_APPROVAL. DRAFT nevyprsi, lebo nikoho
    neblokuje (nalez N-05).
    """
    return now < decision_deadline


def may_cancel_confirmed(starts_at: datetime, now: datetime) -> bool:
    """BR-03: potvrdenu rezervaciu mozno zrusit, len ak do zaciatku zostava
    striktne viac ako CANCELLATION_LEAD_TIME.

    Presne 60:00 min pred zaciatkom uz zrusit NEMOZNO (hranica je ostra).
    Pre stav DRAFT ziadna casova podmienka neplati - preto pre nu tu nie je
    funkcia (nalez N-03 v specifikacii).
    """
    return now + CANCELLATION_LEAD_TIME < starts_at
