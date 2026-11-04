"""Moteur local, lot 1 (04/11/2026, docs/chantiers/HYBRIDE-QWEN.md) : routeur (choix explicite), client
Ollama direct (`moteur_local`), tour local du cœur (`coeur/local_tour.py`) et voix locale seulement.

Un essai RÉEL contre l'Ollama de la machine est à part (`ASTREL_ESSAI_OLLAMA=1`), jamais en CI."""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from astrel import config, core, moteur_local, routeur, state
from astrel.events import bus
from faux_ollama import FauxOllama

# ------------------------------------------------------------------ routeur (fonction pure)

LOCAL_FR = [
    ("En local : explique-moi une injection SQL", "explique-moi une injection SQL"),
    ("Astrel, en local, analyse ce log", "analyse ce log"),
    ("local, salut", "salut"),
    ("Localement : résume ce texte", "résume ce texte"),
    ("Hors ligne : traduis ça", "traduis ça"),
    ("Hey Jarvis, en local: test", "test"),
]
LOCAL_PHRASE = [
    "traite cette demande en local",
    "Analyse ce code sans rien envoyer",
    "analyse ce script sans envoyer de données à l'extérieur",
    "utilise le modèle local pour ça",
    "fais-le en local",
    "please handle this locally.",
    "use the local model for this",
    "explain this without sending anything",
]
LOCAL_EN = [("Locally: explain XSS", "explain XSS"), ("offline: summarize", "summarize")]
CLAUDE = ["avec Claude : refactorise le module", "utilise Claude pour cette tâche", "use claude please"]
AUCUN = [
    "fais tourner le serveur en local",       # parle du serveur, pas du moteur
    "rédige le mail sans l'envoyer",
    "explique le réseau local",
    "n'utilise pas le modèle local",
    "le dépôt local est en retard sur main",
    "how do I run it locally on Windows and macOS with docker compose",
    "",
]


@pytest.mark.parametrize("texte,reste", LOCAL_FR + LOCAL_EN)
def test_prefixe_local_retire(texte, reste):
    assert routeur.forced_engine(texte) == (routeur.LOCAL, reste)


@pytest.mark.parametrize("texte", LOCAL_PHRASE)
def test_demande_locale_dans_la_phrase_garde_le_texte(texte):
    assert routeur.forced_engine(texte) == (routeur.LOCAL, texte)


@pytest.mark.parametrize("texte", CLAUDE)
def test_claude_explicite(texte):
    assert routeur.forced_engine(texte)[0] == routeur.CLAUDE


@pytest.mark.parametrize("texte", AUCUN)
def test_rien_de_demande(texte):
    assert routeur.forced_engine(texte) == ("", texte)


def test_canal_local_vaut_choix_explicite():
    assert routeur.forced_engine("bonjour", "local", {"local"}) == (routeur.LOCAL, "bonjour")
    assert routeur.forced_engine("bonjour", "astrel", {"local"}) == ("", "bonjour")
    # Un préfixe Claude dans le canal Local reste un choix de Claude.
    assert routeur.forced_engine("avec Claude : bonjour", "local", {"local"})[0] == routeur.CLAUDE


# ------------------------------------------------------------------ client direct

def _moteur(faux, **kw):
    return moteur_local.MoteurLocal(url=faux.url, tag="qwen3.5:9b", memoire=lambda: {"ram_dispo": 8 * 2**30}, **kw)


def test_reponse_en_flux_et_corps_de_requete():
    with FauxOllama(chat=["Bonjour", ". Voilà."]) as faux:
        m = _moteur(faux)
        assert "".join(m.repondre("salut")) == "Bonjour. Voilà."
        body = next(b for meth, path, b in faux.requests if path == "/api/chat")
    assert body["model"] == "qwen3.5:9b" and body["stream"] is True and body["think"] is False
    assert body["options"] == {"num_ctx": moteur_local.NUM_CTX, "num_gpu": moteur_local.NUM_GPU_TOUT}
    assert body["keep_alive"] == moteur_local.KEEP_ALIVE
    assert body["messages"][0]["role"] == "system" and body["messages"][-1] == {"role": "user", "content": "salut"}
    assert m.derniere["tokens"] == 20 and m.derniere["tokens_s"] == 40.0


def test_historique_en_memoire_borne_et_rejoue():
    with FauxOllama(chat=["ok"]) as faux:
        m = _moteur(faux)
        for i in range(10):
            list(m.repondre(f"question {i}"))
        dernier = [b for meth, path, b in faux.requests if path == "/api/chat"][-1]
    hist = dernier["messages"][1:-1]
    assert len(hist) <= moteur_local.HISTORIQUE_MAX and hist[0]["role"] == "user"
    assert hist[-1] == {"role": "assistant", "content": "ok"} and hist[-2] == {"role": "user", "content": "question 8"}
    m.oublier()
    assert not m._hist


def test_reflexion_jamais_rendue():
    with FauxOllama(chat=["<think>je réfléchis", " encore</think>", "Réponse."]) as faux:
        assert "".join(_moteur(faux).repondre("x")) == "Réponse."


def test_repli_sans_num_gpu_sur_memoire_de_la_carte():
    with FauxOllama(chat=["ok"], chat_error_gpu="CUDA error: out of memory") as faux:
        m = _moteur(faux)
        assert "".join(m.repondre("x")) == "ok"
        corps = [b for meth, path, b in faux.requests if path == "/api/chat"]
        assert "num_gpu" in corps[0]["options"] and "num_gpu" not in corps[1]["options"]
        assert m._num_gpu is False
        list(m.repondre("y"))  # la suite de la session reste sans num_gpu
        assert "num_gpu" not in [b for meth, path, b in faux.requests if path == "/api/chat"][-1]["options"]


def test_autre_erreur_pas_de_repli():
    with FauxOllama(chat_error_gpu="model not found") as faux:
        with pytest.raises(moteur_local.ErreurLocale, match="model not found"):
            list(_moteur(faux).repondre("x"))


@pytest.mark.parametrize("url", ["http://192.168.1.20:11434", "https://ollama.example.com", "http://10.0.0.1"])
def test_adresse_distante_refusee_sans_rien_envoyer(url):
    m = moteur_local.MoteurLocal(url=url, tag="qwen3.5:9b", client=lambda: pytest.fail("aucune requête attendue"))
    with pytest.raises(moteur_local.ErreurLocale, match="hors de cette machine"):
        list(m.repondre("secret"))


@pytest.mark.parametrize("tag", ["qwen3.5:cloud", "gpt-oss:120b-cloud", "deepseek-v3.1:671b-cloud"])
def test_modele_en_ligne_refuse(tag):
    with FauxOllama() as faux:
        m = moteur_local.MoteurLocal(url=faux.url, tag=tag)
        with pytest.raises(moteur_local.ErreurLocale, match="en ligne"):
            list(m.repondre("secret"))
        assert not [r for r in faux.requests if r[1] == "/api/chat"]


def test_memoire_basse_refuse_le_chargement_mais_pas_un_modele_charge():
    bas = lambda: {"ram_dispo": int(0.5 * 2**30)}  # noqa: E731
    with FauxOllama(chat=["ok"]) as faux:
        m = moteur_local.MoteurLocal(url=faux.url, tag="qwen3.5:9b", memoire=bas)
        with pytest.raises(moteur_local.ErreurLocale, match="mémoire trop basse"):
            list(m.repondre("x"))
    with FauxOllama(chat=["ok"], loaded=["qwen3.5:9b"]) as faux:
        m = moteur_local.MoteurLocal(url=faux.url, tag="qwen3.5:9b", memoire=bas)
        assert "".join(m.repondre("x")) == "ok"


def test_ollama_absent():
    m = moteur_local.MoteurLocal(url="http://127.0.0.1:9", tag="qwen3.5:9b", memoire=lambda: {})
    with pytest.raises(moteur_local.ErreurLocale, match="Ollama ne répond pas"):
        list(m.repondre("x"))


# ------------------------------------------------------------------ tour local du cœur

class FakeSpeaker:
    paused = False

    def __init__(self, piper=True):
        self.busy = False
        self.said = []
        self.piper = piper

    def say(self, text, raw=False, tag="", local_only=False):
        self.said.append((text, local_only))

    def interrupt(self):
        pass

    def local_engine(self):
        return object() if self.piper else None


class FakeBrain:
    alive = True

    def __init__(self):
        self.sent = []

    def send(self, prompt, images=None):
        self.sent.append(prompt)


@pytest.fixture
def canaux(monkeypatch, tmp_path):
    f = tmp_path / "channels.json"
    f.write_text(json.dumps({"default": "general", "channels": [
        {"id": "general", "label": "Général"}, {"id": "astrel", "label": "Astrel"},
        {"id": "local", "label": "Local", "moteur": "local"}]}), encoding="utf-8")
    monkeypatch.setattr(config, "CHANNELS_FILE", f)
    from astrel.channels import channels
    channels.refresh()
    return channels


@pytest.fixture
def j(monkeypatch, tmp_path, canaux):
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    j = object.__new__(core.Astrel)
    j.voice_out = True
    j.speaker, j.status, j.brain = FakeSpeaker(), state.StatePublisher(), FakeBrain()
    j._running, j._off, j._in_conversation, j._followup = True, False, False, False
    j._turn_active, j._usage, j._followup_setting = False, "conversation", 8.0
    j._released = []
    j._release_attachments = lambda ids, items=None: j._released.extend(ids)
    j.attente_notes = []
    j._attente_noter = lambda *a, **k: j.attente_notes.append(a)
    return j


@pytest.fixture
def faux():
    with FauxOllama(chat=["Une injection SQL ", "glisse du code dans une requête. ", "On s'en protège ainsi."]) as f:
        yield f


@pytest.fixture
def events():
    got = []
    stop = bus.subscribe(got.append)
    yield got
    stop()


def _brancher(j, faux):
    j.__dict__["_moteur_local_obj"] = moteur_local.MoteurLocal(url=faux.url, tag="qwen3.5:9b",
                                                               memoire=lambda: {"ram_dispo": 8 * 2**30})


def _attendre(j, delai=5.0):
    fin = time.time() + delai
    while j._local_actif() and time.time() < fin:
        time.sleep(0.02)
    assert not j._local_actif()


def test_demande_locale_jamais_envoyee_a_claude(j, faux, events):
    _brancher(j, faux)
    j.handle("En local : explique une injection SQL", source="clavier", channel="astrel")
    _attendre(j)
    assert j.brain.sent == [] and j.attente_notes == []
    tr = [e for e in events if e.get("type") == "transcript"]
    assert tr and tr[-1]["engine"] == "local" and tr[-1]["text"] == "En local : explique une injection SQL"
    rep = [e for e in events if e.get("type") == "reply"]
    assert rep[-1]["engine"] == "local" and rep[-1]["reply_to"] == tr[-1]["mid"]
    assert rep[-1]["text"].startswith("Une injection SQL")
    assert all(e.get("engine") == "local" for e in events if e.get("type") == "reply_delta")
    demande = next(b for meth, path, b in faux.requests if path == "/api/chat")["messages"][-1]["content"]
    assert demande == "explique une injection SQL"  # le préfixe n'est pas donné au modèle


def test_reponse_locale_dite_par_la_voix_locale_seulement(j, faux):
    _brancher(j, faux)
    j.handle("en local : explique", source="voix", channel="astrel")
    _attendre(j)
    assert j.speaker.said and all(local for _texte, local in j.speaker.said)


def test_claude_occupe_reponse_affichee_pas_dite(j, faux, events):
    _brancher(j, faux)
    j._turn_active = True
    j.handle("en local : explique", source="clavier", channel="astrel")
    _attendre(j)
    assert j.speaker.said == [] and [e for e in events if e.get("type") == "reply"]


def test_mode_silencieux_rien_dit(j, faux):
    _brancher(j, faux)
    j._usage = "silencieux"
    j.handle("en local : explique", source="clavier", channel="astrel")
    _attendre(j)
    assert j.speaker.said == []


def test_canal_local_choix_explicite(j, faux, events):
    _brancher(j, faux)
    j.handle("explique une injection SQL", source="clavier", channel="local")
    _attendre(j)
    assert j.brain.sent == [] and [e for e in events if e.get("type") == "reply"][-1]["channel"] == "local"


def test_message_qui_cite_local_ne_part_pas_dans_le_canal_local(canaux):
    ch, _routed = canaux.route("le serveur local plante", requested="general", follow=False)
    assert ch == "general"


def test_whatsapp_refuse(j, faux, events):
    _brancher(j, faux)
    envoyes = []
    j.whatsapp = type("W", (), {"away": False, "send_text": lambda self, t, channel="": envoyes.append(t)})()
    j.handle("en local : mon mot de passe est-il solide ?", source="whatsapp")
    assert envoyes and "WhatsApp" in envoyes[0] and not j._local_actif()
    assert not [r for r in faux.requests if r[1] == "/api/chat"] and j.brain.sent == []


def test_pieces_jointes_rendues(j, faux, events):
    _brancher(j, faux)
    joined = type("P", (), {"ids": ["a1"], "shown": [{"name": "x.txt"}], "images": [], "notes": []})()
    j.handle("en local : lis ce fichier", source="clavier", joined=joined)
    assert j._released == ["a1"] and not [r for r in faux.requests if r[1] == "/api/chat"]
    assert any(e.get("type") == "notice" and "pièces jointes" in e.get("text", "") for e in events)


def test_ollama_en_panne_notice_et_rien_chez_claude(j, events):
    j.__dict__["_moteur_local_obj"] = moteur_local.MoteurLocal(url="http://127.0.0.1:9", tag="qwen3.5:9b",
                                                               memoire=lambda: {})
    j.handle("en local : bonjour", source="clavier")
    _attendre(j)
    assert j.brain.sent == []
    assert any(e.get("type") == "notice" and e.get("text", "").startswith("Moteur local :") for e in events)


def test_stop_arrete_la_reponse_locale(j, faux):
    _brancher(j, faux)
    e = j._local_etat()
    lent = threading.Event()
    reel = j._moteur_local.repondre

    def repondre(demande, langue="fr", stop=None):
        for morceau in reel(demande, langue, stop):
            yield morceau
            lent.wait(0.2)

    j._moteur_local.repondre = repondre
    j.handle("en local : explique", source="clavier")
    j._cmd_stop("stop", {})
    _attendre(j)
    assert e["stop"].is_set()


def test_journal_de_la_voix_sans_le_texte_local(j, faux, caplog, monkeypatch):
    """La ligne « UTILISATEUR: … » de la conversation ne recopie pas une demande locale."""
    import inspect

    src = inspect.getsource(core.Astrel.conversation)
    assert 'log.info("UTILISATEUR (demande locale, %d caractères)", len(text))' in src
    assert src.index("demande locale") < src.index('log.info("UTILISATEUR: %s", text)')


def test_demande_ordinaire_part_chez_claude(j, monkeypatch):
    """Sans choix explicite, rien ne change : le tour local n'est pas appelé."""
    appels = []
    monkeypatch.setattr(core.Astrel, "_tour_local", lambda self, *a, **k: appels.append(a))
    monkeypatch.setattr(core.Astrel, "_cycle_garder", lambda self, *a, **k: True)  # s'arrête juste après
    j.handle("explique une injection SQL", source="clavier", channel="astrel")
    assert appels == []


# ------------------------------------------------------------------ voix : jamais Edge pour une réponse locale

def test_voix_locale_jamais_edge(monkeypatch):
    from astrel.voice import speaker as sp

    class Moteur:
        def __init__(self, name):
            self.name, self.rendus = name, []

    edge, piper = Moteur("edge:fr-FR-RemyMultilingualNeural"), Moteur("piper:fr_FR-upmc-medium")
    s = object.__new__(sp.Speaker)
    s._primary, s._fallback = edge, piper
    assert s.local_engine() is piper
    rendus = []
    s._render = lambda engine, text, gen: rendus.append(engine.name)
    s._speak_local("phrase locale", 0)
    assert rendus == ["piper:fr_FR-upmc-medium"]
    s._fallback = None  # pas de voix locale : rien n'est dit, jamais de repli sur Edge
    rendus.clear()
    s._speak_local("phrase locale", 0)
    assert rendus == []


# ------------------------------------------------------------------ essai réel (machine de l'utilisateur)

@pytest.mark.skipif(os.environ.get("ASTREL_ESSAI_OLLAMA") != "1", reason="essai réel : ASTREL_ESSAI_OLLAMA=1")
def test_essai_reel_ollama():
    m = moteur_local.MoteurLocal(url=os.environ.get("ASTREL_OLLAMA_URL") or "http://127.0.0.1:11434",
                                 tag=moteur_local.MODELE_DEFAUT)
    texte = "".join(m.repondre("Réponds seulement : bonjour."))
    assert texte.strip() and m.derniere["tokens_s"] > 5
    print("essai réel :", m.derniere)
