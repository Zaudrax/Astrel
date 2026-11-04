"""tools/rendement_metiers.py (04/11, carte T50) : rendement par métier, lecture seule."""
import importlib.util
import json
import time
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("rendement_metiers",
                                              Path(__file__).parent.parent / "tools" / "rendement_metiers.py")
rm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rm)


def _ecrire(path: Path, lignes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lignes), encoding="utf-8")


def test_jointure_exacte_et_par_fenetre(tmp_path):
    now = time.time()
    couts = tmp_path / "agents-couts.jsonl"
    _ecrire(couts, [
        {"fin": now - 100, "duree_s": 600, "type": "codeur", "travail": 300_000, "cache_lu": 9_000_000,
         "depots": [{"outil": "passation", "mission": "lot1", "de": "codeur", "a": "testeur", "ok": True}]},
        {"fin": now - 50, "duree_s": 300, "type": "testeur", "travail": 100_000, "cache_lu": 2_000_000, "depots": []},
    ])
    equipe = tmp_path / "equipe"
    ancien = "jar" + "vis"  # ancien nom d'Astrel, écrit en deux morceaux (test du renommage)
    _ecrire(equipe / "lot1" / "fiches.jsonl", [
        {"de": "codeur", "a": "testeur", "preuves": "pytest vert", "at": now - 120},
        {"de": "testeur", "a": "codeur", "preuves": "non vérifié", "at": now - 60},
        {"de": "relecteur", "a": ancien, "preuves": "ok", "at": now - 10},  # aucun agent mesuré : pas jointe
    ])
    route = [{"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "codeur"},
             {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "testeur"}]
    since = now - 3600
    agents = rm.agents_mesures(couts, [], since)
    vues = rm.fiches(equipe, since)
    assert {f["a"] for f in vues} == {"testeur", "codeur", "astrel"}  # ancien nom ramené à « astrel »
    table = rm.rendement(agents, vues, route, since)
    assert table["codeur"]["fiches"] == 1 and table["codeur"]["travail_total"] == 300_000
    assert table["codeur"]["cache_lu"] == 9_000_000 and table["codeur"]["travail_par_fiche"] == 300_000
    assert table["testeur"]["renvois_vers_codeur"] == 1 and table["testeur"]["part_sans_preuve"] == 1.0
    assert table["codeur"]["lancements"] == 1 and "relecteur" not in table
    assert "codeur" in rm.render(table, 14)


def test_rien_a_lire(tmp_path):
    assert rm.rendement(rm.agents_mesures(tmp_path / "absent.jsonl", [], 0), rm.fiches(tmp_path, 0), [], 0) == {}
