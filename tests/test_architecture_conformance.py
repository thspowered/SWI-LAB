"""Kontrola dodrziavania architektonickeho pravidla z ADR-01.

ARCHITEKTONICKE PRAVIDLO
    Rezervacia smie vstupit do blokujuceho stavu (CONFIRMED,
    PENDING_APPROVAL) iba cez enter_blocking_state(). Ta ista funkcia si
    sama berie zamok nad pristrojom a az potom vyhodnoti predikat BR-02;
    predikat okrem nej smie volat iba citacia check_availability().

PRECO TO STRAZI TEST, A NIE CODE REVIEW
    Pred ADR-01 rozhodovali o BR-02 dve nezavisle miesta - confirm aj
    approve - a kazde malo vlastnu kontrolu i vlastny zapis. Vo v0.1 to
    bolo jedno miesto, vo v0.2 dve. Tretia operacia alebo tretí blokujuci
    stav by pribudol rovnako ticho. Test padne hned, ako niekto zapise
    blokujuci stav mimo vlastnika invariantu, alebo ako zo vlastnika
    zmizne zamok.

    Zamok je sucastou pravidla zamerne. Pri schvalovani (OP-05) ho ziadny
    test spravania nezachyti: kym BR-02 plati, schvalovana ziadost uz
    pristroj blokuje a konflikt k nej vzniknut nemoze. Chybajuci zamok by
    sa tam prejavil az po prvej dalsej zmene - overene pokusom pri review
    PR #7 (cela sada zostala zelena).

Kontrola je staticka (AST), takze nepotrebuje databazu ani bezaucu
aplikaciu a da sa pustit aj v CI pred testami.

CO NEZACHYTI
    Zapis stavu, ktory nie je priradenim ani konstruktorom - setattr()
    alebo hromadny UPDATE cez SQL. Taky zapis v kode dnes nie je a pri
    review je napadny; bezna cesta `reservation.state = ...` neprejde.
"""

import ast
import os
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "swilab"
SERVICE = SRC / "services" / "reservations.py"

#: Jediny vlastnik invariantu BR-02 (ADR-01).
INVARIANT_OWNER = "enter_blocking_state"

#: Predikat, ktorym sa BR-02 vyhodnocuje.
INVARIANT_PREDICATE = "_overlapping_blocking"

#: Zamok, ktory serializuje rozhodnutia o BR-02 pre jeden pristroj.
INSTRUMENT_LOCK = "_lock_instrument"

#: Jedine miesto, kde sluzba nacita rezervaciu, ktorej stav ide menit.
RESERVATION_LOCK = "_lock_reservation"

#: Stavy, ktore blokuju pristroj. Musi sediet s domain.states.BLOCKING_STATES -
#: strazi to test_zoznam_blokujucich_stavov_sedi_s_kodom nizsie.
BLOCKING_STATE_NAMES = frozenset({"CONFIRMED", "PENDING_APPROVAL"})

#: Logicke prvky z G2 a funkcie servisneho modulu, ktore ich tvoria. Prvky
#: nie su samostatne subory, takze smer zavislosti sa neda overit cez
#: importy - overuje sa cez volania medzi funkciami (riadok VERIFY v delte).
ELEMENTS = {
    "Reservation Lifecycle": {
        "create_reservation",
        "confirm_reservation",
        "cancel_reservation",
        "enter_blocking_state",
        "expire_request_if_dead",
        "_write_expired",
        "_lock_reservation",
        "_lock_instrument",
        "_overlapping_blocking",
    },
    "Approval Workflow": {"decide_reservation"},
    "Availability Query": {"check_availability"},
    # Kontroly predpokladov bez stavu; smie ich volat ktorykolvek prvok.
    "spolocne predpoklady": {
        "_require_user",
        "_require_instrument",
        "_require_authorized",
        "_require_allocatable",
    },
}


def _python_files() -> list[pathlib.Path]:
    files = sorted(SRC.rglob("*.py"))
    assert files, f"v {SRC} nie su ziadne zdrojove subory - kontrola by presla naprazdno"
    return files


def _enclosing_functions(tree: ast.Module) -> dict[ast.AST, str]:
    """Priradi kazdemu uzlu meno funkcie, v ktorej lezi."""
    owner: dict[ast.AST, str] = {}

    def walk(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) else current
            owner[child] = name
            walk(child, name)

    walk(tree, "<modul>")
    return owner


def _called_name(node: ast.AST) -> str | None:
    """Meno volanej funkcie - pre `f(...)` aj pre `modul.f(...)`."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _written_state(node: ast.AST) -> ast.AST | None:
    """Hodnota, ktoru uzol zapisuje do stavu rezervacie, alebo None.

    Pozna `<cokolvek>.state = <hodnota>` a `Reservation(state=<hodnota>)`.
    """
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    elif _called_name(node) == "Reservation":
        return next((kw.value for kw in node.keywords if kw.arg == "state"), None)
    else:
        return None
    if any(isinstance(t, ast.Attribute) and t.attr == "state" for t in targets):
        return node.value
    return None


def _is_named_non_blocking_state(value: ast.AST) -> bool:
    """`ReservationState.<STAV>`, kde STAV pristroj neblokuje.

    Vsetko ostatne - blokujuci stav, premenna, podmieneny vyraz - mimo
    vlastnika invariantu neprejde: z vyrazu sa neda staticky vylucit, ze
    nim blokujuci stav pretecie.
    """
    return (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "ReservationState"
        and value.attr not in BLOCKING_STATE_NAMES
    )


def _location(path: pathlib.Path, node: ast.AST, function: str | None) -> str:
    return f"{path.relative_to(SRC.parent.parent)}:{node.lineno} (funkcia {function})"


def _service_functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    return {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }


def test_blokujuci_stav_zapisuje_iba_vlastnik_invariantu():
    """Jadro ADR-01."""
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            value = _written_state(node)
            if value is None or owner.get(node) == INVARIANT_OWNER:
                continue
            if not _is_named_non_blocking_state(value):
                offenders.append(_location(path, node, owner.get(node)))

    assert not offenders, (
        "Porusene pravidlo z ADR-01: mimo "
        f"{INVARIANT_OWNER}() sa do stavu rezervacie zapisuje nieco ine nez "
        "menovany neblokujuci stav.\n  " + "\n  ".join(offenders) + "\n"
        "Invariant BR-02 ma mat jedineho vlastnika - pridaj prechod do "
        f"{INVARIANT_OWNER}() namiesto priameho zapisu."
    )


def test_predikat_br02_vola_iba_vlastnik_invariantu():
    """Kontrola prekryvu nesmie prebiehat inde ako pri zapise stavu.

    Vynimka: check_availability je citacia operacia - BR-02 iba REPORTUJE,
    ziadny stav nemeni, takze zamok nepotrebuje.
    """
    allowed = {INVARIANT_OWNER, "check_availability"}
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if (
                _called_name(node) == INVARIANT_PREDICATE
                and owner.get(node) not in allowed
            ):
                offenders.append(_location(path, node, owner.get(node)))

    assert not offenders, (
        f"Porusene pravidlo z ADR-01: {INVARIANT_PREDICATE}() sa vola mimo "
        f"{sorted(allowed)}.\n  " + "\n  ".join(offenders)
    )


def test_vlastnik_invariantu_si_berie_zamok_sam():
    """Zamok nad pristrojom berie vlastnik invariantu, nie jeho volajuci -
    nepodmienene a skor, nez vyhodnoti predikat BR-02."""
    owner_def = _service_functions()[INVARIANT_OWNER]

    def calls(statement: ast.stmt, name: str) -> bool:
        return any(_called_name(node) == name for node in ast.walk(statement))

    predicate_at = next(
        (i for i, stmt in enumerate(owner_def.body) if calls(stmt, INVARIANT_PREDICATE)),
        None,
    )
    assert predicate_at is not None, (
        f"{INVARIANT_OWNER}() nevola {INVARIANT_PREDICATE}() - kontrola by "
        "strazila prazdne pravidlo"
    )
    # Iba prikazy priamo v tele funkcie: zamok v `if` by bol podmieneny.
    locked_before = any(
        isinstance(stmt, (ast.Expr, ast.Assign))
        and _called_name(stmt.value) == INSTRUMENT_LOCK
        for stmt in owner_def.body[:predicate_at]
    )
    assert locked_before, (
        f"Porusene pravidlo z ADR-01: {INVARIANT_OWNER}() nevola "
        f"{INSTRUMENT_LOCK}() nepodmienene pred {INVARIANT_PREDICATE}(). Bez "
        "zamku je kontrola prekryvu iba odporucanie (REQ-05)."
    )

    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if _called_name(node) == INSTRUMENT_LOCK and owner.get(node) != INVARIANT_OWNER:
                offenders.append(_location(path, node, owner.get(node)))
    assert not offenders, (
        f"{INSTRUMENT_LOCK}() smie volat iba {INVARIANT_OWNER}() - druhe "
        "miesto so zamkom je zaciatok druheho vlastnika invariantu.\n  "
        + "\n  ".join(offenders)
    )


def test_rezervaciu_sluzba_nacita_iba_so_zamkom():
    """REQ-16: operacia, ktora meni stav rezervacie, ju musi nacitat cez
    _lock_reservation(). Nezamknute `session.get(Reservation, ...)` otvara
    okno medzi kontrolou zdrojoveho stavu a zapisom."""
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    owner = _enclosing_functions(tree)
    offenders = [
        _location(SERVICE, node, owner.get(node))
        for node in ast.walk(tree)
        if _called_name(node) == "get"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "Reservation"
        and owner.get(node) != RESERVATION_LOCK
    ]
    assert not offenders, (
        f"Rezervacia sa nacitava mimo {RESERVATION_LOCK}():\n  "
        + "\n  ".join(offenders)
    )


def test_kazda_funkcia_sluzby_patri_prave_jednemu_prvku():
    """Poistka pre priradenie G2 -> kod: nova funkcia sluzby musi dostat
    prvok, inak by kontrola smeru zavislosti o nej nevedela."""
    assigned = [name for names in ELEMENTS.values() for name in names]
    assert len(assigned) == len(set(assigned)), "funkcia je priradena dvom prvkom"
    assert set(assigned) == set(_service_functions()), (
        "ELEMENTS nesedi s funkciami v services/reservations.py - "
        f"rozdiel: {sorted(set(assigned) ^ set(_service_functions()))}"
    )


def test_approval_workflow_zavisi_od_lifecycle_nie_naopak():
    """Riadok VERIFY z delty (J): Approval Workflow si prechod do
    blokujuceho stavu ZIADA od Reservation Lifecycle. Opacna zavislost by
    znamenala, ze lifecycle rozhoduje podla politiky schvalovania."""
    functions = _service_functions()
    approval = ELEMENTS["Approval Workflow"]

    offenders = [
        f"{name}() -> {_called_name(node)}()"
        for element, names in ELEMENTS.items()
        if element != "Approval Workflow"
        for name in sorted(names)
        for node in ast.walk(functions[name])
        if _called_name(node) in approval
    ]
    assert not offenders, (
        "Mimo Approval Workflow sa vola jeho funkcia:\n  " + "\n  ".join(offenders)
    )

    requested = {
        _called_name(node)
        for name in approval
        for node in ast.walk(functions[name])
    }
    assert INVARIANT_OWNER in requested, (
        f"Approval Workflow nevola {INVARIANT_OWNER}() - schvalenie by "
        "pristroj alokovalo mimo vlastnika invariantu"
    )


def test_zoznam_blokujucich_stavov_sedi_s_kodom():
    """Poistka: keby v domene pribudol blokujuci stav, kontrola vyssie by
    ho nepoznala a ticho by ho prehliadla."""
    from swilab.domain.states import BLOCKING_STATES

    assert {state.name for state in BLOCKING_STATES} == BLOCKING_STATE_NAMES, (
        "BLOCKING_STATES sa zmenili - aktualizuj BLOCKING_STATE_NAMES v tomto "
        "teste, inak prestane chranit novy stav"
    )


# =========================================================================
# C05 - Detailed Design jednotky Reservation Lifecycle
#
# Rozhodnutia DD-1, DD-4 a DD-5 z docs/architecture-and-decisions.md,
# C05.3. Rovnaky dovod ako vyssie: su to pravidla o tom, KDE nieco smie
# byt, a test spravania ich nezachyti - druha kopia podmienky sa sprava
# rovnako ako prva, az kym sa jedna z nich nezmeni.
#
# Kontroly hladaju MENA, nie vyznam. Vidia volanie, import aj pristup cez
# atribut (`rules.may_cancel_confirmed`); nevidia pravidlo prepisane inymi
# slovami ani meno poskladane z retazca (getattr).
# =========================================================================

#: Cista politika prechodov (DD-1) a balik, v ktorom lezi.
TRANSITIONS = SRC / "domain" / "transitions.py"
DOMAIN = SRC / "domain"

#: Co sa pri importe politiky nesmie nacitat - ani nepriamo: perzistencia,
#: HTTP, sluzby ani hodiny. Cas dostava politika ako vstup `now`.
FORBIDDEN_FOR_POLICY = (
    "sqlalchemy",
    "psycopg",
    "fastapi",
    "swilab.models",
    "swilab.db",
    "swilab.services",
    "swilab.api",
    "swilab.clock",
    "swilab.main",
)

#: Operacie, ktore menia stav existujucej rezervacie, a funkcia politiky,
#: ktorej rozhodnutie vykonavaju.
POLICY_OF_OPERATION = {
    "confirm_reservation": "confirm_decision",
    "cancel_reservation": "cancel_decision",
    "expire_request_if_dead": "expiry_decision",
}

#: Pomenovane casove pravidla prechodov. Mimo balika domain/ ich nesmie
#: pouzit nikto. (Druha forma BR-08 je SQL podmienka v dotaze na prekryv -
#: ta meno nema a tato kontrola o nej nehovori.)
TRANSITION_TIME_RULES = frozenset(
    {"may_cancel_confirmed", "approval_request_is_alive", "CANCELLATION_LEAD_TIME"}
)

#: Jedina implementacia predpokladov alokacie (DD-5) a ti, co ju volaju.
ALLOCATION_PRECONDITIONS = "_require_allocatable"
ALLOCATING_OPERATIONS = {"confirm_reservation", "decide_reservation"}

#: Kto smie citat Instrument.is_active (BR-05): predpoklady alokacie,
#: vytvorenie (OP-01) a dostupnost (OP-02). Nikto dalsi.
BR05_READERS = {ALLOCATION_PRECONDITIONS, "create_reservation", "check_availability"}

#: Jediny zapisovatel stavu EXPIRED (DD-4).
EXPIRY_WRITER = "_write_expired"

SERVICE_FILE = SERVICE.relative_to(SRC).as_posix()


def _names_used(tree: ast.AST) -> set[str]:
    """Mena, ktore kod pouziva: premenne, atributy aj importovane mena."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {alias.name.split(".")[-1] for alias in node.names}
    return names


def _functions_where(predicate) -> set[tuple[str, str]]:
    """(subor, funkcia) pre kazdy uzol v src/swilab, ktory splna predikat.

    Subor je sucastou odpovede zamerne: api/ aj services/ maju funkcie
    s rovnakym menom (confirm_reservation, create_reservation, ...).
    """
    found: set[tuple[str, str]] = set()
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _enclosing_functions(tree)
        found |= {
            (path.relative_to(SRC).as_posix(), owner.get(node, "<modul>"))
            for node in ast.walk(tree)
            if predicate(node)
        }
    return found


def _in_service(names) -> set[tuple[str, str]]:
    return {(SERVICE_FILE, name) for name in names}


def _mentions_time(node: ast.AST) -> bool:
    return any(
        (isinstance(child, ast.Name) and child.id == "now")
        or (isinstance(child, ast.Attribute) and child.attr in {"starts_at", "ends_at"})
        for child in ast.walk(node)
    )


def test_politika_prechodov_nepozna_perzistenciu_ani_hodiny():
    """DD-1: rozhodnutie o prechode je cista funkcia. Keby politika siahla
    na session alebo na hodiny, prestala by sa dat overit bez databazy
    a dve pravidla jednej operacie by mohli vidiet dva rozne okamihy.

    Import sa overuje SKUTOCNYM importom v cistom procese, nie citanim
    prikazov `import`: zachyti aj `from swilab import db`, relativny import
    aj zavislost, ktoru by pritiahol modul, na ktorom politika stoji.
    """
    probe = (
        "import sys\n"
        "import swilab.domain.transitions\n"
        f"print([m for m in sorted(sys.modules) if m.startswith({FORBIDDEN_FOR_POLICY!r})])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC.parent)},
    )
    assert result.returncode == 0, result.stderr
    loaded = ast.literal_eval(result.stdout.strip())
    assert not loaded, (
        f"Porusene DD-1: import politiky nacita {loaded}. Politika smie "
        "poznat iba domenove hodnoty a kody zamietnuti."
    )

    reads_clock = [
        f"{path.relative_to(SRC).as_posix()}:{node.lineno}"
        for path in sorted(DOMAIN.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if _called_name(node) in {"now", "utcnow", "today"}
    ]
    assert not reads_clock, (
        f"Porusene DD-1: domena cita cas ({reads_clock}). Cas je vstup `now`, "
        "nie volanie."
    )


def test_casove_pravidla_prechodov_pouziva_iba_domena():
    """DD-1: lehotu (BR-03) ani zivost ziadosti si mimo domeny nikto
    nevyhodnocuje sam. Druhe vyhodnotenie v sluzbe alebo v API by bolo
    druhym rozhodnutim o tom istom prechode - presne to, co C03 naslo pri
    BR-02 a pri zapise EXPIRED."""
    offenders = sorted(
        f"{path.relative_to(SRC).as_posix()}: {name}"
        for path in _python_files()
        if DOMAIN not in path.parents
        for name in TRANSITION_TIME_RULES
        & _names_used(ast.parse(path.read_text(encoding="utf-8")))
    )
    assert not offenders, (
        "Porusene DD-1: casove pravidlo prechodu sa pouziva mimo domain/.\n  "
        + "\n  ".join(offenders)
        + "\nO prechode rozhoduje swilab.domain.transitions."
    )

    policy = ast.parse(TRANSITIONS.read_text(encoding="utf-8"))
    assert {"may_cancel_confirmed", "approval_request_is_alive"} <= {
        _called_name(node) for node in ast.walk(policy)
    }, "politika casove pravidla nevola - kontrola by strazila prazdne pravidlo"


def test_operacie_rozhoduju_iba_cez_politiku():
    """DD-1: operacia, ktora meni stav existujucej rezervacie, sa na
    prechod pyta politiky a cas sama neporovnava.

    Bez tejto kontroly DD-1 nestrazi nic: sluzba, ktora by sa vratila
    k vlastnym `if`, by presla vsetkymi testami spravania, kym by sa
    od tabulky neodlisila."""
    functions = _service_functions()

    for operation, decision in POLICY_OF_OPERATION.items():
        called = {_called_name(node) for node in ast.walk(functions[operation])}
        assert decision in called, (
            f"Porusene DD-1: {operation}() nevola {decision}() - rozhoduje sama."
        )

    offenders = [
        f"{name}():{node.lineno}"
        for name in (*POLICY_OF_OPERATION, "decide_reservation")
        for node in ast.walk(functions[name])
        if isinstance(node, ast.Compare) and _mentions_time(node)
    ]
    assert not offenders, (
        "Porusene DD-1: operacia porovnava cas sama, mimo politiky:\n  "
        + "\n  ".join(offenders)
    )


def test_politiku_prechodov_vola_iba_reservation_lifecycle():
    """Politika je rozhodovacia cast Reservation Lifecycle (C05.3) - fyzicky
    lezi v domain/, ale vlastnikom rozhodnutia o prechode ostava lifecycle
    (O-1). Keby ju volal Approval Workflow alebo API, mal by prechod
    dvoch vlastnikov."""
    policy_functions = set(POLICY_OF_OPERATION.values())

    callers = _functions_where(lambda node: _called_name(node) in policy_functions)
    inside_policy = {caller for caller in callers if caller[0] == "domain/transitions.py"}
    allowed = _in_service(ELEMENTS["Reservation Lifecycle"]) | inside_policy

    assert callers - allowed == set(), (
        "Politiku prechodov vola niekto mimo Reservation Lifecycle: "
        f"{sorted(callers - allowed)}"
    )
    assert _in_service(POLICY_OF_OPERATION) <= callers, (
        "operacie lifecycle politiku nevolaju - kontrola by strazila prazdne pravidlo"
    )


def test_predpoklady_alokacie_maju_jednu_implementaciu():
    """DD-5: BR-05 a BR-04 overuje jedna funkcia a obe operacie, ktore
    pristroj alokuju, idu cez nu. Druha kopia ktorejkolvek z dvoch kontrol
    by sa spravala rovnako - preto ju musi najst staticka kontrola."""
    certificate = _functions_where(
        lambda node: _called_name(node) == "certification_covers"
    )
    assert certificate == _in_service({ALLOCATION_PRECONDITIONS}), (
        "Porusene DD-5: certifikat (BR-04) sa vyhodnocuje mimo "
        f"{ALLOCATION_PRECONDITIONS}(): {sorted(certificate)}"
    )

    activity = _functions_where(
        lambda node: isinstance(node, ast.Attribute)
        and node.attr == "is_active"
        and isinstance(node.ctx, ast.Load)
    )
    assert (SERVICE_FILE, ALLOCATION_PRECONDITIONS) in activity
    assert activity <= _in_service(BR05_READERS), (
        "Porusene DD-5: aktivnost pristroja (BR-05) cita niekto mimo "
        f"{sorted(BR05_READERS)}: {sorted(activity - _in_service(BR05_READERS))}"
    )

    callers = _functions_where(
        lambda node: _called_name(node) == ALLOCATION_PRECONDITIONS
    )
    assert callers == _in_service(ALLOCATING_OPERATIONS), (
        f"Porusene DD-5: {ALLOCATION_PRECONDITIONS}() maju volat prave "
        f"{sorted(ALLOCATING_OPERATIONS)}, vola ho {sorted(callers)}."
    )


def test_expired_zapisuje_jedina_funkcia():
    """DD-4: EXPIRED ma jedneho zapisovatela. Dve kopie tohto zapisu uz raz
    existovali (review PR #7, R-3)."""
    writers = sorted(
        _functions_where(
            lambda node: isinstance(value := _written_state(node), ast.Attribute)
            and value.attr == "EXPIRED"
        )
    )

    assert writers == [(SERVICE_FILE, EXPIRY_WRITER)], (
        f"Porusene DD-4: EXPIRED ma zapisovat prave {EXPIRY_WRITER}(), "
        f"zapisuje ho {writers}."
    )
