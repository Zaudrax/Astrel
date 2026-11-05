"""Moteur local, lot 6 (05/11/2026, docs/chantiers/HYBRIDE-QWEN.md) : « Envoyer à Claude » sous une réponse
locale (`local_vers_claude {mid}`, `local_demandes`, coeur/local_tour.py). HUD de l'utilisateur SEUL ; la DEMANDE
d'origine (pas la réponse de Qwen), gardée en mémoire bornée, repasse par la barrière du lot 3."""
from __future__ import annotations

import json
import time

import pytest

from astrel import config, core, hors_ligne, moteur_local, routeur, state
from astrel.coeur import local_tour
from astrel.events import bus
from faux_ollama import FauxOllama

IBAN = "FR76 3000 6000 0112 3456 7890 189"


class FakeSpeaker:
    paused = busy = False

    def __init__(self):
        self.said = []

    def say(self, text, raw=False, tag="", local_only=False):
        self.said.append((text, local_only))

    def interrupt(self):
        pass

    def local_engine(self):
        return object()


class FakeBrain:
    alive = True

    def __init__(self):
        self.sent = []

    def send(self, prompt, images=None):
        self.sent.append(prompt)


@pytest.fixture
def j(monkeypatch, tmp_path):
    f = tmp_path / "channels.json"
    f.write_text(json.dumps({"default": "general", "channels": [{"id": "general", "label": "Général"}]}),
                 encoding="utf-8")
    monkeypatch.setattr(config, "CHANNELS_FILE", f)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    from astrel.channels import channels
    channels.refresh()
    monkeypatch.setattr(core.reglages, "value", lambda k: "auto" if k == "moteur_mode" else None)
    j = object.__new__(core.Astrel)
    j.voice_out = True
    j.speaker, j.status, j.brain = FakeSpeaker(), state.StatePublisher(), FakeBrain()
    j._running, j._off, j._in_conversation, j._followup = True, False, False, False
    j._turn_active, j._usage, j._followup_setting = False, "conversation", 8.0
    j._released, j.envoyes, j.appels = [], [], []
    j._release_attachments = lambda ids, items=None: j._released.extend(ids)
    j._attente_noter = lambda *a, **k: None
    j._outils_locaux_raison = lambda: "fermés pour le test"
    orig = core.Astrel.handle

    def espion(self, text, *a, **k):
        self.appels.append((text, a, k))
        return orig(self, text, *a, **k)

    monkeypatch.setattr(core.Astrel, "handle", espion)
    # Juste après la décision (le chemin d'envoi à Claude) : on note ce qui part, sans le reste du tour.
    monkeypatch.setattr(core.Astrel, "_cycle_garder", lambda self, text, *a, **k: self.envoyes.append(text) or True)
    return j


@pytest.fixture
def events():
    got = []
    stop = bus.subscribe(got.append)
    yield got
    stop()


def _attendre(cond, s=5.0):
    fin = time.time() + s
    while time.time() < fin and not cond():
        time.sleep(0.02)
    return cond()


def _tour_local(j, events, texte):
    """Un vrai tour local (Ollama simulé) : rend (mid de la demande, évènement `reply`)."""
    with FauxOllama(chat=["Réponse ", "de Qwen."]) as f:
        j.__dict__["_moteur_local_obj"] = moteur_local.MoteurLocal(url=f.url, tag="qwen3.5:9b",
                                                                   memoire=lambda: {"ram_dispo": 8 * 2**30})
        j.handle(texte, source="clavier", channel="general")
        assert _attendre(lambda: not j._local_actif())
    tr = [e for e in events if e.get("type") == "transcript" and e.get("engine") == "local"][-1]
    rep = [e for e in events if e.get("type") == "reply" and e.get("engine") == "local"][-1]
    assert rep["reply_to"] == tr["mid"]
    return tr["mid"], rep


def _cmd(j, nom, **kw):
    return j._on_command({"cmd": nom, "_via": "hud", **kw})


@pytest.mark.parametrize("nom", ["local_vers_claude", "local_demandes"])
@pytest.mark.parametrize("cmd", [{"_from_claude": True, "_via": "hud"}, {"_via": "socket"}, {"_via": "bulle"}, {}])
def test_reserve_au_hud_de_utilisateur(j, nom, cmd):
    j._local_memo_noter("m1", "explique la photosynthèse", "general")
    r = j._on_command({"cmd": nom, "mid": "m1", **cmd})
    assert r == {"ok": False, "error": f"{nom} est réservé au HUD de l'utilisateur"}
    assert j.envoyes == [] and j.appels == []
    assert "m1" in j._local_memo()[1]  # rien n'est consommé par un refus


def test_reponse_locale_gardee_puis_envoyee_a_claude(j, events):
    mid, rep = _tour_local(j, events, "En local : explique la photosynthèse")
    assert rep.get("vers_claude") is True and "hors_ligne" not in rep
    d = _cmd(j, "local_demandes")
    assert d == {"ok": True, "mids": [mid], "hors_ligne": False}  # des mid, jamais le texte
    assert j.envoyes == []
    r = _cmd(j, "local_vers_claude", mid=mid)
    assert r == {"ok": True, "mid": mid, "envoye": True}
    assert _attendre(lambda: j.envoyes)
    # La DEMANDE de l'utilisateur (préfixe retiré), jamais la réponse de Qwen ; route="claude" : pas de second routage
    assert j.envoyes == ["explique la photosynthèse"]
    texte, args, kw = j.appels[-1]
    assert texte == "explique la photosynthèse" and kw.get("route") == routeur.CLAUDE and args[0] == "clavier"
    assert any(e.get("type") == "notice" and e.get("text") == local_tour.VC_NOTICE for e in events)
    # une seule fois : la demande partie est oubliée
    r = _cmd(j, "local_vers_claude", mid=mid)
    assert r["ok"] is False and r["raison"] == "oubliee" and r["error"] == local_tour.VC_OUBLIEE
    assert _cmd(j, "local_demandes")["mids"] == []


def test_demande_sensible_carte_avant_tout_envoi(j, events):
    mid, rep = _tour_local(j, events, f"En local : vérifie mon IBAN {IBAN}")
    assert rep.get("vers_claude") is True
    r = _cmd(j, "local_vers_claude", mid=mid)
    assert r == {"ok": True, "mid": mid, "carte": True}
    time.sleep(0.1)
    assert j.envoyes == [] and not [a for a in j.appels if a[2].get("route") == routeur.CLAUDE]
    carte = [e for e in events if e.get("type") == "choice" and e.get("choice_kind") == "routage"][-1]
    # déjà traitée en local : Envoyer ou Annuler, pas de « Rester en local »
    assert [o["label"] for o in carte["options"]] == ["Envoyer à Claude", "Annuler"]
    assert "IBAN" in carte["question"]
    assert not j._choice_answer({"choice_id": carte["id"], "value": "Envoyer à Claude", "_from_claude": True})["ok"]
    assert not j._choice_answer({"choice_id": carte["id"], "value": "Envoyer à Claude", "_via": "socket"})["ok"]
    assert j.envoyes == []
    assert j._choice_answer({"choice_id": carte["id"], "value": "Envoyer à Claude", "_via": "hud"})["ok"]
    assert _attendre(lambda: j.envoyes)
    assert j.envoyes == [f"vérifie mon IBAN {IBAN}"]


def test_demande_sensible_annulee_rien_ne_part(j, events):
    mid, _rep = _tour_local(j, events, f"En local : vérifie mon IBAN {IBAN}")
    _cmd(j, "local_vers_claude", mid=mid)
    carte = [e for e in events if e.get("type") == "choice" and e.get("choice_kind") == "routage"][-1]
    r = j._choice_answer({"choice_id": carte["id"], "value": "Annuler", "_via": "hud"})
    assert r["ok"] and r["routage"] == "annuler"
    time.sleep(0.1)
    assert j.envoyes == []


def test_demande_oubliee_relance_d_astrel(j):
    r = _cmd(j, "local_vers_claude", mid="inconnu")
    assert r == {"ok": False, "error": local_tour.VC_OUBLIEE, "raison": "oubliee"}
    assert _cmd(j, "local_vers_claude")["raison"] == "oubliee"
    assert j.envoyes == [] and j.appels == []


def test_hors_ligne_refuse_et_rien_garde(j, events, monkeypatch):
    j._local_memo_noter("m-avant", "explique la photosynthèse", "general")
    monkeypatch.setattr(hors_ligne, "actif", lambda: True)
    r = _cmd(j, "local_vers_claude", mid="m-avant")
    assert r == {"ok": False, "error": local_tour.VC_HORS_LIGNE, "raison": "hors_ligne"}
    assert "m-avant" in j._local_memo()[1] and j.envoyes == []
    assert _cmd(j, "local_demandes")["hors_ligne"] is True
    # une demande faite hors ligne n'est jamais gardée pour Claude ; sa réponse le dit au HUD
    mid, rep = _tour_local(j, events, "explique la photosynthèse")
    assert rep.get("hors_ligne") is True and "vers_claude" not in rep
    assert mid not in j._local_memo()[1]
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)
    assert _cmd(j, "local_vers_claude", mid=mid)["raison"] == "oubliee"


def test_memoire_bornee_et_jamais_sur_disque(j, tmp_path):
    for i in range(local_tour.MEMO_MAX + 5):
        j._local_memo_noter(f"m{i}", f"demande {i}", "general")
    mids = list(j._local_memo()[1])
    assert len(mids) == local_tour.MEMO_MAX and mids[0] == "m5" and mids[-1] == f"m{local_tour.MEMO_MAX + 4}"
    assert not j._local_memo_noter("", "x", "general") and not j._local_memo_noter("m", "  ", "general")
    # aucun fichier pour cette mémoire (le module n'écrit rien : ni open, ni Path.write_*)
    src = (config.ROOT / "astrel" / "coeur" / "local_tour.py").read_text(encoding="utf-8")
    bloc = src[src.index("def _local_memo("):]
    assert "open(" not in bloc and "write_text" not in bloc and "json.dump" not in bloc
