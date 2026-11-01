"""Effort de réflexion (23/10, plan P28, demande de l'utilisateur) : celui du cerveau changé À CHAUD
(control_request « apply_flag_settings » {effortLevel}, sondée sur le CLI 2.1.285 au lot 0), et
celui des agents réglé par métier depuis la fiche d'agent (`agent_effort`, `runtime/agent-effort.json`,
rechargé à chaud par un nouvel `initialize {agents}`). Claude ne change jamais l'effort."""
import json
import threading

import pytest

from astrel import agents, config, core, detail_agent, events, fournisseurs
from astrel.brain import Brain, BrainEvent
from astrel.events import bus
from astrel.settings import reglages
from test_model import FakeProc, brain_answering, make_astrel


@pytest.fixture
def published():
    out: list = []
    unsub = bus.subscribe(out.append)
    yield out
    unsub()


def of(evs, kind):
    return [e for e in evs if e["type"] == kind]


def astrel_with(response: dict, model: str = "sonnet"):
    b, sent = brain_answering(response)
    b.model = model
    j = make_astrel(b)
    return j, b, sent


# ------------------------------------------------------------------ cerveau : Brain.set_effort

def test_set_effort_a_chaud():
    b, sent = brain_answering({"subtype": "success"})
    b.set_effort("xhigh")
    assert sent[0]["type"] == "control_request"
    assert sent[0]["request"] == {"subtype": "apply_flag_settings", "settings": {"effortLevel": "xhigh"}}
    assert not b._control_waiters


def test_set_effort_reglage_du_cli_jamais_null():
    """« » (Réglage du CLI) : jamais `null` (sonde du 23/10 : « medium » à chaud, alors qu'Opus
    démarre en « high » sans --effort). Rien reçu : rien à faire ; un niveau reçu : relance exigée."""
    b, sent = brain_answering({"subtype": "success"})
    b.effort_sent = ""
    b.set_effort("")
    assert sent == []
    b.effort_sent = "high"
    with pytest.raises(RuntimeError, match="sans relance"):
        b.set_effort("")
    assert sent == [] and b.effort_sent == "high"
    b.effort_sent = None  # inconnu : relance exigée aussi
    with pytest.raises(RuntimeError):
        b.set_effort("")
    assert sent == []


def test_set_effort_retient_ce_qui_est_envoye():
    b, _ = brain_answering({"subtype": "success"})
    b.set_effort("low")
    assert b.effort_sent == "low"
    b2, _ = brain_answering({"subtype": "error", "error": "non"})
    b2.effort_sent = "high"
    with pytest.raises(RuntimeError):
        b2.set_effort("low")
    assert b2.effort_sent == "high"


def test_reglage_du_cli_relance_hors_tache(published):
    """Retour à « Réglage du CLI » après un niveau reçu : relance --resume (sans --effort) hors
    tâche ; pendant une tâche, rien n'est relancé et la notice le dit."""
    j, b, sent = astrel_with({"subtype": "success"})
    b.effort_sent = "high"
    restarts: list = []
    b.restart = lambda fresh=False: restarts.append(fresh)
    j._tasks = {"t1": {}}
    assert j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": ""})["ok"]
    assert sent == [] and restarts == []
    assert any("pris au prochain démarrage du cerveau" in n["text"] for n in of(published, "notice"))
    reglages.set("brain_effort", "high")
    j._tasks = {}
    assert j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": ""})["ok"]
    assert sent == [] and restarts == [False]


def test_set_effort_niveau_inconnu_jamais_envoye():
    """Le CLI accepte un niveau inconnu SANS le prendre : refusé ici, rien n'est envoyé."""
    b, sent = brain_answering({"subtype": "success"})
    with pytest.raises(ValueError):
        b.set_effort("nimp")
    assert sent == []


def test_set_effort_refuse_par_le_cli():
    b, _ = brain_answering({"subtype": "error", "error": "effort changes are turned off"})
    with pytest.raises(RuntimeError, match="turned off"):
        b.set_effort("low")


def test_set_effort_cerveau_arrete():
    Brain().set_effort("low")  # rien à envoyer : pris au prochain démarrage (--effort)


# ------------------------------------------------------------------ cœur : réglage brain_effort

def test_reglage_brain_effort_applique_a_chaud(published):
    j, b, sent = astrel_with({"subtype": "success"})
    r = j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "max"})
    assert r["ok"] and reglages.value("brain_effort") == "max"
    assert [m["request"] for m in sent] == [{"subtype": "apply_flag_settings", "settings": {"effortLevel": "max"}}]
    ev = of(published, "effort")[-1]
    assert ev["active"] == "max" and ev["reason"] == "" and ev["levels"] == list(agents.EFFORT_LEVELS)
    assert any("Effort de réflexion : Maximal" in n["text"] for n in of(published, "notice"))
    assert "effort" in events.STICKY


def test_meme_valeur_rien_envoye():
    j, b, sent = astrel_with({"subtype": "success"})
    j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "medium"})
    sent.clear()
    j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "medium"})
    assert sent == []


def test_reglage_refuse_a_claude(published):
    j, b, sent = astrel_with({"subtype": "success"})
    r = j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "max", "_from_claude": True})
    assert r["ok"] is False and sent == [] and reglages.value("brain_effort") != "max"


def test_haiku_retenu_sans_envoi(published):
    j, b, sent = astrel_with({"subtype": "success"}, model="haiku")
    assert j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "low"})["ok"]
    assert sent == [] and reglages.value("brain_effort") == "low"
    assert of(published, "effort")[-1]["reason"] == "haiku"
    assert any("Haiku n'a pas de réglage d'effort" in n["text"] for n in of(published, "notice"))


def test_autre_fournisseur_refuse():
    j, b, sent = astrel_with({"subtype": "success"})
    b.fournisseur = fournisseurs.Effectif("passerelle", "openai:gpt-5")
    r = j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "max"})
    assert r["ok"] is False and "qu'à Claude" in r["error"] and sent == []


def test_refus_a_chaud_pendant_une_tache_pas_de_relance(published):
    j, b, _ = astrel_with({"subtype": "error", "error": "non"})
    restarts: list = []
    b.restart = lambda fresh=False: restarts.append(fresh)
    j._tasks = {"t1": {}}
    j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "low"})
    assert restarts == []
    assert any("pris au prochain démarrage du cerveau" in n["text"] for n in of(published, "notice"))
    j._tasks = {}
    j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "medium"})
    assert restarts == [False]  # reprise de la session (--resume), pas remise à zéro


def test_effort_publie_avec_le_modele(published):
    j, b, _ = astrel_with({"subtype": "success"}, model="haiku")
    j._publish_model()
    assert of(published, "effort")[-1]["reason"] == "haiku"


@pytest.fixture
def model_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_FILE", tmp_path / "model.txt")


def test_sortie_de_haiku_renvoie_l_effort_choisi(published, model_file):
    """Relecture P28 : choisi pendant Haiku (non envoyé), l'effort part avec le passage à Sonnet."""
    j, b, sent = astrel_with({"subtype": "success"}, model="haiku")
    b.effort_sent = "high"  # reçu au démarrage (--effort high)
    j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "low"})
    assert sent == []
    assert j.on_command({"cmd": "model sonnet"})["ok"]
    assert [m["request"] for m in sent] == [
        {"subtype": "set_model", "model": "sonnet"},
        {"subtype": "apply_flag_settings", "settings": {"effortLevel": "low"}}]
    assert b.effort_sent == "low" and of(published, "effort")[-1]["reason"] == ""
    sent.clear()
    assert j.on_command({"cmd": "model opus"})["ok"]  # hors Haiku : le CLI garde son niveau
    assert [m["request"]["subtype"] for m in sent] == ["set_model"]


def test_sortie_de_haiku_rien_si_deja_recu(model_file):
    j, b, sent = astrel_with({"subtype": "success"}, model="haiku")
    b.effort_sent = str(reglages.value("brain_effort"))
    assert j.on_command({"cmd": "model sonnet"})["ok"]
    assert [m["request"]["subtype"] for m in sent] == ["set_model"]


def test_sortie_de_haiku_effort_impose_par_la_variable(monkeypatch, model_file):
    monkeypatch.setenv("ASTREL_CLAUDE_EFFORT", "max")
    j, b, sent = astrel_with({"subtype": "success"}, model="haiku")
    b.effort_sent = None
    assert j.on_command({"cmd": "model sonnet"})["ok"]
    assert [m["request"]["subtype"] for m in sent] == ["set_model"]


def test_sortie_de_haiku_reglage_du_cli_relance_hors_tache(model_file):
    """« Réglage du CLI » choisi pendant Haiku après un niveau reçu : pas de `null`, relance."""
    j, b, sent = astrel_with({"subtype": "success"}, model="haiku")
    b.effort_sent = "high"
    restarts: list = []
    b.restart = lambda fresh=False: restarts.append(fresh)
    j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": ""})
    assert j.on_command({"cmd": "model sonnet"})["ok"]
    assert [m["request"]["subtype"] for m in sent] == ["set_model"] and restarts == [False]


def test_sortie_de_haiku_par_relance_rien_de_plus(model_file):
    """set_model refusé : la relance (--resume) prend --effort des réglages, rien à renvoyer."""
    j, b, sent = astrel_with({"subtype": "error", "error": "non"}, model="haiku")
    restarts: list = []
    b.restart = lambda fresh=False: restarts.append(fresh)
    j.on_command({"cmd": "settings_set", "key": "brain_effort", "value": "low"})
    assert j.on_command({"cmd": "model sonnet"})["ok"]
    assert [m["request"]["subtype"] for m in sent] == ["set_model"] and restarts == [False]


# ------------------------------------------------------------------ agents : effort par métier

def test_effort_par_metier_ecrit_et_applique():
    assert agents.load_agents()["codeur"]["effort"] == "high"  # en-tête de config/agents/codeur.md
    assert agents.set_agent_effort("codeur", "xhigh") == "xhigh"
    assert json.loads(agents.EFFORT_FILE.read_text(encoding="utf-8")) == {"codeur": "xhigh"}
    assert agents.load_agents()["codeur"]["effort"] == "xhigh"
    assert agents.load_agents(efforts=False)["codeur"]["effort"] == "high"
    data = json.loads(agents.write_agents().read_text(encoding="utf-8"))
    assert data["codeur"]["effort"] == "xhigh"
    # retour à l'en-tête : le choix est oublié
    assert agents.set_agent_effort("codeur", "high") == "high"
    assert json.loads(agents.EFFORT_FILE.read_text(encoding="utf-8")) == {}


def test_effort_par_metier_refus():
    with pytest.raises(ValueError, match="agent inconnu"):
        agents.set_agent_effort("general-purpose", "max")
    with pytest.raises(ValueError, match="niveau"):
        agents.set_agent_effort("codeur", "nimp")
    assert not agents.EFFORT_FILE.exists()


def test_fichier_abime_ou_niveau_inconnu_ignore():
    agents.EFFORT_FILE.write_text('{"codeur": "nimp", "testeur": "low"}', encoding="utf-8")
    assert agents.chosen_efforts() == {"testeur": "low"}
    agents.EFFORT_FILE.write_text("pas du json", encoding="utf-8")
    assert agents.chosen_efforts() == {} and agents.load_agents()["codeur"]["effort"] == "high"


def test_applique_suit_ce_que_le_cli_a_recu():
    agents.write_agents()
    agents.mark_loaded()
    assert agents.effort_info("codeur")["applique"] is True
    agents.set_agent_effort("codeur", "max")
    info = agents.effort_info("codeur")
    assert info["effort_next"] == "max" and info["applique"] is False and info["settable"]
    assert agents.effort_at_launch("codeur") == "high"  # le CLI en marche a encore l'ancienne définition
    agents.write_agents()
    agents.mark_loaded()
    assert agents.effort_info("codeur")["applique"] is True and agents.effort_at_launch("codeur") == "max"
    assert agents.effort_info("chercheur")["haiku"] is True
    assert agents.effort_info("Explore")["settable"] is False


def test_reload_agents_initialize_a_chaud():
    b, sent = brain_answering({"subtype": "success"})
    agents.set_agent_effort("testeur", "max")
    assert b.reload_agents() is True
    req = sent[0]["request"]
    assert req["subtype"] == "initialize" and req["agents"]["testeur"]["effort"] == "max"
    assert set(req) == {"subtype", "agents"}  # ni hooks ni prompt : le reste du process ne change pas
    assert agents.effort_info("testeur")["applique"] is True


def test_reload_agents_refuse_reste_au_prochain_demarrage():
    b, _ = brain_answering({"subtype": "error", "error": "non"})
    agents.set_agent_effort("testeur", "low")
    assert b.reload_agents() is False
    assert json.loads(agents.AGENTS_FILE.read_text(encoding="utf-8"))["testeur"]["effort"] == "low"
    assert agents.effort_info("testeur")["applique"] is False


def test_commande_agent_effort(published):
    j, b, sent = astrel_with({"subtype": "success"})
    r = j.on_command({"cmd": "agent_effort", "agent": "codeur", "effort": "max"})
    assert r["ok"] and r["effort"] == "max" and r["applique"] is True and r["effort_next"] == "max"
    assert sent[-1]["request"]["subtype"] == "initialize"
    assert any("Effort de l'agent codeur : Maximal, pour ses prochains lancements." == n["text"]
               for n in of(published, "notice"))
    # terminal : --send "agent_effort testeur low"
    assert j.on_command({"cmd": "agent_effort", "text": "testeur low"})["effort"] == "low"
    assert j.on_command({"cmd": "agent_effort", "agent": "inconnu", "effort": "low"})["ok"] is False


def test_commande_agent_effort_niveau_exige():
    """Relecture P28 : sans niveau (clé absente, null, terminal « agent_effort codeur »), erreur
    claire et rien d'écrit ; « » (HUD) et « defaut » (terminal) reviennent à l'en-tête."""
    j, b, sent = astrel_with({"subtype": "success"})
    for cmd in ({"cmd": "agent_effort", "agent": "codeur"},
                {"cmd": "agent_effort", "agent": "codeur", "effort": None},
                {"cmd": "agent_effort", "text": "codeur"},
                {"cmd": "agent_effort codeur"}):
        r = j.on_command(cmd)
        assert r["ok"] is False and "niveau d'effort manquant pour l'agent codeur" in r["error"], cmd
        assert "defaut" in r["error"]
    assert sent == [] and not agents.EFFORT_FILE.exists()
    assert j.on_command({"cmd": "agent_effort", "agent": "codeur", "effort": "max"})["effort"] == "max"
    r = j.on_command({"cmd": "agent_effort", "agent": "codeur", "effort": ""})
    assert r["ok"] and r["effort"] == "high"  # en-tête de config/agents/codeur.md
    j.on_command({"cmd": "agent_effort codeur max"})
    r = j.on_command({"cmd": "agent_effort codeur defaut"})
    assert r["ok"] and r["effort"] == "high"
    assert json.loads(agents.EFFORT_FILE.read_text(encoding="utf-8")) == {}


def test_effort_par_metier_ecritures_paralleles():
    """Verrou de agent-effort.json : deux métiers réglés en même temps, aucun choix perdu."""
    names = ["codeur", "testeur", "relecteur", "planificateur"]
    threads = [threading.Thread(target=agents.set_agent_effort, args=(n, "max")) for n in names * 5]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert agents.chosen_efforts() == {n: "max" for n in names}


def test_commande_agent_effort_refusee_a_claude():
    j, b, sent = astrel_with({"subtype": "success"})
    r = j.on_command({"cmd": "agent_effort", "agent": "codeur", "effort": "max", "_from_claude": True})
    assert r == {"ok": False, "error": "hud_only"} and sent == [] and not agents.EFFORT_FILE.exists()


def test_commande_agent_effort_cerveau_arrete():
    j = make_astrel(Brain())
    r = j.on_command({"cmd": "agent_effort", "agent": "codeur", "effort": "low"})
    assert r["ok"] and r["applique"] is False  # pris au prochain démarrage du cerveau


# ------------------------------------------------------------------ fiche d'agent (task_detail)

@pytest.fixture
def astrel_detail(monkeypatch, tmp_path):
    from astrel import equipe
    monkeypatch.setattr(equipe, "EQUIPE_DIR", tmp_path / "equipe")
    from test_reports import make_astrel as make_full
    j, _ = make_full(monkeypatch)
    j.brain.send = lambda *a, **k: None
    j._send_lock = threading.Lock()
    return j


def test_fiche_effort_au_lancement_et_prochains(astrel_detail):
    j = astrel_detail
    agents.write_agents()
    agents.mark_loaded()
    j._on_brain_event(BrainEvent("tool_start", tool="Agent", tool_id="tu1", text="Codeur",
                                 data={"input": {"prompt": "Code.", "subagent_type": "codeur"}}))
    j._on_task({"id": "t1", "phase": "start", "kind": "agent", "desc": "Codeur", "tool_use_id": "tu1"})
    d = j.on_command({"cmd": "task_detail", "task_id": "t1"})
    assert d["effort"] == "high" and d["effort_next"] == "high" and d["effort_settable"] and d["applique"]
    assert d["effort_levels"] == list(agents.EFFORT_LEVELS)
    agents.set_agent_effort("codeur", "max")  # changé pendant qu'il tourne : il garde le sien
    d = j.on_command({"cmd": "task_detail", "task_id": "t1"})
    assert d["effort"] == "high" and d["effort_next"] == "max" and d["applique"] is False


def test_fiche_agent_du_cli_non_reglable(astrel_detail):
    j = astrel_detail
    j._on_brain_event(BrainEvent("tool_start", tool="Agent", tool_id="tu2", text="Explorer",
                                 data={"input": {"prompt": "Cherche.", "subagent_type": "Explore"}}))
    j._on_task({"id": "t2", "phase": "start", "kind": "agent", "desc": "Explorer", "tool_use_id": "tu2"})
    d = j.on_command({"cmd": "task_detail", "task_id": "t2"})
    assert d["effort_settable"] is False and d["effort"] == ""


def test_fiche_sans_effort_toujours_sous_six_ko():
    big = {"id": "t", "desc": "x" * 5000}
    d = detail_agent.assemble(big, {"consigne": "y" * 9000, "type": "codeur"}, None, 0.0, True,
                              effort={"effort": "high", "effort_next": "max", "settable": True, "levels": ["low"] * 50})
    assert len(json.dumps(d, ensure_ascii=False).encode()) <= detail_agent.MAX_BYTES
    assert d["effort_next"] == "max" and len(d["effort_levels"]) == 8


# ------------------------------------------------------------------ geste HOLO retiré avec le graphe

def test_geste_poing_retire():
    src = (config.ROOT / "astrel" / "senses" / "holo.py").read_text(encoding="utf-8")
    assert "Closed_Fist" not in src and '"rotate"' not in src


def test_reglage_inconnu_toujours_refuse_proprement():
    j, b, sent = astrel_with({"subtype": "success"})
    r = j.on_command({"cmd": "settings_set", "key": "n_existe_pas", "value": 1})
    assert r["ok"] is False and "inconnu" in r["error"] and sent == []
