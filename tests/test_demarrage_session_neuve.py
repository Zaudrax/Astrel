"""Démarrage d'Astrel (19/10, demande de l'utilisateur) : la conversation à reprendre dépasse
`boot_fresh_tokens` → session neuve avec l'amorce de la relève, au lieu de `--resume`."""
import json
import logging
import time

import pytest

from astrel import config, core, relief
from astrel.brain import Brain, saved_context
from test_relief import _FakeBrain, end_turn, make_astrel

SID = "750320ff-1fce-4946-bf2f-a20e2e2d81e4"


@pytest.fixture
def session(tmp_path, monkeypatch):
    f = tmp_path / "session.json"
    monkeypatch.setattr(config, "SESSION_FILE", f)
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "transcriptions")
    monkeypatch.setattr(config, "BOOT_FRESH_TOKENS", 80_000)
    monkeypatch.setattr(config, "BOOT_FRESH_COOLDOWN_S", 600.0)
    return f


def _assistant(ctx, sidechain=False):
    return json.dumps({"type": "assistant", "isSidechain": sidechain, "message": {
        "usage": {"input_tokens": 2, "cache_read_input_tokens": ctx - 2, "cache_creation_input_tokens": 0}}})


# ------------------------------------------------------------------ taille notée par le cerveau

def test_taille_du_contexte_notee_dans_session_json(session):
    b = Brain()
    b._proc = object()  # « process » présent : l'écriture est permise
    b.session_id = SID
    b.context_tokens = 0
    b._remember_session()
    assert "context" not in json.loads(session.read_text())  # pas encore mesurée
    list(b._note_context({"input_tokens": 5, "cache_read_input_tokens": 120_000}))
    saved = json.loads(session.read_text())
    assert saved["id"] == SID and saved["context"] == 120_005
    at = saved["at"]
    time.sleep(0.01)
    list(b._note_context({"input_tokens": 5, "cache_read_input_tokens": 130_000}))
    saved = json.loads(session.read_text())
    assert saved["context"] == 130_005 and saved["at"] == at  # l'heure du dernier échange ne bouge pas
    # Relance qui reprend la session : à l'init le contexte est inconnu (0), la taille notée reste.
    b.context_tokens = 0
    b._remember_session()
    assert json.loads(session.read_text())["context"] == 130_005
    # Session neuve (autre id) : l'ancienne taille ne la suit pas.
    b.session_id = "11111111-1111-4111-8111-111111111111"
    b._remember_session()
    assert "context" not in json.loads(session.read_text())


def test_sans_process_ni_en_mode_leger_rien_n_est_ecrit(session):
    b = Brain()
    b.session_id = SID
    list(b._note_context({"input_tokens": 90_000}))
    assert not session.exists()
    b._proc, b.local = object(), "qwen"
    list(b._note_context({"input_tokens": 95_000}))
    assert not session.exists()


def test_contexte_lu_au_demarrage(session):
    assert saved_context("") is None
    assert saved_context(SID) is None  # rien de noté, pas de transcription
    session.write_text(json.dumps({"id": SID, "at": time.time(), "context": 141_000}))
    assert saved_context(SID) == 141_000
    # Session notée avant le 19/10 (sans « context ») : la transcription du CLI.
    session.write_text(json.dumps({"id": SID, "at": time.time()}))
    config.TRANSCRIPTS_DIR.mkdir()
    (config.TRANSCRIPTS_DIR / f"{SID}.jsonl").write_text(
        "pas du json\n" + _assistant(250_000) + "\n" + _assistant(302_000) + "\n"
        + _assistant(9_000, sidechain=True) + "\n" + json.dumps({"type": "user"}) + "\n", encoding="utf-8")
    assert saved_context(SID) == 302_000  # le dernier message du cerveau, pas celui d'un sous-agent


# ------------------------------------------------------------------ décision

def _decide(caplog, sid, ctx, local=False):
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="astrel.relief"):
        fresh = relief.boot_decision(sid, ctx, local=local)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("démarrage :")]
    assert len(lines) == 1  # UNE ligne par démarrage
    return fresh, lines[0]


def test_decision_reprise_ou_session_neuve(session, caplog):
    fresh, line = _decide(caplog, SID, 79_000)
    assert not fresh and line == "démarrage : contexte repris 79 k, seuil 80 k, décision : reprise"
    assert not config.BOOT_FRESH_FILE.exists()
    fresh, line = _decide(caplog, SID, 141_000)
    assert fresh and line == "démarrage : contexte repris 141 k, seuil 80 k, décision : session neuve"
    assert json.loads(config.BOOT_FRESH_FILE.read_text())["from"] == SID


def test_jamais_de_session_neuve_en_boucle(session, caplog):
    assert _decide(caplog, SID, 302_000)[0]
    # Crash et relance aussitôt, même avec un gros contexte : reprise.
    fresh, line = _decide(caplog, "11111111-1111-4111-8111-111111111111", 95_000)
    assert not fresh and "décision : reprise (anti-boucle" in line
    # Le délai passé, la session neuve redevient possible.
    data = json.loads(config.BOOT_FRESH_FILE.read_text())
    data["at"] = time.time() - 601
    config.BOOT_FRESH_FILE.write_text(json.dumps(data))
    assert _decide(caplog, "11111111-1111-4111-8111-111111111111", 95_000)[0]


def test_marque_non_ecrite_reprise_par_prudence(session, caplog, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BOOT_FRESH_FILE", tmp_path / "absent" / "boot-fresh.json")
    fresh, line = _decide(caplog, SID, 200_000)
    assert not fresh and "décision : reprise (anti-boucle" in line


def test_cas_sans_decision(session, caplog, monkeypatch):
    assert _decide(caplog, "", None) == (False, "démarrage : contexte repris 0 k, seuil 80 k, décision : "
                                                "session neuve (aucune session à reprendre)")
    fresh, line = _decide(caplog, SID, None)
    assert not fresh and line.endswith("décision : reprise (contexte inconnu)")
    fresh, line = _decide(caplog, SID, 200_000, local=True)
    assert not fresh and "mode léger" in line
    monkeypatch.setattr(config, "BOOT_FRESH_TOKENS", 0)  # ASTREL_BOOT_FRESH_TOKENS=0 : toujours reprendre
    fresh, line = _decide(caplog, SID, 900_000)
    assert not fresh and line == "démarrage : contexte repris 900 k, seuil aucun, décision : reprise (seuil désactivé)"
    monkeypatch.setattr(config, "BOOT_FRESH_TOKENS", 300_000)
    assert not _decide(caplog, SID, 200_000)[0]


def test_seuil_par_defaut_et_variable(monkeypatch):
    monkeypatch.delenv("ASTREL_BOOT_FRESH_TOKENS", raising=False)
    assert config._env_float("ASTREL_BOOT_FRESH_TOKENS", 80_000) == 80_000  # décision de l'utilisateur
    monkeypatch.setenv("ASTREL_BOOT_FRESH_TOKENS", "150000")
    assert config._env_float("ASTREL_BOOT_FRESH_TOKENS", 80_000) == 150_000


def test_liste_des_agents_interrompus(session, caplog):
    relief.save_list([{"tid": "t1", "desc": "codeur build", "consigne": "Corrige le build"}])
    fresh, line = _decide(caplog, SID, 141_000)
    assert fresh and "1 tâche(s) interrompue(s) jointe(s) à l'amorce" in line
    assert relief.load_list()  # gardée : `take_amorce` la joint au message suivant


def test_arret_d_urgence_jette_la_liste(session, caplog):
    relief.save_list([{"tid": "t1", "desc": "codeur build", "consigne": "Corrige le build"}])
    config.ESTOP_FILE.write_text("1")
    fresh, line = _decide(caplog, SID, 141_000)
    assert fresh and "arrêt d'urgence" in line
    assert relief.load_list() == []  # rien ne sera relancé


# ------------------------------------------------------------------ le cœur au démarrage

class _BootBrain(_FakeBrain):
    def __init__(self, sid=SID):
        super().__init__(0)
        self.local, self.starts, self._sid = "", [], sid

    def _recent_session(self):
        return self._sid

    def start(self, fresh=False):
        self.starts.append(fresh)


def _boot(monkeypatch):
    j, _ = make_astrel(monkeypatch, tokens=0)
    j.brain = _BootBrain()
    j._boot_brain()
    return j


def test_demarrage_trop_gros_session_neuve_et_amorce(session, monkeypatch):
    session.write_text(json.dumps({"id": SID, "at": time.time(), "context": 302_000}))
    relief.save_list([{"tid": "t1", "desc": "codeur build", "consigne": "Corrige le build"}])
    j = _boot(monkeypatch)
    assert j.brain.starts == [True]
    assert config.RELIEF_AMORCE_FILE.exists()
    # Le premier message de l'utilisateur porte l'amorce et la liste, une seule fois.
    j.handle("On en était où ?", source="clavier")
    sent = j.brain.sent[-1]
    assert sent.startswith("[Nouvelle session : relève automatique") and "Corrige le build" in sent
    assert sent.endswith("On en était où ?")
    end_turn(j, "Je relis la note.")
    j.handle("Merci", source="clavier")
    assert j.brain.sent[-1] == "Merci"


def test_demarrage_sous_le_seuil_reprise_inchangee(session, monkeypatch):
    session.write_text(json.dumps({"id": SID, "at": time.time(), "context": 41_000}))
    j = _boot(monkeypatch)
    assert j.brain.starts == [False]
    assert not config.RELIEF_AMORCE_FILE.exists()


def test_decision_en_panne_reprise(session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(relief, "boot_decision", boom)
    j = _boot(monkeypatch)
    assert j.brain.starts == [False]


def test_message_avant_le_demarrage_garde_l_amorce(session, monkeypatch):
    j, _ = make_astrel(monkeypatch, tokens=0)
    relief.set_amorce(True)
    j.brain.alive = False  # cerveau pas encore démarré : l'envoi échouera

    def refuse(text, images=None):
        raise RuntimeError("cerveau non démarré")

    j.brain.send = refuse
    monkeypatch.setattr(core.bus, "update_history", lambda *a, **k: None, raising=False)
    try:
        j.handle("Tu es là ?", source="clavier")
    except RuntimeError:
        pass
    assert config.RELIEF_AMORCE_FILE.exists()  # l'amorce partira avec le prochain message
