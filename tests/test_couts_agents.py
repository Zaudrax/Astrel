"""Coût en direct par agent, alerte « agent qui s'emballe », bouton Stop d'une
tâche, prévision d'épuisement du quota (15/10, idée 10). ALERTE SEULE : rien
n'est jamais arrêté sans l'utilisateur."""
import threading
import time

import pytest

from astrel import config, core
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)


def task(tid="t1", phase="progress", **kw):
    return {"id": tid, "phase": phase, "kind": "agent", "desc": kw.pop("desc", ""), "step": "", **kw}


# ------------------------------------------------------------------ coût par tâche

def test_tache_porte_son_cout(astrel, published):
    j, said, _ = astrel
    j._on_task(task(phase="start", desc="Audit Boutique"))
    item = of(published, "tasks")[-1]["items"][0]
    assert item["tokens"] == 0 and item["requests"] == 0 and item["alert"] is False
    assert isinstance(item["elapsed_s"], float)
    j._on_task(task(tokens=120_000, requests=7, elapsed_s=42.5))
    item = of(published, "tasks")[-1]["items"][0]
    assert item["tokens"] == 120_000 and item["requests"] == 7 and item["elapsed_s"] >= 42.5
    j._on_task(task(tokens=None, requests="x"))  # champs absents (cerveau d'avant le lot 1) : rien ne casse
    item = of(published, "tasks")[-1]["items"][0]
    assert item["tokens"] == 120_000 and item["requests"] == 7
    j._on_task(task(tokens=100))  # un cumul ne recule pas
    assert of(published, "tasks")[-1]["items"][0]["tokens"] == 120_000
    assert said == []


def test_alerte_une_seule_fois_et_sans_arret(astrel, published, monkeypatch):
    j, said, _ = astrel
    monkeypatch.setattr(config, "AGENT_ALERT_TOKENS", 1_000_000, raising=False)
    stopped = []
    j.brain.stop_task = lambda tid: stopped.append(tid) or True
    j._on_task(task(phase="start", desc="Recherche de marché"))
    j._on_task(task(tokens=999_999))
    assert not of(published, "tasks")[-1]["items"][0]["alert"]
    assert not [n for n in of(published, "notice") if n.get("task_alert")]
    j._on_task(task(tokens=1_200_000, requests=40))
    assert of(published, "tasks")[-1]["items"][0]["alert"] is True
    j._on_task(task(tokens=2_500_000, requests=80))
    alerts = [n for n in of(published, "notice") if n.get("task_alert")]
    assert len(alerts) == 1  # un seul message par tâche
    assert "Recherche de marché" in alerts[0]["text"] and "1,2 M" in alerts[0]["text"]
    assert alerts[0]["level"] == "warning" and alerts[0]["task_id"] == "t1"
    assert said == [] and stopped == []  # ni voix, ni arrêt automatique



def test_alerte_sur_le_travail_pas_sur_le_cache(astrel, published, monkeypatch):
    """04/11 (T50) : 185 alertes comptaient le cache relu. Le seuil porte sur le TRAVAIL ; le panneau
    l'affiche (`tokens`), le total reste dans `tokens_total`."""
    j, said, _ = astrel
    monkeypatch.setattr(config, "AGENT_ALERT_TOKENS", 1_000_000, raising=False)
    j._on_task(task(phase="start", desc="Audit du code"))
    j._on_task(task(tokens=13_000_000, travail=400_000, cache_lu=12_600_000, requests=40))
    item = of(published, "tasks")[-1]["items"][0]
    assert item["tokens"] == 400_000 and item["tokens_total"] == 13_000_000 and item["cache_lu"] == 12_600_000
    assert not item["alert"] and not [n for n in of(published, "notice") if n.get("task_alert")]
    j._on_task(task(tokens=30_000_000, travail=1_100_000, cache_lu=28_900_000, requests=90))
    alerts = [n for n in of(published, "notice") if n.get("task_alert")]
    assert len(alerts) == 1 and "1,1 M tokens de travail" in alerts[0]["text"]
    assert "hors cache relu" in alerts[0]["text"] and "28,9 M relus" in alerts[0]["text"]
    assert said == []


def test_cerveau_d_avant_sans_travail(astrel, published, monkeypatch):
    """Un cerveau qui ne donne pas encore `travail` : son `tokens` tel quel (compatibilité)."""
    j, _, _ = astrel
    j._on_task(task(phase="start", desc="Ancien"))
    j._on_task(task(tokens=250_000))
    assert of(published, "tasks")[-1]["items"][0]["tokens"] == 250_000

def test_alerte_a_la_duree_meme_sans_nouvelles(astrel, published, monkeypatch):
    j, _, _ = astrel
    monkeypatch.setattr(config, "AGENT_ALERT_S", 60, raising=False)
    j._on_task(task(phase="start", desc="Build long"))
    j._tasks_tick()
    assert not [n for n in of(published, "notice") if n.get("task_alert")]
    before = len(of(published, "tasks"))
    j._tasks_tick()
    assert len(of(published, "tasks")) == before  # rien publié tant que rien ne change
    j._tasks["t1"]["started"] = time.time() - 120
    j._tasks_tick()
    j._tasks_tick()
    assert of(published, "tasks")[-1]["items"][0]["alert"] is True
    assert len([n for n in of(published, "notice") if n.get("task_alert")]) == 1


def test_seuils_par_defaut_si_config_ne_les_a_pas(monkeypatch):
    monkeypatch.delattr(config, "AGENT_ALERT_TOKENS", raising=False)
    monkeypatch.delattr(config, "AGENT_ALERT_S", raising=False)
    assert core._alert_limits() == (1_000_000, 3600.0)  # 04/11 (T50) : tokens de travail


# ------------------------------------------------------------------ bouton Stop d'une tâche

def test_stop_d_une_tache(astrel, published):
    j, _, _ = astrel
    done = threading.Event()
    stopped = []
    j.brain.stop_task = lambda tid: stopped.append(tid) or done.set() or True
    j._on_task(task(phase="start", desc="Agent perdu"))
    # Le HUD écrase « id » par celui de sa requête : « task_id » prime.
    assert j.on_command({"cmd": "stop_task", "task_id": "t1", "id": 4}) == {"ok": True, "task_id": "t1"}
    assert done.wait(2) and stopped == ["t1"]
    assert not j.on_command({"cmd": "stop_task", "task_id": "inconnue"})["ok"]
    assert not j.on_command({"cmd": "stop_task"})["ok"]


# ------------------------------------------------------------------ quota

Q = lambda u5, reset5, u7=0.1, reset7=None: {  # noqa: E731
    "status": "allowed", "type": "five_hour", "resets_at": reset5,
    "windows": {"five_hour": {"utilization": u5, "resetsAt": reset5},
                "seven_day": {"utilization": u7, "resetsAt": reset7 or reset5 + 5 * 86400}}}


def test_fenetres_en_liste():
    assert core.quota_windows(Q(0.44, 1790458200)) == [
        {"name": "five_hour", "pct": 44, "utilization": 0.44, "resetsAt": 1790458200},
        {"name": "seven_day", "pct": 10, "utilization": 0.1, "resetsAt": 1790458200 + 5 * 86400}]
    assert core.quota_windows({"windows": {"five_hour": {"utilization": 63}}})[0]["pct"] == 63
    assert core.quota_windows({}) == [] and core.quota_windows({"windows": {"x": {"utilization": None}}}) == []


def test_prevision_au_rythme_observe():
    now = time.mktime((2026, 10, 15, 15, 0, 0, 0, 0, -1))
    trend = core.QuotaTrend()
    trend.observe(Q(0.40, now + 4 * 3600), now - 3600)
    assert trend.forecast(now - 3600) == ""  # une seule mesure : rien
    trend.observe(Q(0.60, now + 4 * 3600), now)
    # +20 points par heure, 40 restants : limite dans 2 h → 17 h 00
    assert trend.forecast(now) == "limite vers 17 h 00"


def test_pas_de_prevision_sans_donnees_fiables():
    now = time.mktime((2026, 10, 15, 15, 0, 0, 0, 0, -1))
    trend = core.QuotaTrend()
    trend.observe(Q(0.40, now + 4 * 3600), now - 120)
    trend.observe(Q(0.45, now + 4 * 3600), now)
    assert trend.forecast(now) == ""  # 2 min d'observation : trop court
    slow = core.QuotaTrend()
    slow.observe(Q(0.40, now + 3600), now - 3600)
    slow.observe(Q(0.41, now + 3600), now)
    assert slow.forecast(now) == ""  # hausse sous la précision du CLI
    late = core.QuotaTrend()
    late.observe(Q(0.10, now + 1800), now - 3600)
    late.observe(Q(0.30, now + 1800), now)
    assert late.forecast(now) == ""  # la fenêtre se vide avant la limite


def test_nouvelle_fenetre_efface_le_rythme():
    now = time.mktime((2026, 10, 15, 15, 0, 0, 0, 0, -1))
    trend = core.QuotaTrend()
    trend.observe(Q(0.10, now), now - 3600)
    trend.observe(Q(0.60, now + 5 * 3600), now)  # réinitialisée entre-temps
    assert trend.forecast(now) == ""


def test_prevision_un_autre_jour():
    now = time.mktime((2026, 10, 15, 23, 0, 0, 0, 0, -1))
    trend = core.QuotaTrend()
    trend.observe(Q(0.50, now + 4 * 3600), now - 3600)
    trend.observe(Q(0.70, now + 4 * 3600), now)
    assert trend.forecast(now) == "limite vers vendredi 0 h 30"


def test_evenement_quota_du_coeur(astrel, published):
    j, _, _ = astrel
    reset = time.time() + 4 * 3600
    j._guard_quota = lambda q: None
    data = Q(0.30, reset)
    j._on_brain_event(core.BrainEvent("quota", data=data))
    ev = of(published, "quota")[-1]
    assert ev["forecast"] == "" and [w["name"] for w in ev["windows"]] == ["five_hour", "seven_day"]
    assert ev["limit_type"] == "five_hour"  # « type » du CLI n'écrase plus le type de l'événement
    assert isinstance(data["windows"], dict)  # le format du CLI reste pour brain.quota (réflexes)


def test_ligne_de_cout_a_la_fin_d_un_agent(astrel, published):
    """04/11 (T50) : un sous-agent fini laisse une ligne dans logs/agents-couts.jsonl (rendement par métier)."""
    import json
    from astrel.coeur import taches as taches_coeur
    j, _, _ = astrel
    j._agent_log["toolu_X"] = {"type": "codeur", "model": "opus", "effort": "high",
                               "depots": [{"outil": "passation", "mission": "T50", "de": "codeur", "a": "testeur",
                                           "ok": True, "titre": "x"}]}
    j._on_task(task(phase="start", desc="Coder T50", tool_use_id="toolu_X"))
    j._on_task(task(tokens=9_000_000, travail=300_000, cache_lu=8_700_000, requests=30, estime=False))
    j._on_task(task(phase="end", status="completed"))
    j._on_task(task("t2", phase="start", kind="local_bash", desc="commande"))
    j._on_task(task("t2", phase="end", kind="local_bash", status="completed"))
    lignes = [json.loads(l) for l in taches_coeur.COUTS_LOG.read_text().splitlines()]
    assert len(lignes) == 1  # une commande de fond n'est pas un agent
    l = lignes[0]
    assert (l["type"], l["modele_demande"], l["statut"]) == ("codeur", "opus", "completed")
    assert (l["travail"], l["cache_lu"], l["total"], l["requetes"]) == (300_000, 8_700_000, 9_000_000, 30)
    assert l["estime"] is False and l["depots"] == [{"outil": "passation", "mission": "T50", "de": "codeur",
                                                     "a": "testeur", "ok": True}]
