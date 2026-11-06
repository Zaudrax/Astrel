"""État publié à la fin d'un tour LOCAL (05/11/2026, essais réels de l'utilisateur) : quoi qu'il arrive dans le tour
(avec ou sans outils, panne du modèle, panne avant le flux ou dans sa fin, ■ Stop), « Je réfléchis » ne reste
pas affiché : l'état revient au repos (veille, ou micro coupé) dès que rien d'autre ne le retient."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from astrel import config, core, moteur_local, state
from astrel.events import bus
from faux_ollama import FauxOllama


class FakeSpeaker:
    paused = False

    def __init__(self):
        self.busy = False
        self.said = []

    def say(self, text, raw=False, tag="", local_only=False):
        self.said.append(text)

    def interrupt(self):
        pass

    def local_engine(self):
        return object()


@pytest.fixture
def j(monkeypatch, tmp_path):
    f = tmp_path / "channels.json"
    f.write_text(json.dumps({"default": "general", "channels": [{"id": "general", "label": "Général"}]}),
                 encoding="utf-8")
    monkeypatch.setattr(config, "CHANNELS_FILE", f)
    from astrel.channels import channels
    channels.refresh()
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    j = object.__new__(core.Astrel)
    j.voice_out = True
    j.speaker, j.status = FakeSpeaker(), state.StatePublisher()
    j.brain = SimpleNamespace(alive=True, sent=[], send=lambda *a, **k: None)
    j._running, j._off, j._in_conversation, j._followup = True, False, False, False
    j._turn_active, j._usage, j._followup_setting = False, "silencieux", 8.0
    j.status.set(state.SLEEPING)
    return j


@pytest.fixture
def events():
    got = []
    stop = bus.subscribe(got.append)
    yield got
    stop()


def _attendre(j, delai=5.0):
    fin = time.time() + delai
    while j._local_actif() and time.time() < fin:
        time.sleep(0.02)
    assert not j._local_actif()


def _flux(j, morceaux, outils=False, avant=None):
    """`_local_flux` remplacé : les morceaux donnés (une exception y est levée), `avant` appelé au 1er pas."""
    def gen():
        for m in morceaux:
            if avant is not None:
                avant()
            if isinstance(m, BaseException):
                raise m
            yield m
    if outils:
        j.__dict__["_outils_locaux_obj"] = SimpleNamespace(derniere={"urls_vues": []})
    j._local_flux = lambda *a, **k: (gen(), outils)


def _tour(j, outils=False):
    j._tour_local("en local : q", "q", "clavier", "general", outils=outils)
    _attendre(j)


def test_sans_outil_fini_retour_au_repos(j, events):
    with FauxOllama(chat=["Debian 13 ", "est la version stable."]) as faux:
        j.__dict__["_moteur_local_obj"] = moteur_local.MoteurLocal(url=faux.url, tag="qwen3.5:9b",
                                                                   memoire=lambda: {"ram_dispo": 8 * 2**30})
        _tour(j)
    assert [e for e in events if e.get("type") == "reply"]
    assert j.status.current == state.SLEEPING
    etats = [e["state"] for e in events if e.get("type") == "state"]
    assert etats[0] == state.THINKING and etats[-1] == state.SLEEPING


def test_avec_outils_fini_retour_au_repos(j, events):
    _flux(j, ["Debian 13 est la version stable. ", "Source : debian.org."], outils=True)
    _tour(j, outils=True)
    assert [e for e in events if e.get("type") == "reply"]
    assert j.status.current == state.SLEEPING


def test_micro_coupe_mode_silencieux_retour_a_off(j):
    """Le cas des essais de l'utilisateur (05/11 : micro coupé, Silencieux) : le repos est « micro coupé »."""
    j._off = True
    j.status.set(state.OFF, muted=True)
    _flux(j, ["Réponse lue. ", "Fin."], outils=True)
    _tour(j, outils=True)
    assert j.speaker.said == [] and j.status.current == state.OFF


def test_erreur_du_modele_retour_au_repos(j, events):
    _flux(j, ["Début. ", moteur_local.ErreurLocale("Ollama ne répond plus (délai dépassé)")], outils=True)
    _tour(j, outils=True)
    assert any(e.get("type") == "notice" and e["text"].startswith("Moteur local :") for e in events)
    assert j.status.current == state.SLEEPING


def test_panne_avant_le_flux_retour_au_repos(j, events):
    def casse(*a, **k):
        raise RuntimeError("outils introuvables")
    j._local_flux = casse
    _tour(j, outils=True)
    assert any(e.get("type") == "notice" and e["text"].startswith("Moteur local :") for e in events)
    assert j.status.current == state.SLEEPING


def test_panne_dans_la_fin_du_tour_retour_au_repos(j):
    _flux(j, ["Réponse. "])

    def casse(*a, **k):
        raise RuntimeError("mémoire des demandes")
    j._local_memo_noter = casse
    _tour(j)
    assert j.status.current == state.SLEEPING


def test_stop_pendant_le_tour_retour_au_repos(j):
    lance = threading.Event()

    def attendre_stop():
        lance.set()
        j._local_etat()["stop"].wait(3.0)
    _flux(j, ["Un. ", "Deux. ", "Trois."], outils=True, avant=attendre_stop)
    j._tour_local("en local : q", "q", "clavier", "general", outils=True)
    assert lance.wait(3.0) and j.status.current == state.THINKING
    j._local_arreter()
    _attendre(j)
    assert j.status.current == state.SLEEPING


def test_claude_au_travail_l_etat_reste_le_sien(j):
    """Un tour de Claude en cours : son état reste (c'est sa fin de tour qui remettra au repos)."""
    j._turn_active = True
    j.status.set(state.WORKING, detail="Bash")
    _flux(j, ["Réponse. "])
    _tour(j)
    assert j.status.current in (state.THINKING, state.WORKING)


def test_claude_fini_pendant_le_tour_local_puis_repos(j):
    """Claude finit pendant le tour local : la fin du tour local remet bien au repos (repos posé APRÈS
    `actif` retombé, voir `_tour_local_fil`)."""
    j._turn_active = True
    lance, suite = threading.Event(), threading.Event()

    def pause():
        lance.set()
        suite.wait(3.0)
    _flux(j, ["Réponse. "], avant=pause)
    j._tour_local("en local : q", "q", "clavier", "general")
    assert lance.wait(3.0)
    j._turn_active = False
    suite.set()
    _attendre(j)
    assert j.status.current == state.SLEEPING


# --- 05/11 (essai de l'utilisateur, 19:28 : « /reflechir » = 4 min 14 s de « Je réfléchis » seul) : détail de l'état
# pendant une réflexion du local (`local_tour.detail_reflexion`), temps écoulé ajouté par le HUD.

from pathlib import Path  # noqa: E402

from astrel import i18n  # noqa: E402
from astrel.coeur import local_tour  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _details(events):
    return [e.get("detail", "") for e in events if e.get("type") == "state"]


def _brancher(j, faux):
    j.__dict__["_moteur_local_obj"] = moteur_local.MoteurLocal(url=faux.url, tag="qwen3.5:9b",
                                                               memoire=lambda: {"ram_dispo": 8 * 2**30})


def _tour_reflexion(j):
    j._tour_local("/reflechir q", "q", "clavier", "general", reflexion=True)
    _attendre(j)


def test_reflexion_detail_publie_jetons_puis_relance_puis_relecture_et_efface(j, events, monkeypatch):
    monkeypatch.setattr(moteur_local, "REFLEXION_BUDGET", 4)
    monkeypatch.setattr(moteur_local, "ETAPE_PAS_S", 0.0)  # chaque jeton pensé signalé
    monkeypatch.setattr(local_tour, "JETONS_PAS", 2)
    with FauxOllama(chat=["Il y a 194 jours."], thinking=["PENSEE-SECRETE"] * 8) as faux:
        _brancher(j, faux)
        _tour_reflexion(j)
    m = 1  # budget de 4 jetons : « jusqu'à ~1 min » au plus court
    d = [x for x in _details(events) if x]
    assert d == [local_tour.DETAIL_REFLEXION.format(min=m),
                 local_tour.DETAIL_REFLEXION_JETONS.format(min=m, jetons=2),
                 local_tour.DETAIL_REFLEXION_JETONS.format(min=m, jetons=4),
                 local_tour.DETAIL_DIRECTE, local_tour.DETAIL_RELECTURE]
    assert _details(events)[-1] == "" and j.status.current == state.SLEEPING
    assert [e for e in events if e.get("type") == "reply"]
    # le texte pensé n'est jamais dans le détail
    assert not any("PENSEE" in x for x in d)


def test_reflexion_sous_le_budget_puis_relecture(j, events, monkeypatch):
    monkeypatch.setattr(moteur_local, "VERIFICATION", False)
    with FauxOllama(chat=["Réponse."], thinking=["x"] * 3) as faux:
        _brancher(j, faux)
        _tour_reflexion(j)
    d = [x for x in _details(events) if x]
    assert d == [local_tour.DETAIL_REFLEXION.format(min=4)]  # 8 000 jetons à ~31,5/s : « ~4 min »
    assert j.status.current == state.SLEEPING and _details(events)[-1] == ""


def test_sans_reflexion_aucun_detail(j, events):
    with FauxOllama(chat=["Debian 13 ", "est la version stable."]) as faux:
        _brancher(j, faux)
        _tour(j)
    assert set(_details(events)) == {""}
    assert j.status.current == state.SLEEPING


def test_avec_outils_aucun_detail_de_reflexion(j, events):
    """Le CLI avec outils a sa propre réflexion : rien n'est annoncé par ce chemin."""
    _flux(j, ["Réponse. "], outils=True)
    j._tour_local("/reflechir q", "q", "clavier", "general", outils=True, reflexion=True)
    _attendre(j)
    assert set(_details(events)) == {""}


def test_detail_d_un_autre_jamais_ecrase(j, events):
    """Claude travaille en fond (« je continue en arrière-plan ») : son détail reste, le tour local n'y touche pas."""
    j._turn_active = True
    j.status.set(state.THINKING, detail="je continue en arrière-plan")
    with FauxOllama(chat=["Réponse."], thinking=["x"] * 3) as faux:
        _brancher(j, faux)
        _tour_reflexion(j)
    assert not any(x.startswith(local_tour.DETAIL_PREFIXE) for x in _details(events))
    assert _details(events)[-1] == "je continue en arrière-plan"


def test_repos_non_pose_le_detail_est_efface_a_la_fin(j, events):
    """Claude a commencé un tour pendant la réflexion (état « Je réfléchis » sans détail) : le repos n'est pas posé
    par le tour local, mais son détail part quand même."""
    j._turn_active = True
    j.status.set(state.THINKING)
    with FauxOllama(chat=["Réponse."], thinking=["x"] * 3) as faux:
        _brancher(j, faux)
        _tour_reflexion(j)
    assert any(x.startswith(local_tour.DETAIL_PREFIXE) for x in _details(events))
    assert _details(events)[-1] == "" and j.status.current == state.THINKING


def test_panne_pendant_la_reflexion_detail_efface(j, events, monkeypatch):
    def casse(*a, **k):
        raise RuntimeError("Ollama tombé")
        yield ""
    j.__dict__["_moteur_local_obj"] = SimpleNamespace(repondre=casse, reflexion_max_s=lambda: 250.0, tag="q",
                                                      derniere={})
    _tour_reflexion(j)
    assert local_tour.DETAIL_REFLEXION.format(min=4) in _details(events)
    assert _details(events)[-1] == "" and j.status.current == state.SLEEPING


def test_stop_pendant_la_reflexion(j, events):
    """Échap (■) pendant la réflexion : le tour s'arrête, le détail part avec le repos."""
    lance = threading.Event()

    def lent(demande, langue="fr", stop=None, reflexion=None, sur_etape=None, **kw):
        sur_etape("reflexion", 300)
        lance.set()
        stop.wait(3.0)
        return
        yield ""
    j.__dict__["_moteur_local_obj"] = SimpleNamespace(repondre=lent, reflexion_max_s=lambda: 250.0, tag="q",
                                                      derniere={})
    j._tour_local("/reflechir q", "q", "clavier", "general", reflexion=True)
    assert lance.wait(3.0)
    assert _details(events)[-1] == local_tour.DETAIL_REFLEXION_JETONS.format(min=4, jetons=300)
    j._cmd_stop("stop", {})
    _attendre(j)
    assert _details(events)[-1] == "" and j.status.current == state.SLEEPING


def test_moteur_signale_les_etapes_et_survit_a_une_panne_d_affichage(monkeypatch):
    monkeypatch.setattr(moteur_local, "REFLEXION_BUDGET", 3)
    monkeypatch.setattr(moteur_local, "ETAPE_PAS_S", 0.0)
    vus = []

    def sur_etape(etape, jetons):
        vus.append((etape, jetons))
        raise RuntimeError("HUD en panne")  # l'affichage ne casse jamais le tour
    with FauxOllama(chat=["Réponse."], thinking=["x"] * 6) as faux:
        m = moteur_local.MoteurLocal(url=faux.url, tag="qwen3.5:9b", memoire=lambda: {"ram_dispo": 8 * 2**30})
        assert "".join(m.repondre("q", reflexion=True, verification=False, sur_etape=sur_etape)) == "Réponse."
        # réponse sans réflexion demandée : rien n'est signalé
        n = len(vus)
        assert "".join(m.repondre("q", reflexion=False, verification=False, sur_etape=sur_etape)) == "Réponse."
    assert vus[:n] == [("reflexion", 1), ("reflexion", 2), ("reflexion", 3), ("budget", 4)] and len(vus) == n
    assert m.reflexion_max_s() == pytest.approx(3 / 40.0)  # vitesse mesurée au tour (20 jetons en 0,5 s)


def test_detail_reflexion_textes():
    assert local_tour.detail_reflexion("reflexion", 0, 4) == \
        "modèle local · réflexion (jusqu'à ~4 min) · Échap pour arrêter"
    assert local_tour.detail_reflexion("reflexion", 1299, 4) == \
        "modèle local · réflexion (jusqu'à ~4 min) · ~1200 jetons · Échap pour arrêter"
    assert local_tour.detail_reflexion("budget").startswith("modèle local · réflexion trop longue : réponse directe…")
    assert local_tour.detail_reflexion("inconnue") == ""
    for etape in ("reflexion", "budget", "vide", "relecture"):
        assert local_tour.detail_reflexion(etape, 500).startswith(local_tour.DETAIL_PREFIXE)


def test_detail_reflexion_traduit_en_anglais():
    i18n.reload()
    for modele in (local_tour.DETAIL_REFLEXION, local_tour.DETAIL_REFLEXION_JETONS, local_tour.DETAIL_DIRECTE,
                   local_tour.DETAIL_SANS_REPONSE, local_tour.DETAIL_RELECTURE):
        assert i18n.covered(modele), modele
    assert i18n.tr(local_tour.detail_reflexion("reflexion", 1300, 4), "en") == \
        "local model · thinking (up to ~4 min) · ~1300 tokens · Esc to stop"
    assert i18n.tr(local_tour.detail_reflexion("budget"), "en") == \
        "local model · thinking took too long: direct answer… · Esc to stop"
    for etape in ("reflexion", "vide", "relecture"):
        en = i18n.tr(local_tour.detail_reflexion(etape, 0, 4), "en")
        assert en.startswith("local model · ") and "Échap" not in en and "réflexion" not in en


def test_prefixe_identique_dans_le_hud():
    """Le HUD reconnaît la réflexion du local à ce préfixe (texte du cœur, avant traduction)."""
    main = (ROOT / "hud" / "src" / "main.js").read_text(encoding="utf-8")
    assert f'const LOCAL_THINK_PREFIX = "{local_tour.DETAIL_PREFIXE}";' in main
    langues = (ROOT / "hud" / "src" / "langues.js").read_text(encoding="utf-8")
    assert langues.count('"state.timer_title"') == 2  # français et anglais


# --- 05/11 (essai de l'utilisateur, 19:28 : 203 au lieu de 194, aucune ligne de faits) : les faits exacts du cœur
# (`faits_local`, dates sans année comprises) arrivent au modèle dans un tour /reflechir et au CLI avec outils.

QUESTION_DATES = "combien de jours entre le 1er janvier et le 14 juillet ?"


def test_reflechir_recoit_la_ligne_de_faits(j):
    from astrel import commandes_chat, faits_local

    with FauxOllama(chat=["Il y a 194 jours."], thinking=["x"] * 3) as faux:
        _brancher(j, faux)
        r = j._chat_commande(commandes_chat.analyser(f"/reflechir {QUESTION_DATES}"),
                             {"channel": "general", "_via": "hud"})
        assert r == {"ok": True, "local": True}
        _attendre(j)
        corps = [b for meth, path, b in faux.requests if path == "/api/chat"]
    ligne = faits_local.faits(QUESTION_DATES, "fr")
    assert "jours du 1er janvier au 14 juillet en" in ligne and "en comptant les deux dates" in ligne
    assert corps[0]["think"] is True
    assert corps[0]["messages"][-1]["content"] == f"{QUESTION_DATES}\n\n{ligne}"
    # la relecture relit la question AVEC ses faits
    assert ligne in corps[-1]["messages"][-1]["content"]


def test_cli_avec_outils_recoit_la_ligne_de_faits(j, monkeypatch):
    from astrel import faits_local, outils_local

    recu = []

    class Outils:
        base, derniere = "qwen3.5:9b", {}

        def repondre(self, message, langue="fr", **kw):
            recu.append(message)
            yield "réponse"
    j._total, j._lift = False, False
    j.__dict__["_outils_locaux_obj"] = Outils()
    monkeypatch.setattr(outils_local, "raison_indisponible", lambda *a: "")
    monkeypatch.setattr(outils_local, "raison_actions_indisponible", lambda *a: "fermé")
    monkeypatch.setattr(outils_local, "raison_web_indisponible", lambda *a: "fermé")
    flux, avec = j._local_flux(QUESTION_DATES, "general", True, threading.Event(), reflexion=True)
    assert avec and list(flux) == ["réponse"]
    assert recu == [f"{QUESTION_DATES}\n\n{faits_local.faits(QUESTION_DATES, 'fr')}"]
