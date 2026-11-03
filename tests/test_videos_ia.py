"""Vidéos IA par le navigateur (28/10, plan P41 lot B2) : file, décompte en CRÉDITS sur la fenêtre
de 24 h, modèle payant refusé, report pendant le créneau de pointe (jamais en boucle), fichier
mp4/webm reconnu par ses octets, contrat du worker, évènement `video_job`, journal. Le worker est
FAUX ici (aucun navigateur, aucun site, aucun compte réel)."""
from __future__ import annotations

import chemins_machine  # chemins de la machine qui lance la suite
import calendar
import contextlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from astrel import attachments, config, i18n, ia_web, videos_ia
from astrel.ia_web import Etat
from astrel.videos_ia import Videos

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\x82\x84webm" + b"\x00" * 64
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c63f8cfc0500f0004850180848a6c210000000049454e44ae426082")
MATIN = float(calendar.timegm((2026, 10, 28, 10, 0, 0, 0, 0, 0)))     # 10 h UTC : hors pointe
POINTE = float(calendar.timegm((2026, 10, 28, 15, 0, 0, 0, 0, 0)))    # 15 h UTC : créneau 14-17
FIN_POINTE = float(calendar.timegm((2026, 10, 28, 17, 0, 0, 0, 0, 0)))


class Horloge:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def pointe_14_17(monkeypatch):
    """Créneau de pointe 14-17 h UTC pour Flow, fourni par le TEST (30/10 : plus au catalogue) : le
    mécanisme de report reste vérifié sur un vrai message ou un créneau connu."""
    vrai = ia_web.reglages_video
    monkeypatch.setattr(ia_web, "reglages_video",
                        lambda s, _v=vrai: {**_v(s), "pointe_utc": [14, 17]} if s == "flow" else _v(s))


@pytest.fixture
def lieux(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMAGES_RUNTIME", tmp_path / "imagegen")
    monkeypatch.setattr(config, "IMAGES_PROFILS", tmp_path / "imagegen" / "profils")
    monkeypatch.setattr(config, "IMAGES_ETAT_FILE", tmp_path / "imagegen" / "etat.json")
    monkeypatch.setattr(config, "IMAGES_DEBUG_DIR", tmp_path / "imagegen" / "debug")
    monkeypatch.setattr(config, "IMAGES_LOG", tmp_path / "images.log")
    monkeypatch.setattr(config, "IMAGES_COMPTES", (("amw", "gemini"),))
    monkeypatch.setattr(config, "ESTOP_FILE", tmp_path / "ESTOP")
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "attachments")
    monkeypatch.setattr(videos_ia, "SORTIE", tmp_path / "Videos-IA")
    monkeypatch.setattr(videos_ia, "LOG", tmp_path / "videos.log")
    monkeypatch.setattr(videos_ia, "ATTENTE_MAX_S", 0.02)
    (tmp_path / "imagegen" / "profils" / "amw").mkdir(parents=True)
    (tmp_path / "Videos-IA").mkdir()
    (tmp_path / "attachments").mkdir()
    return tmp_path


def _comptes(lieux, *alias):
    ia_web.ecrire_comptes([{"alias": a, "fournisseur": "google"} for a in alias])
    for a in alias:
        (lieux / "imagegen" / "profils" / a).mkdir(parents=True, exist_ok=True)


class FauxWorker:
    """Rejoue, par compte, une suite d'issues : ok, webm, pointe, rythme, quota, credits,
    activite, captcha, session ; `fichier` impose ce que le faux site rend."""

    def __init__(self, sortie: Path, scenarios: dict[str, list[str]] | None = None,
                 fichier: tuple[str, bytes] | None = None, sortie_hors: Path | None = None) -> None:
        self.sortie = sortie
        self.scenarios = {k: list(v) for k, v in (scenarios or {}).items()}
        self.fichier = fichier
        self.sortie_hors = sortie_hors
        self.entrees: list[dict] = []
        self.chrome: list[bool] = []

    def __call__(self, entree: dict, ligne) -> dict:
        self.entrees.append(entree)
        self.chrome.append(ia_web.CHROME.locked())
        alias = Path(entree["profil"]).name
        suite = self.scenarios.get(alias) or []
        cas = suite.pop(0) if suite else "ok"
        ligne({"t": "etape", "etape": "ouverture"})
        if cas in ("pointe", "rythme", "activite", "captcha", "session"):
            return {"t": "echec", "code": cas, "message": f"faux {cas}", "reprise": ""}
        ligne({"t": "etape", "etape": "saisie"})
        ligne({"t": "envoye"})
        ligne({"t": "etape", "etape": "generation"})
        if cas in ("quota", "credits"):
            return {"t": "echec", "code": cas, "message": f"faux {cas}", "reprise": ""}
        nom, data = self.fichier or (("v.webm", WEBM) if cas == "webm" else ("v.mp4", MP4))
        dossier = self.sortie_hors or self.sortie
        f = dossier / f"{len(self.entrees)}-{nom}"
        f.write_bytes(data)
        return {"t": "fini", "fichier": str(f), "nom": f.name, "modele_utilise": entree["modele"]}


def _videos(lieux, horloge=None, **kw):
    w = FauxWorker(lieux / "Videos-IA", **kw)
    vus: list[dict] = []
    v = Videos(publish=lambda kind, **ev: vus.append({"kind": kind, **ev}), etat=Etat(), runner=w,
               horloge=horloge or Horloge(MATIN))
    return v, vus, w


def _attendre(vus, job_id, etats=("fini", "echec"), timeout=5.0, apres=0):
    fin = time.time() + timeout
    while time.time() < fin:
        for ev in vus[apres:]:
            if ev["job"] == job_id and ev["etat"] in etats:
                return ev
        time.sleep(0.01)
    raise AssertionError(vus)


def _attendre_etape(vus, job_id, etape, timeout=5.0):
    fin = time.time() + timeout
    while time.time() < fin:
        for ev in vus:
            if ev["job"] == job_id and ev["etape"] == etape:
                return ev
        time.sleep(0.01)
    raise AssertionError(vus)


# ---------------------------------------------------------------- catalogue

def test_catalogue_flow_valeurs_relevees_b0():
    data = json.loads(ia_web.CATALOGUE_FILE.read_text(encoding="utf-8"))
    flow = data["services"]["flow"]
    # relevé B0 de l'utilisateur le 28/10/2026 : modèles et coûts lus sur le vrai site, durées et pointe non vérifiées
    assert flow["url"] == "https://flow.google.com/" and flow["modeles_releves"] and "28/10/2026" in flow["_releve_b0"]
    assert ia_web.services("video") == ["flow", "dreamina"] and ia_web.services("image") == ["gemini", "chatgpt", "bing"]
    assert [(m["id"], m["cout"]) for m in ia_web.modeles("flow")] == [("lite", 10), ("fast", 20), ("quality", 100), ("omni-flash", 12)]
    assert ia_web.plafond("flow") == 50 and ia_web.fenetre("flow") == "24h" and ia_web.fournisseur("flow") == "google"
    assert ia_web.modele_defaut("flow") == "lite"  # auto : le moins cher
    # 30/10 : plus de créneau de pointe PRÉVENTIF au catalogue (jamais vérifié, il reportait à 18 h)
    assert ia_web.reglages_video("flow") == {"durees": [4, 6, 8], "duree_defaut": 8, "pointe_utc": None}
    assert videos_ia.payant("flow", "quality") and not videos_ia.payant("flow", "fast")
    assert not videos_ia.payant("flow", "lite") and "gratuit" not in ia_web.modele_info("gemini")


def test_reglages_video_illisibles_valeurs_de_repli(tmp_path, monkeypatch):
    f = tmp_path / "ia-web.json"
    f.write_text(json.dumps({"services": {"flow": {
        "type": "video", "fournisseur": "google", "modeles": [{"id": "lite", "cout": 10}], "plafond": 50,
        "fenetre": "24h", "video": {"durees": [True, "8", 0, 999], "duree_defaut": 3, "pointe_utc": [17, 14]}}}}),
        encoding="utf-8")
    monkeypatch.setattr(ia_web, "CATALOGUE_FILE", f)
    assert ia_web.reglages_video("flow") == {"durees": [4, 6, 8], "duree_defaut": 8, "pointe_utc": None}
    assert videos_ia.fin_pointe("flow", POINTE) is None  # créneau illisible : pas de report


def test_disponible_selon_le_cout_images_inchangees(lieux):
    e = Etat()
    e.compter("amw", "flow", MATIN, unites=40)
    assert e.disponible("amw", "flow", MATIN, unites=20) == (False, "plafond", False)  # Fast ne tient plus
    assert e.disponible("amw", "flow", MATIN, unites=10)[0] is True                     # Lite oui
    e.compter("amw", "gemini", MATIN, unites=19)
    assert e.disponible("amw", "gemini", MATIN)[0] is True                              # règle des images
    e.compter("amw", "gemini", MATIN)
    assert e.disponible("amw", "gemini", MATIN) == (False, "plafond", False)


# ---------------------------------------------------------------- contrat et évènement

def test_contrat_du_worker_et_evenement(lieux):
    v, vus, w = _videos(lieux)
    finies = []
    v.on_fini = finies.append
    r = v.submit("un chat qui saute", "auto", "", 0, "", "general")
    assert r["ok"] and r["etat"] == "file" and "arrière-plan" in r["message"]
    fin = _attendre(vus, r["job"])
    assert fin["kind"] == "video_job" and fin["etat"] == "fini" and fin["service"] == fin["moteur"] == "flow"
    assert fin["modele"] == "lite" and fin["cout"] == 10 and fin["duree"] == 8 and fin["compte"] == "amw"
    assert fin["nom"].endswith(".mp4") and "chemin" not in fin and fin["channel"] == "general"
    e = w.entrees[0]
    assert e["type"] == "video" and e["affichage"] == "masquee" and e["fenetre"] == "24h"  # fenêtre du QUOTA
    assert e["service"] == e["moteur"] == "flow" and e["modele"] == "lite" and e["plafond"] == 50
    assert e["duree"] == 8 and e["fichiers"] == [] and e["sortie"] == str(lieux / "Videos-IA")
    assert e["delai_s"] == videos_ia.DELAI_S == 360 and Path(e["profil"]).parts[-2:] == ("profils", "amw")
    assert [ev["etape"] for ev in vus if ev["job"] == r["job"]][:2] == ["file", "ouverture"]
    limite = time.time() + 2  # `on_fini` suit la publication de l'évènement, dans le fil des vidéos
    while not finies and time.time() < limite:
        time.sleep(0.01)
    assert finies and finies[0].chemin == str((lieux / "Videos-IA" / fin["nom"]).resolve())
    assert w.chrome == [True] and not ia_web.CHROME.locked()  # un seul Chrome, images et vidéos


def test_image_de_depart_et_duree(lieux):
    img = lieux / "attachments" / "depart.png"
    img.write_bytes(PNG)
    v, vus, w = _videos(lieux)
    r = v.submit("anime cette image", "flow", "omni-flash", 4, str(img))
    fin = _attendre(vus, r["job"])
    assert fin["etat"] == "fini" and fin["image"] == "depart.png" and fin["modele"] == "omni-flash"
    assert fin["cout"] == r["credits_estimes"] == 7  # coût de CETTE durée (relevé B0 : Omni 4 s = 7)
    assert w.entrees[0]["fichiers"] == [str(img.resolve())] and w.entrees[0]["duree"] == 4


@pytest.mark.parametrize("args, erreur", [
    (("", "auto"), "prompt vide"),
    (("x" * 2001, "auto"), "trop long"),
    (("un chat", "kling"), "service vidéo inconnu"),
    (("un chat", "auto", "fast"), "pas en auto"),
    (("un chat", "flow", "veo-9"), "modèle inconnu"),
    (("un chat", "flow", "quality"), "refusé : payant"),
    (("un chat", "flow", "", 5), "durée non proposée"),
    (("un chat", "flow", "", "huit"), "durée non proposée"),
    (("un chat", "flow", "", 0, "relatif.png"), "chemin absolu attendu"),
    (("un chat", "flow", "", 0, "HORS"), "hors des pièces jointes"),  # HORS : image hors des pièces jointes
    (("un chat", "flow", "", 0, ["/a.png", "/b.png"]), "une seule image"),
])
def test_demandes_refusees_sans_rien_lancer(lieux, args, erreur):
    if "HORS" in args:  # 03/11 : /etc/hostname n'existe ni sous macOS ni sous Windows (où il n'est pas absolu)
        hors = lieux / "ailleurs" / "chat.png"
        hors.parent.mkdir()
        hors.write_bytes(PNG)
        args = tuple(str(hors) if a == "HORS" else a for a in args)
    v, vus, w = _videos(lieux)
    r = v.submit(*args)
    assert r["ok"] is False and erreur in r["error"] and not vus and not w.entrees


def test_image_de_depart_pas_une_image(lieux):
    faux = lieux / "attachments" / "faux.png"
    faux.write_bytes(b"pas une image du tout")
    v, vus, _ = _videos(lieux)
    r = v.submit("un chat", "flow", "", 0, str(faux))
    assert r["ok"] is False and "PNG, JPEG ou WebP" in r["error"] and not vus


def test_modele_plus_cher_que_le_plafond_refuse(lieux, tmp_path, monkeypatch):
    f = tmp_path / "ia-web.json"
    f.write_text(json.dumps({"services": {"flow": {
        "type": "video", "fournisseur": "google", "plafond": 15, "fenetre": "24h", "rang": 1,
        "modeles": [{"id": "lite", "cout": 10}, {"id": "fast", "cout": 20}]}}}), encoding="utf-8")
    monkeypatch.setattr(ia_web, "CATALOGUE_FILE", f)
    v, vus, _ = _videos(lieux)
    assert "refusé : payant" in v.submit("un chat", "flow", "fast")["error"] and not vus


def test_arret_d_urgence_refuse_la_demande(lieux):
    config.ESTOP_FILE.write_text("1")
    v, vus, _ = _videos(lieux)
    assert "arrêt d'urgence" in v.submit("un chat")["error"] and not vus


# ---------------------------------------------------------------- crédits

def test_decompte_en_credits_fenetre_24h_ouverte_par_la_premiere(lieux):
    h = Horloge(MATIN)
    v, vus, w = _videos(lieux, horloge=h)
    for _ in range(3):
        assert _attendre(vus, v.submit("un chat")["job"])["etat"] == "fini"   # 3 Lite : 30 crédits
    assert v.etat.compte("amw", h(), "flow")["n"] == 30
    assert _attendre(vus, v.submit("un chat", "flow", "fast")["job"])["etat"] == "fini"  # 50/50
    h.t = MATIN + 3600
    fin = _attendre(vus, v.submit("un chat", "flow")["job"])         # moteur nommé : jamais de bascule
    assert fin["etat"] == "echec" and fin["code"] == "plafond" and fin["erreur"] == videos_ia.TOUS_PLAFOND
    assert len(w.entrees) == 4                                       # rien n'est parti sans crédit
    st = v.status()["comptes"][0]
    assert (st["credits_utilises"], st["credits_restants"], st["renouvellement"]) == (50, 0, MATIN + 86400)
    fin = _attendre(vus, v.submit("un chat")["job"])                 # auto : bascule seule (29/10)
    assert (fin["etat"], fin["service"], fin["duree"]) == ("fini", "dreamina", 5)
    assert [e["service"] for e in w.entrees[4:]] == ["dreamina"]     # Flow plein : pas même essayé
    h.t = MATIN + 86400                                              # 24 h après la PREMIÈRE vidéo
    fin = _attendre(vus, v.submit("un chat")["job"])
    assert (fin["etat"], fin["service"]) == ("fini", "flow")         # le réglage reprend la main
    assert v.etat.compte("amw", h(), "flow")["n"] == 10
    journal = (lieux / "videos.log").read_text(encoding="utf-8")
    assert journal.count("FENÊTRE 24h ouverte") == 2 and "renouvellement prévu" in journal
    assert "10 crédits (10/50 sur la fenêtre 24h)" in journal and "20 crédits (50/50" in journal


def test_credits_insuffisants_compte_suivant(lieux):
    _comptes(lieux, "amw", "boutique")
    v, vus, w = _videos(lieux, scenarios={"amw": ["credits"]})
    fin = _attendre(vus, v.submit("un chat")["job"])
    assert fin["etat"] == "fini" and fin["compte"] == "boutique"
    assert [Path(e["profil"]).name for e in w.entrees] == ["amw", "boutique"]
    assert any(ev["etape"] == "bascule" for ev in vus)
    assert v.etat.disponible("amw", "flow", MATIN + 60, unites=10) == (False, "quota", False)
    assert "ÉPUISEMENT (credits) amw flow" in (lieux / "videos.log").read_text(encoding="utf-8")


def test_activite_inhabituelle_arret_et_compte_au_repos(lieux):
    _comptes(lieux, "amw", "boutique")
    v, vus, w = _videos(lieux, scenarios={"amw": ["activite"]})
    fin = _attendre(vus, v.submit("un chat")["job"])
    assert fin["etat"] == "echec" and fin["code"] == "activite" and fin["texte_ia"] == "faux activite"
    assert len(w.entrees) == 1                                       # arrêt : aucun autre compte essayé
    assert v.etat.disponible("amw", "flow", MATIN + 5 * 3600)[1] == "captcha"
    assert v.etat.disponible("amw", "flow", MATIN + 6 * 3600 + 1)[0] is True
    assert v.etat.disponible("amw", "gemini", MATIN + 3600)[1] == "captcha"  # repos du COMPTE


# ---------------------------------------------------------------- fichier rendu

@pytest.mark.parametrize("nom, data, ok", [
    ("v.mp4", MP4, True),
    ("v.webm", WEBM, True),
    ("v.mp4", PNG, False),          # une image nommée .mp4
    ("v.webm", MP4, False),         # extension qui ne va pas avec les octets
    ("v.mp4", b"", False),
])
def test_video_reconnue_par_ses_octets(lieux, nom, data, ok):
    v, vus, _ = _videos(lieux, fichier=(nom, data))
    fin = _attendre(vus, v.submit("un chat")["job"])
    assert (fin["etat"] == "fini") is ok
    if not ok:
        assert fin["code"] == "format" and fin["nom"] == ""


def test_chemin_avec_nul_erreur_propre(lieux):
    # Relecture P41 B2 (M2) : un NUL faisait lever ValueError (submit) ou finir en « interne ».
    v, vus, w = _videos(lieux)
    r = v.submit("un chat", "flow", "", 0, str(lieux / "attachments" / "a\0b.png"))
    assert r == {"ok": False, "error": "image de départ refusée : fichier introuvable"} and not vus
    assert videos_ia.verifier_video(str(lieux / "Videos-IA" / "v\0.mp4")) == (None, "fichier introuvable")
    v.runner = lambda entree, ligne: {"t": "fini", "fichier": str(lieux / "Videos-IA" / "v\0.mp4")}
    job = videos_ia.Job(id="x", prompt="p", demande="auto", duree=8, cree=MATIN)
    v._jobs["x"] = job
    v._traiter(job)
    assert job.etat == "echec" and job.code == "format"


def test_video_hors_du_dossier_de_sortie_refusee(lieux):
    ailleurs = lieux / "ailleurs"
    ailleurs.mkdir()
    v, vus, _ = _videos(lieux, sortie_hors=ailleurs)
    fin = _attendre(vus, v.submit("un chat")["job"])
    assert fin["code"] == "format" and "hors du dossier de sortie" in (lieux / "videos.log").read_text(encoding="utf-8")


# ---------------------------------------------------------------- créneau de pointe

def test_pointe_reportee_sans_essai_puis_lancee(lieux, pointe_14_17):
    h = Horloge(POINTE)
    v, vus, w = _videos(lieux, horloge=h)
    job = v.submit("un chat")["job"]
    ev = _attendre_etape(vus, job, "pointe")
    assert ev["etat"] == "file" and ev["reporte_jusqua"] == FIN_POINTE and ev["position"] == 1
    assert v.status()["pointe_jusqua"] == FIN_POINTE
    time.sleep(0.2)
    assert w.entrees == []                                           # rien n'est tenté pendant la pointe
    h.t = FIN_POINTE + 30
    v.reveiller()
    assert _attendre(vus, job)["etat"] == "fini" and len(w.entrees) == 1
    assert "REPORTÉ (pointe, report 1/3)" in (lieux / "videos.log").read_text(encoding="utf-8")


def test_pointe_annoncee_par_le_site_jamais_en_boucle(lieux):
    h = Horloge(MATIN)
    v, vus, w = _videos(lieux, horloge=h, scenarios={"amw": ["pointe"] * 10})
    job = v.submit("un chat")["job"]
    fin = None
    for _ in range(200):
        fini = [ev for ev in vus if ev["job"] == job and ev["etat"] == "echec"]
        if fini:
            fin = fini[0]
            break
        h.t += 86400                                                  # le temps passe (toujours 10 h UTC)
        v.reveiller()
        time.sleep(0.02)
    assert fin is not None and fin["code"] == "pointe"
    assert len(w.entrees) == videos_ia.REPORTS_MAX + 1                # 3 reports, puis abandon
    assert v.etat.compte("amw", h(), "flow")["n"] == 0                # rien de débité


def _apres_envoye(lieux, codes, reprise=""):
    """Worker qui émet `envoye` PUIS l'échec `codes[i]` (pointe, rythme, activite), puis `fini`."""
    appels: list[dict] = []

    def runner(entree, ligne):
        appels.append(entree)
        ligne({"t": "etape", "etape": "ouverture"})
        ligne({"t": "envoye"})
        if len(appels) <= len(codes):
            return {"t": "echec", "code": codes[len(appels) - 1], "message": "faux", "reprise": reprise}
        f = lieux / "Videos-IA" / f"{len(appels)}.mp4"
        f.write_bytes(MP4)
        return {"t": "fini", "fichier": str(f), "modele_utilise": entree["modele"]}
    return runner, appels


@pytest.mark.parametrize("code", ["pointe", "rythme"])
def test_envoye_puis_report_credits_rendus(lieux, code):
    # Relecture P41 B2 (M1) : avant, chaque essai recomptait (10 puis 20 pour UNE vidéo).
    h = Horloge(MATIN)
    runner, appels = _apres_envoye(lieux, [code], reprise="+1h")
    v = Videos(publish=lambda *a, **k: None, etat=Etat(), runner=runner, horloge=h)
    job = videos_ia.Job(id="x", prompt="p", demande="auto", duree=8, cree=h())
    v._jobs["x"] = job
    v._traiter(job)
    c = v.etat.compte("amw", h(), "flow")
    assert job.etat == "file" and job.etape == code and c["n"] == 0 and c["debut"] is None
    h.t += 3700
    v._traiter(job)
    assert job.etat == "fini" and len(appels) == 2
    assert v.etat.compte("amw", h(), "flow")["n"] == 10                 # le coût d'UNE vidéo
    assert f"{code} après l'envoi : 10 crédits rendus" in (lieux / "videos.log").read_text(encoding="utf-8")


def test_envoye_puis_activite_credits_rendus(lieux):
    runner, _ = _apres_envoye(lieux, ["activite"])
    v = Videos(publish=lambda *a, **k: None, etat=Etat(), runner=runner, horloge=Horloge(MATIN))
    job = videos_ia.Job(id="x", prompt="p", demande="auto", duree=8, cree=MATIN)
    v._jobs["x"] = job
    v._traiter(job)
    assert job.code == "activite" and v.etat.compte("amw", MATIN, "flow")["n"] == 0  # rien débité
    assert v.etat.disponible("amw", "flow", MATIN + 60)[1] == "captcha"             # repos gardé


def test_reprise_lue_sur_le_site_plafonnee(lieux):
    # Relecture P41 B2 (M3) : « +99h » tenait la place de file 99 h.
    runner, _ = _apres_envoye(lieux, ["pointe"], reprise="+99h")
    v = Videos(publish=lambda *a, **k: None, etat=Etat(), runner=runner, horloge=Horloge(MATIN))
    job = videos_ia.Job(id="x", prompt="p", demande="auto", duree=8, cree=MATIN)
    v._jobs["x"] = job
    v._traiter(job)
    assert job.etat == "file" and job.reporte_jusqua == MATIN + videos_ia.REPRISE_MAX_S == MATIN + 6 * 3600
    runner, _ = _apres_envoye(lieux, ["pointe"], reprise="+2h")       # plus courte : gardée telle quelle
    v.runner = runner
    job2 = videos_ia.Job(id="y", prompt="p", demande="auto", duree=8, cree=MATIN)
    v._jobs["y"] = job2
    v._traiter(job2)
    assert job2.reporte_jusqua == MATIN + 2 * 3600


def test_rythme_reporte_a_l_heure_dite_meme_compte(lieux):
    h = Horloge(MATIN)
    v, vus, w = _videos(lieux, horloge=h, scenarios={"amw": ["rythme"]})
    job = v.submit("un chat")["job"]
    ev = _attendre_etape(vus, job, "rythme")
    assert ev["reporte_jusqua"] == MATIN + videos_ia.RYTHME_REPRISE_S
    h.t = MATIN + 601
    v.reveiller()
    assert _attendre(vus, job)["etat"] == "fini"
    assert [Path(e["profil"]).name for e in w.entrees] == ["amw", "amw"]  # le compte n'était pas épuisé


def test_arret_d_urgence_annule_les_videos_reportees(lieux, pointe_14_17):
    v, vus, w = _videos(lieux, horloge=Horloge(POINTE))
    job = v.submit("un chat")["job"]
    _attendre_etape(vus, job, "pointe")
    config.ESTOP_FILE.write_text("1")
    v.reveiller()
    fin = _attendre(vus, job)
    assert fin["code"] == "estop" and w.entrees == []


# ---------------------------------------------------------------- annuler

def test_annuler_en_file_en_cours_et_reportee(lieux, pointe_14_17):
    lache = threading.Event()
    vus: list[dict] = []
    appels: list[dict] = []

    def runner(entree, ligne):
        appels.append(entree)
        ligne({"t": "envoye"})
        lache.wait(5)
        f = lieux / "Videos-IA" / f"{len(appels)}.mp4"
        f.write_bytes(MP4)
        return {"t": "fini", "fichier": str(f), "modele_utilise": entree["modele"]}

    v = Videos(publish=lambda kind, **ev: vus.append(ev), etat=Etat(), runner=runner, horloge=Horloge(MATIN))
    j1 = v.submit("un")["job"]
    _attendre(vus, j1, etats=("en_cours",))
    j2 = v.submit("deux")["job"]
    assert v.cancel(j2) == {"ok": True, "job": j2}
    assert _attendre(vus, j2)["code"] == "annule"
    assert v.cancel(j1)["ok"]
    lache.set()
    fin = _attendre(vus, j1)
    assert fin["etat"] == "echec" and fin["code"] == "annule"          # annulée l'emporte, fichier gardé
    assert len(appels) == 1 and v.cancel(j1)["error"] == "cette vidéo est déjà terminée"
    assert v.etat.compte("amw", MATIN, "flow")["n"] == 10              # crédits partis : comptés
    # reportée (pointe) : annulée tout de suite, sans attendre la fin du créneau
    v.horloge = Horloge(POINTE)
    j3 = v.submit("trois")["job"]
    _attendre_etape(vus, j3, "pointe")
    v.cancel(j3)
    assert _attendre(vus, j3, apres=0)["code"] == "annule" and len(appels) == 1


# ---------------------------------------------------------------- textes d'écran

def test_textes_d_ecran_traduits():
    textes = [*videos_ia.MESSAGES.values(), videos_ia.TOUS_EPUISES, videos_ia.TOUS_PLAFOND, videos_ia.EN_PAUSE,
              videos_ia.NON_CONNECTE, videos_ia.COMPTES_ILLISIBLES, videos_ia.LANCEE]
    manquants = [t for t in textes if not i18n.covered(t)]
    assert manquants == []


# ---------------------------------------------------------------- lot B3 : durée par modèle, réglage

def test_duree_limitee_a_ce_que_le_modele_propose(lieux):
    assert ia_web.durees_video("flow", "lite") == {8: 10} and ia_web.durees_video("flow", "fast") == {8: 20}
    assert ia_web.durees_video("flow", "omni-flash") == {4: 7, 8: 12, 10: 15}  # 6 s : coût non relevé
    assert ia_web.cout("flow", "omni-flash", 10) == 15 and ia_web.cout("flow", "lite", 0) == 10
    v, vus, w = _videos(lieux)
    r = v.submit("un chat", "flow", "lite", 4)
    assert r["ok"] is False and "Veo 3.1 Lite" in r["error"] and "8 s (10 crédits)" in r["error"] and not w.entrees
    assert "4 s (7 crédits)" in v.submit("un chat", "flow", "omni-flash", 6)["error"]
    r = v.submit("un chat", "flow", "omni-flash", 10)
    assert r["ok"] and r["duree"] == 10 and r["credits_estimes"] == 15
    assert _attendre(vus, r["job"])["cout"] == 15 and v.etat.compte("amw", MATIN, "flow")["n"] == 15


def test_reglage_modele_et_duree(lieux):
    from astrel import settings

    choix = dict(videos_ia.choix_proposes())
    assert list(choix)[0] == settings._video_choix_defaut() == "flow/lite/8"
    assert choix["flow/omni-flash/4"] == "Google Flow · Omni 1.1 Flash (720p) · 4 s · 7 crédits"
    assert not any("/quality/" in c for c in choix)  # payant : jamais proposé
    assert i18n.tr(choix["flow/lite/8"], "en").endswith("8 s · 10 credits")
    settings.reglages.set("video_choix", "flow/omni-flash/4")
    v, vus, w = _videos(lieux)
    r = v.submit("un chat")                      # auto : modèle et durée du réglage
    assert r["ok"] and r["duree"] == 4 and r["credits_estimes"] == 7
    r10 = v.submit("un chat", duree=10)
    assert r10["credits_estimes"] == 15  # durée demandée prioritaire
    assert _attendre(vus, r["job"])["modele"] == "omni-flash"
    assert w.entrees[0]["modele"] == "omni-flash" and w.entrees[0]["duree"] == 4
    # 30/10 : la 2e vidéo est attendue AVANT la fin du test (sinon son fil finissait après le retrait
    # des chemins de test et écrivait dans le VRAI logs/videos.log)
    assert _attendre(vus, r10["job"])["etat"] == "fini"


def test_reglage_plus_propose_defaut_du_service(lieux, monkeypatch):
    from astrel import settings

    monkeypatch.setattr(settings.reglages, "value", lambda key: "flow/quality/8")
    assert videos_ia.reglage_choix() is None
    v, vus, w = _videos(lieux)
    r = v.submit("un chat")
    assert r["ok"] and r["credits_estimes"] == 10 and _attendre(vus, r["job"])["modele"] == "lite"


def test_memoire_en_blocage_aucune_video(lieux, monkeypatch):
    monkeypatch.setattr(videos_ia, "memoire_bloquee", lambda: True)
    v, vus, w = _videos(lieux)
    assert "mémoire du PC" in v.submit("un chat")["error"] and not vus


# ---------------------------------------------------------------- lot B3 : arrêt d'urgence

def test_stop_tout_annule_en_cours_et_en_file(lieux):
    vus: list[dict] = []

    def runner(entree, ligne):  # tient la génération jusqu'à l'annulation
        ligne({"t": "envoye"})
        while not v.en_cours.annule:
            time.sleep(0.01)
        return {"t": "echec", "code": "interne"}

    v = Videos(publish=lambda kind, **ev: vus.append(ev), etat=Etat(), runner=runner, horloge=Horloge(MATIN))
    j1 = v.submit("un")["job"]
    _attendre(vus, j1, etats=("en_cours",))
    j2 = v.submit("deux")["job"]
    assert v.estop() == {"ok": True, "annulees": 2}
    assert _attendre(vus, j1)["code"] == _attendre(vus, j2)["code"] == "estop"
    assert v.estop()["annulees"] == 0


def _vivant(pid: int) -> bool:
    return ia_web.vivant(pid)


@pytest.mark.parametrize("par", ["fichier", "estop"])
def test_arret_d_urgence_tue_le_worker_et_son_chrome(lieux, monkeypatch, tmp_path, par):
    """Vrai processus, sans navigateur : le « worker » lance un faux Chrome DÉTACHÉ (sa propre session,
    comme Playwright) sur le profil du compte. `runtime/ESTOP` écrit à la main ou « Stop tout » :
    la vidéo finit « estop » et aucun des deux ne survit."""
    import sys

    pids = tmp_path / "pids"
    script = tmp_path / "faux_worker.py"
    script.write_text(
        "import json, subprocess, sys, time\n"
        "entree = json.loads(sys.stdin.readline())\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)',\n"
        "                      '--user-data-dir=' + entree['profil']], start_new_session=True)\n"
        f"open({str(pids)!r}, 'w').write(f'{{c.pid}}')\n"
        "print(json.dumps({'t': 'etape', 'etape': 'generation'}), flush=True)\n"
        "time.sleep(120)\n", encoding="utf-8")
    monkeypatch.setattr(Videos, "commande", staticmethod(lambda: [sys.executable, str(script)]))
    monkeypatch.setattr(ia_web, "ARRET_SONDE_S", 0.05)
    monkeypatch.setattr(ia_web, "ARRET_GRACE_S", 2.0)
    vus: list[dict] = []
    import psutil

    from astrel.platform import systeme

    v = Videos(publish=lambda kind, **ev: vus.append(ev), etat=Etat(), horloge=Horloge(MATIN))
    job = v.submit("un chat")["job"]
    workers: list[int] = []
    chromes: list[int] = []
    try:
        _attendre_etape(vus, job, "generation", timeout=10)
        chrome = int(pids.read_text())
        workers = systeme.processus_avec(str(script))  # /proc sous Linux, psutil ailleurs (03/11)
        chromes = ia_web.chrome_du_profil(Videos.profil("amw"))
        # 03/11 (CI Windows) : le python.exe d'une venv Windows est un LANCEUR qui relance le vrai python en
        # enfant, même ligne de commande : le faux Chrome y fait deux processus (lui et son enfant), tous deux
        # sur le profil, tous deux à arrêter. Ailleurs, un seul.
        assert workers and chrome in chromes, (workers, chromes, chrome)
        assert all(p == chrome or psutil.Process(p).ppid() == chrome for p in chromes), (chromes, chrome)
        if par == "fichier":
            config.ESTOP_FILE.write_text("1")
        else:
            v.estop()
        assert _attendre(vus, job, timeout=10)["code"] == "estop"
        fin = time.time() + 10
        while any(_vivant(p) for p in workers + chromes) and time.time() < fin:
            time.sleep(0.05)
        assert not any(_vivant(p) for p in workers + chromes) and ia_web.chrome_du_profil(Videos.profil("amw")) == []
    finally:
        # 03/11 : un échec ci-dessus laissait le worker tenir `ia_web.CHROME` 120 s : les tests vidéo SUIVANTS
        # restaient « en_cours » (étape « ouverture », en attente du verrou) sous Windows. Toujours arrêté ici.
        v.estop()
        fin = time.time() + 15
        while (j := v.job(job)) is not None and j.etat not in ("fini", "echec") and time.time() < fin:
            time.sleep(0.05)
        for p in {*workers, *chromes, *systeme.processus_avec(str(script)),
                  *ia_web.chrome_du_profil(Videos.profil("amw"))}:
            with contextlib.suppress(OSError):
                os.kill(p, systeme.SIGKILL)


# ---------------------------------------------------------------- lot B3 : cœur, fil, outil MCP

def test_video_job_dans_l_historique_du_hud():
    from astrel import events

    assert "video_job" in events.HISTORY and "video_job" not in events.STICKY


def test_commandes_du_coeur(lieux, monkeypatch):
    from astrel import core

    v, vus, w = _videos(lieux)
    monkeypatch.setattr(videos_ia, "videos", v)
    j = object.__new__(core.Astrel)
    r = j._on_command({"cmd": "video_generate", "prompt": "un chat", "duree": 8, "_from_claude": True})
    assert r["ok"] and r["credits_estimes"] == 10, r
    assert _attendre(vus, r["job"])["channel"]
    st = j._on_command({"cmd": "video_status"})
    assert st["comptes"][0]["credits_utilises"] == 10 and st["comptes"][0]["credits_restants"] == 40
    assert j._on_command({"cmd": "video_cancel", "job_id": r["job"]})["error"] == "cette vidéo est déjà terminée"
    bad = j._on_command({"cmd": "video_generate", "prompt": "un chat", "channel": "inexistant"})
    assert bad["ok"] is False and "canal inconnu" in bad["error"]
    config.ESTOP_FILE.write_text("1")
    assert "arrêt d'urgence" in j._on_command({"cmd": "video_generate", "prompt": "x"})["error"]


def test_video_finie_montree_dans_le_fil(lieux, monkeypatch):
    from astrel import core, medias

    monkeypatch.setattr(medias, "MEDIA_DIR", lieux / "medias")
    monkeypatch.setattr(medias, "source_roots", lambda: [lieux / "Videos-IA"])
    publies: list[tuple[str, dict]] = []
    monkeypatch.setattr(core.bus, "publish", lambda kind, **ev: publies.append((kind, ev)))
    v, vus, w = _videos(lieux)
    v.on_fini = core.Astrel._video_finie
    r = v.submit("un chat qui saute", channel="general")
    _attendre(vus, r["job"])
    fin = time.time() + 5  # `on_fini` suit la publication de la carte finie
    while not any(k == "media" for k, _ev in publies) and time.time() < fin:
        time.sleep(0.01)
    ev = next(ev for k, ev in publies if k == "media")
    assert ev["channel"] == "general" and ev["title"] == "un chat qui saute"
    assert [(i["kind"], i["mime"]) for i in ev["items"]] == [("video", "video/mp4")]


def test_outil_mcp_generate_video(monkeypatch):
    from astrel import capacites, mcp_server

    sent = []
    monkeypatch.setattr(mcp_server, "_core", lambda cmd, ensure_ascii=True: sent.append(cmd) or
                        {"ok": True, "job": "abc", "credits_estimes": 10})
    assert mcp_server.generate_video("un chat")["credits_estimes"] == 10
    assert sent[-1] == {"cmd": "video_generate", "prompt": "un chat", "engine": "auto"}
    mcp_server.generate_video("un chat", "flow", "omni-flash", 4, f"{chemins_machine.PROJETS}/a.png")
    assert sent[-1] == {"cmd": "video_generate", "prompt": "un chat", "engine": "flow", "model": "omni-flash",
                        "duree": 4, "image": f"{chemins_machine.PROJETS}/a.png"}
    monkeypatch.setattr(mcp_server, "_core", lambda cmd, ensure_ascii=True: {"ok": False, "error": "prompt vide"})
    with pytest.raises(Exception, match="prompt vide"):
        mcp_server.generate_video("")
    assert any(motif == "generate_video" for motif, _g in capacites.OUTILS_GROUPES)


# ---------------------------------------------------------------- lot B4 : état pour le HUD

def test_etat_video_pour_le_hud(lieux):
    """`video_etat` (sticky) suit chaque étape : crédits par compte, choix proposés, réglage, file."""
    from astrel import events, settings

    assert "video_etat" in events.STICKY and "video_etat" not in events.HISTORY
    etats: list[dict] = []
    v = Videos(publish=lambda kind, **ev: None, etat=Etat(), runner=FauxWorker(lieux / "Videos-IA"),
               horloge=Horloge(MATIN), publish_etat=lambda kind, **ev: etats.append({"kind": kind, **ev}))
    settings.reglages.set("video_choix", "flow/fast/8")
    job = v.submit("un chat")["job"]
    fin = time.time() + 5
    # attendre AUSSI la fin du job : l'état est publié dès l'envoi (crédits comptés), avant « fini »
    while not (etats and etats[-1]["comptes"][0]["credits_utilises"] == 20 and v.job(job).etat == "fini") and time.time() < fin:
        time.sleep(0.01)
    e = etats[-1]
    assert e["kind"] == "video_etat" and "ok" not in e and v.job(job).etat == "fini"
    c = e["comptes"][0]
    assert (c["compte"], c["service_libelle"], c["credits_restants"], c["plafond"]) == ("amw", "Google Flow", 30, 50)
    assert c["renouvellement"] == MATIN + 86400 and c["a_reconnecter"] is False
    assert e["reglage"] == "flow/fast/8" and e["choix"][0] == {
        "valeur": "flow/lite/8", "libelle": "Google Flow · Veo 3.1 Lite · 8 s · 10 crédits",
        # 29/10 : parties du choix, pour les menus « Modèle » puis « Durée » du HUD
        "service": "flow", "service_libelle": "Google Flow", "modele": "lite", "modele_libelle": "Veo 3.1 Lite",
        "duree": 8, "cout": 10, "duree_libelle": "8 s · 10 crédits", "pieces": True}
    rapide = next(c for c in e["choix"] if c["valeur"] == "dreamina/seedance-2-rapide/15")
    assert (rapide["service_libelle"], rapide["modele_libelle"], rapide["duree_libelle"]) == (
        "Dreamina", "Seedance 2.0 rapide (720p)", "15 s · 180 crédits")


def test_video_status_republie_l_etat(lieux, monkeypatch):
    from astrel import core

    etats: list[dict] = []
    v = Videos(publish=lambda kind, **ev: None, etat=Etat(), runner=FauxWorker(lieux / "Videos-IA"),
               horloge=Horloge(MATIN), publish_etat=lambda kind, **ev: etats.append(ev))
    monkeypatch.setattr(videos_ia, "videos", v)
    st = object.__new__(core.Astrel)._on_command({"cmd": "video_status"})
    assert st["ok"] and st["reglage"] == "flow/lite/8" and len(etats) == 1 and etats[0]["choix"] == st["choix"]


def test_annulee_pendant_que_une_image_tient_chrome(lieux, monkeypatch):
    """Petit reste de B3 : une vidéo qui attend le Chrome commun (image en cours) s'annule tout de suite,
    sans attendre la fin de l'image, et n'en lance aucun."""
    monkeypatch.setattr(ia_web, "ARRET_SONDE_S", 0.02)
    v, vus, w = _videos(lieux)
    with ia_web.CHROME:  # une image tient Chrome
        job = v.submit("un chat")["job"]
        _attendre(vus, job, etats=("en_cours",))
        v.cancel(job)
        fin = _attendre(vus, job, timeout=2)
        assert fin["code"] == "annule" and w.entrees == []


# ---------------------------------------------------------------- lot B5 : Dreamina, second site

def test_catalogue_dreamina_releve_b5():
    data = json.loads(ia_web.CATALOGUE_FILE.read_text(encoding="utf-8"))
    d = data["services"]["dreamina"]
    # relevé B5 du 29/10/2026 : 120 crédits gratuits par jour (plus les bonus : limite au solde du site),
    # prix catalogue de TOUS les modèles relevés l'après-midi (720p ; brouillon 480p, 1.0 en 1080p)
    assert d["url"] == "https://dreamina.capcut.com/ai-tool/home?type=video" and "29/10/2026" in d["_releve"]
    assert "dreamina" in videos_ia.SERVICES_PRETS and ia_web.libelle("dreamina") == "Dreamina"
    assert ia_web.fournisseur("dreamina") == "google" and ia_web.plafond("dreamina") == 120
    assert ia_web.fenetre("dreamina") == "jour" and ia_web.modele_defaut("dreamina") == "seedance-2-mini"
    assert [(m["id"], m["cout"]) for m in ia_web.modeles("dreamina")] == [
        ("seedance-2-mini", 55), ("seedance-2-rapide", 60), ("seedance-2", 75), ("seedance-25-brouillon", 75),
        ("seedance-25", 160), ("seedance-15-pro", 90), ("seedance-1-rapide", 25), ("seedance-1", 125),
        ("happyhorse-11", 135), ("wan-3", 80)]
    assert ia_web.durees_video("dreamina") == {5: 55, 10: 110, 15: 165} and ia_web.duree_defaut("dreamina") == 5
    assert ia_web.durees_video("dreamina", "seedance-25-brouillon") == {5: 75}  # seul prix relevé
    assert ia_web.durees_video("dreamina", "seedance-1") == {5: 125, 10: 250}
    assert ia_web.reglages_video("dreamina") == {"durees": [5, 10, 15], "duree_defaut": 5, "pointe_utc": None}
    assert ia_web.limite_solde("dreamina") and not ia_web.limite_solde("flow")
    assert not videos_ia.payant("dreamina") and videos_ia.fin_pointe("dreamina", POINTE) is None
    assert ia_web.accepte_pieces("flow") and not ia_web.accepte_pieces("dreamina")  # image de départ : Flow seul
    choix = dict(videos_ia.choix_proposes())
    assert choix["dreamina/seedance-2-mini/5"] == "Dreamina · Seedance 2.0 Mini (720p) · 5 s · 55 crédits"
    assert i18n.tr(choix["dreamina/seedance-2-mini/5"], "en").endswith("5 s · 55 credits")


def test_dreamina_manuel_contrat_du_worker(lieux):
    v, vus, w = _videos(lieux)
    r = v.submit("un chat roux sur un canapé", "dreamina")
    assert r["ok"] and r["duree"] == 5 and r["credits_estimes"] == 55
    fin = _attendre(vus, r["job"])
    assert fin["etat"] == "fini" and fin["service"] == "dreamina" and fin["modele"] == "seedance-2-mini"
    assert fin["cout"] == 55 and fin["duree"] == 5 and fin["service_libelle"] == "Dreamina"
    e = w.entrees[0]
    assert e["service"] == e["moteur"] == "dreamina" and e["modele"] == "seedance-2-mini" and e["duree"] == 5
    assert e["plafond"] == videos_ia.PLAFOND_SOLDE and e["fenetre"] == "jour" and e["affichage"] == "masquee"
    assert e["fichiers"] == []
    assert v.etat.compte("amw", MATIN, "dreamina")["n"] == 55 and v.etat.compte("amw", MATIN, "flow")["n"] == 0
    # Limite au solde (29/10) : une 3e vidéo part au-delà des 120 du jour ; le worker lit le solde du site
    for _ in range(2):
        assert _attendre(vus, v.submit("un chat", "dreamina")["job"])["etat"] == "fini"
    assert len(w.entrees) == 3 and v.etat.compte("amw", MATIN, "dreamina")["n"] == 165


def test_dreamina_refus_avant_la_file(lieux):
    img = lieux / "attachments" / "depart.png"
    img.write_bytes(PNG)
    v, vus, w = _videos(lieux)
    r = v.submit("un chat", "dreamina", "", 8)
    assert r["ok"] is False and "5 s (55 crédits)" in r["error"]
    assert "pas prise en charge par Dreamina" in v.submit("un chat", "dreamina", "", 0, str(img))["error"]
    assert "modèle inconnu" in v.submit("un chat", "dreamina", "seedance-9")["error"]
    assert not w.entrees and not vus


def test_auto_suit_le_reglage_et_bascule_seule(lieux, monkeypatch):
    # Auto = le réglage `video_choix` (B3 ; défaut Flow Lite 8 s). Réponse de l'utilisateur du 29/10 : Flow
    # épuisé, la vidéo part SEULE sur Dreamina, à son modèle et sa durée par défaut (Mini 5 s).
    v, vus, w = _videos(lieux, scenarios={"amw": ["quota"]})
    r = v.submit("un chat")
    assert r["ok"] and r["service"] == "auto" and r["duree"] == 8 and r["credits_estimes"] == 10
    fin = _attendre(vus, r["job"])
    assert (fin["etat"], fin["service"], fin["duree"], fin["cout"], fin["proposition"]) == ("fini", "dreamina", 5, 55, "")
    assert [(e["service"], e["modele"], e["duree"]) for e in w.entrees] == [("flow", "lite", 8), ("dreamina", "seedance-2-mini", 5)]
    assert "BASCULE flow → dreamina (quota sur flow)" in (lieux / "videos.log").read_text(encoding="utf-8")
    # Moteur NOMMÉ : pas de bascule, le refus PROPOSE Dreamina (bouton « Essayer avec »)
    v1, vus1, w1 = _videos(lieux, scenarios={"amw": ["quota"]})
    fin = _attendre(vus1, v1.submit("un chat", "flow")["job"])
    assert fin["etat"] == "echec" and fin["code"] == "quota" and fin["proposition"] == "dreamina"
    assert not w1.entrees                                           # Flow déjà noté épuisé : pas relancé
    config.IMAGES_ETAT_FILE.unlink()                                # comptes neufs pour la suite
    # Réglé sur un modèle de Dreamina : auto y va, à ce modèle et cette durée ; épuisé : Flow, Lite 8 s
    monkeypatch.setattr(videos_ia, "reglage_choix", lambda: ("dreamina", "seedance-2-rapide", 10))
    v2, vus2, w2 = _videos(lieux)
    r = v2.submit("un chat")
    assert r["ok"] and r["duree"] == 10 and r["credits_estimes"] == 120
    assert _attendre(vus2, r["job"])["service"] == "dreamina"
    assert (w2.entrees[0]["modele"], w2.entrees[0]["duree"]) == ("seedance-2-rapide", 10)
    v3, vus3, w3 = _videos(lieux, scenarios={"amw": ["credits"]})
    fin = _attendre(vus3, v3.submit("un chat")["job"])
    assert (fin["etat"], fin["service"], fin["modele"], fin["duree"]) == ("fini", "flow", "lite", 8)
    # Image de départ avec un réglage sur Dreamina (pas d'image) : refus clair, comme avant
    img = lieux / "attachments" / "depart.png"
    img.write_bytes(PNG)
    assert "pas prise en charge par Dreamina" in v3.submit("anime", image=str(img))["error"]


def test_dreamina_limite_au_solde_du_site(lieux, monkeypatch):
    # Décision du 29/10 : sur Dreamina, la limite est le SOLDE affiché (lu par le
    # worker) ; un modèle plus cher que les 120 crédits du jour reste proposé et part.
    valeurs = dict(videos_ia.choix_proposes())
    assert "dreamina/happyhorse-11/5" in valeurs and "dreamina/seedance-25/15" in valeurs
    assert valeurs["dreamina/seedance-2-mini/10"].endswith("10 s · 110 crédits")
    assert "flow/quality/8" not in valeurs                          # payant : jamais
    assert videos_ia.payant("dreamina", "seedance-25") is False and videos_ia.payant("flow", "quality") is True
    v, vus, w = _videos(lieux)
    r = v.submit("un cheval", "dreamina", "happyhorse-11", 5)
    assert r["ok"] and r["credits_estimes"] == 135
    assert _attendre(vus, r["job"])["etat"] == "fini"
    assert w.entrees[0]["plafond"] == videos_ia.PLAFOND_SOLDE      # le compteur du worker ne bloque pas
    assert v.etat.compte("amw", MATIN, "dreamina")["n"] == 135
    assert v.etat.disponible("amw", "dreamina", MATIN, unites=480) == (True, "", False)
    assert v.etat.disponible("amw", "flow", MATIN, unites=60)[1] == "plafond"  # Flow : plafond inchangé


def test_auto_sans_reglage_flow_puis_dreamina_a_sa_propre_duree(lieux, monkeypatch):
    # Réglage illisible : vrai auto, ordre du catalogue ; Flow épuisé et durée NON demandée : Dreamina
    # à SA durée (5 s, 55 crédits)
    monkeypatch.setattr(videos_ia, "reglage_choix", lambda: None)
    v, vus, w = _videos(lieux, scenarios={"amw": ["quota"]})
    r = v.submit("un chat")
    assert r["ok"] and r["duree"] == 8 and r["credits_estimes"] == 10  # estimation : premier site
    fin = _attendre(vus, r["job"])
    assert fin["etat"] == "fini" and fin["service"] == "dreamina" and fin["duree"] == 5 and fin["cout"] == 55
    assert [(e["service"], e["duree"], e["modele"]) for e in w.entrees] == [("flow", 8, "lite"), ("dreamina", 5, "seedance-2-mini")]
    assert v.etat.compte("amw", MATIN, "dreamina")["n"] == 55


def test_auto_sans_reglage_duree_demandee_sur_un_site_qui_la_propose(lieux, monkeypatch):
    monkeypatch.setattr(videos_ia, "reglage_choix", lambda: None)
    # 5 s demandées : Flow (8 s) sauté, Dreamina directement
    v, vus, w = _videos(lieux)
    r = v.submit("un chat", duree=5)
    assert r["ok"] and r["credits_estimes"] == 55
    assert _attendre(vus, r["job"])["service"] == "dreamina" and w.entrees[0]["duree"] == 5
    # 8 s demandées et Flow épuisé : jamais Dreamina à une autre durée
    v2, vus2, w2 = _videos(lieux, scenarios={"amw": ["quota"]})
    fin = _attendre(vus2, v2.submit("un chat", duree=8)["job"])
    assert fin["etat"] == "echec" and fin["code"] == "quota" and [e["service"] for e in w2.entrees] == ["flow"]
    assert "durée non proposée" in v.submit("un chat", duree=7)["error"]  # aucun site : refus clair


def test_auto_image_de_depart_jamais_sur_dreamina(lieux, monkeypatch):
    monkeypatch.setattr(videos_ia, "reglage_choix", lambda: None)
    img = lieux / "attachments" / "depart.png"
    img.write_bytes(PNG)
    v, vus, w = _videos(lieux, scenarios={"amw": ["quota"]})
    fin = _attendre(vus, v.submit("anime cette image", image=str(img))["job"])
    assert fin["etat"] == "echec" and fin["code"] == "quota" and [e["service"] for e in w.entrees] == ["flow"]


def test_dreamina_solde_trop_bas_ne_bloque_que_les_couts_aussi_chers(lieux):
    # 29/10 : sur un site limité au solde, « crédits insuffisants » pour HappyHorse 10 s (270) n'épuise
    # pas le compte : Mini 5 s (55) part encore ; 270 ou plus est refusé jusqu'à minuit.
    v, vus, w = _videos(lieux, scenarios={"amw": ["credits"]})
    fin = _attendre(vus, v.submit("un cheval", "dreamina", "happyhorse-11", 10)["job"])
    assert fin["etat"] == "echec" and fin["code"] == "quota"
    assert "SOLDE INSUFFISANT amw dreamina pour 270 crédits" in (lieux / "videos.log").read_text(encoding="utf-8")
    assert v.etat.compte("amw", MATIN, "dreamina").get("epuise_le") is None
    assert v.etat.disponible("amw", "dreamina", MATIN, unites=55) == (True, "", False)
    assert v.etat.disponible("amw", "dreamina", MATIN, unites=405)[1] == "quota"
    assert _attendre(vus, v.submit("un chat", "dreamina")["job"])["etat"] == "fini"
    assert v.etat.disponible("amw", "dreamina", MATIN + 86400, unites=405) == (True, "", False)  # lendemain


def test_annulation_pendant_que_une_image_tient_chrome(lieux):
    # 29/10 : la note « Non fait » du 28/10 (vidéo bloquée « en cours » derrière une image) est
    # périmée : `chrome_libre` regarde l'annulation chaque seconde pendant l'attente du verrou.
    v, vus, w = _videos(lieux)
    assert ia_web.CHROME.acquire(timeout=1)  # une image tient le seul Chrome
    try:
        r = v.submit("un chat")
        _attendre(vus, r["job"], etats=("en_cours",))
        debut = time.time()
        assert v.cancel(r["job"])["ok"]
        fin = _attendre(vus, r["job"])
        assert fin["etat"] == "echec" and fin["code"] == "annule" and time.time() - debut < 3
        assert not w.entrees  # aucun worker lancé
    finally:
        ia_web.CHROME.release()


def test_image_de_debut_et_de_fin_mode_frames(lieux):
    # 30/10 (pub Boutique) : la vidéo va d'une image de début à une image de fin (Flow, mode Frames)
    debut, fin = lieux / "attachments" / "debut.png", lieux / "attachments" / "fin.png"
    debut.write_bytes(PNG)
    fin.write_bytes(PNG)
    v, vus, w = _videos(lieux)
    r = v.submit("il sourit à son téléphone", "flow", "lite", 8, str(debut), image_fin=str(fin), format="9:16")
    assert r["ok"] and _attendre(vus, r["job"])["etat"] == "fini"
    assert w.entrees[0]["fichiers"] == [str(debut.resolve()), str(fin.resolve())]
    assert w.entrees[0]["format"] == "9:16"
    assert "format inconnu" in v.submit("x", "flow", format="4:5")["error"]
    assert "image de départ" in v.submit("x", "flow", image_fin=str(fin))["error"]
    assert "pas prise en charge par Dreamina" in v.submit("x", "dreamina", image=str(debut), image_fin=str(fin))["error"]


def test_session_perdue_sur_dreamina_ne_marque_pas_le_compte_google(lieux):
    # 30/10 : Dreamina a sa propre connexion (CapCut) : une session perdue y est notée pour Dreamina seul
    v, vus, w = _videos(lieux, scenarios={"amw": ["session"]})
    fin = _attendre(vus, v.submit("un chat", "dreamina")["job"])
    assert fin["etat"] == "echec" and fin["code"] == "session"
    assert v.etat.compte("amw", MATIN, "flow").get("a_reconnecter") is not True  # Google intact
    etat = {(c["compte"], c["service"]): c["a_reconnecter"] for c in v.status()["comptes"]}
    assert etat[("amw", "dreamina")] is True and etat[("amw", "flow")] is False
    assert _attendre(vus, v.submit("un chat", "flow")["job"])["etat"] == "fini"
