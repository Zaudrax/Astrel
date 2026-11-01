"""Détail d'agent au clic dans le HUD (21/10, plan P18, lot A) : le cœur dérive du flux déjà
reçu les dernières actions, les échecs, la liste TodoWrite et le résumé d'un agent, garde les
tâches finies 10 min, et répond à `task_detail` par des données seules, 6 Ko au plus."""
import json
import threading

import pytest

from astrel import detail_agent, equipe
from astrel.brain import BrainEvent
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(equipe, "EQUIPE_DIR", tmp_path / "equipe")
    return tmp_path / "equipe"


def lance(j, tid="t1", tu="tu1", kind="codeur", prompt="Écris le lot A.\nPuis les tests.", desc="Codeur P18"):
    j._on_brain_event(BrainEvent("tool_start", tool="Agent", tool_id=tu, text=desc,
                                 data={"input": {"prompt": prompt, "subagent_type": kind, "description": desc}}))
    j._on_task({"id": tid, "phase": "start", "kind": "agent", "desc": desc, "tool_use_id": tu})


def outil(j, tu, tool_id, tool, text="", tool_input=None):
    j._on_brain_event(BrainEvent("tool_start", tool=tool, tool_id=tool_id, text=text,
                                 data={"input": tool_input or {}, "agent": tu}))


def rendu(j, tu, tool_id, tool, error=""):
    j._on_brain_event(BrainEvent("tool_result", tool=tool, tool_id=tool_id, text=error,
                                 data={"is_error": bool(error), "agent": tu}))


def detail(j, tid="t1", **kw):
    return j.on_command({"cmd": "task_detail", "task_id": tid, **kw})


# ------------------------------------------------------------------ en cours

def test_detail_d_un_agent_en_cours(astrel, published):
    j, _, _ = astrel
    lance(j)
    outil(j, "tu1", "r1", "Read", "core.py", {"file_path": "/p/astrel/core.py"})
    rendu(j, "tu1", "r1", "Read")
    outil(j, "tu1", "b1", "Bash", "pytest", {"command": "pytest -q"})
    rendu(j, "tu1", "b1", "Bash", error="Exit code 1 : 2 failed")
    outil(j, "tu1", "w1", "Edit", "detail_agent.py", {"file_path": "/p/astrel/detail_agent.py"})
    d = detail(j)
    assert d["ok"] and d["live"] and d["state"] == "running" and d["type"] == "codeur"
    assert d["consigne"] == "Écris le lot A.\nPuis les tests."  # lignes gardées
    assert [(a["tool"], a["ok"]) for a in d["actions"]] == [("Read", True), ("Bash", False), ("Edit", None)]
    assert d["errors"] == 1 and d["last_errors"] == ["Exit code 1 : 2 failed"]
    assert d["files"] == ["/p/astrel/detail_agent.py"] and d["doing"] == [{"tool": "Edit", "desc": "detail_agent.py"}]
    assert d["todos"] is None  # pas de liste TodoWrite : « reste à faire » inconnu, rien d'inventé
    assert d["passation"] is None and d["ended"] is None and d["truncated"] is False


def test_liste_todowrite_suivie(astrel):
    j, _, _ = astrel
    lance(j)
    todos = [{"content": "Lot A", "status": "completed", "activeForm": "Écrit le lot A"},
             {"content": "Tests", "status": "in_progress"}, {"content": "Rapport", "status": "bizarre"}, "x"]
    outil(j, "tu1", "td1", "TodoWrite", "", {"todos": todos})
    assert detail(j)["todos"] == [{"text": "Lot A", "status": "completed"}, {"text": "Tests", "status": "in_progress"},
                                  {"text": "Rapport", "status": "pending"}]
    outil(j, "tu1", "td2", "TodoWrite", "", {"todos": [{"content": "Lot A", "status": "completed"}]})
    assert len(detail(j)["todos"]) == 1  # la dernière liste fait foi


def test_trente_dernieres_actions_et_trois_dernieres_erreurs(astrel):
    j, _, _ = astrel
    lance(j)
    for n in range(40):
        outil(j, "tu1", f"b{n}", "Bash", f"commande {n}")
        rendu(j, "tu1", f"b{n}", "Bash", error=f"erreur {n}" if n % 2 else "")
    d = detail(j)
    assert len(d["actions"]) == detail_agent.ACTIONS_MAX and d["actions"][-1]["desc"] == "commande 39"
    assert d["errors"] == 20 and d["last_errors"] == ["erreur 35", "erreur 37", "erreur 39"]


# ------------------------------------------------------------------ fin de tâche

def test_tache_finie_gardee_avec_resume_et_passation(astrel, published, journal):
    j, _, _ = astrel
    lance(j)
    equipe.deposer("codeur", "testeur", "lot A fait", a_tester="task_detail", risques="aucun", mission="P18")
    j._on_task({"id": "t1", "phase": "end", "status": "completed"})
    j._on_task({"id": "t1", "phase": "end", "status": "completed", "summary": "Lot A   livré."})  # 2e évènement de fin
    d = detail(j)
    assert d["ok"] and not d["live"] and d["state"] == "completed" and d["ended"] is not None
    assert d["summary"] == "Lot A livré."
    assert d["passation"]["mission"] == "p18" and d["passation"]["fait"] == "lot A fait"
    assert d["passation"]["a_tester"] == "task_detail" and d["passation"]["n"] == 1
    done = of(published, "tasks")[-1]["done"]
    assert [x["id"] for x in done] == ["t1"] and of(published, "tasks")[-1]["items"] == []


def test_echec_et_oubli_apres_dix_minutes(astrel, monkeypatch):
    j, _, _ = astrel
    lance(j)
    j._on_task({"id": "t1", "phase": "end", "status": "failed"})
    assert detail(j)["state"] == "failed"
    j._tasks_done["t1"]["ended"] -= detail_agent.DONE_KEEP_S + 1
    assert detail(j) == {"ok": False, "error": "unknown_task", "task_id": "t1"}


def test_dix_taches_finies_au_plus(astrel):
    j, _, _ = astrel
    for n in range(12):
        lance(j, tid=f"t{n}", tu=f"tu{n}")
        j._on_task({"id": f"t{n}", "phase": "end", "status": "completed"})
    assert list(j._tasks_done) == [f"t{n}" for n in range(2, 12)]


def test_passation_d_un_autre_metier_ou_d_avant_ignoree(astrel, journal):
    j, _, _ = astrel
    equipe.deposer("codeur", "testeur", "ancien travail", mission="P17")
    for fiche in equipe.read("p17"):
        assert fiche["at"] > 0
    lines = (journal / "p17" / "fiches.jsonl").read_text(encoding="utf-8").splitlines()
    old = json.loads(lines[0])
    old["at"] -= 3600  # déposée une heure avant le lancement
    (journal / "p17" / "fiches.jsonl").write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    lance(j)
    equipe.deposer("testeur", "relecteur", "tests passés", mission="P18")
    assert detail(j)["passation"] is None


# ------------------------------------------------------------------ garde-fous

def test_refuse_a_claude_et_tache_inconnue(astrel):
    j, _, _ = astrel
    lance(j)
    assert detail(j, _from_claude=True) == {"ok": False, "error": "hud_only"}
    assert detail(j, tid="nope") == {"ok": False, "error": "unknown_task", "task_id": "nope"}


def test_donnees_seules_sous_six_ko(astrel):
    """Agent de 100 actions, consigne et erreurs énormes : réponse ≤ 6 Ko, `truncated` le dit."""
    j, _, _ = astrel
    lance(j, prompt="consigne " * 2000)
    for n in range(100):
        outil(j, "tu1", f"w{n}", "Write", "x" * 500, {"file_path": f"/p/{'d' * 250}/f{n}.py"})
        rendu(j, "tu1", f"w{n}", "Write", error="é" * 1000)
    outil(j, "tu1", "td", "TodoWrite", "", {"todos": [{"content": "t" * 500, "status": "pending"}] * 50})
    d = detail(j)
    size = len(json.dumps(d, ensure_ascii=False).encode("utf-8"))
    assert d["ok"] and d["truncated"] and size <= detail_agent.MAX_BYTES
    # Les plus récentes gardées (TodoWrite en dernier), descriptions coupées.
    assert d["actions"][-1]["tool"] == "TodoWrite" and d["actions"][-2]["desc"].endswith("…")
    assert d["todos"] and d["files"] and d["last_errors"]
    assert all(len(e) <= detail_agent.ERROR_MAX for e in d["last_errors"])


def test_commande_de_fond_tronquee_a_200(astrel):
    j, _, _ = astrel
    j._on_brain_event(BrainEvent("tool_start", tool="Bash", tool_id="bg1", text="build",
                                 data={"input": {"command": "npm run build " + "a" * 400, "run_in_background": True}}))
    j._on_task({"id": "t9", "phase": "start", "kind": "local_bash", "desc": "build", "tool_use_id": "bg1"})
    d = detail(j, tid="t9")
    assert d["type"] == "commande" and len(d["consigne"]) == detail_agent.COMMANDE_MAX


def test_tache_sans_journal_d_agent(astrel):
    """Tâche du CLI dont le cœur n'a pas vu l'appel (avant une relance) : détail réduit, pas d'erreur."""
    j, _, _ = astrel
    j._on_task({"id": "t5", "phase": "start", "kind": "agent", "desc": "Agent inconnu", "step": "Read x"})
    d = detail(j, tid="t5")
    assert d["ok"] and d["actions"] == [] and d["todos"] is None and d["consigne"] == "" and d["step"] == "Read x"


def test_ancien_journal_sans_champs_p18(astrel):
    """Entrée du journal d'agent écrite à l'ancienne (tests P9/P13) : rien ne casse."""
    j, _, _ = astrel
    j._on_task({"id": "t1", "phase": "start", "kind": "agent", "desc": "x", "tool_use_id": "tu1"})
    j._agent_log["tu1"] = {"type": "codeur", "consigne": "c", "files": [], "pending": {}}
    outil(j, "tu1", "r1", "Read", "a.py")
    rendu(j, "tu1", "r1", "Read", error="introuvable")
    d = detail(j)
    assert [a["ok"] for a in d["actions"]] == [False] and d["errors"] == 1
    assert j._task_journal("t1")["outil_en_cours"] == ""  # format de la relève inchangé


# ------------------------------------------------------------------ corrections après test et relecture (lot B)

def test_echo_d_un_id_enorme_tronque(astrel):
    j, _, _ = astrel
    d = detail(j, tid="x" * 100_000)
    assert d["ok"] is False and d["error"] == "unknown_task"
    assert len(json.dumps(d, ensure_ascii=False).encode("utf-8")) < 500


def test_deux_codeurs_en_parallele_aucune_passation_jointe(astrel, journal):
    """A et B (deux codeurs) tournent ensemble ; A dépose : B ne doit pas montrer la fiche de A,
    et A non plus (on ne sait pas laquelle est la sienne) : `passation_ambiguous` le dit."""
    j, _, _ = astrel
    lance(j, tid="tA", tu="tuA")
    lance(j, tid="tB", tu="tuB")
    equipe.deposer("codeur", "testeur", "FAIT PAR A", mission="P18")
    for tid in ("tA", "tB"):
        d = detail(j, tid=tid)
        assert d["passation"] is None and d["passation_ambiguous"] is True


def test_codeurs_successifs_chacun_sa_passation(astrel, journal):
    """A finit (avec sa fiche), puis B démarre : A garde la sienne, B ne la voit pas."""
    j, _, _ = astrel
    lance(j, tid="tA", tu="tuA")
    j._tasks["tA"]["started"] -= 100
    equipe.deposer("codeur", "testeur", "FAIT PAR A", mission="P18")
    fiches = journal / "p18" / "fiches.jsonl"
    fiche = json.loads(fiches.read_text(encoding="utf-8"))
    fiche["at"] -= 30  # déposée il y a 30 s, juste avant la fin de A
    fiches.write_text(json.dumps(fiche, ensure_ascii=False) + "\n", encoding="utf-8")
    j._on_task({"id": "tA", "phase": "end", "status": "completed"})
    j._tasks_done["tA"]["ended"] -= 20  # fini il y a 20 s ; B démarre maintenant
    detail_agent._fiches_cache["key"] = None
    lance(j, tid="tB", tu="tuB")
    a, b = detail(j, tid="tA"), detail(j, tid="tB")
    assert a["passation"]["fait"] == "FAIT PAR A" and a["passation_ambiguous"] is False
    assert b["passation"] is None and b["passation_ambiguous"] is False


def test_fenetre_de_passation():
    assert detail_agent.passation_window(100, 200, []) == (95, 230)
    assert detail_agent.passation_window(100, 200, [(150, 250)]) is None          # en même temps
    assert detail_agent.passation_window(100, 200, [(210, 300)]) == (95, 210)     # le suivant borne la fin
    assert detail_agent.passation_window(100, 200, [(10, 98)]) == (98, 230)       # le précédent borne le début


def test_six_ko_tenus_avec_des_emojis_partout(astrel):
    """Caractères de 4 octets dans la description, l'étape, le résumé et les outils en cours."""
    j, _, _ = astrel
    big = "😀" * 400
    lance(j, desc=big, prompt=big * 4)
    j._tasks["t1"]["step"] = big
    for n in range(40):
        outil(j, "tu1", f"w{n}", "Write", big, {"file_path": f"/p/{big}{n}"})
        if n < 32:
            rendu(j, "tu1", f"w{n}", "Write", error=big)
    outil(j, "tu1", "td", "TodoWrite", "", {"todos": [{"content": big, "status": "pending"}] * 30})
    d = detail(j)
    assert d["ok"] and d["truncated"] and len(json.dumps(d, ensure_ascii=False).encode("utf-8")) <= detail_agent.MAX_BYTES
    j._on_task({"id": "t1", "phase": "end", "status": "completed", "summary": big})
    d = detail(j)
    assert len(json.dumps(d, ensure_ascii=False).encode("utf-8")) <= detail_agent.MAX_BYTES and d["summary"]


def test_couper_a_zero_rend_vide():
    assert detail_agent._cut("abcdef", 0) == "" and detail_agent._cut("abcdef", -3) == ""
    assert detail_agent._cut("abcdef", 3) == "ab…" and detail_agent._cut("abc", 3) == "abc"


def test_tache_finie_sans_outil_en_cours(astrel):
    """Tuée en plein outil : plus d'entrée « en cours » ; l'action reste sans résultat (ok None)."""
    j, _, _ = astrel
    lance(j)
    outil(j, "tu1", "b1", "Bash", "npm test")
    assert detail(j)["doing"] == [{"tool": "Bash", "desc": "npm test"}]
    j._on_task({"id": "t1", "phase": "end", "status": "killed"})
    d = detail(j)
    assert d["live"] is False and d["doing"] == [] and d["actions"][-1]["ok"] is None and d["state"] == "killed"


def test_liste_done_rafraichie_a_l_expiration_sans_tache_vivante(astrel, published):
    j, _, _ = astrel
    lance(j)
    j._on_task({"id": "t1", "phase": "end", "status": "completed"})
    j._tasks_tick()  # rien n'a changé : rien de publié
    n = len(of(published, "tasks"))
    j._tasks_done["t1"]["ended"] -= detail_agent.DONE_KEEP_S + 1
    j._tasks_tick()
    events = of(published, "tasks")
    assert len(events) == n + 1 and events[-1]["items"] == [] and events[-1]["done"] == []


def test_nouvelle_session_garde_les_taches_finies(astrel, published, monkeypatch):
    j, _, _ = astrel
    lance(j, tid="t1", tu="tu1")
    j._on_task({"id": "t1", "phase": "end", "status": "completed"})
    lance(j, tid="t2", tu="tu2")
    monkeypatch.setattr(j.brain, "restart", lambda *a, **k: None, raising=False)
    j._turn_lock = threading.Lock()
    j.reset_session()
    last = of(published, "tasks")[-1]
    assert last["items"] == [] and [x["id"] for x in last["done"]] == ["t1"]


def test_fiches_d_equipe_relues_au_plus_toutes_les_trois_secondes(astrel, monkeypatch):
    j, _, _ = astrel
    lance(j)
    detail_agent._fiches_cache["key"] = None
    equipe.deposer("codeur", "testeur", "lot A fait", mission="P18")
    calls = []
    real = equipe.read
    monkeypatch.setattr(equipe, "read", lambda m: calls.append(m) or real(m))
    detail(j)
    detail(j)
    assert len(calls) == 1  # deuxième demande servie par le cache
    detail_agent._fiches_cache["at"] -= detail_agent.PASSATION_CACHE_S + 0.1
    assert detail(j)["passation"]["fait"] == "lot A fait" and len(calls) == 2
