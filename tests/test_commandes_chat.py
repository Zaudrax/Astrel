"""Commandes « / » de la barre de chat (05/11, demande de l'utilisateur : « /clear pour clear une conversation et d'autres
commandes utiles »). Analyse (astrel/commandes_chat.py et hud/src/commandes.js : mêmes réponses sur les mêmes
textes), commande HUD `chat_commande` (l'utilisateur seul : refusée à Claude et hors du HUD), effets (chaque commande
appelle le chemin existant), et `say` tapé dans la bulle ou au terminal (jamais envoyé à un modèle). Le parcours
dans le HUD (liste « / », complétion, exécution, /aide) est vérifié en Chrome headless (tools/hud_check_rendu.py,
`check_commandes_chat` ; tools/hud_check_langue.py, `commandes_en`)."""
from __future__ import annotations

import chemins_machine  # chemins de la machine qui lance la suite
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from astrel import commandes_chat as cc, core, hors_ligne, mission as mission_mod
from astrel.coeur import commandes_chat as ccoeur

ROOT = Path(__file__).resolve().parent.parent
HUD = ROOT / "hud"

TEXTES = [
    "/clear", "/Clear  ", "/nouveau", "/new", "/clear maintenant", "/aide", "/help", "/AIDE", "/local", "/local  ",
    "/local résume ce fichier\nen 3 points", "/claude salut", "/mode", "/mode auto", "/mode Hybrid", "/mode turbo",
    "/modèle opus", "/model Sonnet", "/modele gpt", "/horsligne", "/hors-ligne", "/offline", "/enligne", "/online",
    "/silence", "/silent", "/vocal", "/voix", "/dictée", "/dictation", "/stop", "/tout-arrêter", "/stop-all",
    "/xyz", "/tmp est plein ?", "/a-b-c", f"{chemins_machine.PROJETS}/x.py est-ce que ça marche ?", "/tmp.log", "/1", "/", "/ clear",
    "//clear", " /clear", "/clear.", "/clear!", "/mode-", "~/clear", "clear", "", f"{chemins_machine.HOME}", "/données",
    "/é" * 40,
]


def vue(a):
    return None if a is None else {"nom": a.nom, "arg": a.arg, "code": a.code, "inconnue": a.inconnue}


# --------------------------------------------------------------------------- analyse

def test_nom_connu_en_premier_mot_seulement():
    assert vue(cc.analyser("/clear")) == {"nom": "clear", "arg": "", "code": "", "inconnue": ""}
    assert cc.analyser("/nouveau").nom == "clear"
    assert cc.analyser("/Dictée").nom == "dictee"
    assert vue(cc.analyser("/modèle Opus")) == {"nom": "modele", "arg": "opus", "code": "", "inconnue": ""}
    assert cc.analyser("/mode hybrid").arg == "hybride"


@pytest.mark.parametrize("texte", [f"{chemins_machine.PROJETS}/x.py est-ce que ça marche ?", "/tmp.log", "/1", "/", "/ clear",
                                   "//clear", " /clear", "/clear.", "~/clear", f"{chemins_machine.HOME}/Documents", "bonjour"])
def test_chemin_ou_espace_devant_pas_une_commande(texte):
    assert cc.analyser(texte) is None


def test_commande_inconnue_et_arguments():
    a = cc.analyser("/xyz")
    assert a.code == "inconnue" and a.inconnue == "xyz" and not a.ok
    assert a.erreur == "Commande inconnue : /xyz. /aide donne la liste ; rien n'est envoyé."
    assert cc.analyser("/tmp est plein ?").code == "inconnue"  # dans le HUD : une espace devant l'envoie
    assert cc.analyser("/clear maintenant").code == "sans_argument"
    assert cc.analyser("/local").erreur == "Écris ta demande après /local."
    assert cc.analyser("/mode turbo").erreur == "/mode attend une de ces valeurs : auto, local, claude, hybride."


def test_local_et_claude_ecrivent_le_prefixe_du_routeur():
    from astrel import routeur

    for texte, moteur in (("/local résume ça", routeur.LOCAL), ("/claude résume ça", routeur.CLAUDE)):
        envoye = cc.texte_routage(cc.analyser(texte))
        assert routeur.forced_engine(envoye) == (moteur, "résume ça")
    assert cc.texte_routage(cc.analyser("/clear")) == ""


@pytest.mark.skipif(not shutil.which("node"), reason="Node absent")
def test_hud_et_coeur_analysent_pareil():
    """Même table (noms, alias, arguments, valeurs) et même réponse sur chaque texte, en JavaScript et en Python."""
    code = ("import('./hud/src/commandes.js').then(m => { const t = JSON.parse(process.argv[1]); "
            "console.log(JSON.stringify({ table: m.COMMANDES.map(c => [c.nom, c.en, c.alias, c.arg, c.valeurs || []]), "
            "vues: t.map(s => m.analyser(s)), routage: t.map(s => m.texteRoutage(m.analyser(s))) })); })")
    r = subprocess.run(["node", "-e", code, json.dumps(TEXTES)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["table"] == [[c.nom, c.en, list(c.alias), c.arg, list(c.valeurs)] for c in cc.COMMANDES]
    assert got["vues"] == [vue(cc.analyser(t)) for t in TEXTES]
    assert got["routage"] == [cc.texte_routage(cc.analyser(t)) if cc.analyser(t) else "" for t in TEXTES]


@pytest.mark.skipif(not shutil.which("node"), reason="Node absent")
def test_fonctions_pures_du_hud_node():
    r = subprocess.run(["node", "--test", str(HUD / "tests" / "commandes.test.mjs")], cwd=ROOT, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


@pytest.mark.skipif(not shutil.which("node"), reason="Node absent")
def test_chaque_commande_decrite_en_francais_et_en_anglais():
    code = ("import('./hud/src/langues.js').then(m => console.log(JSON.stringify({ fr: Object.keys(m.FR), "
            "en: Object.keys(m.EN) })))")
    r = subprocess.run(["node", "-e", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    voulues = {f"slash.desc.{c.nom}" for c in cc.COMMANDES} | {
        f"slash.val.{c.nom}.{v}" for c in cc.COMMANDES for v in c.valeurs} | {
        f"slash.err.{k}" for k in ("inconnue", "sans_argument", "texte_manquant", "choix")}
    for lang in ("fr", "en"):
        assert voulues <= set(got[lang]), (lang, sorted(voulues - set(got[lang])))


# --------------------------------------------------------------------------- cœur

@pytest.fixture
def astrel(monkeypatch):
    j = object.__new__(core.Astrel)
    j._off = False
    j._tasks_lock = threading.Lock()
    j._tasks = {}
    appels: list = []
    j.appels = appels
    j.reset_session = lambda *a, **k: appels.append(("reset",))
    j._settings_command = lambda name, cmd: (appels.append(("settings", name, cmd["key"], cmd["value"], cmd["_via"]))
                                             or {"ok": True, "groups": []})
    j._hors_ligne_switch = lambda on, by="HUD": appels.append(("hors_ligne", on, by)) or {"ok": True, "text": "fait"}
    j._set_model = lambda m: appels.append(("model", m)) or {"ok": True, "model": m}
    j.set_usage = lambda w: appels.append(("usage", w)) or {"ok": True, "usage": w}
    j._cmd_stop = lambda name, cmd: appels.append(("stop",)) or {"ok": True}
    j.handle = lambda text, source="voix", channel="", **k: appels.append(("handle", text, source, channel))
    j._user_present = lambda *a: None
    j._mark_resent = lambda cmd: None
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)
    return j


def hud(nom, arg="", **extra):
    return {"cmd": "chat_commande", "nom": nom, "arg": arg, "channel": "general", "_via": "hud", "_from_claude": False,
            **extra}


def attendre(appels, n=1, delai=2.0):
    t0 = time.time()
    while len(appels) < n and time.time() - t0 < delai:
        time.sleep(0.01)
    return appels


@pytest.mark.parametrize("cmd", [
    {"cmd": "chat_commande", "nom": "clear", "_from_claude": True, "_via": "hud"},
    {"cmd": "chat_commande", "nom": "clear", "_from_claude": True, "_via": "socket"},
    {"cmd": "chat_commande", "nom": "clear", "_from_claude": False, "_via": "socket"},  # terminal, bulle
    {"cmd": "chat_commande", "nom": "clear"},  # interne, sans HUD
])
def test_refusee_a_claude_et_hors_du_hud(astrel, cmd):
    r = astrel._on_command(cmd)
    assert r == {"ok": False, "error": "chat_commande est réservé au HUD de l'utilisateur"}
    assert astrel.appels == []


def test_clear_remet_la_session_a_zero_et_oublie_le_local(astrel):
    oubli = []
    astrel.__dict__["_moteur_local_obj"] = type("M", (), {"oublier": lambda self: oubli.append(1)})()
    r = astrel._on_command(hud("clear"))
    assert r == {"ok": True, "vider": True}
    assert attendre(astrel.appels) == [("reset",)] and oubli == [1]
    assert astrel._on_command(hud("nouveau"))["ok"] is True


def test_clear_refusee_pendant_une_tache_un_tour_une_mission_ou_le_local(astrel, monkeypatch):
    astrel._tasks = {"t1": object()}
    assert astrel._on_command(hud("clear")) == {"ok": False, "error": ccoeur.CLEAR_TACHE}
    astrel._tasks = {}
    astrel._turn_active = True
    assert astrel._on_command(hud("clear")) == {"ok": False, "error": ccoeur.CLEAR_TOUR}
    astrel._turn_active = False
    monkeypatch.setattr(type(mission_mod.mission), "status", property(lambda self: mission_mod.PAUSED))
    assert astrel._on_command(hud("clear")) == {"ok": False, "error": ccoeur.CLEAR_MISSION}
    monkeypatch.setattr(type(mission_mod.mission), "status", property(lambda self: ""))
    astrel._local_etat()["actif"] = True
    assert astrel._on_command(hud("clear")) == {"ok": False, "error": ccoeur.CLEAR_LOCAL}
    time.sleep(0.05)
    assert astrel.appels == []


@pytest.mark.parametrize("nom, arg, attendu", [
    ("mode", "local", ("settings", "settings_set", "moteur_mode", "local", "hud")),
    ("mode", "hybrid", ("settings", "settings_set", "moteur_mode", "hybride", "hud")),
    ("horsligne", "", ("hors_ligne", True, "HUD")),
    ("enligne", "", ("hors_ligne", False, "HUD")),
    ("modele", "opus", ("model", "opus")),
    ("silence", "", ("usage", "silencieux")),
    ("vocal", "", ("usage", "conversation")),
    ("dictee", "", ("usage", "dictee")),
    ("stop", "", ("stop",)),
])
def test_chaque_commande_prend_le_chemin_existant(astrel, nom, arg, attendu):
    r = astrel._on_command(hud(nom, arg))
    assert r.get("ok") is True, r
    assert astrel.appels == [attendu]


def test_aide_tout_arreter_et_arguments_revalides_par_le_coeur(astrel):
    assert astrel._on_command(hud("aide"))["text"].startswith("Commandes du chat : /aide, /clear, /local …")
    assert astrel._on_command(hud("tout-arreter")) == {"ok": False, "error": ccoeur.TOUT_ARRETER_HUD}
    assert astrel._on_command(hud("mode", "turbo"))["error"].startswith("/mode attend")
    assert astrel._on_command(hud("clear", "tout"))["error"].startswith("/clear ne prend rien")
    assert astrel._on_command(hud("xyz"))["error"].startswith("Commande inconnue : /xyz.")
    assert astrel._on_command(hud("data/x"))["error"].startswith("Commande inconnue")
    assert astrel.appels == []


def test_mode_refuse_hors_ligne_et_refus_du_mode_rien_ne_sort_transmis(astrel, monkeypatch):
    monkeypatch.setattr(hors_ligne, "actif", lambda: True)
    assert astrel._on_command(hud("mode", "claude")) == {"ok": False, "error": ccoeur.MODE_HORS_LIGNE}
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)
    astrel._hors_ligne_switch = lambda on, by="HUD": {"ok": False, "error": "occupé", "stop_tout": True}
    assert astrel._on_command(hud("horsligne")) == {"ok": False, "error": "occupé", "stop_tout": True}
    astrel._hors_ligne_switch = lambda on, by="HUD": {"ok": True, "hors_ligne": False, "text": "déjà"}
    assert astrel._on_command(hud("enligne"))["deja"] is True


def test_local_et_claude_par_chat_commande_passent_par_say_avec_le_prefixe(astrel):
    vus = []
    astrel._cmd_say = lambda name, cmd: vus.append((cmd["text"], cmd["_via"], cmd["channel"])) or {"ok": True}
    assert astrel._on_command(hud("local", "résume ça"))["ok"] is True
    assert astrel._on_command(hud("claude", "et toi ?"))["ok"] is True
    assert vus == [("en local : résume ça", "hud", "general"), ("avec Claude : et toi ?", "hud", "general")]


def test_reflechir_part_au_local_avec_la_reflexion(astrel):
    """05/11 (lot 8) : /reflechir <question> → `_tour_local(..., reflexion=True)` (jamais Claude, rien de routé),
    depuis le HUD, son nom anglais et la bulle ; refusée sans question ou si le local répond déjà."""
    from astrel.coeur import local_tour
    vus = []
    astrel._tour_local = lambda text, demande, source, channel="", shown=None, joined=None, outils=False, \
        reflexion=None, navigateur=False: vus.append((text, demande, source, channel, outils, reflexion))
    assert astrel._on_command(hud("reflechir", "pourquoi le ciel est bleu ?")) == {"ok": True, "local": True}
    assert astrel._on_command(hud("think", "why?"))["ok"] is True
    assert astrel._on_command(say("/réfléchir 17 fois 23 ?", channel="general"))["resultat"]["ok"] is True
    assert vus == [("/reflechir pourquoi le ciel est bleu ?", "pourquoi le ciel est bleu ?", "clavier", "general",
                    False, True), ("/reflechir why?", "why?", "clavier", "general", False, True),
                   ("/reflechir 17 fois 23 ?", "17 fois 23 ?", "clavier", "general", False, True)]
    assert astrel._on_command(hud("reflechir"))["error"] == "Écris ta demande après /reflechir."
    astrel._local_etat()["actif"] = True
    assert astrel._on_command(hud("reflechir", "x")) == {"ok": False, "error": local_tour.OCCUPE}
    assert len(vus) == 3 and not [a for a in astrel.appels if a[0] == "handle"]


# --------------------------------------------------------------------------- say : bulle, terminal, Claude, HUD

def say(text, via="socket", claude=False, **extra):
    return {"cmd": "say", "text": text, "source": "clavier", "_via": via, "_from_claude": claude, **extra}


def test_bulle_commande_jamais_envoyee_a_un_modele(astrel):
    r = astrel._on_command(say("/clear"))
    assert r["ok"] is True and r["commande"] == "clear" and r["resultat"]["ok"] is True
    assert attendre(astrel.appels) == [("reset",)]
    r = astrel._on_command(say("/xyz"))
    assert r["resultat"] == {"ok": False, "error": "Commande inconnue : /xyz. /aide donne la liste ; rien n'est envoyé."}
    assert astrel._on_command(say("/tout-arreter"))["resultat"]["error"] == ccoeur.TOUT_ARRETER_HUD
    time.sleep(0.05)
    assert not [a for a in astrel.appels if a[0] == "handle"]


def test_bulle_local_et_chemin(astrel):
    astrel._on_command(say("/local résume ça"))
    astrel._on_command(say(f"{chemins_machine.PROJETS}/x.py est-ce que ça marche ?"))
    got = sorted(a[1] for a in attendre(astrel.appels, 2) if a[0] == "handle")
    assert got == [f"{chemins_machine.PROJETS}/x.py est-ce que ça marche ?", "en local : résume ça"]


def test_say_de_claude_ou_du_hud_jamais_lu_comme_une_commande(astrel):
    astrel._on_command(say("/clear", via="socket", claude=True, source="fond"))
    astrel._on_command(say("/tmp est plein ?", via="hud"))  # HUD : « ␣/tmp… », envoyé tel quel
    textes = sorted(a[1] for a in attendre(astrel.appels, 2) if a[0] == "handle")
    assert ("reset",) not in astrel.appels
    assert textes == ["/tmp est plein ?", "[Message envoyé par un process de Claude, pas par l'utilisateur.] /clear"]
