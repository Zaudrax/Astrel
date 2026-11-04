"""T58 pas 3 (04/11) : sources des tours en liste fermée (`astrel/sources.py`), mêmes valeurs qu'avant."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from astrel import attente, core, relief, silence, sources
from astrel.coeur import langue as coeur_langue
from astrel.sources import Source

ASTREL = Path(__file__).resolve().parent.parent / "astrel"


def test_groupes_identiques_aux_listes_d_avant():
    # Les tuples écrits en dur jusqu'au 04/11, dans le même ordre.
    assert core._UNASKED == ("fond", "réflexe", "relève", "mission", "plan", "equipe", "quota")
    assert core._UTILISATEUR == coeur_langue._UTILISATEUR == relief.UTILISATEUR == ("voix", "clavier", "whatsapp")
    assert core._LOCAL_FROZEN == ("réflexe", "relève", "mission", "plan", "equipe", "quota")
    assert silence.AUTO_SOURCES == ("fond", "réflexe", "plan", "equipe", "quota")
    assert relief.HELD_SOURCES == ("voix", "clavier", "whatsapp", "réflexe", "equipe", "quota")
    assert attente.SOURCES == ("voix", "clavier")


def test_une_source_vaut_sa_chaine():
    assert Source.EQUIPE == "equipe" and hash(Source.REFLEXE) == hash("réflexe")
    assert f"{Source.RELEVE}" == "relève" and json.dumps([Source.VOIX]) == '["voix"]'
    assert "clavier" in sources.UTILISATEUR and "équipe" not in set(Source)


def _litteraux() -> list[tuple[str, int, str]]:
    """Chaînes données comme source d'un tour : `handle(..., source="x")`, `_begin_turn("x")`, `Held("x")`."""
    out = []
    for f in ASTREL.rglob("*.py"):
        arbre = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(arbre):
            if not isinstance(n, ast.Call):
                continue
            nom = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            vals = []
            if nom == "handle":
                vals = [k.value for k in n.keywords if k.arg == "source"]
            elif nom in ("_begin_turn", "Held") and n.args:
                vals = [n.args[0]]
            for v in vals:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append((f.name, n.lineno, v.value))
    return out


def test_toute_source_ecrite_dans_le_code_est_connue():
    vus = _litteraux()
    assert len(vus) >= 8  # le scan voit vraiment les appels (relève, quota, réflexe, mission, clavier…)
    inconnues = [(f, l, s) for f, l, s in vus if s not in set(Source)]
    assert not inconnues, inconnues
