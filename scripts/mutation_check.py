"""Mutacne overenie testov jednotky Reservation Lifecycle (C05).

Otazka, na ktoru skript odpoveda: su testy odvodene z navrhu VAKUOVE,
alebo naozaj padnu, ked sa navrh porusi?

Pre kazdu vlozenu chybu sa vyrobi cista KOPIA src/ + tests/ v docasnom
adresari, vlozi sa do nej jedna zmena a spusti sa cela sada. Repozitar
sam sa nemeni. Vypis hovori, kolko testov padlo - zvlast testy pridane
v C05 a zvlast testy spred C05.

Spustenie (databaza musi bezat, rovnako ako pre testy):
    docker compose up -d --wait db
    .venv/bin/python scripts/mutation_check.py            # vsetky
    .venv/bin/python scripts/mutation_check.py M3 R1      # iba vybrane

Pozor: sada robi drop_all nad testovacou databazou. Nespustat sucasne
s inym behom testov.

Kazda mutacia je nahrada presneho textu. Ked sa kod zmeni a text sa uz
nenajde prave raz, skript skonci chybou - mutacia, ktora sa neaplikovala,
by inak vyzerala ako test, ktory nic nezachytil.

Navratovy kod je 0, ked kazdu chybu zachyti aspon jeden test.
"""

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent

SERVICE = "src/swilab/services/reservations.py"
POLICY = "src/swilab/domain/transitions.py"
RULES = "src/swilab/domain/rules.py"

#: Testy pridane v C05 - subory a staticke kontroly v spolocnom subore.
C05_FILES = ("tests/test_transitions.py", "tests/test_lifecycle_contracts.py")
C05_STATIC = (
    "test_politika_prechodov_nepozna_perzistenciu_ani_hodiny",
    "test_casove_pravidla_prechodov_pouziva_iba_domena",
    "test_operacie_rozhoduju_iba_cez_politiku",
    "test_politiku_prechodov_vola_iba_reservation_lifecycle",
    "test_predpoklady_alokacie_maju_jednu_implementaciu",
    "test_expired_zapisuje_jedina_funkcia",
)

# --- useky kodu, na ktore sa mutacie viazu --------------------------------

RULES_IMPORT = "from swilab.domain.rules import certification_covers, is_interval_valid\n"
POLICY_IMPORT = (
    "from swilab.domain.transitions import (\n"
    "    cancel_decision,\n"
    "    confirm_decision,\n"
    "    expiry_decision,\n"
    ")\n"
)
AUTHORIZE_IN_CONFIRM = (
    "    requester = _require_user(session, requested_by)\n"
    "    _require_authorized(reservation, requester)\n"
    "\n"
    "    # Pristroj sa nacita PRED rozhodnutim, lebo politika potrebuje\n"
)
CONFIRM_ASKS_POLICY = (
    "    decision = confirm_decision(\n"
    "        reservation.state,\n"
    "        reservation.starts_at,\n"
    "        now,\n"
    "        requires_approval=instrument.requires_approval,\n"
    "    )\n"
)
CONFIRM_ENTERS = (
    "    _require_allocatable(session, reservation=reservation, instrument=instrument)\n"
    "    enter_blocking_state(\n"
    "        session, reservation=reservation, target_state=decision.target"
)
CONFIRM_UNKNOWN_TARGET = (
    "    if decision.target not in BLOCKING_STATES:\n"
    "        # Politika vratila ciel, ktory potvrdenie nevie vykonat. Potvrdenie\n"
    "        # bud poziada o blokujuci stav, alebo sa zamietne - \"uspech bez\n"
    "        # zmeny\" nepozna. Kontrola je TU, hned pri rozhodnuti: strazca by\n"
    "        # to iste odmietol tiez, ale az po predpokladoch alokacie, ktore by\n"
    "        # chybu programu mohli prekryt beznym zamietnutim.\n"
    "        raise AssertionError(\n"
    "            f\"potvrdenie nevie vykonat prechod do {decision.target}\"\n"
    "        )\n"
)
CANCEL_ASKS_POLICY = (
    "    decision = cancel_decision(reservation.state, reservation.starts_at, now)\n"
)
CANCEL_EXPIRES = (
    "        case ReservationState.EXPIRED:\n"
    "            _write_expired(session, reservation)\n"
    "        case _:\n"
)
CANCEL_UNKNOWN_TARGET = (
    "        case _:\n"
    "            # Politika vratila ciel, ktory zrusenie nevie vykonat. Tiche\n"
    "            # pokracovanie by vratilo uspech bez zapisu.\n"
    "            raise AssertionError(\n"
    "                f\"zrusenie nevie vykonat prechod do {decision.target}\"\n"
    "            )\n"
)
EXPIRE_WRITES = (
    "            _write_expired(session, reservation)\n"
    "            raise DomainError(decision.rejection, decision.reason)\n"
)
EXPIRE_UNKNOWN_TARGET = (
    "        case _:\n"
    "            raise AssertionError(\n"
    "                f\"vyprsanie nevie vykonat prechod do {decision.target}\"\n"
    "            )\n"
)
DECIDE_ALLOCATES = (
    "    instrument = _require_instrument(session, reservation.instrument_id)\n"
    "    _require_allocatable(session, reservation=reservation, instrument=instrument)\n"
)
INACTIVE = (
    "    if not instrument.is_active:\n"
    '        raise DomainError(ErrorCode.INSTRUMENT_INACTIVE, "pristroj nie je aktivny")\n'
)
CERTIFICATE_REJECTED = (
    '            "vlastnik rezervacie nema platny certifikat na kategoriu pristroja",\n'
    "        )\n"
)
WRITE_EXPIRED = "    reservation.state = ReservationState.EXPIRED\n    session.commit()\n"
GUARD_REFUSES = (
    "    if target_state not in BLOCKING_STATES:\n"
    '        raise ValueError(f"{target_state} nie je blokujuci stav")\n\n'
)
GUARD_LOCK = 'with_for_update={"key_share": True}'

STATE_CHECK = (
    "    if state != ReservationState.DRAFT:\n"
    "        return Decision(\n"
    "            rejection=ErrorCode.INVALID_STATE,\n"
    '            reason=f"potvrdit mozno iba rezervaciu v stave DRAFT, nie {state}",\n'
    "        )\n"
)
TIME_CHECK = (
    "    if now >= starts_at:\n"
    "        return Decision(\n"
    "            rejection=ErrorCode.ALREADY_STARTED,\n"
    '            reason="rezervaciu po jej zaciatku uz nemozno potvrdit",\n'
    "        )\n"
)
CONFIRM_TARGET = "    # REQ-10: pristroj rozhoduje, ci rezervacia rovno alokuje, alebo sa\n"
TERMINAL_ROW = '            # REQ-14: ticho "uspesne" zrusenie by zakrylo, ze rezervaciu\n'
SHAPE_CHECK = "        if expires != (self.rejection is ErrorCode.EXPIRED) or (\n"
REASON_CHECK = "        if (self.rejection is None) != (self.reason is None):\n"

#: (oznacenie, co mutacia robi, [(subor, povodny text, novy text), ...])
#:
#: M* a S* - chyby v spravani a v strukture, ktorymi boli testy overene
#:           hned po implementacii.
#: R*      - scenare z nezavislej kontroly: obidenia, ktore povodne testy
#:           prepustili, a vetvy, ktore nemali test.
MUTATIONS = [
    ("M1", "_write_expired() necommituje",
     [(SERVICE, WRITE_EXPIRED, "    reservation.state = ReservationState.EXPIRED\n")]),
    ("M1b", "_write_expired() robi flush() namiesto commit()",
     [(SERVICE, WRITE_EXPIRED,
       "    reservation.state = ReservationState.EXPIRED\n    session.flush()\n")]),
    ("M2", "BR-03: hranica 60:00 neostra (< na <=)",
     [(RULES, "return now + CANCELLATION_LEAD_TIME < starts_at",
       "return now + CANCELLATION_LEAD_TIME <= starts_at")]),
    ("M3", "enter_blocking_state() commituje sam",
     [(SERVICE, "    reservation.state = target_state\n",
       "    reservation.state = target_state\n    session.commit()\n")]),
    ("M4", "predpoklady alokacie: BR-04 pred BR-05",
     [(SERVICE, INACTIVE + "\n    certifications = session.scalars(",
       "    certifications = session.scalars("),
      (SERVICE, CERTIFICATE_REJECTED, CERTIFICATE_REJECTED + INACTIVE)]),
    ("M5", "zrusenie vykona rozhodnutie EXPIRED ako CANCELLED",
     [(SERVICE, CANCEL_EXPIRES,
       "        case ReservationState.EXPIRED:\n"
       "            reservation.state = ReservationState.CANCELLED\n"
       "            session.commit()\n"
       "        case _:\n")]),
    ("M6", "confirm_decision: cas pred stavom",
     [(POLICY, STATE_CHECK + TIME_CHECK, TIME_CHECK + STATE_CHECK)]),
    ("M7", "BR-08: ziadost zije aj v okamihu starts_at",
     [(RULES, "    return now < starts_at", "    return now <= starts_at")]),
    ("M8", "strazca prijme neblokujuci ciel",
     [(SERVICE, GUARD_REFUSES, "")]),
    ("M9", "Decision neoveruje tvar rozhodnutia",
     [(POLICY, SHAPE_CHECK, "        if False and (\n")]),
    ("M10", "cancel_decision ma tichy predvoleny riadok",
     [(POLICY, "    assert_never(state)\n",
       "    return Decision(target=ReservationState.CANCELLED)\n")]),
    ("M11", "potvrdenie rozhodne skor, nez overi opravnenie",
     [(SERVICE, AUTHORIZE_IN_CONFIRM,
       "    # Pristroj sa nacita PRED rozhodnutim, lebo politika potrebuje\n"),
      (SERVICE, CONFIRM_ENTERS,
       "    requester = _require_user(session, requested_by)\n"
       "    _require_authorized(reservation, requester)\n" + CONFIRM_ENTERS)]),
    ("S1", "sluzba importuje casove pravidlo",
     [(SERVICE, RULES_IMPORT,
       "from swilab.domain.rules import (\n    certification_covers,\n"
       "    is_interval_valid,\n    may_cancel_confirmed,\n)\n")]),
    ("S2", "druhy zapisovatel EXPIRED",
     [(SERVICE, EXPIRE_WRITES,
       "            reservation.state = ReservationState.EXPIRED\n"
       "            session.commit()\n"
       "            raise DomainError(decision.rejection, decision.reason)\n")]),
    ("S3", "druhe vyhodnotenie BR-04 v schvalovani",
     [(SERVICE, DECIDE_ALLOCATES,
       DECIDE_ALLOCATES
       + "    certification_covers(reservation.starts_at, reservation.starts_at)\n")]),
    ("S4", "politika importuje sqlalchemy",
     [(POLICY, "from dataclasses import dataclass\n",
       "from dataclasses import dataclass\n\nfrom sqlalchemy import select  # noqa: F401\n")]),
    ("S5", "politika cita hodiny namiesto vstupu now",
     [(POLICY, "    if now >= starts_at:",
       "    if datetime.now(starts_at.tzinfo) >= starts_at:")]),
    ("R1", "potvrdenie rozhoduje samo, mimo politiky, s hranicou o tik inde",
     [(SERVICE, POLICY_IMPORT, POLICY_IMPORT.replace("(\n", "(\n    Decision,\n", 1)),
      (SERVICE, CONFIRM_ASKS_POLICY,
       "    if reservation.state is not ReservationState.DRAFT:\n"
       '        decision = Decision(rejection=ErrorCode.INVALID_STATE, reason="x")\n'
       "    elif now > reservation.starts_at:\n"
       '        decision = Decision(rejection=ErrorCode.ALREADY_STARTED, reason="x")\n'
       "    elif instrument.requires_approval:\n"
       "        decision = Decision(target=ReservationState.PENDING_APPROVAL)\n"
       "    else:\n"
       "        decision = Decision(target=ReservationState.CONFIRMED)\n")]),
    ("R2", "sluzba vola casove pravidlo cez atribut modulu",
     [(SERVICE, RULES_IMPORT, "from swilab.domain import rules\n" + RULES_IMPORT),
      (SERVICE, CANCEL_ASKS_POLICY,
       CANCEL_ASKS_POLICY
       + "    rules.may_cancel_confirmed(reservation.starts_at, now)\n")]),
    ("R3", "politika importuje perzistenciu tvarom 'from swilab import db'",
     [(POLICY, "from dataclasses import dataclass\n",
       "from dataclasses import dataclass\n\nfrom swilab import db  # noqa: F401\n")]),
    ("R4", "cancel_decision: REJECTED sa po termine da zrusit",
     [(POLICY, TERMINAL_ROW,
       "            if state == ReservationState.REJECTED and now >= starts_at:\n"
       "                return Decision(target=ReservationState.CANCELLED)\n"
       + TERMINAL_ROW)]),
    ("R5", "confirm_decision vrati 'uspech bez zmeny' tesne pred zaciatkom",
     [(POLICY, CONFIRM_TARGET,
       "    if (starts_at - now).total_seconds() < 1800:\n"
       "        return Decision()\n" + CONFIRM_TARGET)]),
    ("R6", "druha kopia BR-05 v schvalovani",
     [(SERVICE, DECIDE_ALLOCATES,
       "    instrument = _require_instrument(session, reservation.instrument_id)\n"
       + INACTIVE
       + "    _require_allocatable(session, reservation=reservation, instrument=instrument)\n")]),
    ("R7a", "zrusenie neznamy ciel ticho ignoruje",
     [(SERVICE, CANCEL_UNKNOWN_TARGET, "        case _:\n            pass\n")]),
    ("R7b", "potvrdenie neznamy ciel necha na strazcu",
     [(SERVICE, CONFIRM_UNKNOWN_TARGET, "")]),
    ("R7c", "vyprsanie neznamy ciel ticho ignoruje",
     [(SERVICE, EXPIRE_UNKNOWN_TARGET, "        case _:\n            return\n")]),
    ("R8a", "zamok nad pristrojom je FOR UPDATE",
     [(SERVICE, GUARD_LOCK, "with_for_update=True")]),
    ("R8b", "zamok nad pristrojom je FOR SHARE",
     [(SERVICE, GUARD_LOCK, 'with_for_update={"read": True}')]),
    ("R9", "expiry_decision porovnava stav identitou",
     [(POLICY, "    if state == ReservationState.PENDING_APPROVAL and not",
       "    if state is ReservationState.PENDING_APPROVAL and not")]),
    ("R10", "Decision prijme zamietnutie bez dovodu",
     [(POLICY, REASON_CHECK, "        if False:\n")]),
    ("R11", "Approval Workflow vola politiku priamo",
     [(SERVICE, DECIDE_ALLOCATES,
       "    expiry_decision(reservation.state, reservation.starts_at, now)\n"
       + DECIDE_ALLOCATES)]),
    ("R12", "zrusenie porovnava cas samo",
     [(SERVICE, CANCEL_ASKS_POLICY,
       CANCEL_ASKS_POLICY
       + "    if now >= reservation.starts_at:\n        pass\n")]),
]


def fresh_copy(work: pathlib.Path) -> None:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for name in ("src", "tests"):
        shutil.copytree(
            REPO / name, work / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    shutil.copy(REPO / "pyproject.toml", work / "pyproject.toml")


def apply(work: pathlib.Path, label: str, edits) -> None:
    for relative, old, new in edits:
        path = work / relative
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(
                f"{label}: mutacia sa neda aplikovat - text sa v {relative} "
                f"nachadza {text.count(old)}x:\n{old}"
            )
        path.write_text(text.replace(old, new), encoding="utf-8")


def run_suite(work: pathlib.Path) -> tuple[str, list[str]]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rfE", "--no-header",
         "--tb=no", "-p", "no:cacheprovider"],
        cwd=work, capture_output=True, text=True,
    )
    lines = result.stdout.strip().splitlines()
    failed = [
        re.sub(r" - .*", "", line.split(" ", 1)[1])
        for line in lines
        if line.startswith(("FAILED ", "ERROR "))
    ]
    return (lines[-1] if lines else result.stderr[-300:]), failed


def from_c05(test_id: str) -> bool:
    return test_id.startswith(C05_FILES) or any(name in test_id for name in C05_STATIC)


def names(test_ids: list[str]) -> str:
    counts: dict[str, int] = {}
    for test_id in test_ids:
        name = test_id.split("::")[-1].split("[")[0]
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{n} x{c}" if c > 1 else n for n, c in counts.items()) or "-"


def main() -> int:
    wanted = set(sys.argv[1:])
    selected = [m for m in MUTATIONS if not wanted or m[0] in wanted]
    if not selected:
        raise SystemExit(f"ziadna mutacia s oznacenim {sorted(wanted)}")

    work = pathlib.Path(tempfile.mkdtemp(prefix="swilab-mutant-"))
    try:
        fresh_copy(work)
        summary, failed = run_suite(work)
        print(f"bez mutacie: {summary}")
        if failed:
            raise SystemExit("sada nie je zelena ani bez mutacie - nema zmysel pokracovat")

        survived = []
        for label, what, edits in selected:
            fresh_copy(work)
            apply(work, label, edits)
            summary, failed = run_suite(work)
            new = [t for t in failed if from_c05(t)]
            old = [t for t in failed if not from_c05(t)]
            print(f"\n{label}  {what}\n     {summary}")
            print(f"     C05     ({len(new)}): {names(new)}")
            print(f"     starsie ({len(old)}): {names(old)}")
            if not failed:
                survived.append(label)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    only_c05 = "vsetky zachytene"
    print(f"\n{len(selected)} mutacii, prezilo {len(survived)}: {survived or only_c05}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
