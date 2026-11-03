"""Images IA par le navigateur (21/10, plan P14, lots 2 et 3) : file, état des comptes,
quotas, plafond, bascule, évènements `image_job`, commandes du cœur, outil MCP. Le worker
est FAUX ici (aucun navigateur) ; le vrai est éprouvé par `cd imagegen && node --test`."""
from __future__ import annotations

import chemins_machine  # chemins de la machine qui lance la suite
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from astrel import attachments, config, core, events, images_ia
from astrel.images_ia import Etat, Images, Job
from conftest import mode_prive

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c63f8cfc0500f0004850180848a6c210000000049454e44ae426082")


@pytest.fixture
def lieux(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMAGES_RUNTIME", tmp_path / "imagegen")
    monkeypatch.setattr(config, "IMAGES_PROFILS", tmp_path / "imagegen" / "profils")
    monkeypatch.setattr(config, "IMAGES_ETAT_FILE", tmp_path / "imagegen" / "etat.json")
    monkeypatch.setattr(config, "IMAGES_DEBUG_DIR", tmp_path / "imagegen" / "debug")
    monkeypatch.setattr(config, "IMAGES_LOG", tmp_path / "images.log")
    monkeypatch.setattr(config, "IMAGES_SORTIE", tmp_path / "Images-IA")
    monkeypatch.setattr(config, "IMAGES_COMPTES", (("amw", "gemini"),))
    monkeypatch.setattr(config, "IMAGES_PLAFONDS", {"gemini": 20, "chatgpt": 3, "bing": 15})
    monkeypatch.setattr(config, "IMAGES_FENETRES", {"gemini": "jour", "chatgpt": "24h", "bing": "jour"})
    monkeypatch.setattr(config, "ESTOP_FILE", tmp_path / "ESTOP")
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "attachments")
    (tmp_path / "imagegen" / "profils" / "amw").mkdir(parents=True)
    (tmp_path / "Images-IA").mkdir()
    # Tests d'avant P40 A4 : Gemini seul en auto (le repli vers ChatGPT et Bing : test_images_ia_a4.py).
    monkeypatch.setattr(images_ia, "MOTEURS_PRETS", ("gemini",))
    return tmp_path


class FauxWorker:
    """Rejoue un scénario par compte : ok, quota, refus, captcha, session, page…"""

    def __init__(self, sortie: Path, scenarios: dict[str, str]) -> None:
        self.sortie = sortie
        self.scenarios = scenarios
        self.entrees: list[dict] = []

    def __call__(self, entree: dict, ligne) -> dict:
        self.entrees.append(entree)
        alias = Path(entree["profil"]).name
        cas = self.scenarios.get(alias, "ok")
        ligne({"t": "etape", "etape": "ouverture"})
        if cas in ("session", "captcha", "page"):
            return {"t": "echec", "code": cas, "message": f"faux {cas}", "capture": ""}
        ligne({"t": "etape", "etape": "saisie"})
        ligne({"t": "envoye"})
        ligne({"t": "etape", "etape": "generation"})
        if cas == "quota":
            return {"t": "echec", "code": "quota", "message": "You've reached your limit. Try again after 3:00 PM.",
                    "reprise": "15:00"}
        if cas == "refus":
            return {"t": "echec", "code": "refus", "message": "I can't create images like that."}
        if cas == "dehors":
            return {"t": "fini", "fichier": "/etc/hostname", "nom": "hostname"}
        if cas == "variantes":  # forme du résultat de Bing (lots suivants) : 4 images, la 1re principale
            fs = []
            for i in range(5):
                f = self.sortie / f"20261021-120000-bing-{alias}-{i}.png"
                f.write_bytes(PNG)
                fs.append({"fichier": str(f), "nom": f.name})
            fs.insert(2, {"fichier": "/etc/hostname", "nom": "intrus"})
            return {"t": "fini", "fichier": fs[0]["fichier"], "nom": fs[0]["nom"], "fichiers": fs}
        ligne({"t": "etape", "etape": "telechargement"})
        f = self.sortie / f"20261021-120000-gemini-{alias}.png"
        f.write_bytes(PNG)
        return {"t": "fini", "fichier": str(f), "nom": f.name, "octets": len(PNG), "type": "png"}


def fabrique(lieux, scenarios=None):
    vus: list[dict] = []
    worker = FauxWorker(lieux / "Images-IA", scenarios or {})
    img = Images(publish=lambda kind, **ev: vus.append({"type": kind, **ev}), etat=Etat(), runner=worker)
    return img, vus, worker


def attendre(vus, job_id, etats=("fini", "echec"), timeout=5.0):
    fin = time.time() + timeout
    while time.time() < fin:
        for ev in vus:
            if ev["job"] == job_id and ev["etat"] in etats:
                return ev
        time.sleep(0.01)
    raise AssertionError(f"pas d'état final pour {job_id} : {vus}")


# ---------------------------------------------------------------- demande et évènements

def test_image_generee_evenements_et_copie_hud(lieux):
    img, vus, worker = fabrique(lieux)
    r = img.submit("  un caméléon   sur une branche ", "auto", [], "general")
    assert r["ok"] and r["etat"] == "file" and "N'attends pas" in r["message"]
    fin = attendre(vus, r["job"])
    assert fin["etat"] == "fini" and fin["moteur"] == "gemini" and fin["moteur_libelle"] == "Gemini"
    assert fin["compte"] == "amw" and fin["prompt"] == "un caméléon sur une branche" and fin["channel"] == "general"
    # contrat `image_job` (plan P14 étape 3)
    assert {"job", "etat", "moteur", "compte", "prompt", "file", "etape", "erreur"} <= set(fin)
    etats = [(e["etat"], e["etape"]) for e in vus if e["job"] == r["job"]]
    assert etats[0] == ("file", "") and ("en_cours", "generation") in etats and etats[-1][0] == "fini"
    # copie servie au HUD par /api/attachment/<file>, nom simple
    assert attachments.hud_image(fin["file"]) == (PNG, "image/png")
    assert fin["nom"] == "20261021-120000-gemini-amw.png"
    assert fin["images"] == [{"file": fin["file"], "nom": fin["nom"]}]
    # l'entrée du worker : profil du compte, sortie Images-IA, plafond, sans pièces jointes
    e = worker.entrees[0]
    assert e["profil"] == str(lieux / "imagegen" / "profils" / "amw") and e["sortie"] == str(lieux / "Images-IA")
    assert e["fichiers"] == [] and e["plafond"] == 20 and e["fenetre"] == "jour" and e["moteur"] == "gemini"
    assert img.etat.compte("amw")["n"] == 1
    assert "fini : 20261021-120000-gemini-amw.png" in (lieux / "images.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("prompt, engine, files, erreur", [
    ("", "auto", [], "prompt vide"),
    ("x" * 2001, "auto", [], "prompt trop long"),
    ("un chat", "midjourney", [], "moteur inconnu"),
    ("un chat", "chatgpt", [], "moteur pas encore piloté"),  # MOTEURS_PRETS réduit à Gemini (fixture)
    ("un chat", "auto", [f"{chemins_machine.PROJETS}/absente-p40-a4.png"], "pièce jointe 1 refusée : fichier introuvable"),
    ("un chat", "auto", ["relatif.png"], "pièce jointe 1 refusée : chemin absolu attendu"),
    ("un chat", "gemini/x", [], "moteur inconnu"),
])
def test_demandes_refusees(lieux, prompt, engine, files, erreur):
    img, vus, _ = fabrique(lieux)
    r = img.submit(prompt, engine, files, "general")
    assert r["ok"] is False and erreur in r["error"] and not vus


def test_file_pleine_et_un_job_a_la_fois(lieux, monkeypatch):
    monkeypatch.setattr(config, "IMAGES_FILE_MAX", 2)
    porte = threading.Event()
    actifs, maxi = [0], [0]
    worker = FauxWorker(lieux / "Images-IA", {})

    def lent(entree, ligne):
        actifs[0] += 1
        maxi[0] = max(maxi[0], actifs[0])
        porte.wait(5)
        try:
            return worker(entree, ligne)
        finally:
            actifs[0] -= 1

    vus: list[dict] = []
    img = Images(publish=lambda kind, **ev: vus.append(ev), etat=Etat(), runner=lent)
    a = img.submit("un", "auto", [], "general")
    b = img.submit("deux", "auto", [], "general")
    c = img.submit("trois", "auto", [], "general")
    assert a["ok"] and b["ok"] and c["ok"] is False and "file d'images pleine" in c["error"]
    assert b["position"] >= 1
    porte.set()
    attendre(vus, a["job"])
    attendre(vus, b["job"])
    assert maxi[0] == 1  # jamais deux images en même temps


# ---------------------------------------------------------------- quotas, plafond, bascule

def test_quota_marque_le_compte_et_bascule_puis_tous_epuises(lieux, monkeypatch):
    monkeypatch.setattr(config, "IMAGES_COMPTES", (("amw", "gemini"), ("perso", "gemini")))
    (lieux / "imagegen" / "profils" / "perso").mkdir()
    img, vus, worker = fabrique(lieux, {"amw": "quota"})
    r = img.submit("un chat", "auto", [], "general")
    fin = attendre(vus, r["job"])
    assert fin["etat"] == "fini" and fin["compte"] == "perso"  # bascule sur l'autre compte
    assert any(e["etape"] == "bascule" and e["compte"] == "amw" for e in vus)
    amw = img.etat.compte("amw")
    assert amw["epuise_le"] and amw["message_vu"].startswith("You've reached") and amw["reprise_apres"]
    log = (lieux / "images.log").read_text(encoding="utf-8")
    assert "ÉPUISEMENT amw gemini" in log and "reprise=15:00" in log
    # tous épuisés : échec clair, sans boucle
    worker.scenarios["perso"] = "quota"
    vus.clear()
    r2 = img.submit("un chien", "auto", [], "general")
    fin2 = attendre(vus, r2["job"])
    assert fin2["etat"] == "echec" and fin2["code"] == "quota" and fin2["erreur"] == images_ia.TOUS_EPUISES
    assert len(worker.entrees) == 3  # amw sauté (épuisé), perso essayé une fois


def test_disponibilite_sonde_et_repli_24h(lieux):
    e = Etat()
    t0 = time.mktime((2026, 10, 21, 10, 0, 0, 0, 0, -1))
    e.epuise("amw", "limit", "", sonde=False, now=t0)
    assert e.disponible("amw", "gemini", t0 + 3600) == (False, "quota", False)
    assert e.disponible("amw", "gemini", t0 + 6 * 3600) == (True, "", True)  # une sonde après 6 h
    e.epuise("amw", "limit", "", sonde=True, now=t0 + 6 * 3600)          # sonde ratée
    assert e.compte("amw", t0 + 6 * 3600)["epuise_le"] == t0             # épuisement d'origine gardé
    assert e.disponible("amw", "gemini", t0 + 8 * 3600)[0] is False
    assert e.disponible("amw", "gemini", t0 + 12 * 3600)[0] is True            # sonde suivante
    # heure de reprise lue dans le message du site
    e2 = Etat(e.path.with_name("etat2.json"))
    e2.epuise("amw", "limit", "15:00", sonde=False, now=t0)
    assert e2.disponible("amw", "gemini", t0 + 4 * 3600)[0] is False
    assert e2.disponible("amw", "gemini", t0 + 5 * 3600)[0] is True
    # premier succès : épuisement levé, durée mesurée
    assert e2.succes("amw", t0 + 5 * 3600) == pytest.approx(5 * 3600)
    assert e2.compte("amw", t0 + 5 * 3600)["epuise_le"] is None


def test_plafond_gemini_remis_a_zero_le_lendemain(lieux, monkeypatch):
    monkeypatch.setattr(config, "IMAGES_PLAFONDS", {"gemini": 3, "chatgpt": 3})
    e = Etat()
    t0 = time.mktime((2026, 10, 21, 22, 0, 0, 0, 0, -1))
    for _ in range(3):
        e.compter("amw", "gemini", t0)
    assert e.disponible("amw", "gemini", t0) == (False, "plafond", False)
    lendemain = time.mktime((2026, 10, 22, 0, 5, 0, 0, 0, -1))  # minuit passé : remis à zéro
    assert e.disponible("amw", "gemini", lendemain)[0] is True and e.compte("amw", lendemain)["n"] == 0
    assert mode_prive(e.path, 0o600)
    assert not list(e.path.parent.glob(".*.tmp"))  # écriture atomique, rien de traînant


def test_plafond_chatgpt_fenetre_de_24h_depuis_la_premiere_image(lieux):
    """Structure prête pour le lot 5 : ChatGPT gratuit, 3 images, 24 h après la PREMIÈRE."""
    e = Etat()
    t0 = time.mktime((2026, 10, 21, 22, 0, 0, 0, 0, -1))
    for h in (0, 1, 2):
        e.compter("perso", "chatgpt", t0 + h * 3600)
    assert e.compte("perso", t0)["debut"] == t0
    minuit = time.mktime((2026, 10, 22, 0, 30, 0, 0, 0, -1))
    assert e.disponible("perso", "chatgpt", minuit) == (False, "plafond", False)  # minuit ne remet rien à zéro
    assert e.disponible("perso", "chatgpt", t0 + 23.9 * 3600)[0] is False
    assert e.disponible("perso", "chatgpt", t0 + 24 * 3600)[0] is True
    e.compter("perso", "chatgpt", t0 + 25 * 3600)
    c = e.compte("perso", t0 + 25 * 3600)
    assert c["n"] == 1 and c["debut"] == t0 + 25 * 3600


def test_plafonds_par_moteur_dans_la_config():
    assert config.IMAGES_PLAFONDS["gemini"] >= 1 and config.IMAGES_FENETRES["chatgpt"] == "24h"
    assert images_ia.plafond("gemini") == config.IMAGES_PLAFONDS["gemini"]


def test_plafond_atteint_echec_sans_lancer(lieux, monkeypatch):
    monkeypatch.setattr(config, "IMAGES_PLAFONDS", {"gemini": 1, "chatgpt": 3})
    img, vus, worker = fabrique(lieux)
    img.etat.compter("amw", "gemini")
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["code"] == "plafond" and fin["erreur"] == images_ia.TOUS_PLAFOND and worker.entrees == []


@pytest.mark.parametrize("cas, code", [("refus", "refus"), ("captcha", "captcha"), ("session", "session"),
                                       ("page", "page")])
def test_echecs_sans_bascule(lieux, monkeypatch, cas, code):
    monkeypatch.setattr(config, "IMAGES_COMPTES", (("amw", "gemini"), ("perso", "gemini")))
    (lieux / "imagegen" / "profils" / "perso").mkdir()
    img, vus, worker = fabrique(lieux, {"amw": cas})
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["etat"] == "echec" and fin["code"] == code and fin["erreur"] == images_ia.MESSAGES[code]
    assert len(worker.entrees) == 1  # pas de bascule, pas de relance
    if cas == "refus":
        assert fin["texte_ia"] == "I can't create images like that."
    if cas == "captcha":  # compte au repos : la demande suivante ne le touche pas
        assert img.etat.disponible("amw", "gemini")[1] == "captcha"
    if cas == "session":
        assert img.etat.compte("amw")["a_reconnecter"] is True


def test_compte_non_connecte(lieux):
    (lieux / "imagegen" / "profils" / "amw").rmdir()
    img, vus, worker = fabrique(lieux)
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["code"] == "session" and fin["erreur"] == images_ia.NON_CONNECTE and not worker.entrees


def test_fichier_hors_du_dossier_images_refuse(lieux):
    img, vus, _ = fabrique(lieux, {"amw": "dehors"})
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["etat"] == "echec" and fin["file"] == ""


def test_resultat_liste_de_fichiers_avec_principale(lieux):
    """Structure prête pour Bing (4 variantes) : toutes remontent, la principale en tête,
    jamais plus de 4, jamais un fichier hors du dossier Images-IA."""
    img, vus, _ = fabrique(lieux, {"amw": "variantes"})
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["etat"] == "fini" and len(fin["images"]) == images_ia.IMAGES_PAR_JOB == 4
    assert [i["nom"] for i in fin["images"]] == [f"20261021-120000-bing-amw-{i}.png" for i in (0, 1, 2)] + [
        "20261021-120000-bing-amw-3.png"]
    assert fin["file"] == fin["images"][0]["file"] and fin["nom"] == fin["images"][0]["nom"]
    assert all(attachments.hud_image(i["file"]) for i in fin["images"])


def test_moteurs_extensibles():
    # P40 A4 : les trois sites du lot A3 sont pilotés (jamais essayés sur les vrais sites : pilote).
    assert set(images_ia.MOTEURS) == {"gemini", "chatgpt", "bing"}
    assert images_ia.MOTEURS_PRETS == ("gemini", "chatgpt", "bing")
    assert config.IMAGES_PLAFONDS["bing"] >= 1 and config.IMAGES_FENETRES["bing"] == "jour"


def test_estop_annule(lieux):
    (lieux / "ESTOP").write_text("1")
    img, vus, worker = fabrique(lieux)
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["code"] == "estop" and not worker.entrees


def test_estop_pendant_la_generation(lieux):
    """29/10 (petit reste de P41 B3) : « Stop tout » PENDANT une image (le vrai worker est alors arrêté avec
    son Chrome par `ia_web.executer_worker`) : image « estop », jamais « interne » ni montrée."""
    def runner(entree, ligne):
        ligne({"t": "envoye"})
        (lieux / "ESTOP").write_text("1")
        return {"t": "echec", "code": "interne", "message": "worker arrêté"}

    vus: list[dict] = []
    img = Images(publish=lambda kind, **ev: vus.append(ev), etat=Etat(), runner=runner)
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["code"] == "estop" and fin["etat"] == "echec"


def test_reessayer_cree_une_nouvelle_demande(lieux):
    img, vus, worker = fabrique(lieux, {"amw": "page"})
    r = img.submit("un chat", "auto", [], "studio")
    attendre(vus, r["job"])
    assert img.retry("inconnu")["ok"] is False
    worker.scenarios["amw"] = "ok"
    r2 = img.retry(r["job"])
    assert r2["ok"] and r2["job"] != r["job"]
    r3 = img.retry(r["job"])  # second clic sur la même carte : ignoré, aucune nouvelle demande
    assert r3 == {"ok": True, "job": r2["job"], "deja": True}
    fin = attendre(vus, r2["job"])
    assert fin["etat"] == "fini" and fin["channel"] == "studio"
    assert img.retry(r2["job"])["ok"] is False  # une image réussie ne se « réessaie » pas


def test_reprise_lue():
    t0 = time.mktime((2026, 10, 21, 10, 0, 0, 0, 0, -1))
    assert images_ia.reprise_apres("15:00", t0) == t0 + 5 * 3600
    assert images_ia.reprise_apres("09:00", t0) == t0 + 23 * 3600  # demain matin
    assert images_ia.reprise_apres("+3h", t0) == t0 + 3 * 3600
    assert images_ia.reprise_apres("demain", t0) == time.mktime((2026, 10, 22, 0, 0, 0, 0, 0, -1))
    assert images_ia.reprise_apres("", t0) is None and images_ia.reprise_apres("25:00", t0) is None


# ---------------------------------------------------------------- vrai processus, faux worker

FAUX_WORKER = r'''
import json, sys, pathlib
entree = json.loads(sys.stdin.readline())
print(json.dumps({"t": "etape", "etape": "ouverture"}), flush=True)
print("bruit qui n'est pas du JSON", flush=True)
print(json.dumps({"t": "envoye"}), flush=True)
sortie = pathlib.Path(entree["sortie"])
f = sortie / "20261021-120000-gemini-vrai.png"
f.write_bytes(bytes.fromhex(sys.argv[1]))
sys.stderr.write("[images] journal du worker\n")
print(json.dumps({"t": "fini", "fichier": str(f), "nom": f.name}), flush=True)
'''


def test_lancement_reel_d_un_processus(lieux, monkeypatch, tmp_path):
    script = tmp_path / "faux_worker.py"
    script.write_text(FAUX_WORKER, encoding="utf-8")
    monkeypatch.setattr(Images, "commande", staticmethod(lambda: [sys.executable, str(script), PNG.hex()]))
    monkeypatch.setenv("ASTREL_IMAGEGEN_RYTHME", "test")
    monkeypatch.setenv("CLE_API_OPENAI", "secret")
    vus: list[dict] = []
    img = Images(publish=lambda kind, **ev: vus.append(ev), etat=Etat())
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"], timeout=15)
    assert fin["etat"] == "fini" and img.etat.compte("amw")["n"] == 1
    env = images_ia._env_worker()
    assert "ASTREL_IMAGEGEN_RYTHME" not in env and "CLE_API_OPENAI" not in env and "HOME" in env


def test_worker_absent(lieux, monkeypatch):
    monkeypatch.setattr(Images, "commande", staticmethod(lambda: None))
    vus: list[dict] = []
    img = Images(publish=lambda kind, **ev: vus.append(ev), etat=Etat())
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["code"] == "absent"


# ---------------------------------------------------------------- cœur, évènements, outil MCP

def test_image_job_dans_l_historique_du_hud():
    assert "image_job" in events.HISTORY and "image_job" not in events.STICKY


def test_commandes_du_coeur(lieux, monkeypatch):
    img, vus, _ = fabrique(lieux)
    monkeypatch.setattr(images_ia, "images", img)
    j = object.__new__(core.Astrel)
    r = j._on_command({"cmd": "image_generate", "prompt": "un chat", "engine": "auto", "files": [],
                       "_from_claude": True})  # Q4 : libre pour Claude
    assert r["ok"], r
    fin = attendre(vus, r["job"])
    assert fin["etat"] == "fini" and fin["channel"]
    st = j._on_command({"cmd": "image_status"})
    assert st["comptes"][0]["compte"] == "amw" and st["comptes"][0]["images"] == 1
    assert st["comptes"][0]["plafond"] == 20 and st["comptes"][0]["fenetre"] == "jour"
    assert j._on_command({"cmd": "image_retry", "job_id": r["job"]})["ok"] is False
    bad = j._on_command({"cmd": "image_generate", "prompt": "un chat", "channel": "inexistant"})
    assert bad["ok"] is False and "canal inconnu" in bad["error"]
    (lieux / "ESTOP").write_text("1")
    assert "arrêt d'urgence" in j._on_command({"cmd": "image_generate", "prompt": "x"})["error"]


def test_outil_mcp_generate_image(monkeypatch):
    from astrel import mcp_server

    sent = []
    monkeypatch.setattr(mcp_server, "_core", lambda cmd, ensure_ascii=True: sent.append(cmd) or
                        {"ok": True, "job": "abc", "etat": "file"})
    assert mcp_server.generate_image("un caméléon")["job"] == "abc"
    assert sent[0] == {"cmd": "image_generate", "prompt": "un caméléon", "engine": "auto", "files": []}
    monkeypatch.setattr(mcp_server, "_core", lambda cmd, ensure_ascii=True: {"ok": False, "error": "prompt vide"})
    with pytest.raises(Exception, match="prompt vide"):
        mcp_server.generate_image("")


def test_profils_hors_du_navigateur_playwright():
    """Un profil par compte, jamais celui du MCP « playwright » (deux Chrome ne partagent pas un profil)."""
    assert config.IMAGES_PROFILS != config.BROWSER_PROFILE
    assert not config.IMAGES_PROFILS.is_relative_to(config.BROWSER_PROFILE)
