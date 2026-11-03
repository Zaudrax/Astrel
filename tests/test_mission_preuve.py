"""Mission PROUVÉE (02/11, plan P43) : « MISSION : FINIE » déclenche une vérification, jamais une fin.

Machine à états (phases d'une mission active, fins distinctes), garde-fou maintenu pendant la
vérification, résultats obsolètes jetés (pause, arrêt, ESTOP, redémarrage, nouvelle tentative),
boucle bornée, décisions réservées à l'utilisateur, origine du critère fixée par le cœur."""
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from astrel import config, core, guard, preuve, totalgate
from astrel import mission as mission_mod
from astrel.mission import (ACCEPTED, ACTIVE, BLOCKED, DONE, PAUSED, STOPPED, TRAVAIL, VERIF_BLOCKED,
                            VERIF_FAILED, VERIFICATION, Mission)
from conftest import remplacer_coeur


@pytest.fixture
def projet(tmp_path):
    p = (tmp_path / "projet").resolve()
    p.mkdir()
    return p


def critere(projet, nom="RAPPORT.md"):
    return {"controles": [{"type": "fichier_existe", "chemin": str(projet / nom), "genre": "fichier"}]}


def lancer(projet, typé=True, origine="utilisateur", **spec):
    m = Mission()
    s = {"objective": "écrire le rapport", "done_when": "RAPPORT.md existe", "scope": [str(projet)], **spec}
    if typé:
        s.setdefault("verification", critere(projet))
    r = m.start(s, origine=origine)
    assert r["ok"], r
    return m


def tour(m, texte):
    assert m.take_prompt()
    return m.after_turn(texte, {})


def verifier(m):
    inst = m.begin_verification()
    assert inst is not None
    r = preuve.verifier(inst, doit_arreter=lambda: not m.still_current(inst))
    return m.apply_verification(inst, r)


# ------------------------------------------------------------------ FINIE n'est qu'une déclaration

def test_finie_sans_le_fichier_n_est_pas_terminee(projet):
    m = lancer(projet)
    assert tour(m, "J'ai écrit le rapport.\nMISSION : FINIE — RAPPORT.md écrit") == (VERIFICATION, "")
    assert m.status == ACTIVE and m.phase == VERIFICATION and m.data["declaration"]["texte"] == "RAPPORT.md écrit"
    assert m.take_prompt() == ""  # aucun tour de Claude pendant la vérification
    assert verifier(m) == (TRAVAIL, "")
    assert m.status == ACTIVE and m.data["preuve"]["statut"] == "echec" and m.data["verifs_ratees"] == 1
    relance = m.take_prompt()
    assert "vérification d'Astrel a échoué" in relance and "absent" in relance


def test_finie_avec_le_fichier_est_verifiee(projet):
    m = lancer(projet)
    (projet / "RAPPORT.md").write_text("fait")
    tour(m, "MISSION : FINIE — fait")
    assert verifier(m)[0] == DONE
    d = m.describe()
    assert d["status"] == DONE and d["preuve"]["verifie"] is True and d["preuve"]["statut"] == "verifiee"
    assert d["historique_preuves"][-1]["statut"] == "verifiee"
    assert "vérifiée" in m.report()


def test_les_verifications_ratees_sont_bornees(projet):
    m = lancer(projet, max_attempts=10, max_turns=50)
    for n in range(1, config.MISSION_MAX_VERIFS + 1):
        tour(m, "MISSION : CONTINUE")  # un CONTINUE ne remet pas le compteur des vérifications à zéro
        tour(m, "MISSION : FINIE — promis")
        etat, _ = verifier(m)
    assert etat == VERIF_FAILED and m.status == VERIF_FAILED and n == config.MISSION_MAX_VERIFS
    assert m.take_prompt() == "" and "vérification a échoué" in m.report()


def test_finie_au_dernier_tour_verifiee_sans_tour_de_plus(projet):
    m = lancer(projet, max_turns=1)
    tour(m, "MISSION : FINIE — fini")
    assert m.over_budget() and m.phase == VERIFICATION  # le budget est atteint : vérifiée quand même
    assert verifier(m)[0] == VERIF_FAILED and m.data["turns"] == 1  # échec : aucun tour de plus
    m2 = lancer(projet, max_turns=1)
    (projet / "RAPPORT.md").write_text("ok")
    tour(m2, "MISSION : FINIE — fini")
    assert verifier(m2)[0] == DONE


def test_bloquee_reste_inchangee(projet):
    m = lancer(projet, max_attempts=1)
    assert tour(m, "MISSION : BLOQUÉE — il manque une clé")[0] == BLOCKED


# ------------------------------------------------------------------ critère libre, décisions de l'utilisateur

def test_critere_libre_jamais_done_mais_acceptable(projet):
    m = lancer(projet, typé=False)
    assert m.data["critere"] is None and "critère libre" in m._prompt(first=True)
    tour(m, "MISSION : FINIE — c'est bon")
    assert m.begin_verification() is None and m.status == ACTIVE and m.phase == VERIF_BLOCKED
    assert not m.reverify()["ok"]
    assert m.accept()["ok"] and m.status == ACCEPTED
    assert m.data["preuve"] is None and "sans preuve" in m.report()


def test_decisions_sur_une_verification_bloquee(projet):
    m = lancer(projet, verification=critere(projet), max_turns=3)
    (projet / "RAPPORT.md").symlink_to("/etc/hostname")  # contrôle bloqué : un lien
    tour(m, "MISSION : FINIE — fini")
    assert verifier(m)[0] == VERIF_BLOCKED and m.status == ACTIVE and m.take_prompt() == ""
    assert m.back_to_work()["ok"] and m.phase == TRAVAIL and "remet au travail" in m.take_prompt()
    m.after_turn("MISSION : FINIE — corrigé", {})
    (projet / "RAPPORT.md").unlink()
    (projet / "RAPPORT.md").write_text("vrai fichier")
    assert verifier(m)[0] == DONE
    # sans budget, on ne remet pas au travail
    m2 = lancer(projet, verification=critere(projet, "AUTRE.md"), max_turns=1)
    (projet / "AUTRE.md").symlink_to("/etc/hostname")
    tour(m2, "MISSION : FINIE — fini")
    assert verifier(m2)[0] == VERIF_BLOCKED
    assert "budget" in m2.back_to_work()["error"] and m2.phase == VERIF_BLOCKED
    (projet / "AUTRE.md").unlink()
    (projet / "AUTRE.md").write_text("ok")
    assert m2.reverify()["ok"] and m2.phase == VERIFICATION and verifier(m2)[0] == DONE


def test_garde_fou_maintenu_pendant_la_verification(projet):
    m = lancer(projet)
    assert guard.mission_fence() is not None
    tour(m, "MISSION : FINIE — fini")
    assert m.phase == VERIFICATION and guard.mission_fence() is not None
    verifier(m)
    tour(m, "MISSION : FINIE — fini")
    (projet / "RAPPORT.md").symlink_to("/etc/hostname")
    assert verifier(m)[0] == VERIF_BLOCKED
    scope, _interdits = guard.mission_fence()
    assert projet in scope  # la barrière tient pendant l'attente de la décision
    v = guard.assess("Write", {"file_path": "/data/ailleurs/x.txt", "content": "x"}, mode="autonome")
    assert v.needs_confirm and guard.L_MISSION_OUT in v.desc


# ------------------------------------------------------------------ résultats obsolètes, concurrence

def test_pause_pendant_la_verification_jette_le_resultat(projet):
    m = lancer(projet)
    (projet / "RAPPORT.md").write_text("ok")
    tour(m, "MISSION : FINIE — fini")
    inst = m.begin_verification()
    m.pause()
    r = preuve.verifier(inst, doit_arreter=lambda: not m.still_current(inst))
    assert r["statut"] == "interrompue"
    assert m.apply_verification(inst, r) == ("", "")
    assert m.status == PAUSED and m.phase == VERIFICATION and m.data["preuve"] is None
    assert m.data["historique_preuves"][-1]["statut"] == "jetee"
    m.resume()  # la vérification est REFAITE
    assert m.status == ACTIVE and m.phase == VERIFICATION and m.take_prompt() == ""
    inst2 = m.begin_verification()
    assert inst2["verification_id"] != inst["verification_id"]
    # un résultat POSITIF de l'ancienne tentative ne vaut rien non plus
    vieux = preuve.verifier(inst)
    assert vieux["statut"] == "verifiee" and m.apply_verification(inst, vieux) == ("", "")
    assert m.apply_verification(inst2, preuve.verifier(inst2))[0] == DONE


def test_arret_ou_seconde_tentative_jettent_le_resultat(projet):
    m = lancer(projet)
    (projet / "RAPPORT.md").write_text("ok")
    tour(m, "MISSION : FINIE — fini")
    inst = m.begin_verification()
    assert m.begin_verification() is None  # une seule vérification à la fois
    r = preuve.verifier(inst)
    m.stop()
    assert m.apply_verification(inst, r) == ("", "") and m.status == STOPPED


def test_redemarrage_pendant_la_verification(projet):
    m = lancer(projet)
    (projet / "RAPPORT.md").write_text("ok")
    tour(m, "MISSION : FINIE — fini")
    inst = m.begin_verification()
    apres = Mission()
    apres.load()  # Astrel redémarre
    d = json.loads(config.MISSION_FILE.read_text())
    assert d["status"] == PAUSED and d["phase"] == VERIFICATION and d["verif_en_cours"] is None
    assert d["generation"] > inst["generation"]
    assert apres.apply_verification(inst, preuve.verifier(inst)) == ("", "")
    apres.resume()
    assert verifier(apres)[0] == DONE


def test_redemarrage_en_attente_de_decision(projet):
    m = lancer(projet, typé=False)
    tour(m, "MISSION : FINIE — fini")
    m.begin_verification()
    apres = Mission()
    apres.load()
    assert apres.status == PAUSED and apres.phase == VERIF_BLOCKED
    apres.resume()
    assert apres.status == ACTIVE and apres.phase == VERIF_BLOCKED and apres.take_prompt() == ""
    assert apres.accept()["ok"] and apres.status == ACCEPTED


def test_application_et_arret_concurrents(projet):
    """Arrêt pendant que le résultat s'applique : l'un OU l'autre, jamais un état mêlé."""
    (projet / "RAPPORT.md").write_text("ok")
    for _ in range(30):
        m = lancer(projet)
        tour(m, "MISSION : FINIE — fini")
        inst = m.begin_verification()
        r = preuve.verifier(inst)
        go = threading.Barrier(2)
        t = threading.Thread(target=lambda: (go.wait(), m.apply_verification(inst, r)))
        t.start()
        go.wait()
        m.stop()
        t.join()
        d = m.describe()
        assert d["status"] in (DONE, STOPPED) and d["verif_en_cours"] is None and d["ended_at"]
        assert (d["status"] == DONE) == bool(d["preuve"] and d["preuve"]["verifie"])


def test_critere_modifie_apres_le_lancement(projet):
    m = lancer(projet)
    (projet / "AUTRE.md").write_text("x")
    assert "tourne déjà" in m.start({"objective": "x", "done_when": "y", "scope": [str(projet)],
                                     "verification": critere(projet, "AUTRE.md")})["error"]
    m.data["critere"]["controles"][0]["chemin"] = str(projet / "AUTRE.md")  # fichier trafiqué puis relu
    tour(m, "MISSION : FINIE — fini")
    assert verifier(m)[0] == VERIF_BLOCKED
    assert m.data["preuve"]["controles"][0]["code"] == "critere_modifie"


def test_ancienne_mission_schema_1(projet):
    config.MISSION_FILE.write_text(json.dumps({
        "objective": "vieux", "done_when": "pytest passe", "scope": [str(projet)], "forbidden": [],
        "forbidden_paths": [], "channel": "", "max_turns": 5, "max_minutes": 60, "max_tokens": 10 ** 6,
        "max_attempts": 3, "lancee_par": "utilisateur", "status": "active", "started_at": 1.0, "ended_at": 0.0,
        "turns": 1, "tokens": 0, "attempts": 0, "reason": "", "last": ""}))
    m = Mission()
    m.load()
    assert m.status == PAUSED and m.phase == TRAVAIL and m.data["schema"] == 1 and m.data["critere"] is None
    m.data["started_at"] = __import__("time").time()
    m.resume()
    tour(m, "MISSION : FINIE — tout passe")
    assert m.begin_verification() is None and m.phase == VERIF_BLOCKED  # jamais « done » sans preuve


# ------------------------------------------------------------------ origine et droits

@pytest.mark.parametrize("action", ["accepter", "reverifier", "reprendre_travail", "approuver", "accept",
                                    "verifier", "travail"])
def test_decisions_reservees_a_utilisateur(action):
    cmd = {"action": action}
    assert totalgate.loosens_guard("mission", cmd)
    assert totalgate.refused_to_claude("mission", {**cmd, "_from_claude": True})


def test_critere_d_astrel_type_obligatoire_et_jamais_approuve_d_office(projet):
    m = Mission()
    r = m.start({"objective": "x", "done_when": "y", "scope": [str(projet)]}, origine="claude")
    assert not r["ok"] and "TYPÉ obligatoire" in r["error"]
    r = m.start({"objective": "x", "done_when": "y", "scope": [str(projet)], "verification": critere(projet),
                 "origine": "utilisateur", "approbation": {"sha": "x"}, "lancee_par": "utilisateur"}, origine="claude")
    d = r["mission"]
    assert d["origine"] == "claude" and d["lancee_par"] == "astrel" and d["approuve"] is False
    assert m.approve()["ok"] and m.describe()["approuve"] is True
    m.data["critere_sha"] = "0" * 64  # l'approbation est liée à l'empreinte EXACTE
    assert m.describe()["approuve"] is False
    m2 = lancer(projet)
    assert "vient déjà de toi" in m2.approve()["error"]


def test_critere_hors_perimetre_refuse_au_lancement(projet, tmp_path):
    m = Mission()
    r = m.start({"objective": "x", "done_when": "y", "scope": [str(projet)],
                 "verification": {"controles": [{"type": "fichier_existe", "chemin": str(tmp_path / "x"),
                                                 "genre": "fichier"}]}})
    assert not r["ok"] and "hors du périmètre" in r["error"]
    r = m.start({"objective": "x", "scope": [str(projet)], "verification": critere(projet)})
    assert r["ok"] and "RAPPORT.md existe" in r["mission"]["done_when"]  # phrase tirée du critère


# ------------------------------------------------------------------ le cœur

@pytest.fixture
def jp(monkeypatch, projet):
    monkeypatch.setattr(core.mission, "data", {})
    monkeypatch.setattr(core.mission, "awaiting", False)
    monkeypatch.setattr(core.mission, "next_prompt", "")
    monkeypatch.setattr(core.channels, "workdir_of", lambda cid: str(projet) if cid == "projet" else "")
    j = object.__new__(core.Astrel)
    j.voice_out = False
    j._turn_active, j._turn_source = False, ""
    j._pending_confirm = None
    j._local_active = lambda: False
    j._floor_taken = lambda: False
    j.rapports = []
    j._post_report = lambda text, channel="": j.rapports.append(text)
    return j


def finir_et_verifier(j):
    assert core.mission.take_prompt()
    core.mission.awaiting = True
    j._mission_turn_end("MISSION : FINIE — fini", {"usage": {}})
    assert core.mission.phase == VERIFICATION and not j.rapports  # pas de compte rendu : pas une fin
    j._mission_tick()
    for t in threading.enumerate():
        if t.name == "mission-preuve":
            t.join(5)


def test_le_coeur_verifie_puis_rend_compte(jp, projet):
    r = jp.on_command({"cmd": "mission", "action": "start", "_via": "hud",
                       "spec": {"objective": "rapport", "channel": "projet", "verification": critere(projet)}})
    assert r["ok"] and r["mission"]["origine"] == "utilisateur"
    (projet / "RAPPORT.md").write_text("ok")
    finir_et_verifier(jp)
    assert core.mission.status == DONE and "vérifiée" in jp.rapports[-1]


def test_le_coeur_previent_quand_utilisateur_doit_decider(jp, projet):
    jp.on_command({"cmd": "mission", "action": "start", "spec": {"objective": "rapport", "done_when": "à voir",
                                                                 "channel": "projet"}})
    finir_et_verifier(jp)
    assert core.mission.phase == VERIF_BLOCKED and "attend ta décision" in jp.rapports[-1]
    r = jp.on_command({"cmd": "mission", "action": "accepter", "_from_claude": True})
    assert not r["ok"] and core.mission.phase == VERIF_BLOCKED  # Claude ne s'accepte pas lui-même
    assert jp.on_command({"cmd": "mission", "action": "accepter", "_via": "hud"})["ok"]
    assert core.mission.status == ACCEPTED and "sans preuve" in jp.rapports[-1]


def test_estop_pendant_la_verification(jp, projet, monkeypatch):
    jp.on_command({"cmd": "mission", "action": "start",
                   "spec": {"objective": "rapport", "channel": "projet", "verification": critere(projet)}})
    (projet / "RAPPORT.md").write_text("ok")
    entre, sors = threading.Event(), threading.Event()
    vrai = preuve.verifier

    def lent(inst, doit_arreter, **k):
        entre.set()
        sors.wait(5)
        return vrai(inst, doit_arreter=doit_arreter, **k)
    monkeypatch.setattr(preuve, "verifier", lent)
    assert core.mission.take_prompt()
    jp._mission_turn_end("MISSION : FINIE — fini", {"usage": {}})
    jp._mission_tick()
    assert entre.wait(5)
    config.ESTOP_FILE.write_text("1")
    jp._mission_tick()  # la boucle voit l'arrêt d'urgence pendant le contrôle
    assert core.mission.status == STOPPED
    sors.set()
    for t in threading.enumerate():
        if t.name == "mission-preuve":
            t.join(5)
    assert core.mission.status == STOPPED and core.mission.data["preuve"] is None


def test_mission_auto_ne_peut_pas_se_donner_d_origine(jp, projet, monkeypatch):
    monkeypatch.setattr(core.Astrel, "_non_claude", lambda self: False)
    monkeypatch.setattr(config, "WORKDIR", projet.parent)  # T25 : périmètre DANS les projets
    remplacer_coeur(monkeypatch, "bus", type("Bus", (), {"publish": lambda self, *a, **k: None})())
    jp._turn_active, jp._turn_source = True, "voix"
    r = jp.on_command({"cmd": "mission_auto", "objective": "x", "done_when": "y", "channel": "projet",
                       "verification": critere(projet), "origine": "utilisateur", "lancee_par": "utilisateur",
                       "_from_claude": True, "_via": "socket"})
    assert r["ok"] and r["mission"]["origine"] == "claude" and r["mission"]["approuve"] is False
    assert not jp.on_command({"cmd": "mission", "action": "approuver", "_from_claude": True})["ok"]
    jp.on_command({"cmd": "mission", "action": "stop"})
    r = jp.on_command({"cmd": "mission_auto", "objective": "x", "done_when": "y", "channel": "projet",
                       "_from_claude": True, "_via": "socket"})
    assert not r["ok"] and "TYPÉ" in r["error"]


def test_le_hud_lance_avec_la_syntaxe_courte(jp, projet):
    r = jp.on_command({"cmd": "mission", "action": "start", "_via": "hud", "spec": {
        "objective": "rapport", "channel": "projet", "verification_lignes": "existe RAPPORT.md\ncommit ."}})
    assert not r["ok"] and "HEAD du dépôt illisible" in r["error"]  # pas de dépôt : refusé au lancement, pas à la fin
    r = jp.on_command({"cmd": "mission", "action": "start", "_via": "hud", "spec": {
        "objective": "rapport", "channel": "projet", "verification_lignes": "existe RAPPORT.md\nlance pytest"}})
    assert not r["ok"] and "ligne 2" in r["error"]
    r = jp.on_command({"cmd": "mission", "action": "start", "_via": "hud", "spec": {
        "objective": "rapport", "channel": "projet", "verification_lignes": "existe RAPPORT.md"}})
    assert r["ok"] and r["mission"]["critere"]["controles"][0]["chemin"] == str(projet / "RAPPORT.md")
    assert r["mission"]["done_when"] == f"le fichier {projet / 'RAPPORT.md'} existe"


# ------------------------------------------------------------------ revue indépendante du 02/11

def test_approuver_pendant_une_verification_ne_la_coince_pas(projet):
    m = lancer(projet, origine="claude")
    (projet / "RAPPORT.md").write_text("ok")
    tour(m, "MISSION : FINIE — fini")
    inst = m.begin_verification()
    assert m.approve()["ok"]
    assert m.apply_verification(inst, preuve.verifier(inst))[0] == DONE
    assert m.describe()["approuve"] is True and "non approuvé" not in m.report()


def test_chien_de_garde_d_une_verification_coincee(projet, monkeypatch):
    m = lancer(projet)
    tour(m, "MISSION : FINIE — fini")
    inst = m.begin_verification()
    assert not m.verification_stuck(45)
    m.data["verif_en_cours"]["depuis"] -= 60
    assert m.verification_stuck(45) and m.phase == VERIF_BLOCKED and m.data["verif_en_cours"] is None
    assert m.apply_verification(inst, preuve.verifier(inst)) == ("", "")
    assert [h["statut"] for h in m.data["historique_preuves"]] == ["abandonnee", "jetee"]  # le résultat tardif
    assert m.take_prompt() == "" and m.phase == VERIF_BLOCKED


def test_astrel_ne_remplace_pas_une_mission_en_pause(projet):
    m = lancer(projet)
    m.pause()
    r = m.start({"objective": "autre", "scope": [str(projet)], "verification": critere(projet, "X")}, origine="claude")
    assert not r["ok"] and "en pause" in r["error"] and m.data["objective"] == "écrire le rapport"
    assert m.start({"objective": "autre", "scope": [str(projet)], "verification": critere(projet, "X")})["ok"]


def test_done_selon_un_critere_d_astrel_le_dit(projet):
    m = lancer(projet, origine="claude")
    (projet / "RAPPORT.md").write_text("ok")
    tour(m, "MISSION : FINIE — fini")
    assert verifier(m)[0] == DONE and "critère choisi par Astrel (non approuvé par toi)" in m.report()


# ------------------------------------------------------------------ essai réel du 02/11

def test_preuve_relative_lue_depuis_le_premier_dossier_du_perimetre(tmp_path, projet):
    canal = (tmp_path / "canal").resolve()
    canal.mkdir()
    m = Mission()
    r = m.start({"objective": "bonjour", "scope": [str(projet)], "base": str(canal),
                 "verification_lignes": "contient BONJOUR.md :: P43 ok"})
    assert r["ok"], r  # avant : lu depuis le canal → « hors du périmètre »
    assert m.data["critere"]["controles"][0]["chemin"] == str(projet / "BONJOUR.md")
    m2 = Mission()
    r = m2.start({"objective": "x", "scope": [str(projet)], "base": str(canal),
                  "verification_lignes": f"existe {canal / 'a.md'}"})
    assert not r["ok"] and "hors du périmètre" in r["error"]  # un chemin absolu ailleurs reste refusé


def test_deux_bloquee_de_suite_arretent_la_mission(projet):
    m = lancer(projet, max_attempts=5)
    assert tour(m, "MISSION : BLOQUÉE — hors périmètre")[0] == "continue"
    end = tour(m, "MISSION : BLOQUÉE — toujours hors périmètre")
    assert end[0] == BLOCKED and "deux fois de suite" in end[1] and m.data["turns"] == 2


def test_bloquee_puis_progres_remet_le_compte_a_zero(projet):
    m = lancer(projet, max_attempts=5)
    tour(m, "MISSION : BLOQUÉE — souci")
    tour(m, "MISSION : CONTINUE — contourné")
    assert tour(m, "MISSION : BLOQUÉE — autre souci")[0] == "continue"
    tour(m, "MISSION : FINIE — fait")  # vérification ratée : le FINIE coupe la série
    assert verifier(m) == (TRAVAIL, "")
    assert tour(m, "MISSION : BLOQUÉE — encore")[0] == "continue"


def test_ligne_absente_n_est_pas_un_bloquee(projet):
    m = lancer(projet, max_attempts=5)
    tour(m, "MISSION : BLOQUÉE — souci")
    assert tour(m, "j'ai oublié la ligne")[0] == "continue"
    assert m.data["bloquees"] == 0


def test_l_empreinte_est_donnee_entiere_au_prompt(projet):
    h = "0" * 64
    m = lancer(projet, verification={"controles": [{"type": "empreinte", "chemin": str(projet / "E.md"),
                                                     "sha256": h}]})
    p = m.take_prompt()
    assert h in p and "ne cherche jamais par calcul" in p


@pytest.mark.skipif(not shutil.which("node"), reason="Node absent")
def test_le_formulaire_signale_un_objectif_hors_du_perimetre():
    js = Path(__file__).resolve().parent.parent / "hud" / "tests" / "mission-chemins.test.mjs"
    r = subprocess.run(["node", "--test", str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


# ------------------------------------------------------------------ lot C : commande observée (03/11)

def cmd_critere(projet, cmd="pytest -q"):
    return {"controles": [{"type": "commande_observee", "dossier": str(projet), "commande": cmd}]}


def lancer_cmd(projet):
    m = lancer(projet, verification=cmd_critere(projet), max_attempts=5)
    return m, preuve.commande_attendue(m.data["critere"]["controles"][0])


def test_commande_observee_reussie_apres_la_derniere_ecriture(projet):
    m, cmd = lancer_cmd(projet)
    assert m.take_prompt()
    m.observe_tool_start("Edit", "e1", {"file_path": str(projet / "a.py")})
    m.observe_tool_start("Bash", "b1", {"command": cmd})
    m.observe_tool_result("b1", False, "3 passed")
    m.observe_tool_start("Read", "r1", {"file_path": str(projet / "a.py")})  # lire après : sans effet
    m.awaiting = True
    assert m.after_turn("MISSION : FINIE — tests verts", {})[0] == VERIFICATION
    assert verifier(m)[0] == DONE


def test_commande_observee_ecriture_apres_le_test(projet):
    m, cmd = lancer_cmd(projet)
    assert m.take_prompt()
    m.observe_tool_start("Bash", "b1", {"command": cmd})
    m.observe_tool_result("b1", False, "ok")
    m.observe_tool_start("Write", "w1", {"file_path": str(projet / "b.py")}, )
    m.awaiting = True
    m.after_turn("MISSION : FINIE — fait", {})
    assert verifier(m) == (TRAVAIL, "")
    assert m.data["preuve"]["controles"][0]["code"] == "commande_perimee"
    assert "commande_perimee" in m.take_prompt()


@pytest.mark.parametrize("entree, resultat, code", [
    ({"run_in_background": True}, (False, "Command running in background"), "commande_absente"),
    ({}, (True, "Exit code 1 FAILED test_a.py"), "commande_echouee"),
    ({}, (True, "Exit code 143 Command timed out after 1s"), "commande_echouee"),
])
def test_commande_observee_echecs(projet, entree, resultat, code):
    m, cmd = lancer_cmd(projet)
    assert m.take_prompt()
    m.observe_tool_start("Bash", "b1", {"command": cmd, **entree})
    m.observe_tool_result("b1", *resultat)
    m.awaiting = True
    m.after_turn("MISSION : FINIE — fait", {})
    assert verifier(m) == (TRAVAIL, "")
    assert m.data["preuve"]["controles"][0]["code"] == code


def test_commande_observee_partie_en_fond_par_le_cli(projet):
    m, cmd = lancer_cmd(projet)
    assert m.take_prompt()
    m.observe_tool_start("Bash", "b1", {"command": cmd})
    m.observe_tool_result("b1", False, "", fond=True)
    assert m.data["observations"]["commandes"][-1]["etat"] == "echec"


def test_commande_observee_refusee_a_claude(projet):
    r = Mission().start({"objective": "x", "scope": [str(projet)], "verification": cmd_critere(projet)},
                        origine="claude")
    assert not r["ok"] and "l'utilisateur" in r["error"]


def test_commande_observee_pas_hors_mission(projet):
    m, cmd = lancer_cmd(projet)
    m.pause()
    m.observe_tool_start("Bash", "b1", {"command": cmd})
    assert m.data["observations"]["commandes"] == []


def test_commande_observee_dans_le_prompt(projet):
    m, cmd = lancer_cmd(projet)
    p = m.take_prompt()
    assert cmd in p and "EXACTEMENT" in p


def test_le_coeur_observe_le_flux(jp, projet):
    from astrel.brain import BrainEvent
    r = jp.on_command({"cmd": "mission", "action": "start", "_via": "hud", "spec": {
        "objective": "tests", "channel": "projet", "verification_lignes": "commande . :: pytest -q"}})
    assert r["ok"], r
    cmd = preuve.commande_attendue(core.mission.data["critere"]["controles"][0])
    jp._mission_observe(BrainEvent("tool_start", tool="Bash", tool_id="b1", data={"input": {"command": cmd},
                                                                                     "agent": "sous-agent"}))
    jp._mission_observe(BrainEvent("tool_result", tool="Bash", tool_id="b1", data={"is_error": False}))
    assert core.mission.data["observations"]["commandes"][-1]["etat"] == "ok"
    finir_et_verifier(jp)
    assert core.mission.status == DONE


def test_le_cerveau_signale_une_commande_en_fond():
    from astrel.brain import _en_fond
    assert _en_fond({"tool_use_result": {"stdout": "", "backgroundTaskId": "bnc2"}})
    assert not _en_fond({"tool_use_result": "Error: Exit code 1"})
    assert not _en_fond({"tool_use_result": {"stdout": "ok", "interrupted": False}})
