"""Équipe d'agents (20/10, plan P13, lot 1) : journal de passation, relais testeur puis
relecteur après un codeur (source « equipe », jamais dite), un seul écrivain par fichier."""
import json
import time

import pytest

from astrel import config, core, equipe, mcp_server, silence
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(equipe, "EQUIPE_DIR", tmp_path / "equipe")
    monkeypatch.setattr(config, "ESTOP_FILE", tmp_path / "ESTOP")
    monkeypatch.setattr(equipe, "APRES_CODEUR", True)
    return tmp_path / "equipe"


def wait_for(calls, n=1):
    deadline = time.time() + 2
    while len(calls) < n and time.time() < deadline:
        time.sleep(0.01)
    return calls


# ------------------------------------------------------------------ journal de passation

def test_passation_numerotee_et_lisible(journal):
    r = equipe.deposer("codeur", "testeur", "ajout de l'outil passation", a_tester="fiche trop longue",
                       fichiers=["/p/astrel/equipe.py"], preuves="pytest -q : 12 passed", mission="P13")
    assert r == {"ok": True, "mission": "p13", "fiche": 1, "journal": str(journal / "p13" / "journal.md")}
    r2 = equipe.deposer("testeur", "relecteur", "tests passés", mission="P13")
    assert r2["fiche"] == 2
    text = (journal / "p13" / "journal.md").read_text(encoding="utf-8")
    assert text.startswith("# Journal d'équipe : p13\n")
    assert "## Fiche 1 · codeur → testeur" in text and "- À tester : fiche trop longue" in text
    assert "- Fichiers : /p/astrel/equipe.py" in text and "- Preuves : pytest -q : 12 passed" in text
    # Sans preuve : « non vérifié », jamais vide.
    assert "## Fiche 2 · testeur → relecteur" in text and "- Preuves : non vérifié" in text
    lines = (journal / "p13" / "fiches.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["n"] for x in lines] == [1, 2]


def test_passation_refusee_si_incomplete_ou_trop_longue(journal):
    assert not equipe.deposer("", "testeur", "x")["ok"]
    assert not equipe.deposer("codeur", "testeur", "")["ok"]
    r = equipe.deposer("codeur", "testeur", "é" * 1200, risques="é" * 1200)
    assert not r["ok"] and "2048 au plus" in r["error"]
    assert not (journal / equipe.DEFAULT_MISSION / "fiches.jsonl").exists()  # rien d'écrit à moitié


def test_nom_de_mission_sur():
    assert equipe.slug("Site Boutique / v2") == "site-boutique-v2"
    assert equipe.slug("../../etc") == "etc" and equipe.slug("") == "general" and equipe.slug(None) == "general"


def test_outil_mcp_passation(journal):
    r = mcp_server.passation("relecteur", "astrel", "rien de bloquant", fichiers=["a.py"], mission="P13")
    assert r["ok"] and r["fiche"] == 1 and (journal / "p13" / "journal.md").exists()


# ------------------------------------------------------------------ relais après le codeur

def test_relais_seulement_apres_un_codeur_qui_a_travaille(journal):
    task = {"desc": "outil passation", "started": time.time() - 60}
    assert equipe.after_codeur({"type": "relecteur", "files": ["a.py"]}, task, "completed") is None
    assert equipe.after_codeur({"type": "codeur", "files": ["a.py"]}, task, "failed") is None
    assert equipe.after_codeur({"type": "codeur", "files": []}, task, "completed") is None  # rien écrit, rien passé
    text, shown = equipe.after_codeur({"type": "codeur", "files": ["/p/a.py"]}, task, "completed")
    assert "testeur" in text and "relecteur" in text and "/p/a.py" in text and "un agent à la fois" in text
    equipe.deposer("codeur", "testeur", "fait", mission="P13")
    text, shown = equipe.after_codeur({"type": "codeur", "files": []}, task, "completed")
    assert "sur la fiche 1 du journal" in text and shown == "Équipe : testeur puis relecteur sur la fiche 1 (p13)."


def test_correction_revérifiee_par_celui_qui_l_a_demandee(journal):
    """29/10 (économie de jetons) : une correction du même lot n'a plus de seconde passe complète ;
    un nouveau lot de la même mission, ou un fichier du garde-fou, garde testeur puis relecteur."""
    task = {"desc": "corrections", "started": time.time() - 60}
    lot = ["/p/videos_ia.py"]
    equipe.deposer("codeur", "testeur", "lot B2", fichiers=lot, mission="P41")
    equipe.deposer("testeur", "relecteur", "tests", fichiers=lot + ["/tmp/essai.py"], mission="P41")
    equipe.deposer("relecteur", "astrel", "M1 crédits comptés deux fois", fichiers=lot, mission="P41")
    equipe.deposer("codeur", "testeur", "corrections M1", fichiers=lot, mission="P41")
    text, shown = equipe.after_codeur({"type": "codeur", "files": lot}, task, "completed")
    assert shown == "Équipe : relecteur seul sur la correction (fiche 4, p41)."
    assert "seulement le relecteur sur la fiche 4" in text and "testeur" not in text.split("relecteur")[0]
    assert "remarques mineures vont en cartes" in text
    # nouveau lot (autres fichiers) dans la même mission : cycle complet
    equipe.deposer("codeur", "testeur", "lot B1", fichiers=["/p/video.mjs"], mission="P41")
    text, shown = equipe.after_codeur({"type": "codeur", "files": ["/p/video.mjs"]}, task, "completed")
    assert shown == "Équipe : testeur puis relecteur sur la fiche 5 (p41)."
    # deux fiches du codeur de suite (reprise après une coupure) : premier passage
    assert equipe.verificateur_correction("p41", {"n": 6, "fichiers": lot}, []) == ""


def test_correction_du_garde_fou_toujours_cycle_complet(journal):
    garde = str(config.ROOT / "astrel" / "guard.py")
    equipe.deposer("codeur", "testeur", "lot", fichiers=[garde], mission="P29")
    equipe.deposer("relecteur", "codeur", "trou", fichiers=[garde], mission="P29")
    equipe.deposer("codeur", "testeur", "corrigé", fichiers=[garde], mission="P29")
    task = {"desc": "garde-fou", "started": time.time() - 60}
    assert equipe.after_codeur({"type": "codeur", "files": [garde]}, task, "completed")[1] \
        == "Équipe : testeur puis relecteur sur la fiche 3 (p29)."


def test_fiche_d_avant_la_tache_ignoree(journal):
    equipe.deposer("codeur", "testeur", "ancien travail", mission="P12")
    later = {"desc": "x", "started": time.time() + 3600}
    assert equipe.after_codeur({"type": "codeur", "files": []}, later, "completed") is None


def test_coeur_envoie_le_relais_equipe(astrel, published):
    j, said, _ = astrel
    calls = []
    j.handle = lambda text, source="voix", channel="", shown=None, joined=None: calls.append(
        {"text": text, "source": source, "shown": shown})
    j._on_task({"id": "t1", "phase": "start", "kind": "agent", "desc": "Codeur P13", "tool_use_id": "tu1"})
    j._agent_log["tu1"] = {"type": "codeur", "consigne": "c", "files": ["/p/a.py"], "pending": {}}
    equipe.deposer("codeur", "testeur", "fait", mission="P13")
    j._on_task({"id": "t1", "phase": "end", "status": "completed"})
    wait_for(calls)
    assert len(calls) == 1 and calls[0]["source"] == "equipe" and "fiche 1" in calls[0]["text"]
    assert "equipe" in core._UNASKED and "equipe" in silence.AUTO_SOURCES and "equipe" in core._LOCAL_FROZEN


def test_pas_de_relais_sous_estop_ni_pour_un_autre_agent(astrel, journal):
    j, _, _ = astrel
    calls = []
    j.handle = lambda *a, **k: calls.append(a)
    j._on_task({"id": "t2", "phase": "start", "kind": "agent", "desc": "Relecture", "tool_use_id": "tu2"})
    j._agent_log["tu2"] = {"type": "relecteur", "consigne": "c", "files": [], "pending": {}}
    j._on_task({"id": "t2", "phase": "end", "status": "completed"})
    config.ESTOP_FILE.write_text("1", encoding="utf-8")
    j._on_task({"id": "t3", "phase": "start", "kind": "agent", "desc": "Codeur", "tool_use_id": "tu3"})
    j._agent_log["tu3"] = {"type": "codeur", "consigne": "c", "files": ["/p/a.py"], "pending": {}}
    j._on_task({"id": "t3", "phase": "end", "status": "completed"})
    time.sleep(0.05)
    assert calls == []


def test_tour_equipe_jamais_dit(astrel, published):
    j, said, chimes = astrel
    j.handle("[Équipe, message automatique du cœur] …", source="equipe", shown="Équipe : testeur puis relecteur.")
    assert j._turn_source == "equipe"
    j._on_stream(core.BrainEvent("text", text="Je lance le testeur. "))
    j._on_stream(core.BrainEvent("turn_end"))
    assert said == [] and chimes == []


# ------------------------------------------------------------------ un seul écrivain par fichier

def _write(j, scope, path, tid):
    j._on_stream(core.BrainEvent("tool_start", tool="Write", tool_id=tid,
                                 data={"input": {"file_path": path}, "agent": scope}))


def test_alerte_deux_ecrivains_vivants(astrel, published):
    j, said, _ = astrel
    nudges = []
    j._nudge = nudges.append
    for n, kind in (("1", "codeur"), ("2", "general-purpose")):
        j._on_task({"id": f"t{n}", "phase": "start", "kind": "agent", "desc": kind, "tool_use_id": f"a{n}"})
        j._agent_log[f"a{n}"] = {"type": kind, "consigne": "", "files": [], "pending": {}}
    _write(j, "a1", "/p/site/index.html", "w1")
    assert not [e for e in of(published, "notice") if "même fichier" in e.get("text", "")]
    _write(j, "a2", "/p/site/index.html", "w2")
    _write(j, "a2", "/p/site/index.html", "w3")  # même fichier, même paire : une seule alerte
    alerts = [e for e in of(published, "notice") if "même fichier" in e.get("text", "")]
    assert len(alerts) == 1 and "index.html (codeur et general-purpose)" in alerts[0]["text"]
    wait_for(nudges)
    assert len(nudges) == 1 and nudges[0].startswith("[Alerte automatique d'Astrel]") and said == []


def test_pas_d_alerte_si_l_autre_ecrivain_a_fini(astrel, published):
    j, _, _ = astrel
    j._nudge = lambda text: None
    j.handle = lambda *a, **k: None  # le relais du codeur fini n'est pas l'objet de ce test
    j._on_task({"id": "t1", "phase": "start", "kind": "agent", "desc": "codeur", "tool_use_id": "a1"})
    j._agent_log["a1"] = {"type": "codeur", "consigne": "", "files": [], "pending": {}}
    _write(j, "a1", "/p/a.py", "w1")
    j._on_task({"id": "t1", "phase": "end", "status": "completed"})
    j._on_task({"id": "t2", "phase": "start", "kind": "agent", "desc": "codeur 2", "tool_use_id": "a2"})
    j._agent_log["a2"] = {"type": "codeur", "consigne": "", "files": [], "pending": {}}
    _write(j, "a2", "/p/a.py", "w2")
    assert not [e for e in of(published, "notice") if "même fichier" in e.get("text", "")]
