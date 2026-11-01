"""Plan P8, lot 3 : mode léger (modèle local par Ollama). La sûreté vient de la CONSTRUCTION
de la commande (aucun outil, aucun MCP, ni agents ni contournement des permissions), pas du
garde-fou ; le cœur refuse la bascule à Claude, pendant une tâche, avec Autonome TOTAL ou la
levée ; il gèle ce qui demande des outils et revient sur Claude à la réinitialisation."""
import chemins_machine  # chemins de la machine qui lance la suite
import json
import queue
import shutil
import time

import pytest

from astrel import brain, config, core, modeles_locaux, reprise
from astrel.brain import Brain, ThinkFilter, local_command, local_env
from astrel.mission import mission
from faux_ollama import FauxOllama
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

FORBIDDEN = ("--mcp-config", "--agents", "--dangerously-skip-permissions", "--allowedTools", "--allowed-tools",
             "--permission-mode", "--settings", "--add-dir", "--resume", "--append-system-prompt",
             "--plugin-dir", "--mcp-debug")


def assert_sure(cmd: list[str]) -> None:
    """ÉCHOUE si la commande locale donne un outil, un serveur MCP, des agents, ou contourne les permissions."""
    for flag in FORBIDDEN:
        assert flag not in cmd, flag
    assert not [a for a in cmd if "mcp__" in a or a.startswith("--dangerously")]
    i = cmd.index("--tools")
    assert cmd[i + 1] == "" and (i + 2 == len(cmd) or cmd[i + 2].startswith("--")), "un outil est nommé"
    assert cmd.count("--tools") == 1 and "--strict-mcp-config" in cmd and "--bare" in cmd


def test_commande_locale_sans_outil_ni_mcp_ni_agents():
    cmd = local_command("qwen3.5:4b", "00000000-0000-4000-8000-000000000000")
    assert_sure(cmd)
    assert cmd[cmd.index("--model") + 1] == "qwen3.5:4b"
    assert cmd[cmd.index("--system-prompt") + 1] == brain.LOCAL_SYSTEM_PROMPT
    with pytest.raises(AssertionError):  # le contrôle lui-même attrape un outil glissé
        assert_sure(cmd[:cmd.index("--tools") + 2] + ["Bash"] + cmd[cmd.index("--tools") + 2:])
    with pytest.raises(AssertionError):
        assert_sure(cmd + ["--mcp-config", "x.json"])


def test_invite_systeme_de_astrel_intacte():
    assert chemins_machine.longueur_type(brain.VOICE_SYSTEM_PROMPT) <= 10_800  # plafond de l'utilisateur, non touché par P8
    assert len(brain.LOCAL_SYSTEM_PROMPT) < 1200 and "AUCUN outil" in brain.LOCAL_SYSTEM_PROMPT


def test_demarrage_local_ignore_mcp_agents_et_session_claude(tmp_path, monkeypatch):
    mcp = tmp_path / "mcp.json"
    mcp.write_text("{}")
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"id": "11111111-1111-4111-8111-111111111111", "at": time.time()}))
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", mcp)
    monkeypatch.setattr(config, "SESSION_FILE", session)
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "jeton-de-la-machine")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ailleurs.example")
    seen = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen.update(cmd=cmd, env=kw["env"])
            self.stdout, self.stderr, self.stdin = iter(()), iter(()), None

        def poll(self):
            return None

    monkeypatch.setattr(brain.subprocess, "Popen", FakePopen)
    b = Brain()
    b.local = "astrel-qwen3.5-4b"
    b.start(fresh=True)
    assert_sure(seen["cmd"])
    env = seen["env"]
    assert env["ANTHROPIC_BASE_URL"] == config.OLLAMA_URL and env["ANTHROPIC_API_KEY"] == "ollama"
    assert "ANTHROPIC_AUTH_TOKEN" not in env and env.get("ASTREL_BRAIN")  # marqueur du cerveau gardé
    assert session.exists()  # la session de Claude reste à reprendre
    b.session_id = "22222222-2222-4222-8222-222222222222"
    b._remember_session()
    assert json.loads(session.read_text())["id"].startswith("1111")  # jamais écrasée par le local
    b.local = ""
    b.start()  # retour sur Claude : reprise de SA session
    assert seen["cmd"][seen["cmd"].index("--resume") + 1].startswith("1111")
    env = local_env("x", {"ANTHROPIC_API_KEY": "sk-vrai", "ANTHROPIC_AUTH_TOKEN": "t", "CLAUDE_CODE_USE_BEDROCK": "1"})
    assert env["ANTHROPIC_API_KEY"] == "ollama" and "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env  # jamais la vraie clé ni un autre fournisseur


def test_reflexion_jamais_dite():
    f = ThinkFilter()
    parts = ["Bon", "jour <thi", "nk>je réfléchis", " encore</th", "ink> à toi."]
    assert "".join(f.feed(p) for p in parts) == "Bonjour  à toi."
    assert brain.strip_think("<think>x</think>Salut") == "Salut"
    b = Brain()
    b.local = "qwen3.5:4b"
    b._events = queue.Queue()
    for ev in ({"type": "stream_event", "event": {"type": "content_block_delta",
                                                    "delta": {"type": "thinking_delta", "thinking": "hmm"}}},
               {"type": "stream_event", "event": {"type": "content_block_delta",
                                                    "delta": {"type": "text_delta", "text": "<think>a</think>Oui."}}},
               {"type": "result", "result": "<think>a</think>Oui."}):
        b._events.put(ev)
    b.send = lambda *a, **k: None
    out = list(b.turn("?"))
    assert [e.text for e in out if e.kind == "text"] == ["Oui."] and out[-1].text == "Oui."


@pytest.mark.skipif(not shutil.which("claude"), reason="CLI claude absent")
def test_vrai_cli_contre_faux_ollama(monkeypatch, tmp_path):
    """Bout à bout : le VRAI CLI, lancé comme le fait Astrel en mode léger, parle au faux
    Ollama (/v1/messages) ; aucun outil annoncé, aucun quota de l'abonnement."""
    with FauxOllama(reply="Bonjour depuis le faux Ollama.") as srv:
        monkeypatch.setattr(config, "OLLAMA_URL", srv.url)
        monkeypatch.setattr(config, "WORKDIR", tmp_path)
        # Rien dans ~/.claude (disque racine presque plein, règle de l'utilisateur) : config du CLI à part.
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
        b = Brain()
        b.local = "faux-modele"
        b.start()
        try:
            events = []
            for ev in b.turn("Dis bonjour."):
                events.append(ev)
                if len(events) > 200:
                    break
        finally:
            b.stop()
    init = next(e for e in events if e.kind == "init").data
    assert init["tools"] == [] and init["mcp_servers"] == []
    assert "Bonjour depuis le faux Ollama." in "".join(e.text for e in events if e.kind == "text")
    assert events[-1].kind == "turn_end" and not [e for e in events if e.kind == "quota"]
    posted = [r for r in srv.requests if r[1].startswith("/v1/messages")]
    assert posted and all(r[2].get("model") == "faux-modele" and not r[2].get("tools") for r in posted)


# ------------------------------------------------------------------ le cœur

@pytest.fixture
def leger(astrel, monkeypatch):
    """Un Astrel de test, un modèle local « prêt », des relances de cerveau notées."""
    j, _, _ = astrel
    restarts = []
    monkeypatch.setattr(core.Brain, "restart", lambda self, fresh=False: restarts.append((self.local, fresh)))
    monkeypatch.setattr(modeles_locaux.local, "ready", lambda: "qwen3.5:4b")
    monkeypatch.setattr(modeles_locaux.local, "prepare", lambda tag: "astrel-qwen3.5-4b")
    modeles_locaux.local._save(model="qwen3.5:4b")
    return j, restarts


def test_bascule_et_retour(leger, published):
    j, restarts = leger
    j.brain.model = "sonnet"
    assert j._local_switch(True)["ok"] and j.brain.local == "astrel-qwen3.5-4b"
    assert restarts == [("astrel-qwen3.5-4b", True)] and modeles_locaux.local.active
    assert of(published, "model")[-1]["label"] == "Mode léger · qwen3.5:4b"
    assert j._local_switch(False)["ok"] and j.brain.local == "" and restarts[-1] == ("", False)
    assert not modeles_locaux.local.active and of(published, "model")[-1]["label"] == "Sonnet"


def test_jamais_pendant_une_tache_ni_avec_total_ou_levee(leger):
    j, restarts = leger
    j._tasks["t1"] = {"id": "t1"}
    assert not j._local_switch(True)["ok"]
    j._tasks.clear()
    j._total = True
    assert "Sans confirmation" in j._local_switch(True)["error"]
    j._total = False
    j._lift = True
    assert not j._local_switch(True)["ok"]
    j._lift = False
    assert restarts == [] and j._local_switch(True)["ok"]
    assert not j.set_total(True, "HUD")["ok"] and j._total is False
    assert not j.set_lift(True, "HUD")["ok"] and j._lift is False


def test_refuse_a_claude(leger, published):
    j, restarts = leger
    assert not j.on_command({"cmd": "local on", "_from_claude": True})["ok"]
    assert not j.on_command({"cmd": "local_on", "_from_claude": True})["ok"]
    res = j.on_command({"cmd": "settings_action", "key": "local.mode", "action": "local_on", "_from_claude": True})
    assert res["ok"] is False and restarts == []
    assert j.on_command({"cmd": "local status"})["local"] is False
    assert j.on_command({"cmd": "local on"})["ok"] and j._local_active()


def test_reflexes_plans_gele_puis_relances(leger, monkeypatch):
    j, _ = leger
    j._local_switch(True)
    sent = []
    j.brain.send = lambda prompt, images=None: sent.append(prompt)
    j.handle("briefing du soir", source="réflexe")
    j.handle("Plan P9 validé", source="plan")
    j.handle("sauvegarde", source="relève")
    assert sent == [] and len(j._local_frozen) == 2
    j._local_tick()
    assert sent == []  # toujours en mode léger
    j.handle("salut", source="clavier")
    assert sent and sent[-1].endswith("salut")  # l'utilisateur parle au modèle local
    j._end_turn(followup=False)
    j._local_switch(False)
    j._local_tick()
    assert "briefing du soir" in sent[-1] and len(j._local_frozen) == 1


def test_mission_gelee_en_mode_leger(leger, monkeypatch):
    j, _ = leger
    j._local_switch(True)
    monkeypatch.setattr(type(mission), "active", property(lambda self: True))
    monkeypatch.setattr(mission, "take_prompt", lambda: pytest.fail("mission relancée en mode léger"))
    j._mission_tick()


def test_retour_sur_claude_a_la_reinitialisation(leger, monkeypatch):
    j, restarts = leger
    j._limit_reached(time.time() + 3600, "cinq_heures")
    j._local_switch(True)
    j._begin_turn("voix")
    j._on_stream(core.BrainEvent("turn_end", text="ok"))
    assert reprise.state.info().get("pending")  # parler au modèle local ne l'annule pas
    sent = []
    monkeypatch.setattr(j, "handle", lambda text, source="voix", **k: sent.append((text, source, j.brain.local)))
    d = reprise.state.info()
    d["reset_at"] = time.time() - config.REPRISE_DELAY_S - 1
    config.REPRISE_FILE.write_text(json.dumps(d), encoding="utf-8")
    j._reprise_tick()
    time.sleep(0.1)
    assert restarts[-1] == ("", False) and not modeles_locaux.local.active
    assert sent == [(reprise.PROMPT, "réflexe", "")]  # la reprise part vers Claude, pas vers le local


def test_changer_de_modele_claude_en_mode_leger(leger, monkeypatch, tmp_path):
    j, _ = leger
    monkeypatch.setattr(config, "MODEL_FILE", tmp_path / "model.txt")
    j.brain.model = "sonnet"
    j._local_switch(True)
    monkeypatch.setattr(j.brain, "set_model", lambda m: pytest.fail("set_model envoyé au modèle local"))
    assert j._set_model("opus")["local"] and j.brain.model == "opus"
    assert (tmp_path / "model.txt").read_text().strip() == "opus"


def test_vue_du_reglage(leger):
    j, _ = leger
    view = j._local_view()
    assert [a["id"] for a in view["actions"]] == ["local_on"] and not view["actions"][0].get("disabled")
    j._total = True
    assert j._local_view()["actions"][0]["disabled"]
    j._total = False
    j._local_switch(True)
    view = j._local_view()
    assert view["actions"] == [{"id": "local_off", "label": "Revenir sur Claude"}]
    assert "sans aucun outil" in view["value"]["lines"][0]["text"]


def test_demarrage_reprend_le_mode_leger_seulement_s_il_est_pret(leger, monkeypatch, published):
    j, _ = leger
    modeles_locaux.local.set_active(True, "astrel-qwen3.5-4b")
    j._local_boot()
    assert j.brain.local == "astrel-qwen3.5-4b"
    j.brain.local = ""
    monkeypatch.setattr(modeles_locaux.local, "ready", lambda: "")
    j._local_boot()
    assert j.brain.local == "" and not modeles_locaux.local.active
    assert "démarrage sur Claude" in of(published, "notice")[-1]["text"]


# ------------------------------------------------------------------ relecture du 19/10

def passe_l_heure_de_retour(seconds_ago):
    """Recule l'heure de retour retenue dans runtime/local-model.json."""
    modeles_locaux.local.remember(return_at=time.time() - seconds_ago)


def test_retour_sur_claude_sans_limite_atteinte(leger, monkeypatch):
    """Point 1 : carte à 80 % ou passage à la main, AUCUNE limite enregistrée (ni `_limit_reached`,
    ni reprise.json) : l'heure de réinitialisation du dernier évènement de quota ramène sur Claude."""
    j, restarts = leger
    reset = time.time() + 2 * 3600
    j.brain.quota = {"status": "allowed", "windows": {"five_hour": {"utilization": 81}},
                     "resets_at": int(reset), "reset_at": reset}
    assert j._local_switch(True, by="carte")["ok"]
    assert not reprise.state.info().get("pending")  # aucune limite : pas de reprise en attente
    assert modeles_locaux.local.return_at == pytest.approx(reset)
    j._local_tick()
    assert j._local_active()  # pas encore l'heure
    passe_l_heure_de_retour(5)
    j._local_tick()
    assert j._local_active()  # réinitialisation + REPRISE_DELAY_S pas encore passés
    passe_l_heure_de_retour(config.REPRISE_DELAY_S + 1)
    j._begin_turn("voix")  # l'utilisateur parle au modèle local : on attend la fin du tour
    j._local_tick()
    assert j._local_active()
    j._end_turn(followup=False)
    j._local_tick()
    assert not j._local_active() and restarts[-1] == ("", False) and not modeles_locaux.local.active


def test_heure_de_retour_inconnue_repli_et_relance(leger, monkeypatch):
    """Sans évènement de quota : retour 5 h plus tard ; l'heure survit à une relance, et un mode
    léger retenu d'avant le correctif (sans heure) en reçoit une au démarrage."""
    j, _ = leger
    j.brain.quota = {}
    j._local_switch(True)
    assert modeles_locaux.local.return_at == pytest.approx(time.time() + config.LOCAL_RETURN_FALLBACK_S, abs=5)
    j.brain.local = ""  # relance d'Astrel : le cerveau repart de l'état retenu
    j._local_boot()
    assert j._local_active() and modeles_locaux.local.return_at > time.time()
    modeles_locaux.local.remember(return_at=0)
    j.brain.local = ""
    j._local_boot()
    assert modeles_locaux.local.return_at == pytest.approx(time.time() + config.LOCAL_RETURN_FALLBACK_S, abs=5)


def test_reverifie_apres_la_preparation(leger, monkeypatch):
    """Point 2 : pendant `local.prepare` (jusqu'à 120 s), un tour, une tâche, l'Autonome TOTAL ou
    la levée ne doivent ni être tués ni cohabiter avec le modèle local."""
    j, restarts = leger

    def prepare_puis(action):
        def prepare(tag):
            action()
            return "astrel-qwen3.5-4b"
        monkeypatch.setattr(modeles_locaux.local, "prepare", prepare)

    prepare_puis(lambda: j._begin_turn("voix"))
    assert "tâche" in j._local_switch(True)["error"] and restarts == [] and j.brain.local == ""
    j._end_turn(followup=False)
    prepare_puis(lambda: j._tasks.update(t1={"id": "t1"}))
    assert not j._local_switch(True)["ok"] and restarts == []
    j._tasks.clear()
    tried = {}
    prepare_puis(lambda: tried.update(total=j.set_total(True, "HUD"), lift=j.set_lift(True, "HUD")))
    assert j._local_switch(True)["ok"]
    assert not tried["total"]["ok"] and not tried["lift"]["ok"] and j._total is False and j._lift is False
    assert j._local_switching is False and j._local_switch(False)["ok"]
    # Activé par un autre chemin juste avant la relance : revérifié sous le verrou.
    prepare_puis(lambda: setattr(j, "_total", True))
    assert "Sans confirmation" in j._local_switch(True)["error"] and j.brain.local == ""
    assert j._local_switching is False and j.set_total(False, "HUD")["ok"]


def test_messages_geles_survivent_a_une_relance(leger):
    """Point 3 : un plan validé en mode léger est retenu sur disque, relu au démarrage et
    relancé au retour sur Claude, une seule fois."""
    j, _ = leger
    j._local_switch(True)
    sent = []
    j.brain.send = lambda prompt, images=None: sent.append(prompt)
    j.handle("Plan P9 validé : lance le codeur", source="plan")
    assert json.loads(config.LOCAL_MODEL_FILE.read_text())["frozen"][0]["source"] == "plan"
    j._local_frozen = None  # relance d'Astrel : la mémoire est perdue
    j.brain.local = ""
    j._local_boot()
    assert j._local_active() and len(j._local_frozen) == 1
    j._local_switch(False)
    j._local_tick()
    assert sent and sent[-1].endswith("Plan P9 validé : lance le codeur")
    assert json.loads(config.LOCAL_MODEL_FILE.read_text())["frozen"] == []
    j._end_turn(followup=False)
    j._local_frozen = None
    j._local_boot()
    j._local_tick()
    assert len(sent) == 1  # jamais relancé deux fois
