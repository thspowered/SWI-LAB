"""Domenove pravidla BR-01 az BR-04 zo specifikacie baseline v0.1.

Funkcie su ciste: nepoznaju HTTP ani databazu, pracuju iba s hodnotami.
Kazda nesie v docstringu pravidlo, ktore vynucuje, aby sa dalo prejst od
kodu k docs/specification.md a spat.
"""

from datetime import datetime, timedelta

#: BR-03 - prevadzkova lehota laboratoria na zrusenie potvrdenej rezervacie.
#: Rozhodnutie timu, nie odvodena velicina.
CANCELLATION_LEAD_TIME = timedelta(minutes=60)


def is_interval_valid(starts_at: datetime, ends_at: datetime) -> bool:
    """BR-01: interval je [starts_at, ends_at), takze ends_at > starts_at.

    starts_at == ends_at je neplatny interval, nie prazdna rezervacia.
    """
    return ends_at > starts_at


def intervals_overlap(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    """BR-01: prekryv polootvorenych intervalov.

    Susediace intervaly ([10,11) a [11,12)) sa NEPREKRYVAJU - preto su obe
    porovnania ostre.
    """
    return a_start < b_end and b_start < a_end


def certification_covers(valid_until: datetime, starts_at: datetime) -> bool:
    """BR-04: certifikat plati k zaciatku rezervacie.

    valid_until == starts_at je NEPLATNY certifikat - platnost konci v ten
    okamih, rezervacia v ten okamih zacina. Je to ta ista polootvorena
    logika ako pri intervale rezervacie, zamerne.
    """
    return valid_until > starts_at


def approval_request_is_alive(starts_at: datetime, now: datetime) -> bool:
    """BR-08: ziadost o schvalenie plati, kym nenastane starts_at.

    Hranica je ostra: v okamihu starts_at uz ziadost neplati. Lehota je
    odvodena z dat, ktore uz mame - specifikacia tym nezavadza ziadne
    vymyslene cislo.

    Tyka sa IBA stavu PENDING_APPROVAL. DRAFT nevyprsi, lebo nikoho
    neblokuje (nalez N-05).
    """
    return now < starts_at


def may_cancel_confirmed(starts_at: datetime, now: datetime) -> bool:
    """BR-03: potvrdenu rezervaciu mozno zrusit, len ak do zaciatku zostava
    striktne viac ako CANCELLATION_LEAD_TIME.

    Presne 60:00 min pred zaciatkom uz zrusit NEMOZNO (hranica je ostra).
    Pre stav DRAFT ziadna casova podmienka neplati - preto pre nu tu nie je
    funkcia (nalez N-03 v specifikacii).
    """
    return now + CANCELLATION_LEAD_TIME < starts_at
