"""Socle commun des générateurs IA par le navigateur (27/10, plan P40 A1) : catalogue
`config/ia-web.json`, comptes `comptes.json`, état v2 par (compte, service) en unités, choix
auto/manuel, verrou de Chrome, et ce que `images_ia` en tire (modèle, contrat du worker)."""
from __future__ import annotations

import json
import threading
import time

import pytest

from astrel import attachments, config, ia_web, images_ia
from astrel.ia_web import Choix, Etat, Refus
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
    return tmp_path


def _comptes(lieux, *paires):
    ia_web.ecrire_comptes([{"alias": a, "fournisseur": f} for a, f in paires])
    for a, _ in paires:
        (lieux / "imagegen" / "profils" / a).mkdir(parents=True, exist_ok=True)


def _catalogue(tmp_path, monkeypatch, services):
    f = tmp_path / "ia-web.json"
    f.write_text(json.dumps({"version": 1, "services": services}), encoding="utf-8")
    monkeypatch.setattr(ia_web, "CATALOGUE_FILE", f)
    return f


# ---------------------------------------------------------------- catalogue

def test_catalogue_livre_lisible_et_sans_modele_invente():
    data = json.loads(ia_web.CATALOGUE_FILE.read_text(encoding="utf-8"))
    assert set(data["services"]) == {"gemini", "chatgpt", "bing", "flow", "dreamina"}  # vidéo : flow (P41 B2), dreamina (B5)
    assert ia_web.services("image") == ["gemini", "chatgpt", "bing"]  # rang du mode auto
    for s in ("gemini", "chatgpt", "bing"):
        # modèles réels pas encore relevés (Q5) : seul le défaut du site, jamais un nom inventé
        assert [m["id"] for m in ia_web.modeles(s)] == ["defaut"] and not data["services"][s]["modeles_releves"]
    assert ia_web.fournisseur("gemini") == "google" and ia_web.fournisseur("bing") == "aucun"
    assert ia_web.fenetre("chatgpt") == "24h"


def test_catalogue_illisible_repli_et_relu_a_chaud(tmp_path, monkeypatch):
    f = tmp_path / "ia-web.json"
    f.write_text("{pas du json", encoding="utf-8")
    monkeypatch.setattr(ia_web, "CATALOGUE_FILE", f)
    assert ia_web.services("image") == ["gemini", "chatgpt", "bing"]
    time.sleep(0.01)
    f.write_text(json.dumps({"services": {
        "flow": {"type": "video", "fournisseur": "google", "modeles": [{"id": "veo-rapide", "libelle": "Rapide",
                                                                         "cout": 20}], "plafond": 100,
                 "fenetre": "jour", "rang": 1},
        "mauvais": {"type": "son", "fournisseur": "google"}}}), encoding="utf-8")
    assert ia_web.services("video") == ["flow"] and ia_web.services("image") == []
    assert ia_web.cout("flow", "veo-rapide") == 20 and ia_web.plafond("flow") == 100
    assert ia_web.modele_valide("flow", "veo-rapide") and not ia_web.modele_valide("flow", "veo-pro")


def test_plafond_variable_prime_sur_le_catalogue(lieux, monkeypatch):
    monkeypatch.setattr(config, "IMAGES_PLAFONDS", {"gemini": 7})
    assert ia_web.plafond("gemini") == 7 and ia_web.plafond("chatgpt") == 3  # catalogue


# ---------------------------------------------------------------- comptes

def test_comptes_repris_de_l_ancien_reglage_sans_rien_ecrire(lieux):
    assert ia_web.comptes() == [{"alias": "amw", "fournisseur": "google"}]
    assert not ia_web.chemin_comptes().exists()
    assert ia_web.comptes_du_service("gemini") == ["amw"] and ia_web.comptes_du_service("chatgpt") == []
    assert ia_web.comptes_du_service("bing") == [ia_web.ALIAS_ANONYME]


def test_comptes_json_ordre_filtre_et_droits(lieux):
    gardes = ia_web.ecrire_comptes([
        {"alias": "boutique", "fournisseur": "google"}, {"alias": "amw", "fournisseur": "google"},
        {"alias": "boutique", "fournisseur": "openai"},          # doublon
        {"alias": "anonyme", "fournisseur": "google"},          # alias réservé
        {"alias": "Perso Mail", "fournisseur": "google"},       # alias invalide
        {"alias": "x", "fournisseur": "aucun"},                 # « aucun » n'est pas un compte
        {"alias": "oa", "fournisseur": "openai"}, "pas un dict"])
    assert [c["alias"] for c in gardes] == ["boutique", "amw", "oa"]
    assert ia_web.comptes_du_service("gemini") == ["boutique", "amw"] and ia_web.comptes_du_service("chatgpt") == ["oa"]
    assert mode_prive(ia_web.chemin_comptes(), 0o600)


def test_comptes_json_illisible_aucun_compte(lieux):
    ia_web.chemin_comptes().write_text("{", encoding="utf-8")
    assert ia_web.comptes() == []  # jamais de retour silencieux à l'ancien réglage


# ---------------------------------------------------------------- état v2

def test_migration_v1_avec_copie(lieux):
    v1 = {"comptes": {"amw": {"moteur": "gemini", "jour": time.strftime("%Y-%m-%d"), "n": 5,
                              "epuise_le": None, "a_reconnecter": True, "dernier_succes": 1.0}}}
    config.IMAGES_ETAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.IMAGES_ETAT_FILE.write_text(json.dumps(v1), encoding="utf-8")
    e = Etat()
    c = e.compte("amw")
    assert c["n"] == 5 and c["service"] == "gemini" and c["a_reconnecter"] is True
    e.compter("amw", "gemini")
    data = json.loads(config.IMAGES_ETAT_FILE.read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["comptes"]["amw"]["services"]["gemini"]["n"] == 6
    assert data["comptes"]["amw"]["compte"] == {"a_reconnecter": True}
    copie = config.IMAGES_ETAT_FILE.with_name("etat.v1.json")
    assert json.loads(copie.read_text(encoding="utf-8")) == v1 and mode_prive(copie, 0o600)


def test_quotas_par_compte_et_service_en_unites(lieux):
    e = Etat()
    t0 = time.mktime((2026, 10, 27, 10, 0, 0, 0, 0, -1))
    e.compter("amw", "gemini", t0)
    e.compter("amw", "flow", t0, unites=20)        # vidéo : N crédits
    assert e.compte("amw", t0, "gemini")["n"] == 1 and e.compte("amw", t0, "flow")["n"] == 20
    e.epuise("amw", "limit", "", sonde=False, now=t0, moteur="gemini")
    assert e.compte("amw", t0, "gemini")["epuise_le"] == t0 and not e.compte("amw", t0, "flow").get("epuise_le")
    # session et défi anti-robot : propres au COMPTE, vus par tous ses services
    e.noter("amw", t0, moteur="gemini", pause_jusqua=t0 + 3600)
    assert e.disponible("amw", "flow", t0)[1] == "captcha"


def test_rendre_les_credits_non_debites(lieux):
    """P41 B2 : crédits comptés à `envoye` puis pointe/rythme/activité : rendus, jamais sous zéro ;
    fenêtre 24h revenue à zéro : départ effacé ; autre service du compte intact."""
    e = Etat()
    t0 = time.mktime((2026, 10, 27, 10, 0, 0, 0, 0, -1))
    e.compter("amw", "gemini", t0)
    e.compter("amw", "flow", t0, unites=10)
    assert e.compte("amw", t0, "flow")["debut"] == t0
    assert e.rendre("amw", "flow", t0 + 60, unites=10) == 0
    c = e.compte("amw", t0 + 60, "flow")
    assert c["n"] == 0 and c["debut"] is None and e.compte("amw", t0, "gemini")["n"] == 1
    e.compter("amw", "flow", t0 + 120, unites=20)
    e.compter("amw", "flow", t0 + 180, unites=10)
    assert e.rendre("amw", "flow", t0 + 240, unites=10) == 20
    assert e.compte("amw", t0 + 240, "flow")["debut"] == t0 + 120          # fenêtre ouverte par une vraie
    assert e.rendre("amw", "flow", t0 + 300, unites=99) == 0              # jamais sous zéro


def test_epuise_remis_a_zero_au_reset_quotidien(lieux):
    e = Etat()
    soir = time.mktime((2026, 10, 27, 21, 0, 0, 0, 0, -1))
    e.epuise("amw", "limit", "", sonde=False, now=soir, moteur="gemini")
    assert e.disponible("amw", "gemini", soir + 2 * 3600)[0] is False           # 23 h : même jour
    matin = time.mktime((2026, 10, 28, 0, 10, 0, 0, 0, -1))
    assert e.disponible("amw", "gemini", matin) == (True, "", True)               # après minuit
    e.epuise("amw", "limit", "", sonde=True, now=matin, moteur="gemini")        # sonde ratée
    assert e.disponible("amw", "gemini", matin + 3600)[0] is False               # pas deux fois le même jour
    # fenêtre « 24h » (ChatGPT) : minuit ne remet rien à zéro
    e.epuise("oa", "limit", "", sonde=False, now=soir, moteur="chatgpt")
    assert e.disponible("oa", "chatgpt", matin)[0] is False


# ---------------------------------------------------------------- choix

def _choisir(lieux, **kw):
    kw.setdefault("prets", ("gemini", "chatgpt", "bing"))
    kw.setdefault("etat", Etat())
    kw.setdefault("profil_existe", lambda a: (lieux / "imagegen" / "profils" / a).is_dir())
    return ia_web.choisir(**kw)


def test_auto_ordre_du_catalogue_puis_des_comptes(lieux):
    _comptes(lieux, ("boutique", "google"), ("amw", "google"), ("oa", "openai"))
    e = Etat()
    assert _choisir(lieux, mode="auto", etat=e) == Choix("boutique", "gemini", "defaut", False)
    e.compter("boutique", "gemini", unites=20)                                   # au plafond
    assert _choisir(lieux, mode="auto", etat=e).alias == "amw"
    essais = [ia_web.cle_essai("amw", "gemini")]
    assert _choisir(lieux, mode="auto", etat=e, essais=essais) == Choix("oa", "chatgpt", "defaut", False)
    essais.append(ia_web.cle_essai("oa", "chatgpt"))
    # Bing sans compte : profil anonyme, aucun dossier exigé
    assert _choisir(lieux, mode="auto", etat=e, essais=essais).alias == ia_web.ALIAS_ANONYME


def test_auto_seulement_les_services_prets(lieux):
    _comptes(lieux, ("oa", "openai"))
    r = _choisir(lieux, mode="auto", prets=("gemini",), essais=[ia_web.cle_essai("amw", "gemini")])
    assert isinstance(r, Refus) and r.code == "quota" and r.proposition == ""


def test_manuel_repli_sur_un_compte_du_meme_service_jamais_un_autre_moteur(lieux):
    _comptes(lieux, ("boutique", "google"), ("amw", "google"), ("oa", "openai"))
    e = Etat()
    e.epuise("boutique", "limit", "", sonde=False, moteur="gemini")
    r = _choisir(lieux, mode="manuel", service="gemini", modele="defaut", etat=e)
    assert r == Choix("amw", "gemini", "defaut", False)
    e.epuise("amw", "limit", "", sonde=False, moteur="gemini")
    r = _choisir(lieux, mode="manuel", service="gemini", modele="defaut", etat=e)
    assert isinstance(r, Refus) and r.code == "quota" and r.proposition == "chatgpt"  # « Essayer avec ChatGPT »


def test_non_connecte_et_pause(lieux):
    _comptes(lieux, ("amw", "google"), ("neuf", "google"))
    (lieux / "imagegen" / "profils" / "neuf").rmdir()
    e = Etat()
    e.noter("amw", moteur="gemini", pause_jusqua=time.time() + 600)
    r = _choisir(lieux, mode="manuel", service="gemini", etat=e, prets=("gemini",))
    assert isinstance(r, Refus) and r.code == "captcha"


def test_manuel_deux_services_indisponibles_sans_recursion(lieux):
    """Relecture fiche 10 : la recherche de proposition se rappelait sans fin (RecursionError)."""
    _comptes(lieux, ("amw", "google"), ("oa", "openai"))
    e = Etat()
    e.epuise("amw", "x", "", False, moteur="gemini")
    e.epuise("oa", "x", "", False, moteur="chatgpt")
    r = _choisir(lieux, mode="manuel", service="gemini", etat=e, prets=("gemini", "chatgpt"))
    assert r == Refus("quota", "")


def test_comptes_illisibles_refus_qui_le_dit(lieux, monkeypatch):
    # P40 A4 : Gemini seul piloté ici (sinon l'auto finit sur Bing, qui n'a pas besoin de compte)
    monkeypatch.setattr(images_ia, "MOTEURS_PRETS", ("gemini",))
    ia_web.chemin_comptes().parent.mkdir(parents=True, exist_ok=True)
    ia_web.chemin_comptes().write_text("{", encoding="utf-8")
    r = _choisir(lieux, mode="auto", prets=("gemini",))
    assert r == Refus("comptes", "")
    img, vus, _ = _images(lieux)
    fin = _attendre(vus, img.submit("un chat", "auto", [], "general")["job"])
    assert fin["code"] == "comptes" and fin["erreur"] == images_ia.COMPTES_ILLISIBLES


def test_etat_illisible_garde_avant_ecrasement(lieux):
    config.IMAGES_ETAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.IMAGES_ETAT_FILE.write_text("{tronqué", encoding="utf-8")
    e = Etat()
    assert e.compte("amw")["n"] == 0  # lu comme vide
    e.compter("amw", "gemini")
    gardes = list(config.IMAGES_ETAT_FILE.parent.glob("etat.illisible-*.json"))
    assert len(gardes) == 1 and gardes[0].read_text(encoding="utf-8") == "{tronqué"
    assert mode_prive(gardes[0], 0o600)
    assert json.loads(config.IMAGES_ETAT_FILE.read_text(encoding="utf-8"))["version"] == 2


def test_images_prend_le_verrou_chrome_autour_du_worker(lieux):
    """Relecture fiche 10 : le verrou doit être pris par `images_ia` lui-même. Tenu ailleurs
    (une vidéo en cours), le worker d'image attend ; relâché, il part."""
    vu_tenu: list[bool] = []
    entrees: list[dict] = []

    def runner(entree, ligne):
        vu_tenu.append(ia_web.CHROME.locked())
        entrees.append(entree)
        f = lieux / "Images-IA" / "img.png"
        f.write_bytes(PNG)
        return {"t": "fini", "fichier": str(f), "nom": f.name}

    vus: list[dict] = []
    img = images_ia.Images(publish=lambda kind, **ev: vus.append(ev), etat=Etat(), runner=runner)
    ia_web.CHROME.acquire()
    try:
        job = img.submit("un chat", "auto", [], "general")["job"]
        time.sleep(0.3)
        assert entrees == []  # bloqué tant que l'autre Chrome tourne
    finally:
        ia_web.CHROME.release()
    assert _attendre(vus, job)["etat"] == "fini"
    assert vu_tenu == [True] and not ia_web.CHROME.locked()


# ---------------------------------------------------------------- images_ia sur le socle

def _attendre(vus, job_id, timeout=5.0):
    fin = time.time() + timeout
    while time.time() < fin:
        for ev in vus:
            if ev["job"] == job_id and ev["etat"] in ("fini", "echec"):
                return ev
        time.sleep(0.01)
    raise AssertionError(vus)


def _images(lieux, modele_utilise=""):
    entrees: list[dict] = []

    def runner(entree, ligne):
        entrees.append(entree)
        ligne({"t": "envoye"})
        f = lieux / "Images-IA" / f"img-{len(entrees)}.png"
        f.write_bytes(PNG)
        return {"t": "fini", "fichier": str(f), "nom": f.name, "modele_utilise": modele_utilise}

    vus: list[dict] = []
    img = images_ia.Images(publish=lambda kind, **ev: vus.append(ev), etat=Etat(), runner=runner)
    return img, vus, entrees


def test_contrat_du_worker_et_modele(lieux):
    img, vus, entrees = _images(lieux, modele_utilise="defaut")
    fin = _attendre(vus, img.submit("un chat", "gemini", [], "general", "defaut")["job"])
    assert fin["etat"] == "fini" and fin["modele"] == "defaut" and fin["proposition"] == ""
    e = entrees[0]
    assert e["service"] == "gemini" and e["modele"] == "defaut" and e["type"] == "image"
    assert e["affichage"] in ia_web.AFFICHAGES and e["fenetre"] == "jour"  # fenêtre du QUOTA


@pytest.mark.parametrize("engine, model, erreur", [
    ("auto", "defaut", "pas en auto"),
    ("gemini", "imagen-9", "modèle inconnu"),
])
def test_modele_refuse(lieux, engine, model, erreur):
    img, vus, _ = _images(lieux)
    r = img.submit("un chat", engine, [], "general", model)
    assert r["ok"] is False and erreur in r["error"] and not vus


def test_substitution_de_modele_jamais_silencieuse(lieux, tmp_path, monkeypatch):
    _catalogue(tmp_path, monkeypatch, {"gemini": {
        "type": "image", "fournisseur": "google", "plafond": 20, "fenetre": "jour", "rang": 1,
        "modeles": [{"id": "rapide", "libelle": "Rapide"}, {"id": "pro", "libelle": "Pro"}],
        "modeles_releves": True}})  # P40 A4 : un modèle n'est proposé que relevé
    img, vus, _ = _images(lieux, modele_utilise="rapide")
    fin = _attendre(vus, img.submit("un chat", "gemini", [], "general", "pro")["job"])
    assert fin["etat"] == "echec" and fin["code"] == "page" and fin["file"] == "" and fin["images"] == []
    assert "modèle 'rapide' au lieu de 'pro'" in (lieux / "images.log").read_text(encoding="utf-8")


def test_reessayer_garde_le_modele_du_manuel(lieux, tmp_path, monkeypatch):
    _catalogue(tmp_path, monkeypatch, {"gemini": {
        "type": "image", "fournisseur": "google", "plafond": 20, "fenetre": "jour", "rang": 1,
        "modeles": [{"id": "rapide"}, {"id": "pro"}], "modeles_releves": True}})
    img, vus, entrees = _images(lieux, modele_utilise="rapide")
    r = img.submit("un chat", "gemini", [], "general", "pro")
    _attendre(vus, r["job"])
    r2 = img.retry(r["job"])
    _attendre(vus, r2["job"])
    assert [e["modele"] for e in entrees] == ["pro", "pro"]
    # auto : le modèle choisi n'est pas renvoyé (sinon « pas en auto »)
    r3 = img.submit("un chien", "auto", [], "general")
    assert _attendre(vus, r3["job"])["modele"] == "rapide"


# ---------------------------------------------------------------- worker commun (P41 B3, 29/10)

@pytest.mark.parametrize("cause", ["arret", "delai"])
def test_worker_arrete_avec_son_chrome(tmp_path, monkeypatch, cause):
    """`executer_worker` : annulation (ou arrêt d'urgence) et délai dépassé arrêtent le worker ET le
    Chrome qu'il a détaché sur le profil (comme Playwright). Vrais processus, aucun navigateur."""
    import sys

    from astrel import config

    monkeypatch.setattr(ia_web, "ARRET_SONDE_S", 0.05)
    monkeypatch.setattr(ia_web, "ARRET_GRACE_S", 2.0)
    monkeypatch.setattr(config, "IMAGES_WORKER_DIR", tmp_path)
    profil, pids = tmp_path / "profils" / "amw", tmp_path / "pids"
    script = tmp_path / "faux_worker.py"
    script.write_text(
        "import json, subprocess, sys, time\n"
        "entree = json.loads(sys.stdin.readline())\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)',\n"
        "                      '--user-data-dir=' + entree['profil']], start_new_session=True)\n"
        f"open({str(pids)!r}, 'w').write(str(c.pid))\n"
        "print(json.dumps({'t': 'etape', 'etape': 'generation'}), flush=True)\n"
        "time.sleep(120)\n", encoding="utf-8")
    vus: list[dict] = []
    stop = threading.Event()
    threading.Timer(1.0, stop.set).start()
    t0 = time.monotonic()
    fin = ia_web.executer_worker([sys.executable, str(script)], {"profil": str(profil)}, vus.append,
                                 delai_s=1.0 if cause == "delai" else 60.0,
                                 arreter_si=stop.is_set if cause == "arret" else (lambda: False))
    chrome = int(pids.read_text())
    assert vus == [{"t": "etape", "etape": "generation"}] and fin["t"] == "echec"
    assert time.monotonic() - t0 < 10
    deadline = time.time() + 5
    while ia_web.vivant(chrome) and time.time() < deadline:
        time.sleep(0.05)
    assert not ia_web.vivant(chrome) and ia_web.chrome_du_profil(profil) == []


def test_chrome_libre_abandonne_l_attente(monkeypatch):
    monkeypatch.setattr(ia_web, "ARRET_SONDE_S", 0.02)
    with ia_web.CHROME:
        with ia_web.chrome_libre(lambda: True) as pris:
            assert pris is False
    with ia_web.chrome_libre() as pris:
        assert pris is True and ia_web.CHROME.locked()
    assert not ia_web.CHROME.locked()
