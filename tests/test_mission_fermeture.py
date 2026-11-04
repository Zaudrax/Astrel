"""Fermer une mission terminée (04/11, l'utilisateur : une mission d'essai bloquée le 02/11 restait dans le
panneau Mission après relance ; le × ne masquait que dans le navigateur).

`mission_close` (× du HUD, clé `mission_id`) : mission TERMINÉE seulement, `closed_at` ajouté, rien
d'effacé ; refusée à Claude ; une mission close (ou finie depuis plus de 24 h) est publiée comme
absente. La barrière du garde-fou d'une mission active ou en pause n'en dépend jamais."""
import json
import time
from pathlib import Path

import pytest

from astrel import config, core, guard
from astrel import mission as mission_mod
from astrel.mission import Mission
from conftest import remplacer_coeur

PROJ = config.WORKDIR.resolve() / "ProjetFermeture"
SPEC = {"objective": "écrire ECHEC.md", "done_when": "le fichier existe", "scope": [str(PROJ)]}


class Bus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, kind, **data):
        self.events.append((kind, data))

    def missions(self) -> list[dict]:
        return [d for k, d in self.events if k == "mission"]


@pytest.fixture
def jf(monkeypatch):
    """Un Astrel minimal sur la mission globale (fichier isolé par conftest), bus relevé."""
    m = Mission()
    remplacer_coeur(monkeypatch, "mission", m)
    bus = Bus()
    remplacer_coeur(monkeypatch, "bus", bus)
    j = object.__new__(core.Astrel)
    j.voice_out = False
    j._turn_active, j._turn_source = False, ""
    j._pending_confirm = None
    j._post_report = lambda text, channel="": None
    return j, m, bus


def finie(m: Mission, status: str) -> None:
    assert m.start(SPEC)["ok"]
    if status == mission_mod.PAUSED:
        m.pause()
    elif status != mission_mod.ACTIVE:
        assert m.stop("fin d'essai", status=status)["ok"]


def fichier() -> dict:
    return json.loads(config.MISSION_FILE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ statuts permis et refusés

@pytest.mark.parametrize("status", mission_mod.ENDED)
def test_une_mission_terminee_se_ferme_sans_rien_effacer(status):
    m = Mission()
    finie(m, status)
    avant = fichier()
    r = m.close(avant["id"])
    assert r["ok"], r
    apres = fichier()
    assert apres["closed_at"] > 0 and apres["status"] == status  # le statut ne change pas
    # rien d'effacé : seul `closed_at` est nouveau (la génération avance, comme à chaque transition)
    assert set(apres) - set(avant) == {"closed_at"}
    for cle in ("objective", "done_when", "scope", "reason", "ended_at", "historique_preuves", "preuve", "critere"):
        assert apres[cle] == avant[cle]
    assert apres["generation"] == avant["generation"] + 1
    assert r["mission"]["closed"] is True
    # deux fois : rien de réécrit
    assert m.close(avant["id"])["ok"] and fichier()["closed_at"] == apres["closed_at"]


@pytest.mark.parametrize("status", [mission_mod.ACTIVE, mission_mod.PAUSED])
def test_jamais_une_mission_active_ou_en_pause(status):
    m = Mission()
    finie(m, status)
    r = m.close(fichier()["id"])
    assert not r["ok"] and "arrête-la d'abord" in r["error"]
    assert "closed_at" not in fichier() and m.status == status
    assert not m.describe()["closed"]


def test_mission_id_autre_ou_absente():
    m = Mission()
    assert m.close()["error"] == "aucune mission à fermer"
    finie(m, mission_mod.STOPPED)
    r = m.close("m-000000000000")
    assert not r["ok"] and "une autre l'a remplacée" in r["error"] and "closed_at" not in fichier()
    assert m.close("")["ok"] and fichier()["closed_at"]  # terminal : la mission du fichier


def test_nouvelle_mission_apres_fermeture_visible():
    m = Mission()
    finie(m, mission_mod.BLOCKED)
    m.close()
    assert m.start(SPEC)["ok"]
    assert "closed_at" not in fichier() and m.vue_publiee()["status"] == mission_mod.ACTIVE


# ------------------------------------------------------------------ commande du cœur

def test_commande_refusee_a_claude(jf):
    j, m, bus = jf
    finie(m, mission_mod.BLOCKED)
    mid = fichier()["id"]
    for via in ("socket", "hud"):
        r = j.on_command({"cmd": "mission_close", "mission_id": mid, "_from_claude": True, "_via": via})
        assert not r["ok"] and "réservée à l'utilisateur" in r["error"]
    assert "closed_at" not in fichier() and not bus.missions()


def test_commande_du_hud_ferme_et_publie_comme_absente(jf):
    j, m, bus = jf
    finie(m, mission_mod.BLOCKED)
    mid = fichier()["id"]
    # `id` (identifiant de la requête du HUD) n'est jamais lu comme mission
    r = j.on_command({"cmd": "mission_close", "id": 7, "mission_id": mid, "_from_claude": False, "_via": "hud"})
    assert r["ok"], r
    assert fichier()["closed_at"]
    vue = bus.missions()[-1]
    assert vue["status"] == "" and vue["closed"] is True and vue["id"] == mid
    assert "objective" not in vue  # ni le HUD ni la bulle n'ont de quoi la remontrer


def test_commande_refusee_publie_l_etat_inchange(jf):
    j, m, bus = jf
    finie(m, mission_mod.ACTIVE)
    r = j.on_command({"cmd": "mission_close", "mission_id": fichier()["id"], "_via": "hud"})
    assert not r["ok"] and bus.missions()[-1]["status"] == mission_mod.ACTIVE


def test_permise_sous_arret_d_urgence(jf):
    j, m, _bus = jf
    finie(m, mission_mod.STOPPED)
    config.ESTOP_FILE.write_text("1")
    assert j.on_command({"cmd": "mission_close", "mission_id": fichier()["id"], "_via": "hud"})["ok"]


def test_relance_d_astrel_la_mission_close_reste_absente(jf):
    _j, m, _bus = jf
    finie(m, mission_mod.BLOCKED)
    m.close()
    m2 = Mission()
    m2.load()
    assert m2.status == mission_mod.BLOCKED and m2.vue_publiee() == {
        "status": "", "closed": True, "id": fichier()["id"], "closed_at": fichier()["closed_at"]}


# ------------------------------------------------------------------ fermeture automatique à 24 h

def _vieillir(m: Mission, heures: float) -> None:
    with m.lock:
        m.data["ended_at"] = time.time() - heures * 3600
    m._save()


def test_finie_depuis_plus_de_24_h_close_sans_ecriture():
    m = Mission()
    finie(m, mission_mod.BLOCKED)
    _vieillir(m, 23)
    assert m.vue_publiee()["status"] == mission_mod.BLOCKED
    _vieillir(m, 25)
    brut = config.MISSION_FILE.read_text(encoding="utf-8")
    assert m.vue_publiee()["status"] == "" and m.describe()["closed"]
    assert config.MISSION_FILE.read_text(encoding="utf-8") == brut and "closed_at" not in fichier()


@pytest.mark.parametrize("status", [mission_mod.ACTIVE, mission_mod.PAUSED])
def test_24_h_ne_ferme_jamais_une_mission_en_cours(status):
    m = Mission()
    finie(m, status)
    with m.lock:
        m.data["started_at"] = time.time() - 3 * 86400
        m.data["ended_at"] = time.time() - 3 * 86400  # même un champ incohérent ne la ferme pas
    assert not mission_mod.est_close(m.data) and m.vue_publiee()["status"] == status


def test_est_close_champs_illisibles():
    assert not mission_mod.est_close(None) and not mission_mod.est_close({})
    assert not mission_mod.est_close({"status": "blocked", "ended_at": "hier"})
    assert not mission_mod.est_close({"status": "blocked", "ended_at": 0, "started_at": 0})
    assert mission_mod.est_close({"status": "blocked", "ended_at": 1000.0}, now=1000.0 + 86400)
    assert not mission_mod.est_close({"status": "blocked", "ended_at": 1000.0}, now=1000.0 + 86399)


def test_la_boucle_republie_une_fois_au_passage_des_24_h(jf):
    j, m, bus = jf
    finie(m, mission_mod.BLOCKED)
    _vieillir(m, 23.99)
    j._publish_mission()
    assert bus.missions()[-1]["status"] == mission_mod.BLOCKED
    n = len(bus.missions())
    j._mission_tick()
    assert len(bus.missions()) == n  # rien à republier avant 24 h
    _vieillir(m, 24.01)
    j._mission_tick()
    assert len(bus.missions()) == n + 1 and bus.missions()[-1]["status"] == ""
    j._mission_tick()
    assert len(bus.missions()) == n + 1  # une seule fois


# ------------------------------------------------------------------ barrière du garde-fou intacte

@pytest.fixture
def grants(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def test_barriere_d_une_mission_active_ignore_closed_at(grants):
    m = Mission()
    finie(m, mission_mod.ACTIVE)
    assert m.take_prompt()
    assert not m.close()["ok"]
    # même un `closed_at` écrit à la main (fichier protégé) ne lève pas la barrière d'une mission en cours
    d = fichier()
    d["closed_at"] = time.time() - 3 * 86400
    config.MISSION_FILE.write_text(json.dumps(d), encoding="utf-8")
    fence = guard.mission_fence()
    assert fence and Path(str(PROJ)).resolve() in fence[0]
    autre = str(config.WORKDIR.resolve() / "AutreProjet" / "x.py")
    assert guard.assess("Write", {"file_path": autre}, mode="autonome", cwd=str(PROJ)).needs_confirm
    # en pause pendant le tour : barrière gardée aussi
    m.pause()
    d = fichier()
    d["closed_at"] = time.time()
    config.MISSION_FILE.write_text(json.dumps(d), encoding="utf-8")
    assert d["in_turn"] is True and guard.mission_fence()


def test_mission_fermee_pas_de_barriere_comme_avant(grants):
    m = Mission()
    finie(m, mission_mod.STOPPED)
    assert m.close()["ok"] and guard.mission_fence() is None


# ------------------------------------------------------------------ HUD (contrôle complet : tools/hud_check_rendu.py)

def test_le_hud_envoie_mission_close_et_garde_le_repli():
    js = (config.ROOT / "hud" / "src" / "main.js").read_text(encoding="utf-8")
    assert 'command("mission_close", { mission_id: m.id || "" })' in js
    i = js.index('$("#mission-hide").onclick')
    bloc = js[i:js.index("};", i)]
    # le souvenir du navigateur n'est écrit QU'APRÈS un refus du cœur
    assert bloc.index("if (r.ok) return;") < bloc.index("localStorage.setItem(MISSION_MASQUEE")
    langues = (config.ROOT / "hud" / "src" / "langues.js").read_text(encoding="utf-8")
    assert '"mission.hide": "Fermer"' in langues and '"mission.hide": "Close"' in langues
