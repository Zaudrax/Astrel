"""Mode « rien ne sort » (04/11/2026, lot 2 du moteur local, docs/chantiers/HYBRIDE-QWEN.md) :
réglage, commande dite ou tapée, refus pendant une tâche et à Claude, cerveau arrêté puis relancé, demandes
routées au moteur local, voix Piper seule, chaque sortie coupée à la source, inventaire, survie à une relance."""
from __future__ import annotations

import queue
import re
import threading
from pathlib import Path

import pytest

from astrel import config, core, cycle_cerveau, hors_ligne, state
from astrel.settings import SettingError, reglages

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def hl():
    """Mode actif, par le réglage (comme le cœur l'écrit)."""
    reglages.set(hors_ligne.CLE, True)
    yield
    reglages.reset()


# ------------------------------------------------------------------ module

@pytest.mark.parametrize("texte, attendu", [
    ("passe hors ligne", "on"), ("Astrel, passe en mode hors ligne.", "on"), ("mets-toi hors ligne", "on"),
    ("rien ne sort", "on"), ("active le mode rien ne sort", "on"), ("go offline", "on"), ("offline mode on", "on"),
    ("repasse en ligne", "off"), ("quitte le mode hors ligne", "off"), ("sors du mode rien ne sort", "off"),
    ("go back online", "off"), ("go online", "off"), ("exit offline mode", "off"),
    ("explique le mode hors ligne", ""), ("hors ligne : explique x", ""), ("le site est hors ligne ?", ""),
    ("en local : salut", ""), ("is the server offline", ""),
])
def test_commande(texte, attendu):
    assert hors_ligne.commande(texte) == attendu


def test_actif_suit_le_reglage_et_l_environnement(monkeypatch):
    assert not hors_ligne.actif()
    reglages.set(hors_ligne.CLE, True)
    assert hors_ligne.actif()
    with pytest.raises(hors_ligne.HorsLigne, match="rien ne part"):
        hors_ligne.verifier("essai")
    reglages.set(hors_ligne.CLE, False)
    monkeypatch.setenv(hors_ligne.ENV, "1")
    reglages.reset()
    assert hors_ligne.actif()
    with pytest.raises(SettingError, match="ASTREL_HORS_LIGNE"):  # imposé : jamais coupé depuis le HUD
        reglages.set(hors_ligne.CLE, False)


# ------------------------------------------------------------------ inventaire

URL_DISTANTE = re.compile(r"https?://(?!(?:127\.0\.0\.1|localhost|\[::1\]|0\.0\.0\.0)\b)"
                          r"(?!(?:www\.)?(?:exemple|example)\.)(?!www\.w3\.org)[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.I)
FAMILLES = {
    "adresse distante": URL_DISTANTE,
    "bibliothèque réseau": re.compile(r"\b(?:httpx|aiohttp|requests|edge_tts|websockets)\.[A-Za-z]|urllib\.request"
                                      r"|socket\.create_connection"),
    "CLI claude": re.compile(r"\bCLAUDE_BIN\b"),
    "worker ou pont Node": re.compile(r"executer_worker\(|\bnpx\b|NODE_BIN|\[\s*\"node\""),
}
# Modules repérés qui ne consultent pas `hors_ligne` eux-mêmes, avec la raison : rien ne sort par eux, ou ils
# sont coupés ailleurs (le cœur, ou le cerveau arrêté).
SANS_SORTIE = {
    "astrel/banc.py": "banc d'essai lancé à la main par l'utilisateur, jamais par le cœur",
    "astrel/bubble.py": "bulle : WebSocket vers le HUD local (127.0.0.1)",
    "astrel/bulle_commun.py": "bulle Qt : WebSocket vers le HUD local (127.0.0.1)",
    "astrel/cli.py": "terminal : parle au HUD local",
    "astrel/coeur/medias.py": "adresse d'exemple en commentaire ; `open_link` ouvre le navigateur sur un clic de l'utilisateur",
    "astrel/config.py": "constantes seulement (protégé par le garde-fou) ; chaque appelant est coupé",
    "astrel/guard.py": "garde-fou : juge des commandes, ne lance rien",
    "astrel/ipc.py": "canal de contrôle local",
    "astrel/moteur_local.py": "moteur local : Ollama sur la machine seulement (adresse distante refusée)",
    "astrel/platform/linux_capture.py": "capture d'écran locale (portail du bureau)",
    "astrel/server.py": "HUD sur 127.0.0.1",
    "astrel/tools/connector.py": "réglages des connecteurs ; httpx pour les types d'erreur seulement",
}
# Coupés AILLEURS que dans leur module (fichier protégé par le garde-fou) : vérifié par les tests du cœur.
COUPES_PAR_LE_COEUR = {"whatsapp": "pont arrêté par `_hors_ligne_entrer`, commandes refusées par le cœur"}


def _repérés() -> dict[str, list[str]]:
    out = {}
    for p in sorted((ROOT / "astrel").rglob("*.py")):
        t = p.read_text(encoding="utf-8")
        fam = [nom for nom, motif in FAMILLES.items() if motif.search(t)]
        if fam:
            out[p.relative_to(ROOT).as_posix()] = fam
    return out


def test_inventaire_tout_module_qui_sort_est_coupe():
    oublis = []
    for rel, fam in _repérés().items():
        if rel in SANS_SORTIE:
            continue
        if "hors_ligne" not in (ROOT / rel).read_text(encoding="utf-8"):
            oublis.append(f"{rel} ({', '.join(fam)})")
    assert not oublis, ("modules qui sortent sans consulter hors_ligne.actif() (couper, ou justifier dans "
                        "SANS_SORTIE) : " + " ; ".join(oublis))
    # La liste blanche ne garde pas de nom mort.
    assert not [rel for rel in SANS_SORTIE if not (ROOT / rel).exists()]


def test_inventaire_sorties_designent_des_modules_coupes():
    for titre, _quoi, modules, _coupe in hors_ligne.SORTIES:
        for mod in modules:
            if mod in COUPES_PAR_LE_COEUR:
                continue
            path = ROOT / "astrel" / (mod.replace(".", "/") + ".py")
            assert path.is_file(), (titre, mod)
            assert "hors_ligne" in path.read_text(encoding="utf-8"), (titre, mod)


def test_page_confidentialite_tiree_de_l_inventaire(hl):
    from astrel import confidentialite

    labels = [l["label"] for l in confidentialite.view()["value"]["lines"]]
    assert labels[0] == "Rien ne sort"
    assert labels[1:1 + len(hors_ligne.SORTIES)] == [s[0] for s in hors_ligne.SORTIES]
    reglages.set(hors_ligne.CLE, False)
    assert confidentialite.view()["value"]["lines"][0]["label"] == hors_ligne.SORTIES[0][0]


# ------------------------------------------------------------------ chaque sortie coupée à la source

def test_images_et_videos_refusees(hl):
    from astrel.images_ia import images
    from astrel.videos_ia import videos

    assert images.submit("un chat")["error"].startswith("pas d'image")
    assert videos.submit("un chat")["error"].startswith("pas de vidéo")


def test_worker_et_connexion_ia_jamais_lances(hl, monkeypatch):
    from astrel import ia_connexion, ia_web

    monkeypatch.setattr(ia_web.enfants, "popen", lambda *a, **k: pytest.fail("worker lancé hors ligne"))
    assert ia_web.executer_worker(["node", "x"], {}, lambda l: None, delai_s=1)["code"] == "hors_ligne"
    assert ia_connexion.ouvrir("amw", lanceur=lambda *a, **k: pytest.fail("Chrome lancé"))["ok"] is False


def test_veille_maj_quota(hl, monkeypatch):
    from astrel import maj, quota_releve, veille_secu

    monkeypatch.setattr(veille_secu.urllib.request, "urlopen", lambda *a, **k: pytest.fail("réseau"))
    with pytest.raises(veille_secu.NetError, match="rien ne part"):
        veille_secu._get("https://api.osv.dev/v1/query")
    assert maj.maj.blocked({"git": True, "origin": "x"}) == hors_ligne.REFUS
    r = quota_releve.relever(lanceur=lambda *a, **k: pytest.fail("claude -p lancé"))
    assert r["ok"] is False and "rien ne part" in r["error"]


def test_ollama_registre_et_telechargement(hl, monkeypatch):
    from astrel import modeles_locaux

    monkeypatch.setattr(config, "OLLAMA_REGISTRY", "https://registry.ollama.ai")

    with pytest.raises(hors_ligne.HorsLigne):
        modeles_locaux.manifest_size("qwen3.5:9b")
    with pytest.raises(hors_ligne.HorsLigne):
        modeles_locaux.pull("qwen3.5:9b", lambda *a: None, threading.Event())
    assert modeles_locaux.local.download("qwen3.5:9b")["error"].startswith("pas de téléchargement")


def test_compte_xai_checks_voix_cerveau(hl, monkeypatch):
    from astrel import brain, compte, xai_oauth
    from astrel.senses import checks
    from astrel.voice import engines

    assert compte.compte.login()["ok"] is False
    monkeypatch.setattr(compte.threading, "Thread", lambda *a, **k: pytest.fail("sonde lancée"))
    compte.compte.refresh()
    with pytest.raises(xai_oauth.ErreurReseau, match="rien ne part"):
        xai_oauth._requete("GET", "https://auth.x.ai/.well-known/openid-configuration")
    with pytest.raises(hors_ligne.HorsLigne):  # le réflexe la prend comme une erreur : rien n'est déclenché
        checks.run({"kind": "url", "url": "https://example.org/"})
    with pytest.raises(hors_ligne.HorsLigne):
        list(engines.EdgeEngine().synthesize("bonjour"))
    with pytest.raises(hors_ligne.HorsLigne):
        object.__new__(brain.Brain).start()


def test_connecteurs_appeles_par_le_coeur(hl):
    from astrel.tools import agenda, translate, transport

    with pytest.raises(hors_ligne.HorsLigne):
        agenda._client({"CALDAV_USERNAME": "u", "CALDAV_PASSWORD": "p"})
    with pytest.raises(hors_ligne.HorsLigne):
        translate._post("/v2/translate", {})
    with pytest.raises(hors_ligne.HorsLigne):
        transport._get("journeys")


def test_voix_piper_pour_tout(hl):
    from astrel.voice import speaker as sp

    class Moteur:
        def __init__(self, name):
            self.name = name

    s = object.__new__(sp.Speaker)
    s._primary, s._fallback = Moteur("edge:fr-FR-RemyMultilingualNeural"), Moteur("piper:fr_FR-upmc-medium")
    s._queue, s._idle, s._generation, s._cancelled = queue.Queue(), threading.Event(), 0, set()
    s.say("Une phrase ordinaire.")
    assert s._queue.get_nowait().local_only is True


# ------------------------------------------------------------------ cœur

class FakeBrain:
    alive, local, model = True, "", "sonnet"

    def __init__(self):
        self.sent, self.stops, self.starts = [], 0, 0

    def send(self, prompt, images=None):
        self.sent.append(prompt)

    def stop(self):
        self.stops += 1

    def start(self, fresh=False):
        self.starts += 1

    def restart(self, fresh=False):
        self.stop()
        self.start(fresh)


class FakeWhatsApp:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")


class FakeMission:
    status = ""


@pytest.fixture
def j(monkeypatch, tmp_path, events):
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    from astrel.coeur import hors_ligne as hlc

    monkeypatch.setattr(hlc.mission_mod, "mission", FakeMission())
    j = object.__new__(core.Astrel)
    j.brain, j.whatsapp, j.status = FakeBrain(), FakeWhatsApp(), state.StatePublisher()
    j._tasks_lock, j._tasks, j._turn_active = threading.Lock(), {}, False
    j._turn_lock, j._send_lock = threading.RLock(), threading.RLock()
    j._local_frozen = None
    j._local_keep_frozen = lambda: None
    j.locaux, j.dits = [], []
    j._tour_local = lambda text, demande, source, channel="", shown=None, joined=None: j.locaux.append((demande, source))
    j.say = lambda text, raw=False, **k: j.dits.append(text)
    return j


@pytest.fixture
def events():
    from astrel.events import bus

    got = []
    stop = bus.subscribe(got.append)
    yield got
    stop()


def test_activation_refusee_pendant_une_tache(j):
    j._turn_active = True
    r = j._hors_ligne_switch(True)
    assert r["ok"] is False and "Stop tout" in r["error"] and r["stop_tout"]
    j._turn_active, j._tasks = False, {"t1": {}}
    assert "tâche de fond" in j._hors_ligne_switch(True)["error"]
    j._tasks = {}
    from astrel.coeur import hors_ligne as hlc

    hlc.mission_mod.mission.status = "active"
    assert "mission" in j._hors_ligne_switch(True)["error"]
    assert not hors_ligne.actif() and j.brain.stops == 0 and j.whatsapp.calls == []


def test_refuse_a_claude_dans_les_deux_sens(j):
    r = j._settings_command("settings_set", {"key": "hors_ligne", "value": True, "_from_claude": True})
    assert r["ok"] is False and not hors_ligne.actif()
    reglages.set(hors_ligne.CLE, True)
    r = j._settings_command("settings_set", {"key": "hors_ligne", "value": False, "_from_claude": True})
    assert r["ok"] is False and hors_ligne.actif()


def test_entree_puis_sortie_par_les_reglages(j, events):
    r = j._settings_command("settings_set", {"key": "hors_ligne", "value": True, "_via": "hud"})
    assert r["ok"] is True and hors_ligne.actif()
    assert j.brain.stops == 1 and j._cycle.etat == cycle_cerveau.ARRET and j.whatsapp.calls == ["stop"]
    model = [e for e in events if e.get("type") == "model"][-1]
    assert model["offline"] is True and model["label"].startswith("Hors ligne")
    # Le cycle ne relance rien pendant le mode.
    assert j._cerveau_lancer() is False and j.brain.starts == 0
    j._cerveau_relancer(fresh=True)
    assert j.brain.starts == 0
    r = j._settings_command("settings_set", {"key": "hors_ligne", "value": False, "_via": "hud"})
    assert r["ok"] is True and not hors_ligne.actif()
    assert j.brain.starts == 1 and j._cycle.etat == cycle_cerveau.OK and j.whatsapp.calls == ["stop", "start"]
    assert "offline" not in [e for e in events if e.get("type") == "model"][-1]


def test_demandes_routees_au_local_et_automatiques_gelees(j, hl, events):
    j.handle("explique une injection SQL", source="clavier", channel="astrel")
    j.handle("avec Claude : refactorise", source="voix", channel="astrel")
    assert j.locaux == [("explique une injection SQL", "clavier"), ("refactorise", "voix")]
    assert any("Claude est arrêté" in (e.get("text") or "") for e in events if e.get("type") == "notice")
    j.handle("résume les mails", source="réflexe")
    assert j._local_frozen and j._local_frozen[0]["source"] == "réflexe" and j.brain.sent == []
    j._local_tick()  # rien ne repart vers Claude tant que le mode est actif
    assert j._local_frozen and j.brain.sent == []


def test_commande_dite_ou_tapee(j, monkeypatch):
    vu = []
    monkeypatch.setattr(core.Astrel, "_hors_ligne_switch", lambda self, on, by="": vu.append((on, by)) or
                        {"ok": True, "text": "fait"})
    j.handle("passe hors ligne", source="voix")
    j.handle("repasse en ligne", source="clavier")
    j.handle("passe hors ligne", source="fond")  # une source automatique (ou Claude) ne bascule rien
    assert vu == [(True, "voix"), (False, "clavier")] and j.dits == ["fait"]


def test_mode_leger_et_whatsapp_refuses_hors_ligne(j, hl):
    assert j._local_switch(True)["ok"] is False
    assert "rien ne part" in j._cmd_whatsapp("whatsapp", {"text": "on"})["error"]
    assert "rien ne part" in j._cmd_whatsapp_file("whatsapp_file", {"path": "/tmp/x"})["error"]


def test_survit_a_une_relance(j, events):
    reglages.set(hors_ligne.CLE, True)
    reglages.reset()  # relu depuis le fichier, comme au démarrage
    assert hors_ligne.actif()
    j._boot_brain()
    assert j.brain.starts == 0 and j._cycle.etat == cycle_cerveau.ARRET
    assert any("Démarrage en mode « rien ne sort »" in (e.get("text") or "") for e in events
               if e.get("type") == "notice")
