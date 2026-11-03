"""Images IA, lot A4 du plan P40 (28/10, demande de l'utilisateur : « coder les étapes manquantes » du
multicompte) : pièces jointes par le filtre du cœur, moteur ET modèle, auto (ordre des Réglages,
repli de compte en compte puis de moteur en moteur) ou manuel (même moteur seulement, proposition
« Essayer avec X »), modèles du catalogue seulement, refus rythme/nuit du worker, mémoire en
blocage, comptes (ajouter, retirer, ordre) et connexion (Chrome visible SANS pilotage, l'utilisateur
seul), évènement `ia_etat`, outil MCP. Le worker et Chrome sont FAUX ici : rien n'est lancé."""
from __future__ import annotations

import chemins_machine  # chemins de la machine qui lance la suite
import json
import os
import threading
import time
from pathlib import Path

import pytest

from astrel import attachments, config, core, events, ia_connexion, ia_web, images_ia, memoire, settings
from astrel.images_ia import Etat, Images
from astrel.platform import systeme
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
    monkeypatch.setattr(ia_connexion, "_ouvertes", {})
    monkeypatch.setattr(ia_connexion, "on_change", None)
    (tmp_path / "imagegen" / "profils" / "amw").mkdir(parents=True)
    (tmp_path / "Images-IA").mkdir()
    (tmp_path / "attachments").mkdir()
    return tmp_path


def comptes_json(lieux, *comptes):
    ia_web.ecrire_comptes([{"alias": a, "fournisseur": f} for a, f in comptes])
    for a, _f in comptes:
        (lieux / "imagegen" / "profils" / a).mkdir(parents=True, exist_ok=True)


class FauxWorker:
    """Scénario par (alias, service) ou par alias : ok, quota, rythme, nuit, occupe…"""

    def __init__(self, sortie: Path, scenarios: dict[str, str] | None = None) -> None:
        self.sortie = sortie
        self.scenarios = scenarios or {}
        self.entrees: list[dict] = []

    def __call__(self, entree: dict, ligne) -> dict:
        self.entrees.append(entree)
        alias = Path(entree["profil"]).name
        cas = self.scenarios.get(f"{alias}/{entree['service']}", self.scenarios.get(alias, "ok"))
        if cas in ("rythme", "nuit"):
            return {"t": "echec", "code": cas, "message": f"faux {cas}", "reprise": "07:00" if cas == "nuit" else "13:40"}
        if cas in ("occupe", "plafond", "fichiers"):
            return {"t": "echec", "code": cas, "message": f"faux {cas}"}
        ligne({"t": "envoye"})
        if cas == "quota":
            return {"t": "echec", "code": "quota", "message": "limit", "reprise": ""}
        f = self.sortie / f"{entree['service']}-{alias}-{len(self.entrees)}.png"
        f.write_bytes(PNG)
        return {"t": "fini", "fichier": str(f), "nom": f.name, "modele_utilise": entree["modele"]}


def fabrique(lieux, scenarios=None):
    vus: list[dict] = []
    etats: list[dict] = []
    worker = FauxWorker(lieux / "Images-IA", scenarios)
    img = Images(publish=lambda kind, **ev: vus.append({"type": kind, **ev}), etat=Etat(), runner=worker,
                 publish_etat=lambda kind, **ev: etats.append({"type": kind, **ev}))
    return img, vus, worker, etats


def attendre(vus, job_id, timeout=5.0):
    fin = time.time() + timeout
    while time.time() < fin:
        for ev in vus:
            if ev.get("job") == job_id and ev["etat"] in ("fini", "echec"):
                return ev
        time.sleep(0.01)
    raise AssertionError(f"pas d'état final pour {job_id} : {vus}")


def png(lieux, nom="ref.png", data=PNG, dossier="attachments") -> str:
    f = lieux / dossier / nom
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return str(f)


# ---------------------------------------------------------------- auto : ordre et repli

def test_auto_repli_compte_puis_moteur_dans_l_ordre(lieux):
    """Demande du 27/10 : comptes Google pour le volume, compte épuisé sauté, puis moteur suivant."""
    comptes_json(lieux, ("amw", "google"), ("perso", "google"))
    img, vus, worker, _ = fabrique(lieux, {"amw": "quota", "perso": "quota"})
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    # gemini/amw épuisé → gemini/perso épuisé → chatgpt sans compte openai → bing (sans compte)
    assert [(Path(e["profil"]).name, e["service"]) for e in worker.entrees] == [
        ("amw", "gemini"), ("perso", "gemini"), ("anonyme", "bing")]
    assert fin["etat"] == "fini" and fin["moteur"] == "bing" and fin["compte"] == "anonyme" and fin["mode"] == "auto"
    # la demande suivante saute les deux comptes épuisés sans les relancer
    r2 = img.submit("un chien", "auto", [], "general")
    attendre(vus, r2["job"])
    assert [e["service"] for e in worker.entrees[3:]] == ["bing"]


def test_auto_suit_l_ordre_des_reglages(lieux):
    comptes_json(lieux, ("amw", "google"), ("chat", "openai"))
    settings.reglages.set("images_ordre", "chatgpt,gemini,bing")
    img, vus, worker, _ = fabrique(lieux)
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["moteur"] == "chatgpt" and fin["compte"] == "chat"
    assert Path(worker.entrees[0]["profil"]).name == "chat" and worker.entrees[0]["modele"] == "defaut"


def test_rythme_et_profil_occupe_compte_suivant_sans_rien_noter(lieux):
    comptes_json(lieux, ("amw", "google"), ("perso", "google"))
    img, vus, worker, _ = fabrique(lieux, {"amw": "rythme"})
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["etat"] == "fini" and fin["compte"] == "perso"
    assert not img.etat.compte("amw", moteur="gemini").get("epuise_le")  # rien contre ce compte
    assert "passé (rythme)" in (lieux / "images.log").read_text(encoding="utf-8")


def test_tous_au_rythme_dit_l_heure_de_reprise(lieux, monkeypatch):
    monkeypatch.setattr(images_ia, "MOTEURS_PRETS", ("gemini",))
    comptes_json(lieux, ("amw", "google"), ("perso", "google"))
    img, vus, worker, _ = fabrique(lieux, {"amw": "rythme", "perso": "occupe"})
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["code"] == "rythme" and "13:40" in fin["erreur"] and len(worker.entrees) == 2


def test_nuit_echec_avec_reprise_sans_autre_compte(lieux):
    comptes_json(lieux, ("amw", "google"), ("perso", "google"))
    img, vus, worker, _ = fabrique(lieux, {"amw": "nuit"})
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["code"] == "nuit" and "07:00" in fin["erreur"] and len(worker.entrees) == 1


# ---------------------------------------------------------------- manuel

def test_manuel_meme_moteur_seulement_puis_proposition(lieux):
    """Manuel : repli sur un autre compte du MÊME moteur, jamais un autre moteur sans l'utilisateur."""
    comptes_json(lieux, ("amw", "google"), ("perso", "google"))
    img, vus, worker, _ = fabrique(lieux, {"amw": "quota", "perso": "quota"})
    r = img.submit("un chat", "gemini", [], "general", "defaut")
    fin = attendre(vus, r["job"])
    assert {e["service"] for e in worker.entrees} == {"gemini"} and len(worker.entrees) == 2
    assert fin["etat"] == "echec" and fin["code"] == "quota" and fin["mode"] == "manuel"
    assert fin["proposition"] == "bing" and fin["proposition_libelle"] == "Bing Image Creator"
    # « Essayer avec X » : seul le moteur proposé, une seule fois
    assert img.retry(r["job"], "chatgpt")["ok"] is False
    r2 = img.retry(r["job"], "bing")
    assert r2["ok"] and r2["mode"] == "manuel"
    assert attendre(vus, r2["job"])["moteur"] == "bing"
    assert img.retry(r["job"], "bing")["deja"] is True


def test_manuel_par_les_reglages(lieux):
    comptes_json(lieux, ("amw", "google"), ("chat", "openai"))
    settings.reglages.set("images_mode", "manuel")
    settings.reglages.set("images_manuel", "chatgpt/defaut")
    img, vus, worker, _ = fabrique(lieux, {"chat": "quota"})
    r = img.submit("un chat", "auto", [], "general")
    fin = attendre(vus, r["job"])
    assert r["mode"] == "manuel" and [e["service"] for e in worker.entrees] == ["chatgpt"]
    assert fin["code"] == "quota" and fin["proposition"] == "gemini"  # proposé, jamais pris seul


def test_modeles_du_catalogue_seulement(lieux, monkeypatch, tmp_path):
    img, vus, _w, _ = fabrique(lieux)
    r = img.submit("un chat", "gemini", [], "general", "nano-banana")
    assert r["ok"] is False and "modèle inconnu" in r["error"] and "defaut" in r["error"] and not vus
    # Catalogue qui liste un modèle NON relevé (modeles_releves faux) : jamais proposé ni accepté
    cat = json.loads(ia_web.CATALOGUE_FILE.read_text(encoding="utf-8"))
    cat["services"]["gemini"]["modeles"].append({"id": "pro", "libelle": "Pro", "cout": 1})
    faux = tmp_path / "ia-web.json"
    faux.write_text(json.dumps(cat), encoding="utf-8")
    monkeypatch.setattr(ia_web, "CATALOGUE_FILE", faux)
    assert [m["id"] for m in ia_web.modeles_proposes("gemini")] == ["defaut"]
    assert img.submit("un chat", "gemini", [], "general", "pro")["ok"] is False
    assert all(v.endswith("/defaut") for v, _ in settings._images_manuels())
    cat["services"]["gemini"]["modeles_releves"] = True
    faux.write_text(json.dumps(cat), encoding="utf-8")
    assert [m["id"] for m in ia_web.modeles_proposes("gemini")] == ["defaut", "pro"]


# ---------------------------------------------------------------- pièces jointes

def test_pieces_jointes_filtrees_par_le_coeur(lieux):
    img, vus, worker, _ = fabrique(lieux)
    a = png(lieux)
    r = img.submit("comme cette photo", "auto", [a], "general")
    fin = attendre(vus, r["job"])
    assert fin["etat"] == "fini" and fin["pieces"] == 1 and worker.entrees[0]["fichiers"] == [str(Path(a).resolve())]


def test_pieces_jointes_refusees(lieux, tmp_path):
    img, vus, _w, _ = fabrique(lieux)
    ok = png(lieux)
    dehors = tmp_path / "ailleurs" / "x.png"
    dehors.parent.mkdir()
    dehors.write_bytes(PNG)
    lien = lieux / "attachments" / "lien.png"
    lien.symlink_to(dehors)
    dur = lieux / "attachments" / "dur.png"
    os.link(png(lieux, "origine.png"), dur)
    cas = {
        "relatif.png": "chemin absolu",
        str(dehors): "hors des pièces jointes",
        str(lien): "hors des pièces jointes",
        str(dur): "pas un fichier ordinaire",
        png(lieux, "texte.png", b"<html>pas une image</html>"): "PNG, JPEG ou WebP",
        png(lieux, "lourde.png", PNG + b"\0" * (5 * 1024 * 1024)): "trop lourde",
        png(lieux, "mes-secrets.png"): "secret",
        png(lieux, "gif.png", b"GIF89a" + b"\0" * 20): "PNG, JPEG ou WebP",
    }
    for chemin, raison in cas.items():
        r = img.submit("un chat", "auto", [chemin], "general")
        assert r["ok"] is False and raison in r["error"], (chemin, r)
    r = img.submit("un chat", "auto", [ok] * 2, "general")
    assert r["ok"] is False and "deux fois" in r["error"]
    sept = [png(lieux, f"p{i}.png") for i in range(7)]
    assert "6 pièces jointes au plus" in img.submit("un chat", "auto", sept, "general")["error"]
    assert not vus  # rien n'est parti


def test_pieces_jointes_jamais_sur_bing(lieux):
    img, vus, worker, _ = fabrique(lieux, {"amw": "quota"})
    a = png(lieux)
    r = img.submit("un chat", "bing", [a], "general")
    assert r["ok"] is False and "ne prend pas de pièce jointe" in r["error"]
    # auto : Gemini épuisé, ChatGPT sans compte, Bing sauté (pas de pièce) → échec, Bing jamais lancé
    fin = attendre(vus, img.submit("un chat", "auto", [a], "general")["job"])
    assert fin["etat"] == "echec" and {e["service"] for e in worker.entrees} == {"gemini"}


# ---------------------------------------------------------------- mémoire, ESTOP

def test_rien_ne_part_si_la_memoire_est_en_blocage(lieux):
    memoire.STATE_FILE.write_text(json.dumps({"at": time.time(), "state": "blocage", "swap_pct": 95}))
    img, vus, worker, _ = fabrique(lieux)
    r = img.submit("un chat", "auto", [], "general")
    assert r["ok"] is False and "mémoire du PC" in r["error"] and not worker.entrees
    assert "Mémoire" in img.compte_commande("image_compte_connecter", {"alias": "amw"})["error"]
    # mesure périmée : on laisse passer (comme le hook des agents)
    memoire.STATE_FILE.write_text(json.dumps({"at": time.time() - 120, "state": "blocage"}))
    assert attendre(vus, img.submit("un chat", "auto", [], "general")["job"])["etat"] == "fini"


def test_blocage_pendant_l_attente_en_file(lieux, monkeypatch):
    img, vus, worker, _ = fabrique(lieux)
    appels = iter([False, True])
    monkeypatch.setattr(images_ia, "memoire_bloquee", lambda: next(appels, True))
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["code"] == "memoire" and not worker.entrees


# ---------------------------------------------------------------- comptes

def _coeur():
    return object.__new__(core.Astrel)


def test_comptes_ajouter_retirer_ordre_reserves_a_utilisateur(lieux, monkeypatch):
    img, _vus, _w, etats = fabrique(lieux)
    monkeypatch.setattr(images_ia, "images", img)
    j = _coeur()
    for name in images_ia.COMMANDES_COMPTES:
        r = j._on_command({"cmd": name, "alias": "x", "fournisseur": "google", "_from_claude": True})
        assert r["ok"] is False and "réservés à l'utilisateur" in r["error"]
    assert not ia_web.chemin_comptes().exists()
    r = j._on_command({"cmd": "image_compte_ajouter", "alias": "Perso", "fournisseur": "google"})
    # l'ancien réglage (amw) est repris dans comptes.json, le nouveau vient après
    assert r["ok"] and [c["alias"] for c in r["comptes"]] == ["amw", "perso"]
    assert mode_prive(ia_web.chemin_comptes(), 0o600)
    assert "existe déjà" in j._on_command({"cmd": "image_compte_ajouter", "alias": "perso",
                                           "fournisseur": "google"})["error"]
    assert "invalide" in j._on_command({"cmd": "image_compte_ajouter", "alias": "anonyme",
                                        "fournisseur": "google"})["error"]
    assert "Fournisseur" in j._on_command({"cmd": "image_compte_ajouter", "alias": "b",
                                           "fournisseur": "aucun"})["error"]
    r = j._on_command({"cmd": "image_compte_ordre", "ordre": ["perso", "amw"]})
    assert r["ok"] and [c["alias"] for c in ia_web.comptes()] == ["perso", "amw"]
    assert "Ordre refusé" in j._on_command({"cmd": "image_compte_ordre", "ordre": ["perso"]})["error"]
    r = j._on_command({"cmd": "image_compte_retirer", "alias": "amw"})
    assert r["ok"] and [c["alias"] for c in r["comptes"]] == ["perso"]
    assert (lieux / "imagegen" / "profils" / "amw").is_dir()  # profil gardé : rien n'est supprimé
    assert etats and etats[-1]["type"] == "ia_etat" and [c["alias"] for c in etats[-1]["declares"]] == ["perso"]


def test_comptes_illisibles_jamais_ecrases(lieux):
    img, *_ = fabrique(lieux)
    ia_web.chemin_comptes().parent.mkdir(parents=True, exist_ok=True)
    ia_web.chemin_comptes().write_text("{abîmé", encoding="utf-8")
    r = img.compte_commande("image_compte_ajouter", {"alias": "perso", "fournisseur": "google"})
    assert r["ok"] is False and "illisible" in r["error"]
    assert ia_web.chemin_comptes().read_text(encoding="utf-8") == "{abîmé"


def test_ia_etat_sticky_et_contenu(lieux):
    assert "ia_etat" in events.STICKY
    img, *_ = fabrique(lieux)
    st = img.status()
    assert [s["service"] for s in st["services"]] == ["gemini", "chatgpt", "bing"]
    assert {s["service"]: s["pieces"] for s in st["services"]} == {"gemini": True, "chatgpt": True, "bing": False}
    assert all([m["id"] for m in s["modeles"]] == ["defaut"] for s in st["services"])
    assert st["reglages"]["mode"] == "auto" and st["reglages"]["ordre"] == ["gemini", "chatgpt", "bing"]
    assert st["declares"] == [{"alias": "amw", "fournisseur": "google"}] and st["pieces_max"] == 6
    bing = next(c for c in st["comptes"] if c["moteur"] == "bing")
    assert bing["compte"] == "anonyme" and bing["sans_compte"] and bing["connecte"]


# ---------------------------------------------------------------- réglages

def test_reglages_images_valeurs_sures_et_choix():
    from astrel.settings import ITEMS, reglages
    assert reglages.value("images_mode") == "auto"
    assert reglages.value("images_ordre") == "gemini,chatgpt,bing"
    assert reglages.value("images_manuel") == "gemini/defaut"
    groupe = next(g for g in reglages.describe()["groups"] if g["id"] == "images")
    ordre = next(i for i in groupe["items"] if i["key"] == "images_ordre")
    assert len(ordre["choices"]) == 6 and ordre["choices"][0]["label"] == "Gemini → ChatGPT → Bing Image Creator"
    with pytest.raises(settings.SettingError):
        reglages.set("images_ordre", "gemini,midjourney")
    with pytest.raises(settings.SettingError):
        reglages.set("images_manuel", "gemini/pro")
    assert ITEMS["images_mode"].applies == "hot"


def test_reglage_illisible_valeurs_par_defaut(monkeypatch):
    monkeypatch.setattr(settings.reglages, "value", lambda key: (_ for _ in ()).throw(RuntimeError("x")))
    reg = images_ia.reglages_images()
    assert reg["mode"] == "auto" and reg["service"] == "gemini" and reg["modele"] == "defaut"


def test_reglages_valeurs_invalides_deja_presentes_defauts_surs(monkeypatch):
    """Fiche 8 du relecteur (A4) : runtime/settings.json porte DÉJÀ des valeurs fausses (édition à la
    main, ancien catalogue). Deux barrières : `settings` les écarte à la lecture, et
    `reglages_images` revalide ce que `value()` rend (mode, service, modèle proposé, ordre)."""
    services = ia_web.services("image")
    defaut = {"mode": "auto", "ordre": list(services), "service": services[0],
              "modele": ia_web.modeles_proposes(services[0])[0]["id"]}
    # 1) le fichier lui-même (barrière de settings.py)
    config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"version": 1, "values": {
        "images_mode": "zzz", "images_ordre": "dalle,midjourney", "images_manuel": "dalle/zz"}}),
        encoding="utf-8")
    settings.reglages.reset()
    assert images_ia.reglages_images() == defaut
    # 2) valeurs fausses qui passeraient la première barrière (barrière de images_ia)
    for valeurs, mode in (
            ({"images_mode": "zzz", "images_ordre": "dalle,midjourney", "images_manuel": "dalle/defaut"}, "auto"),
            ({"images_mode": None, "images_ordre": None, "images_manuel": None}, "auto"),
            ({"images_mode": "manuel", "images_ordre": "", "images_manuel": "gemini/chatgpt-zz"}, "manuel")):
        monkeypatch.setattr(settings.reglages, "value", lambda key, v=valeurs: v[key])
        reg = images_ia.reglages_images()
        assert reg == {**defaut, "mode": mode}
        assert ia_web.modele_propose(reg["service"], reg["modele"])
    # ordre : inconnus jetés, doublons fusionnés, services manquants complétés
    monkeypatch.setattr(settings.reglages, "value", lambda key: {"images_ordre": "dalle,bing,bing"}.get(key))
    assert images_ia.reglages_images()["ordre"] == ["bing", *[s for s in services if s != "bing"]]


# ---------------------------------------------------------------- outil MCP

def test_outil_mcp_generate_image_moteur_modele_pieces(monkeypatch):
    from astrel import mcp_server

    sent = []
    monkeypatch.setattr(mcp_server, "_core", lambda cmd, ensure_ascii=True: sent.append(cmd) or {"ok": True, "job": "a"})
    mcp_server.generate_image("un chat", "gemini", [f"{chemins_machine.PROJETS}/a.png"], "defaut")
    assert sent[-1] == {"cmd": "image_generate", "prompt": "un chat", "engine": "gemini",
                        "files": [f"{chemins_machine.PROJETS}/a.png"], "model": "defaut"}
    mcp_server.generate_image("un chat", files=f"{chemins_machine.PROJETS}/b.png")  # un chemin seul
    assert sent[-1]["files"] == [f"{chemins_machine.PROJETS}/b.png"] and "model" not in sent[-1]
    mcp_server.generate_image(prompt="un chat", engine="auto")  # appel d'avant A4 : inchangé
    assert sent[-1] == {"cmd": "image_generate", "prompt": "un chat", "engine": "auto", "files": []}


def test_coeur_transmet_le_modele(lieux, monkeypatch):
    img, vus, worker, _ = fabrique(lieux)
    monkeypatch.setattr(images_ia, "images", img)
    r = _coeur()._on_command({"cmd": "image_generate", "prompt": "un chat", "engine": "gemini", "model": "defaut",
                              "files": [png(lieux)], "_from_claude": True})
    assert r["ok"] and r["mode"] == "manuel"
    attendre(vus, r["job"])
    assert worker.entrees[0]["modele"] == "defaut" and len(worker.entrees[0]["fichiers"]) == 1


# ---------------------------------------------------------------- connexion d'un compte

class FauxChrome:
    """Processus Chrome factice : vivant jusqu'à `fermer()`. Rien n'est lancé."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._fin = threading.Event()

    def poll(self):
        return 0 if self._fin.is_set() else None

    def wait(self, timeout=None):
        self._fin.wait(timeout)
        return 0

    def fermer(self):
        self._fin.set()


@pytest.fixture
def chrome(lieux, monkeypatch, tmp_path):
    binaire = tmp_path / "google-chrome"
    binaire.write_text("#!/bin/sh\nexit 1\n")
    binaire.chmod(0o755)
    monkeypatch.setattr(ia_connexion, "CHROME", str(binaire))
    lances: list[dict] = []

    def lanceur(argv, **kw):
        # pid d'un processus VIVANT (le nôtre) : le verrou du profil doit être vu tenu
        p = FauxChrome(os.getpid())
        lances.append({"argv": argv, "kw": kw, "proc": p})
        return p

    monkeypatch.setattr(ia_connexion, "ouvrir", lambda alias, _o=ia_connexion.ouvrir: _o(alias, lanceur=lanceur))
    return lances


def test_connexion_chrome_visible_sans_pilotage(lieux, chrome, monkeypatch):
    monkeypatch.setenv("CLE_API_OPENAI", "secret")
    monkeypatch.setenv("ASTREL_IMAGEGEN_RYTHME", "test")
    img, *_ = fabrique(lieux)
    r = img.compte_commande("image_compte_connecter", {"alias": "amw"})
    assert r["ok"] and "connecte-toi toi-même" in r["message"] and len(chrome) == 1
    argv, kw = chrome[0]["argv"], chrome[0]["kw"]
    profil = lieux / "imagegen" / "profils" / "amw"
    # 30/10 : un onglet par site du fournisseur (Dreamina a sa propre connexion)
    assert argv[1] == f"--user-data-dir={profil}" and argv[-3:] == ["https://gemini.google.com/app", "https://flow.google.com/",
                                                                  "https://dreamina.capcut.com/ai-tool/home?type=video"]
    assert not any(a.startswith(ia_connexion.INTERDITS) for a in argv)  # ni débogage ni automation
    assert "CLE_API_OPENAI" not in kw["env"] and "ASTREL_IMAGEGEN_RYTHME" not in kw["env"]
    assert kw["start_new_session"] is True
    # profil verrouillé au pid du Chrome : un worker recevrait « occupe »
    assert Path(f"{profil}.lock").read_text() == str(os.getpid()) and ia_connexion.ouverte("amw")
    # deuxième demande : rien de plus n'est ouvert
    assert img.compte_commande("image_compte_connecter", {"alias": "amw"})["deja"] is True and len(chrome) == 1
    chrome[0]["proc"].fermer()
    fin = time.time() + 3
    while ia_connexion.ouverte("amw") or Path(f"{profil}.lock").exists():
        assert time.time() < fin, "verrou du profil jamais rendu"
        time.sleep(0.01)


def test_connexion_ouverte_compte_saute_par_les_images(lieux, chrome):
    comptes_json(lieux, ("amw", "google"), ("perso", "google"))
    img, vus, worker, _ = fabrique(lieux)
    assert img.compte_commande("image_compte_connecter", {"alias": "amw"})["ok"]
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["compte"] == "perso" and all(Path(e["profil"]).name != "amw" for e in worker.entrees)
    assert img.status()["connexions"] == ["amw"]
    chrome[0]["proc"].fermer()


def test_connexion_refusee(lieux, chrome):
    img, *_ = fabrique(lieux)
    profil = lieux / "imagegen" / "profils" / "amw"
    assert "inconnu" in img.compte_commande("image_compte_connecter", {"alias": "nimporte"})["error"].lower()
    # profil tenu par un worker vivant
    Path(f"{profil}.lock").write_text(str(os.getpid()))
    assert "déjà ouvert" in img.compte_commande("image_compte_connecter", {"alias": "amw"})["error"]
    Path(f"{profil}.lock").write_text("999999999")  # pid mort : repris
    r = img.compte_commande("image_compte_connecter", {"alias": "amw"})
    assert r["ok"], r
    chrome[0]["proc"].fermer()
    fin = time.time() + 3
    while ia_connexion.ouverte("amw"):
        assert time.time() < fin
        time.sleep(0.01)
    # Chrome déjà ouvert sur ce profil (SingletonLock d'un pid vivant)
    (profil / "SingletonLock").symlink_to(f"{systeme.nom_machine()}-{os.getpid()}")  # 03/11 : os.uname absent sous Windows
    assert "déjà ouvert" in img.compte_commande("image_compte_connecter", {"alias": "amw"})["error"]
    (profil / "SingletonLock").unlink()
    (lieux / "ESTOP").write_text("1")
    assert "Arrêt d'urgence" in img.compte_commande("image_compte_connecter", {"alias": "amw"})["error"]
    (lieux / "ESTOP").unlink()
    ia_connexion.CHROME = str(lieux / "absent")
    assert "introuvable" in img.compte_commande("image_compte_connecter", {"alias": "amw"})["error"]
    assert len(chrome) == 1  # un seul Chrome (factice) lancé de tout le test


def test_verrou_du_profil_sous_windows_sans_os_kill(lieux, monkeypatch):
    """03/11 (CI Windows, WinError 87) : `os.kill(pid, 0)` y envoie CTRL_C_EVENT au lieu de tester le pid.
    Windows simulé : le pid est lu par psutil (simulé lui aussi : sous Linux, il appelle os.kill), `os.kill`
    n'est jamais appelé."""
    import psutil

    def interdit(*a):
        raise AssertionError("os.kill appelé sous Windows")

    lus: list[int] = []
    monkeypatch.setattr(systeme, "UNIX", False)
    monkeypatch.setattr(systeme.os, "kill", interdit)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: lus.append(pid) or pid == os.getpid())
    profil = lieux / "imagegen" / "profils" / "amw"
    Path(f"{profil}.lock").write_text(str(os.getpid()))
    assert ia_connexion.verrou_tenu(profil)                     # pid vivant : tenu
    Path(f"{profil}.lock").write_text("999999999")
    assert not ia_connexion.verrou_tenu(profil)                 # pid mort : libre
    assert not ia_connexion._pid_vivant(0) and lus == [os.getpid(), 999999999]


def test_connexion_par_le_coeur_refusee_a_claude(lieux, chrome, monkeypatch):
    img, *_ = fabrique(lieux)
    monkeypatch.setattr(images_ia, "images", img)
    r = _coeur()._on_command({"cmd": "image_compte_connecter", "alias": "amw", "_from_claude": True})
    assert r["ok"] is False and not chrome
    r = _coeur()._on_command({"cmd": "image_compte_connecter", "alias": "amw"})
    assert r["ok"] and len(chrome) == 1
    chrome[0]["proc"].fermer()


def test_connexion_ne_s_ouvre_jamais_seule(lieux, monkeypatch):
    """Un échec « session » ne lance aucune fenêtre : seule la commande de l'utilisateur le fait."""
    appels = []
    monkeypatch.setattr(ia_connexion, "ouvrir", lambda *a, **k: appels.append(a) or {"ok": True})
    img, vus, _w, _ = fabrique(lieux, {"amw": "ok"})
    img.runner = lambda entree, ligne: {"t": "echec", "code": "session", "message": "déconnecté"}
    fin = attendre(vus, img.submit("un chat", "gemini", [], "general")["job"])
    assert fin["code"] == "session" and not appels


# ---------------------------------------------------------------- retours du testeur (fiche 2)

def test_retry_engine_refuse_si_from_claude(lieux, monkeypatch):
    """« Essayer avec X » change de moteur : l'utilisateur seul. Venant de Claude : refus, aucun worker."""
    comptes_json(lieux, ("amw", "google"))
    img, vus, worker, _ = fabrique(lieux, {"amw": "quota"})
    monkeypatch.setattr(images_ia, "images", img)
    j = _coeur()
    r = j._on_command({"cmd": "image_generate", "prompt": "un chat", "engine": "gemini", "_from_claude": True})
    fin = attendre(vus, r["job"])
    assert fin["code"] == "quota" and fin["proposition"] == "bing"
    n = len(worker.entrees)
    refus = j._on_command({"cmd": "image_retry", "job_id": r["job"], "engine": "bing", "_from_claude": True})
    assert refus["ok"] is False and "réservé à l'utilisateur" in refus["error"]
    time.sleep(0.1)
    assert len(worker.entrees) == n and not any(e["service"] == "bing" for e in worker.entrees)
    assert img._jobs[r["job"]].relance == ""  # la carte garde son bouton pour l'utilisateur
    # l'utilisateur (HUD) : accepté
    ok = j._on_command({"cmd": "image_retry", "job_id": r["job"], "engine": "bing"})
    assert ok["ok"] and attendre(vus, ok["job"])["moteur"] == "bing"


def test_auto_comptes_illisibles_avertit(lieux):
    """comptes.json abîmé : en auto, échec clair, jamais un départ silencieux sur Bing sans compte."""
    ia_web.chemin_comptes().parent.mkdir(parents=True, exist_ok=True)
    ia_web.chemin_comptes().write_text("{", encoding="utf-8")
    img, vus, worker, _ = fabrique(lieux)
    fin = attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["etat"] == "echec" and fin["code"] == "comptes" and fin["erreur"] == images_ia.COMPTES_ILLISIBLES
    assert not worker.entrees  # aucun Chrome, Bing compris
    # manuel sur Bing (moteur choisi par l'utilisateur, sans compte) : permis
    assert attendre(vus, img.submit("un chat", "bing", [], "general")["job"])["moteur"] == "bing"


# ---------------------------------------------------------------- retours du relecteur (fiche 4)

def test_deux_clics_reessayer_simultanes_une_seule_image(lieux, monkeypatch):
    img, vus, worker, _ = fabrique(lieux, {"amw": "fichiers"})
    r = img.submit("un chat", "auto", [], "general")
    assert attendre(vus, r["job"])["etat"] == "echec"
    vrai_submit = img.submit
    demandes = []

    def lent(*a, **k):
        demandes.append(a)
        time.sleep(0.2)  # le second clic arrive pendant que le premier part
        return vrai_submit(*a, **k)

    monkeypatch.setattr(img, "submit", lent)
    worker.scenarios["amw"] = "ok"
    res: list[dict] = []
    fils = [threading.Thread(target=lambda: res.append(img.retry(r["job"]))) for _ in range(2)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(5)
    assert len(demandes) == 1 and sum(1 for x in res if x.get("deja")) == 1
    relance = next(x["job"] for x in res if not x.get("deja"))
    assert img._jobs[r["job"]].relance == relance
    # 30/10 : attendre la relance AVANT la fin du test ; sinon son fil finit après le retrait des chemins
    # de test et écrivait dans le VRAI journal (logs/images.log) et le vrai compteur
    assert attendre(vus, relance)["etat"] == "fini"


def test_connexion_n_efface_jamais_un_verrou_vivant(lieux, chrome, monkeypatch):
    """Un worker prend le verrou entre le contrôle et la reprise : son verrou vivant reste."""
    img, *_ = fabrique(lieux)
    profil = lieux / "imagegen" / "profils" / "amw"
    Path(f"{profil}.lock").write_text(str(os.getpid()))  # pid VIVANT (le worker)
    monkeypatch.setattr(ia_connexion, "verrou_tenu", lambda p: False)  # contrôle fait avant qu'il le prenne
    r = img.compte_commande("image_compte_connecter", {"alias": "amw"})
    assert r["ok"] is False and "déjà ouvert" in r["error"] and not chrome
    assert Path(f"{profil}.lock").read_text() == str(os.getpid())


def _faux_cookies(profil: Path, lignes) -> None:
    """Base de cookies factice au format de Chrome (seuls hôte, nom, expiration sont lus)."""
    import sqlite3
    (profil / "Default").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(profil / "Default" / "Cookies")
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, expires_utc INTEGER)")
    con.executemany("INSERT INTO cookies VALUES (?, ?, '', x'00', ?)", lignes)
    con.commit()
    con.close()


def test_session_lue_a_la_fermeture_de_la_fenetre(lieux, monkeypatch):
    # 30/10 (demande : « il faut que ce soit dynamique ») : fenêtre de connexion fermée, la session
    # Google est relue dans le profil et « à reconnecter » suit tout de suite, sans générer d'image.
    profil = lieux / "imagegen" / "profils" / "amw"
    futur = int((time.time() + 86400 + ia_connexion._EPOQUE_CHROME) * 1_000_000)
    passe = int((time.time() - 60 + ia_connexion._EPOQUE_CHROME) * 1_000_000)
    assert ia_connexion.session_ouverte(profil, "google") is None            # pas de base : on ne sait pas
    _faux_cookies(profil, [(".google.com", "NID", futur), (".google.com", "SID", passe)])
    assert ia_connexion.session_ouverte(profil, "google") is False           # SID expiré, NID ≠ session
    assert ia_connexion.session_ouverte(profil, "aucun") is None
    (profil / "Default" / "Cookies").unlink()
    _faux_cookies(profil, [(".google.com", "__Secure-1PSID", futur)])
    assert ia_connexion.session_ouverte(profil, "google") is True

    # Etat neuf sur le fichier du test : celui de l'objet global garde le chemin RÉEL (pris à l'import)
    monkeypatch.setattr(images_ia.images, "etat", Etat())
    images_ia.images.etat.noter("amw", moteur="gemini", a_reconnecter=True)
    vus = []
    monkeypatch.setattr(ia_connexion, "on_change", lambda: vus.append("etat"))

    class Fenetre:  # Chrome de la connexion, déjà fermé par l'utilisateur
        pid = 999999

        def wait(self):
            return 0

    ia_connexion._surveiller("amw", Fenetre(), profil)
    assert images_ia.images.etat.compte("amw", moteur="gemini").get("a_reconnecter") is False
    assert vus == ["etat"]
    assert "session ouverte à la fermeture" in (lieux / "images.log").read_text(encoding="utf-8")


def test_renommer_un_compte(lieux):
    # 30/10 (demande : nom mal tapé à l'ajout) : même place, même fournisseur ; profil Chrome (la
    # connexion), compteur du worker et état suivent ; jamais pendant qu'il sert, jamais d'écrasement.
    comptes_json(lieux, ("amw", "google"), ("camaelo", "google"))
    profils = lieux / "imagegen" / "profils"
    (profils / "camaelo" / "Default").mkdir(parents=True)
    (profils / "camaelo.compteur.json").write_text("{}")
    im, *_ = fabrique(lieux)  # jamais l'objet global : son état écrit dans le VRAI etat.json
    im.etat.noter("camaelo", moteur="gemini", a_reconnecter=True)
    cmd = lambda nouveau, alias="camaelo": im.compte_commande("image_compte_renommer", {"alias": alias, "nouveau": nouveau})  # noqa: E731
    r = cmd("boutique")
    assert r["ok"] and [c["alias"] for c in r["comptes"]] == ["amw", "boutique"]
    assert (profils / "boutique" / "Default").is_dir() and not (profils / "camaelo").exists()
    assert (profils / "boutique.compteur.json").exists() and not (profils / "camaelo.compteur.json").exists()
    assert im.etat.compte("boutique", moteur="gemini").get("a_reconnecter") is True
    assert "renommer camaelo" in (lieux / "images.log").read_text(encoding="utf-8")
    assert cmd("amw", alias="boutique")["error"] == images_ia.COMPTES_MESSAGES["existe"]
    assert cmd("X Y", alias="boutique")["error"] == images_ia.COMPTES_MESSAGES["alias"]
    assert cmd("zzz", alias="inconnu")["error"] == images_ia.COMPTES_MESSAGES["inconnu"]
    (profils / "ancien").mkdir()  # profil d'un compte retiré : jamais écrasé
    assert cmd("ancien", alias="boutique")["error"] == images_ia.COMPTES_MESSAGES["profil"]
    (profils / "boutique.lock").write_text(str(os.getpid()))  # une image ou une connexion le tient
    assert cmd("autre", alias="boutique")["error"] == images_ia.COMPTES_MESSAGES["occupe"]
    (profils / "boutique.lock").unlink()
    assert cmd("boutique", alias="boutique")["ok"]  # même nom : rien à faire
    assert [c["alias"] for c in ia_web.comptes()] == ["amw", "boutique"]


def test_session_dreamina_lue_a_la_fermeture(lieux, monkeypatch):
    # 30/10 : Dreamina a sa propre connexion (CapCut) : sa session est lue elle aussi à la fermeture
    profil = lieux / "imagegen" / "profils" / "amw"
    futur = int((time.time() + 86400 + ia_connexion._EPOQUE_CHROME) * 1_000_000)
    _faux_cookies(profil, [(".google.com", "SID", futur), (".capcut.com", "passport_csrf_token", futur)])
    monkeypatch.setattr(images_ia.images, "etat", Etat())
    monkeypatch.setattr(ia_connexion, "on_change", lambda: None)
    images_ia.images.etat.noter("amw", moteur="dreamina", site_a_reconnecter=False)

    class Fenetre:
        pid = 999999

        def wait(self):
            return 0

    ia_connexion._surveiller("amw", Fenetre(), profil)
    assert images_ia.images.etat.compte("amw", moteur="dreamina").get("site_a_reconnecter") is True  # pas de session CapCut
    (profil / "Default" / "Cookies").unlink()
    _faux_cookies(profil, [(".google.com", "SID", futur), (".capcut.com", "sessionid", futur)])
    ia_connexion._surveiller("amw", Fenetre(), profil)
    assert images_ia.images.etat.compte("amw", moteur="dreamina").get("site_a_reconnecter") is False
