from datetime import UTC, datetime


def now() -> datetime:
    """Jediny zdroj casu pre business pravidla (BR-03).

    Cas sa odcitava raz na zaciatku operacie a ta ista hodnota sa posle
    do vsetkych podmienok. Keby si kazda podmienka siahla po vlastnom
    'teraz', dlhsia operacia by mohla vyhodnotit dve pravidla na dvoch
    roznych casoch - a hranica typu '60:00 min' by prestala byt
    overitelna.
    """
    return datetime.now(UTC)
