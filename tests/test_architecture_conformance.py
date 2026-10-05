"""Kontrola dodrziavania architektonickeho pravidla z ADR-01.

ARCHITEKTONICKE PRAVIDLO
    Rezervacia smie vstupit do blokujuceho stavu (CONFIRMED,
    PENDING_APPROVAL) iba cez enter_blocking_state(), a predikat BR-02
    smie volat iba ta ista funkcia.

PRECO TO STRAZI TEST, A NIE CODE REVIEW
    Pred ADR-01 rozhodovali o BR-02 dve nezavisle miesta - confirm aj
    approve - a kazde malo vlastnu kontrolu i vlastny zapis. Vo v0.1 to
    bolo jedno miesto, vo v0.2 dve. Tretia operacia alebo tretí blokujuci
    stav by pribudol rovnako ticho. Test padne hned, ako niekto zapise
    blokujuci stav mimo vlastnika invariantu.

Kontrola je staticka (AST), takze nepotrebuje databazu ani bezaucu
aplikaciu a da sa pustit aj v CI pred testami.
"""

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "swilab"

#: Jediny vlastnik invariantu BR-02 (ADR-01).
INVARIANT_OWNER = "enter_blocking_state"

#: Predikat, ktorym sa BR-02 vyhodnocuje.
INVARIANT_PREDICATE = "_overlapping_blocking"

#: Stavy, ktore blokuju pristroj. Musi sediet s domain.states.BLOCKING_STATES -
#: strazi to test_zoznam_blokujucich_stavov_sedi_s_kodom nizsie.
BLOCKING_STATE_NAMES = frozenset({"CONFIRMED", "PENDING_APPROVAL"})


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


def _writes_to_blocking_state(node: ast.AST) -> bool:
    """Priradenie typu `<cokolvek>.state = ReservationState.CONFIRMED`."""
    if not isinstance(node, ast.Assign):
        return False
    targets_state = any(
        isinstance(t, ast.Attribute) and t.attr == "state" for t in node.targets
    )
    if not targets_state:
        return False
    value = node.value
    if isinstance(value, ast.Attribute) and value.attr in BLOCKING_STATE_NAMES:
        return True
    # `reservation.state = target_state` vo vlastnikovi invariantu
    return isinstance(value, ast.Name) and value.id == "target_state"


def test_blokujuci_stav_zapisuje_iba_vlastnik_invariantu():
    """Jadro ADR-01."""
    offenders: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if _writes_to_blocking_state(node) and owner.get(node) != INVARIANT_OWNER:
                offenders.append(
                    f"{path.relative_to(SRC.parent.parent)}:{node.lineno} "
                    f"(funkcia {owner.get(node)})"
                )

    assert not offenders, (
        "Porusene pravidlo z ADR-01: blokujuci stav sa zapisuje mimo "
        f"{INVARIANT_OWNER}().\n  " + "\n  ".join(offenders) + "\n"
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
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == INVARIANT_PREDICATE
                and owner.get(node) not in allowed
            ):
                offenders.append(
                    f"{path.relative_to(SRC.parent.parent)}:{node.lineno} "
                    f"(funkcia {owner.get(node)})"
                )

    assert not offenders, (
        f"Porusene pravidlo z ADR-01: {INVARIANT_PREDICATE}() sa vola mimo "
        f"{sorted(allowed)}.\n  " + "\n  ".join(offenders)
    )


def test_zoznam_blokujucich_stavov_sedi_s_kodom():
    """Poistka: keby v domene pribudol blokujuci stav, kontrola vyssie by
    ho nepoznala a ticho by ho prehliadla."""
    from swilab.domain.states import BLOCKING_STATES

    assert {state.name for state in BLOCKING_STATES} == BLOCKING_STATE_NAMES, (
        "BLOCKING_STATES sa zmenili - aktualizuj BLOCKING_STATE_NAMES v tomto "
        "teste, inak prestane chranit novy stav"
    )
