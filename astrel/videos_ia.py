"""Génération de vidéos IA par le navigateur (plan P41 lot B2, 28/10/2026).

Demande de l'utilisateur (27-28/10) : vidéo IA par pilote navigateur comme les images, premier site
Google Flow (Veo), puis Dreamina (lot B5, 29/10 : Seedance 2.0 Mini 5 s, 55 crédits sur 120 par
jour), quota gratuit quotidien, multi-comptes Google ; réutiliser le socle des
images (`ia_web.py` : comptes, profils, quotas par (compte, service), choix auto/manuel, verrou
de Chrome). Ce module tient la FILE des vidéos (une à la fois), le DÉCOMPTE EN CRÉDITS et publie
l'évènement `video_job` ; il ne parle pas et n'écrit pas dans le fil (cœur, lot B3) :

    {job, etat: file|en_cours|fini|echec, service (= moteur), service_libelle, compte (alias,
     jamais l'adresse), modele, modele_libelle, cout (crédits), duree (s), prompt, image (nom de
     l'image de départ), nom (fichier dans Videos-IA), etape, erreur, code, texte_ia, channel,
     proposition, cree, debut (départ du worker), reporte_jusqua, credits_restants, position}

    etape : file | pointe | rythme (reportée jusqu'à `reporte_jusqua`) | ouverture | saisie |
            generation | telechargement | bascule

Crédits (valeurs PAR DÉFAUT PRUDENTES de `config/ia-web.json`, à confirmer par le relevé B0 de
l'utilisateur) : Lite 10, Fast 20 sur 50 crédits par compte et par fenêtre de 24 h, ouverte par la
PREMIÈRE génération (fenêtre « 24h » du socle). Un modèle payant (`gratuit: false`, Quality) ou
plus cher que le plafond est refusé. Créneau de pointe du site (Flow : 14 h-17 h UTC) : le job
reste en file, reporté à la fin du créneau, JAMAIS retenté en boucle (`REPORTS_MAX` reports au
plus, attente jusqu'à une heure précise, aucun essai entre-temps).

Auto avec deux sites (B5) : ordre du catalogue (Flow, puis Dreamina). Une durée DEMANDÉE n'est
essayée que sur un site qui la propose ; sans durée demandée, chaque site prend la sienne (Flow
8 s, Dreamina 5 s). Une image de départ n'est essayée que sur un site qui en prend (`pieces` du
catalogue : Flow ; pas Dreamina).

Bascule automatique (réponse du 29/10) : une demande en auto prend le modèle et la
durée du réglage `video_choix` (`Job.prefere`) ; si ce site ne peut pas servir (crédits épuisés,
déconnecté, au repos), la vidéo part SEULE sur un autre site prêt, à son modèle et sa durée par
défaut (jamais un autre modèle du même site). Un moteur NOMMÉ (manuel) ne bascule jamais : le
refus porte `proposition` (bouton « Essayer avec X »).

Limite au solde (Dreamina, `limite: solde` du catalogue, décision du 29/10) : tous les
modèles relevés sont proposés, même plus chers que les 120 crédits gratuits du jour (les crédits
bonus du site sont gratuits aussi) ; le worker lit le solde affiché et n'envoie rien s'il ne
couvre pas le coût débité (échec `credits` : compte épuisé, site suivant).

Contrat du worker (`imagegen/video.mjs`, lot B1 ; en-tête de `imagegen/noyau.mjs`) : entrée
{service, moteur, modele, type: "video", affichage: "masquee", fenetre (du QUOTA : « 24h »),
plafond, profil, sortie, prompt, fichiers: [image de départ] ou [], duree, debug, delai_s} ;
`affichage` porte le caché/visible, `fenetre` reste la fenêtre du quota (le texte de P41 dit
« fenetre » pour caché/visible : erreur du plan, le contrat du noyau fait foi). Sortie : lignes
{t: etape|envoye|fini|echec}. Codes d'échec propres à la vidéo attendus du lot B1, en plus de
ceux du noyau : `pointe` (créneau de pointe, `reprise` « HH:MM » si le site la donne),
`activite` (« unusual activity », rien débité : compte au repos 6 h), `credits` (crédits
insuffisants : compte épuisé, compte suivant). `rythme` (30 s entre deux générations d'un
compte sur un même site, 30/10 ; attendues sur place par le worker) est reporté comme `pointe` ; `nuit` est un échec.

Le fichier rendu doit être un mp4 ou un webm reconnu par ses OCTETS (`medias.sniff`), avec
l'extension qui va avec, dans `SORTIE` (~/Projets/Videos-IA : montrable par
`medias.py` tel quel). Journal `logs/videos.log` : demandes, crédits partis, ouverture de la
fenêtre de 24 h et renouvellement prévu, reports, épuisements, premier succès qui suit (mesure
du renouvellement réel), crédits annoncés par le site.
"""
from __future__ import annotations

import calendar
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import attachments, config, ia_web, medias
from .ia_web import Etat

log = logging.getLogger("astrel.videos")

TYPE = "video"
SERVICES_PRETS = ("flow", "dreamina")  # sites pilotés (B1 Flow, B5 Dreamina), dans l'ordre du catalogue
AFFICHAGE = "masquee"           # Q1 du plan P41, réponse de l'utilisateur (29/10) : Chrome MASQUÉ pendant une vidéo
SORTIE = Path(os.environ.get("ASTREL_VIDEOS_SORTIE", str(config.WORKDIR / "Videos-IA")))
LOG = config.LOGS / "videos.log"
PROMPT_MAX = 2000
FILE_MAX = 3                    # vidéos en attente au plus (reportées comprises)
JOBS_GARDES = 50
DELAI_S = max(60.0, min(1800.0, config._env_float("ASTREL_VIDEOS_DELAI_S", 360.0)))  # 30/10 : 6 min (1 à 2 min d'habitude ;
# l'attente de 15 min venait d'une vidéo prête mais pas reconnue, corrigé dans imagegen/sites/flow.mjs)
WORKER_MARGE_S = 240            # au-delà du délai de génération, le worker est tué
PAUSE_S = 6 * 3600              # compte au repos après un défi ou une « activité inhabituelle »
REPORTS_MAX = 3                 # reports d'un même job (pointe, rythme) ; au-delà : échec, pas de boucle
REPORT_MIN_S = 60               # un report attend au moins une minute
POINTE_REPRISE_S = 3600         # pointe annoncée par le site hors du créneau connu, sans heure : +1 h
RYTHME_REPRISE_S = 60           # écart de 30 s par compte ET site (humain.mjs, 30/10) : report d'une minute
REPRISE_MAX_S = 6 * 3600        # reprise lue sur le site (« +99h ») : 6 h au plus
ATTENTE_MAX_S = 30.0            # réveil de la file au plus toutes les 30 s (arrêt d'urgence)
OCTETS_MAX = 1024 * 1024 * 1024
PLAFOND_SOLDE = 1000            # site limité par son solde (Dreamina) : plafond du worker sans effet (sa borne)
IMAGE_MAX = 5 * 1024 * 1024     # image de départ (filtre de P40 A3 : 5 Mo)
_VIDEO_EXT = {"video/mp4": ".mp4", "video/webm": ".webm"}
FORMATS = ("16:9", "9:16")      # 30/10 : formats communs aux sites (le worker refuse ceux qu'un site n'a pas)
_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")

# Texte d'écran par code d'échec (FR ; EN dans astrel/locales/en.json). Le texte du site est
# montré à part, tel quel (`texte_ia`).
MESSAGES = {
    "quota": "Crédit gratuit de vidéo épuisé sur ce compte.",
    "credits": "Crédits insuffisants sur ce compte pour ce modèle.",
    "captcha": "Le site demande de prouver qu'on n'est pas un robot : arrêt, rien n'est contourné.",
    "activite": "Le site signale une activité inhabituelle : arrêt, compte au repos 6 h.",
    "refus": "L'IA a refusé de créer cette vidéo.",
    "session": "Compte déconnecté : à reconnecter une fois, à la main.",
    "page": "La page du site a changé : la génération n'a pas abouti.",
    "format": "Le fichier téléchargé n'est pas une vidéo mp4 ou webm : refusé.",
    "delai": "Pas de vidéo au bout du délai : abandon, sans nouvel essai.",
    "plafond": "Crédits de vidéo insuffisants sur ce compte pour ce modèle.",
    "payant": "Modèle payant ou plus cher que le crédit gratuit : refusé, crédit gratuit seulement.",
    "occupe": "Une génération est déjà en cours sur ce compte.",
    "fichiers": "Image de départ refusée par le générateur de vidéos.",
    "entree": "Demande illisible par le générateur de vidéos.",
    "interne": "Le générateur de vidéos s'est arrêté sur une erreur.",
    "absent": "Générateur de vidéos pas installé (imagegen/video.mjs, puis cd imagegen && npm ci).",
    "estop": "Arrêt d'urgence : vidéo annulée.",
    "pointe": "Créneau de pointe du site toujours en cours après plusieurs reports : abandon, sans nouvel essai.",
    "rythme": "Le compte doit encore se reposer après la génération précédente : abandon après plusieurs reports.",
    "nuit": "Pas de génération la nuit : à redemander dans la journée.",
    "annule": "Vidéo annulée.",
}
TOUS_EPUISES = "Crédit gratuit de vidéo épuisé sur tous les comptes."
TOUS_PLAFOND = "Crédits de vidéo insuffisants sur tous les comptes : nouvel essai après le renouvellement (24 h après la première vidéo)."
EN_PAUSE = "Compte au repos après un défi anti-robot ou une activité inhabituelle : nouvel essai plus tard."
NON_CONNECTE = "Aucun compte connecté pour la vidéo : connexion à faire une fois, à la main."
COMPTES_ILLISIBLES = "Liste des comptes d'images illisible (runtime/imagegen/comptes.json) : à corriger."
LANCEE = ("Vidéo lancée en arrière-plan : elle s'affichera dans le HUD dès qu'elle sera prête (deux à dix minutes, "
          "plus tard si le site est au créneau de pointe). N'attends pas, ne relance pas et ne la décris pas : "
          "l'utilisateur la verra.")


# --------------------------------------------------------------------------- règles

def plafond(service: str) -> int:
    """Crédits permis par compte sur la fenêtre du service (catalogue ; Flow : 50 sur 24 h)."""
    return ia_web.plafond(service)


def fenetre(service: str) -> str:
    return ia_web.fenetre(service)


def payant(service: str, modele: str = "") -> bool:
    """Modèle hors du crédit gratuit : marqué `gratuit: false` au catalogue (Flow Quality) ou plus
    cher que le plafond de la fenêtre (il ne pourrait jamais partir), sauf sur un site limité par
    son solde (Dreamina : le worker lit le solde)."""
    m = ia_web.modele_info(service, modele)
    return bool(m) and (m.get("gratuit") is False
                        or (not ia_web.limite_solde(service) and m["cout"] > plafond(service)))


def tient(service: str, credits: int) -> bool:
    """Ce coût peut partir sur ce service : sous le plafond, ou site limité par son solde."""
    return ia_web.limite_solde(service) or credits <= plafond(service)


def memoire_bloquee() -> bool:
    from .images_ia import memoire_bloquee
    return memoire_bloquee()


def choix_proposes() -> list[tuple[str, str]]:
    """(« service/modèle/secondes », libellé) des vidéos permises en crédit gratuit, coût compris :
    choix de Réglages › Images IA (`video_choix`). Une durée n'existe que si le modèle la propose
    (P41 B3 : Veo 8 s seulement, Omni 4/8/10 s)."""
    return [(f"{s}/{m['id']}/{d}", f"{ia_web.libelle(s)} · {m['libelle']} · {d} s · {c} crédits")
            for s in ia_web.services(TYPE) if s in SERVICES_PRETS
            for m in ia_web.modeles(s) if not payant(s, m["id"])
            for d, c in ia_web.durees_video(s, m["id"]).items() if tient(s, c)]


def _detail_choix(valeur: str, libelle: str) -> dict:
    """Un choix pour le HUD (29/10 : menus « Modèle » puis « Durée », comme les images) : la valeur
    du réglage, son libellé complet, et ses parties pour regrouper par site et par modèle."""
    service, modele, secondes = valeur.split("/")
    info = ia_web.modele_info(service, modele) or {}
    credits = ia_web.cout(service, modele, int(secondes))
    return {"valeur": valeur, "libelle": libelle, "service": service, "service_libelle": ia_web.libelle(service),
            "modele": modele, "modele_libelle": info.get("libelle", modele), "duree": int(secondes),
            "cout": credits, "duree_libelle": f"{secondes} s · {credits} crédits",
            "pieces": ia_web.accepte_pieces(service)}  # 30/10 : images de début et de fin (HUD)


def reglage_choix() -> tuple[str, str, int] | None:
    """(service, modèle, secondes) du réglage `video_choix`, relu à chaque demande ; None s'il est
    illisible ou plus proposé (catalogue changé) : défaut du service."""
    from . import settings
    try:
        valeur = str(settings.reglages.value("video_choix") or "")
    except Exception as exc:  # noqa: BLE001 — un réglage illisible ne bloque pas les vidéos
        log.warning("réglage des vidéos illisible (%s) : défaut du service", exc)
        return None
    return lire_choix(valeur)


def lire_choix(valeur: str) -> tuple[str, str, int] | None:
    """« service/modèle/secondes » proposé → (service, modèle, secondes) ; sinon None."""
    if valeur not in dict(choix_proposes()):
        return None
    service, modele, secondes = valeur.split("/")
    return service, modele, int(secondes)


def fin_pointe(service: str, now: float) -> float | None:
    """Fin du créneau de pointe en cours (horodatage), None hors créneau ou sans créneau connu.
    Créneau en heures UTC [début, fin[ (`reglages_video(service)["pointe_utc"]`)."""
    p = ia_web.reglages_video(service).get("pointe_utc")
    if not p:
        return None
    g = time.gmtime(now)
    if not p[0] <= g.tm_hour < p[1]:
        return None
    return float(calendar.timegm((g.tm_year, g.tm_mon, g.tm_mday, p[1], 0, 0, 0, 0, 0)))


def verifier_video(brut: Any) -> tuple[Path | None, str]:
    """(fichier, "") si `brut` est un mp4 ou un webm reconnu par ses octets, avec l'extension qui
    va avec, fichier ordinaire du dossier de sortie ; sinon (None, raison)."""
    sortie = SORTIE.resolve()
    try:
        reel = Path(str(brut or "")).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):  # ValueError : caractère NUL dans le chemin
        return None, "fichier introuvable"
    if reel == sortie or not reel.is_relative_to(sortie) or not reel.is_file():
        return None, f"hors du dossier de sortie {sortie}"
    taille = reel.stat().st_size
    if not 0 < taille <= OCTETS_MAX:
        return None, f"taille {taille} octets"
    with open(reel, "rb") as f:
        genre = medias.sniff(f.read(64))
    mime = genre[1] if genre else ""
    if mime not in _VIDEO_EXT:
        return None, f"type lu dans les octets : {mime or 'inconnu'}"
    if reel.suffix.lower() != _VIDEO_EXT[mime]:
        return None, f"extension {reel.suffix!r} pour un {mime}"
    return reel, ""


def image_depart(brut: Any) -> tuple[str, str]:
    """(chemin résolu, "") d'une image de départ permise (filtre de P40 A3 : pièces jointes ou
    ~/Projets, hors secrets et dossiers privés, 5 Mo, PNG/JPEG/WebP lus dans les octets,
    pas de lien dur) ; sinon ("", raison)."""
    texte = str(brut or "").strip()
    if not texte or not Path(texte).is_absolute():
        return "", "chemin absolu attendu"
    try:
        reel = Path(texte).resolve(strict=True)
        st = reel.stat()
    except (OSError, RuntimeError, ValueError):  # ValueError : caractère NUL dans le chemin
        return "", "fichier introuvable"
    racines = [attachments.ATTACH_DIR.resolve(), config.WORKDIR.resolve()]
    if not any(reel != r and reel.is_relative_to(r) for r in racines):
        return "", "hors des pièces jointes et du dossier des projets"
    raison = medias.refusal(reel)
    if raison:
        return "", raison
    if not reel.is_file() or st.st_nlink > 1:
        return "", "pas un fichier ordinaire"
    if st.st_size > IMAGE_MAX:
        return "", "image trop lourde (5 Mo au plus)"
    with open(reel, "rb") as f:
        genre = medias.sniff(f.read(16))
    if not genre or genre[1] not in _IMAGE_TYPES:
        return "", "pas une image PNG, JPEG ou WebP"
    return str(reel), ""


# --------------------------------------------------------------------------- jobs

@dataclass
class Job:
    id: str
    prompt: str
    demande: str                    # auto ou un service vidéo (manuel)
    channel: str = ""
    image: str = ""                 # image de départ (image→vidéo), chemin résolu
    image_fin: str = ""             # 30/10 : image de FIN (Flow, mode Frames), chemin résolu
    format: str = ""                # 30/10 : « 16:9 » ou « 9:16 » (vide : celui du site)
    duree: int = 0
    duree_libre: bool = False       # durée pas demandée (auto) : chaque service prend la sienne (B5)
    prefere: tuple = ()             # auto : (service, modèle, secondes) du réglage, bascule ailleurs (29/10)
    etat: str = "file"
    service: str = ""
    compte: str = ""
    modele: str = ""                # demandé (manuel) ou choisi (défaut du service)
    modele_utilise: str = ""        # annoncé par le worker
    etape: str = "file"
    erreur: str = ""
    code: str = ""
    texte_ia: str = ""
    nom: str = ""
    chemin: str = ""                # vidéo rendue (pour medias.py, lot B3) ; jamais dans l'évènement
    essais: list[str] = field(default_factory=list)
    sonde: bool = False
    cree: float = field(default_factory=time.time)
    debut: float = 0.0
    pas_avant: float = 0.0          # report : pas d'essai avant cette heure
    reporte_jusqua: float = 0.0
    reports: int = 0
    annule: bool = False
    motif_annule: str = "annule"    # « estop » : arrêt d'urgence (`Videos.estop`)
    proposition: str = ""
    credits_restants: Any = None

    def event(self) -> dict:
        service = self.service or self.demande
        modele = self.modele_utilise or self.modele
        # Auto pas encore servi : modèle vide, coût 0 (rien n'est annoncé avant le choix).
        info = ia_web.modele_info(service, modele) if modele and service in ia_web.catalogue() else None
        return {"job": self.id, "etat": self.etat, "service": service, "moteur": service,
                "service_libelle": ia_web.libelle(service), "compte": self.compte, "modele": modele,
                "modele_libelle": info["libelle"] if info else "",
                "cout": ia_web.cout(service, modele, self.duree) if info else 0,
                "duree": self.duree, "prompt": self.prompt, "image": Path(self.image).name if self.image else "",
                "nom": self.nom, "etape": self.etape, "erreur": self.erreur, "code": self.code,
                "texte_ia": self.texte_ia, "channel": self.channel, "proposition": self.proposition,
                "cree": self.cree, "debut": self.debut, "reporte_jusqua": self.reporte_jusqua,
                "credits_restants": self.credits_restants}


Runner = Callable[[dict, Callable[[dict], None]], dict]


def _heure(ts: float) -> str:
    return f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))} ({time.strftime('%H:%M', time.gmtime(ts))} UTC)"


class Videos:
    """File des vidéos : un job à la fois dans un fil à part ; `submit` rend la main aussitôt.
    `horloge` : heure courante (tests : créneau de pointe simulé)."""

    def __init__(self, publish: Callable[..., Any] | None = None, etat: Etat | None = None,
                 runner: Runner | None = None, horloge: Callable[[], float] | None = None,
                 publish_etat: Callable[..., Any] | None = None) -> None:
        self._publish = publish
        self._publish_etat = publish_etat
        self.etat = etat or Etat()
        self.runner: Runner = runner or self._lancer_worker
        self.horloge = horloge or time.time
        self._cond = threading.Condition(threading.RLock())
        self._jobs: dict[str, Job] = {}
        self._fil: threading.Thread | None = None
        self.en_cours: Job | None = None
        # Appelé après une vidéo finie (lot B3 : publication dans le fil par medias.py).
        self.on_fini: Callable[[Job], Any] | None = None

    # --- publication ---

    def publish(self, job: Job) -> None:
        pub = self._publish
        if pub is None:
            from .events import bus
            pub = bus.publish
        try:
            pub("video_job", **job.event(), position=self._position(job))
        except Exception:
            log.exception("publication video_job")
        self.publier_etat()  # crédits, file : le panneau et les Réglages suivent chaque étape

    def publier_etat(self) -> None:
        """Évènement sticky `video_etat` (= `status()` sans `ok`) : onglet Vidéo du panneau Images et
        Réglages › Images IA du HUD (lot B4)."""
        pub = self._publish_etat
        if pub is None:
            from .events import bus
            pub = bus.publish
        try:
            etat = self.status()
            etat.pop("ok", None)
            pub("video_etat", **etat)
        except Exception:
            log.exception("publication video_etat")

    def _en_file(self) -> list[Job]:
        with self._cond:
            return sorted((j for j in self._jobs.values() if j.etat == "file" and j is not self.en_cours),
                          key=lambda j: j.cree)

    def _position(self, job: Job) -> int:
        if job.etat != "file":
            return 0
        with self._cond:  # un job qu'on vient de reporter est encore `en_cours` : compté en file
            attente = sorted((j for j in self._jobs.values() if j.etat == "file"), key=lambda j: j.cree)
            actif = self.en_cours is not None and self.en_cours.etat != "file"
        return 1 + attente.index(job) + (1 if actif else 0) if job in attente else 0

    # --- comptes ---

    @staticmethod
    def comptes() -> list[tuple[str, str]]:
        """(alias, service) des comptes vidéo, dans l'ordre du repli (un compte Google sert
        Gemini ET Flow : même profil)."""
        return ia_web.paires(TYPE, SERVICES_PRETS)

    @staticmethod
    def profil(alias: str) -> Path:
        return config.IMAGES_PROFILS / alias

    def _choisir(self, job: Job, now: float) -> ia_web.Choix | ia_web.Refus:
        commun = dict(essais=job.essais, etat=self.etat, profil_existe=lambda alias: self.profil(alias).is_dir(),
                      type_=TYPE, now=now, pieces=bool(job.image))
        if job.prefere:  # auto : le réglage d'abord, puis BASCULE seule vers un autre site (29/10)
            service, modele, secondes = job.prefere
            choix = ia_web.choisir(mode="manuel", service=service, modele=modele, prets=SERVICES_PRETS,
                                   proposer=False, duree=secondes, **commun)
            if isinstance(choix, ia_web.Choix):
                return choix
            autres = tuple(s for s in SERVICES_PRETS if s != service)
            repli = ia_web.choisir(mode="auto", prets=autres, duree_libre=True, **commun) if autres else choix
            if isinstance(repli, ia_web.Choix):
                _journal(f"{job.id} BASCULE {service} → {repli.service} ({choix.code} sur {service})")
                return repli
            return choix  # le refus du site choisi dit le mieux pourquoi
        manuel = job.demande != "auto"
        return ia_web.choisir(mode="manuel" if manuel else "auto", service=job.demande if manuel else "",
                              modele=job.modele if manuel else "", prets=SERVICES_PRETS,
                              duree=job.duree, duree_libre=job.duree_libre, **commun)

    # --- demandes ---

    def submit(self, prompt: Any, engine: Any = "auto", model: Any = "", duree: Any = 0, image: Any = "",
               channel: str = "", image_fin: Any = "", format: Any = "") -> dict:  # noqa: A002
        """Commande `video_generate` / outil `generate_video` (lot B3) : met la vidéo en file et
        rend la main aussitôt ; le résultat arrive au HUD par `video_job`."""
        prompt = re.sub(r"\s+", " ", str(prompt or "")).strip()
        demande = str(engine or "auto").strip().lower() or "auto"
        modele = str(model or "").strip().lower()
        if config.ESTOP_FILE.exists():
            return {"ok": False, "error": "arrêt d'urgence en cours : pas de vidéo"}
        if not prompt:
            return {"ok": False, "error": "prompt vide : décris la vidéo voulue"}
        if len(prompt) > PROMPT_MAX:
            return {"ok": False, "error": f"prompt trop long ({PROMPT_MAX} caractères au plus)"}
        if memoire_bloquee():
            return {"ok": False, "error": "mémoire du PC presque pleine : pas de vidéo (aucun Chrome) tant qu'elle "
                                          "ne redescend pas"}
        prets = [s for s in ia_web.services(TYPE) if s in SERVICES_PRETS]
        if demande != "auto" and demande not in prets:
            noms = ", ".join(prets) or "aucun"
            return {"ok": False, "error": f"service vidéo inconnu ou pas prêt : {demande} (auto, {noms})"}
        if not prets:
            return {"ok": False, "error": "aucun service vidéo prêt au catalogue"}
        service = demande if demande != "auto" else prets[0]
        if modele and demande == "auto":
            return {"ok": False, "error": "un modèle se choisit avec un moteur nommé (mode manuel), pas en auto"}
        libre = demande == "auto" and not duree  # B5 : sans durée demandée, chaque service prend la sienne
        reglage = reglage_choix() if demande == "auto" else None
        if reglage:  # P41 B3 : modèle et durée choisis dans Réglages › Images IA ; durée demandée prioritaire
            service = reglage[0]  # `demande` reste « auto » : bascule permise (Job.prefere, 29/10)
            modele, duree, libre = reglage[1], duree or reglage[2], False
        if modele and not ia_web.modele_valide(service, modele):
            noms = ", ".join(m["id"] for m in ia_web.modeles(service))
            return {"ok": False, "error": f"modèle inconnu pour ce moteur : {modele} (connus : {noms})"}
        if payant(service, modele):
            info = ia_web.modele_info(service, modele) or {}
            libelle, cout, maxi = info.get("libelle", modele), info.get("cout", 0), plafond(service)
            return {"ok": False, "error": f"modèle {libelle} refusé : payant ou plus cher que le crédit gratuit "
                                          f"({cout} crédits, {maxi} par fenêtre) ; crédit gratuit seulement"}
        # P41 B3 (28/10) : durées PROPOSÉES par le modèle (défaut du service en auto), chacune à
        # son coût en crédits (relevé B0 : Veo 8 s seulement, Omni 4/8/10 s).
        if demande == "auto" and not libre and not reglage:  # B5 : durée demandée, 1er site qui la propose
            try:
                voulu = int(duree)
            except (TypeError, ValueError):
                voulu = -1
            service = next((s for s in prets if voulu in ia_web.durees_video(s)), service)
        durees = ia_web.durees_video(service, modele)
        try:
            secondes = int(duree or 0)
        except (TypeError, ValueError):
            secondes = -1
        if secondes == 0:
            secondes = ia_web.duree_defaut(service, modele)
        if secondes not in durees:
            info = ia_web.modele_info(service, modele) or {}
            choix = ", ".join(f"{d} s ({c} crédits)" for d, c in durees.items())
            return {"ok": False, "error": f"durée non proposée par {info.get('libelle', modele or service)} : "
                                          f"{duree} s (au choix : {choix})"}
        if not tient(service, durees[secondes]):
            return {"ok": False, "error": f"{secondes} s coûtent {durees[secondes]} crédits, plus que le crédit "
                                          f"gratuit ({plafond(service)} par fenêtre) : refusé"}
        if isinstance(image, (list, tuple)):
            if len(image) > 1:
                return {"ok": False, "error": "une seule image de départ par vidéo"}
            image = image[0] if image else ""
        chemin = ""
        if str(image or "").strip():
            if (demande != "auto" or reglage) and not ia_web.accepte_pieces(service):
                return {"ok": False, "error": f"image de départ pas prise en charge par {ia_web.libelle(service)}"}
            chemin, raison = image_depart(image)
            if raison:
                return {"ok": False, "error": f"image de départ refusée : {raison}"}
        format_ = str(format or "").strip()
        if format_ and format_ not in FORMATS:
            return {"ok": False, "error": f"format inconnu : {format_} ({' ou '.join(FORMATS)})"}
        # 30/10 : image de FIN (Flow, mode Frames : la vidéo va de l'image de début à celle de fin)
        chemin_fin = ""
        if str(image_fin or "").strip():
            if not chemin:
                return {"ok": False, "error": "une image de fin demande aussi une image de départ"}
            chemin_fin, raison = image_depart(image_fin)
            if raison:
                return {"ok": False, "error": f"image de fin refusée : {raison}"}
        with self._cond:
            attente = sum(1 for j in self._jobs.values() if j.etat in ("file", "en_cours"))
            if attente >= FILE_MAX:
                return {"ok": False, "error": f"file de vidéos pleine ({FILE_MAX} en attente) : réessaie plus tard"}
            job = Job(id=uuid.uuid4().hex[:12], prompt=prompt, demande=demande, channel=str(channel or ""),
                      image=chemin, image_fin=chemin_fin, format=format_, duree=secondes, duree_libre=libre, modele=modele, cree=self.horloge(),
                      prefere=(service, modele, secondes) if reglage else (),
                      service=service if reglage else "")
            self._jobs[job.id] = job
            self._oublier()
            if self._fil is None or not self._fil.is_alive():
                self._fil = threading.Thread(target=self._boucle, name="videos-ia", daemon=True)
                self._fil.start()
            position = self._position(job)
            # Publié AVANT le réveil du fil : la carte « en file » arrive au HUD avant « en cours ».
            self.publish(job)
            self._cond.notify_all()
        _journal(f"{job.id} demandé ({demande}{' ' + modele if modele else ''}, {secondes} s"
                 f"{', image de départ' if chemin else ''}) : {prompt[:120]}")
        return {"ok": True, "job": job.id, "etat": "file", "service": demande, "position": position,
                "duree": secondes, "credits_estimes": durees[secondes], "message": LANCEE}

    def cancel(self, job_id: str, code: str = "annule") -> dict:
        """Bouton « Annuler » de la carte : en file, la vidéo est retirée ; en cours, la garde du worker
        (`ia_web.executer_worker`, chaque seconde) l'arrête AVEC son Chrome, et une vidéo qui attend le
        Chrome commun (`ia_web.chrome_libre`) n'en lance pas ; les crédits déjà partis restent comptés.
        `code` : « estop » pour l'arrêt d'urgence (`estop`)."""
        with self._cond:
            job = self._jobs.get(str(job_id or ""))
            if job is None:
                return {"ok": False, "error": "vidéo inconnue (Astrel a peut-être redémarré)"}
            if job.etat in ("fini", "echec"):
                return {"ok": False, "error": "cette vidéo est déjà terminée"}
            job.annule = True
            job.motif_annule = code
            en_cours = job is self.en_cours
            if not en_cours:
                job.etat = "echec"  # le fil ne peut plus la prendre
            self._cond.notify_all()
        if not en_cours:
            self._echec(job, code, MESSAGES[code])
        else:
            _journal(f"{job.id} arrêt demandé ({code}) : worker et son Chrome")
        return {"ok": True, "job": job.id}

    def estop(self) -> dict:
        """« ⛔ Stop tout » (`Astrel.emergency_stop`) : TOUTES les vidéos en file ou en cours sont
        annulées (code `estop`), la génération en cours arrêtée avec son Chrome. Rend la main
        aussitôt (l'arrêt du Chrome se fait dans un fil à part)."""
        with self._cond:
            ids = [j.id for j in self._jobs.values() if j.etat in ("file", "en_cours")]
        n = sum(1 for i in ids if self.cancel(i, "estop").get("ok"))
        if n:
            log.warning("arrêt d'urgence : %d vidéo(s) annulée(s)", n)
        return {"ok": True, "annulees": n}

    def job(self, job_id: str) -> Job | None:
        with self._cond:
            return self._jobs.get(str(job_id or ""))

    def status(self) -> dict:
        """Crédits par compte (Réglages › Images IA, lot B4), état de la file, choix proposés (modèle et
        durée, coût compris) et celui du réglage `video_choix` (`reglage`, le premier par défaut)."""
        now = self.horloge()
        comptes = []
        for alias, service in self.comptes():
            c = self.etat.compte(alias, now, service)
            ok, raison, _ = self.etat.disponible(alias, service, now, unites=ia_web.cout(service))
            maxi, debut = plafond(service), c.get("debut")
            comptes.append({"compte": alias, "service": service, "service_libelle": ia_web.libelle(service),
                            "connecte": self.profil(alias).is_dir(), "a_reconnecter": bool(c.get("a_reconnecter") or c.get("site_a_reconnecter")),
                            "credits_utilises": c["n"], "credits_restants": max(0, maxi - c["n"]),
                            "plafond": maxi, "fenetre": fenetre(service),
                            "renouvellement": (float(debut) + 86400) if debut and fenetre(service) == "24h" else None,
                            "disponible": ok, "raison": raison, "epuise_le": c.get("epuise_le"),
                            "reprise_apres": c.get("reprise_apres"), "pause_jusqua": c.get("pause_jusqua")})
        pointes = [t for s in SERVICES_PRETS if (t := fin_pointe(s, now))]
        with self._cond:
            attente = len(self._en_file())
            en_cours = self.en_cours.id if self.en_cours else ""
        choix = choix_proposes()
        reglage = reglage_choix()
        return {"ok": True, "comptes": comptes, "en_attente": attente, "en_cours": en_cours,
                "pointe_jusqua": min(pointes) if pointes else None,
                "choix": [_detail_choix(v, libelle) for v, libelle in choix],
                "reglage": "/".join(map(str, reglage)) if reglage else (choix[0][0] if choix else "")}

    def _oublier(self) -> None:
        finis = [j for j in self._jobs.values() if j.etat in ("fini", "echec")]
        for j in sorted(finis, key=lambda j: j.cree)[:max(0, len(self._jobs) - JOBS_GARDES)]:
            self._jobs.pop(j.id, None)

    def reveiller(self) -> None:
        with self._cond:
            self._cond.notify_all()

    # --- file ---

    def _prochain(self) -> Job:
        """Prochain job dû (en file, `pas_avant` passé), dans l'ordre d'arrivée. Attend sinon,
        réveillé par une demande ou au plus toutes les `ATTENTE_MAX_S` (arrêt d'urgence, report
        échu). Sous ESTOP, les vidéos en file (reportées comprises) sont annulées."""
        while True:
            with self._cond:
                en_file = self._en_file()
                estop = bool(en_file) and config.ESTOP_FILE.exists()
                if not estop:
                    now = self.horloge()
                    # Annulée pendant son report : prise tout de suite, pour finir en « annulée ».
                    pret = next((j for j in en_file if j.annule or j.pas_avant <= now), None)
                    if pret is not None:
                        self.en_cours = pret
                        return pret
                    if en_file:
                        self._cond.wait(timeout=min(ATTENTE_MAX_S, max(0.01, min(j.pas_avant for j in en_file) - now)))
                    else:
                        self._cond.wait()
                    continue
                for j in en_file:
                    j.etat = "echec"
            for j in en_file:
                self._echec(j, "estop", MESSAGES["estop"])

    def _boucle(self) -> None:
        while True:
            job = self._prochain()
            try:
                self._traiter(job)
            except Exception as exc:
                log.exception("vidéo %s", job.id)
                self._echec(job, "interne", MESSAGES["interne"], detail=str(exc))
            finally:
                with self._cond:
                    self.en_cours = None

    def _reporter(self, job: Job, motif: str, jusqua: float, now: float) -> None:
        """Remet le job en file jusqu'à `jusqua` : aucun essai entre-temps, `REPORTS_MAX` au plus."""
        job.reports += 1
        job.etat, job.etape, job.compte, job.code, job.erreur = "file", motif, "", "", ""
        job.pas_avant = job.reporte_jusqua = max(jusqua, now + REPORT_MIN_S)
        _journal(f"{job.id} REPORTÉ ({motif}, report {job.reports}/{REPORTS_MAX}) jusqu'à {_heure(job.pas_avant)}")
        self.publish(job)

    def _traiter(self, job: Job) -> None:
        # Bornée : un compte déjà essayé n'est jamais repris (`essais`) ; un report rend la main.
        while True:
            now = self.horloge()
            if config.ESTOP_FILE.exists():  # « ⛔ Stop tout » : rien de nouveau ne part
                self._echec(job, "estop", MESSAGES["estop"])
                return
            if job.annule:
                self._echec(job, job.motif_annule, MESSAGES[job.motif_annule])
                return
            choix = self._choisir(job, now)
            if isinstance(choix, ia_web.Refus):
                job.proposition = choix.proposition
                message = {"quota": TOUS_EPUISES, "plafond": TOUS_PLAFOND, "captcha": EN_PAUSE,
                           "comptes": COMPTES_ILLISIBLES}.get(choix.code, NON_CONNECTE)
                self._echec(job, choix.code, message)
                return
            if payant(choix.service, choix.modele):  # catalogue mal rangé : jamais un modèle payant
                self._echec(job, "payant", MESSAGES["payant"], detail=f"{choix.service} {choix.modele}")
                return
            pointe = fin_pointe(choix.service, now)
            if pointe:
                if job.reports >= REPORTS_MAX:
                    self._echec(job, "pointe", MESSAGES["pointe"])
                else:
                    job.service = choix.service
                    self._reporter(job, "pointe", pointe, now)
                return
            alias, service = choix.alias, choix.service
            job.compte, job.service, job.modele, job.sonde = alias, service, choix.modele, choix.sonde
            # B5 : durée du service choisi (Dreamina 5 s, Flow 8 s) quand aucune n'a été demandée ;
            # bascule (29/10) : durée du réglage sur son site, durée par défaut ailleurs
            if job.prefere:
                job.duree = job.prefere[2] if service == job.prefere[0] else ia_web.duree_defaut(service, job.modele)
            else:
                job.duree = ia_web.duree_video(service, job.modele, job.duree, job.duree_libre) or job.duree
            cle = ia_web.cle_essai(alias, service)
            job.essais.append(cle)
            job.etat, job.etape, job.erreur, job.code = "en_cours", "ouverture", "", ""
            job.debut, job.reporte_jusqua = now, 0.0
            self.publish(job)
            unites = ia_web.cout(service, job.modele, job.duree)
            entree = {"moteur": service, "service": service, "modele": job.modele, "type": TYPE,
                      "affichage": AFFICHAGE, "profil": str(self.profil(alias)), "prompt": job.prompt,
                      "fichiers": [x for x in (job.image, job.image_fin) if x], "duree": job.duree,
                      **({"format": job.format} if job.format else {}),
                      "sortie": str(SORTIE), "debug": str(config.IMAGES_DEBUG_DIR),
                      "plafond": PLAFOND_SOLDE if ia_web.limite_solde(service) else plafond(service),
                      "fenetre": fenetre(service), "delai_s": DELAI_S}
            envoyes: list[int] = []  # crédits comptés pendant CET essai (rendus si le site n'a rien débité)

            def ligne(msg: dict, job: Job = job, alias: str = alias, service: str = service,
                      unites: int = unites, envoyes: list[int] = envoyes) -> None:
                if msg.get("t") == "etape" and msg.get("etape") in ("ouverture", "saisie", "generation", "telechargement"):
                    job.etape = msg["etape"]
                    self.publish(job)
                elif msg.get("t") == "envoye":
                    t = self.horloge()
                    avant = self.etat.compte(alias, t, service)
                    n = self.etat.compter(alias, service, t, unites=unites)
                    envoyes.append(unites)
                    _journal(f"{job.id} {alias} {service} prompt envoyé, modèle {job.modele} : {unites} crédits "
                             f"({n}/{plafond(service)} sur la fenêtre {fenetre(service)})")
                    if fenetre(service) == "24h" and not avant.get("debut"):
                        _journal(f"FENÊTRE 24h ouverte {alias} {service} par {job.id} : renouvellement prévu "
                                 f"{_heure(t + 86400)}")

            arret = lambda job=job: job.annule or config.ESTOP_FILE.exists()  # noqa: E731
            with ia_web.chrome_libre(arret) as pris:  # un seul Chrome à la fois, images et vidéos
                fin = self.runner(entree, ligne) if pris and not arret() else {"t": "echec", "code": "annule"}
            if not job.annule and config.ESTOP_FILE.exists():  # `runtime/ESTOP` posé à la main
                job.annule, job.motif_annule = True, "estop"
            if job.annule:
                _journal(f"{job.id} annulé ({job.motif_annule}) pendant la génération (fichier éventuel gardé : "
                         f"{fin.get('fichier') or '-'})")
                self._echec(job, job.motif_annule, MESSAGES[job.motif_annule])
                return
            if fin.get("t") == "fini":
                self._fini(job, fin)
                return
            code = str(fin.get("code") or "interne")
            message = str(fin.get("message") or "")
            if code in ("pointe", "rythme", "activite") and envoyes:
                # Envoyé puis refusé par le site (rien débité) : sans ça, chaque report recompte.
                n = self.etat.rendre(alias, service, self.horloge(), unites=sum(envoyes))
                _journal(f"{job.id} {alias} {service} {code} après l'envoi : {sum(envoyes)} crédits rendus "
                         f"({n}/{plafond(service)} sur la fenêtre {fenetre(service)})")
            if code == "credits" and ia_web.limite_solde(service):
                # Solde du site trop bas pour CE coût : un modèle moins cher reste permis (29/10)
                seuil = self.etat.trop_cher(alias, service, unites, now=self.horloge())
                _journal(f"SOLDE INSUFFISANT {alias} {service} pour {unites} crédits ({message[:200]!r}) : "
                         f"{seuil} crédits ou plus refusés jusqu'à minuit")
                job.texte_ia, job.etape = message[:500], "bascule"
                self.publish(job)
                continue
            if code in ("quota", "credits"):
                self.etat.epuise(alias, message, str(fin.get("reprise") or ""), job.sonde, now=self.horloge(),
                                 moteur=service)
                _journal(f"ÉPUISEMENT ({code}) {alias} {service}{' (sonde)' if job.sonde else ''} : "
                         f"{message[:200]!r} reprise={fin.get('reprise') or '?'}")
                job.texte_ia, job.etape = message[:500], "bascule"
                self.publish(job)
                continue  # compte suivant (auto ou manuel du même service) ; sinon « tous épuisés »
            if code in ("pointe", "rythme"):
                job.essais.remove(cle)  # le compte n'est pas épuisé : il pourra être repris
                t = self.horloge()
                jusqua = ia_web.reprise_apres(str(fin.get("reprise") or ""), t)
                if jusqua is not None:  # lue sur le site : bornée, une place de file n'est pas tenue 99 h
                    jusqua = min(jusqua, t + REPRISE_MAX_S)
                if jusqua is None:
                    jusqua = (fin_pointe(service, t) or t + POINTE_REPRISE_S) if code == "pointe" else t + RYTHME_REPRISE_S
                if job.reports < REPORTS_MAX:
                    job.texte_ia = message[:500] if code == "pointe" else ""
                    self._reporter(job, code, jusqua, t)
                    return
                self._echec(job, code, MESSAGES[code], detail=message)
                return
            if code == "session":
                # 30/10 : site à connexion propre (Dreamina) : seul CE service est à reconnecter
                champ = "site_a_reconnecter" if ia_web.connexion_propre(service) else "a_reconnecter"
                self.etat.noter(alias, self.horloge(), moteur=service, **{champ: True})
            if code in ("captcha", "activite"):
                self.etat.noter(alias, self.horloge(), moteur=service, pause_jusqua=self.horloge() + PAUSE_S)
            if code in ("refus", "activite"):
                job.texte_ia = message[:500]
            self._echec(job, code, MESSAGES.get(code, MESSAGES["interne"]), detail=message,
                        capture=str(fin.get("capture") or ""))
            return

    def _fini(self, job: Job, fin: dict) -> None:
        reel, raison = verifier_video(fin.get("fichier"))
        if reel is None:
            self._echec(job, "format", MESSAGES["format"], detail=f"{raison} : {fin.get('fichier')!r}")
            return
        # Contrat du worker : le modèle réellement employé ; un autre que celui demandé est une
        # substitution : échec `page`, jamais tu (le fichier reste dans Videos-IA).
        job.modele_utilise = str(fin.get("modele_utilise") or "")[:40]
        if job.modele_utilise and job.modele not in ("", ia_web.MODELE_DEFAUT) and job.modele_utilise != job.modele:
            self._echec(job, "page", MESSAGES["page"], detail=f"modèle {job.modele_utilise!r} au lieu de {job.modele!r}")
            return
        job.chemin, job.nom = str(reel), reel.name
        now = self.horloge()
        if "credits_restants" in fin:
            job.credits_restants = fin.get("credits_restants")
            c = self.etat.compte(job.compte, now, job.service)
            _journal(f"{job.id} {job.compte} {job.service} crédits restants annoncés par le site : "
                     f"{fin.get('credits_restants')!r} (compteur d'Astrel : {c['n']}/{plafond(job.service)})")
        depuis = self.etat.succes(job.compte, now, moteur=job.service)
        if depuis is not None:
            _journal(f"PREMIER SUCCÈS après épuisement {job.compte} {job.service} : {depuis / 3600:.1f} h")
        job.etat, job.etape, job.erreur, job.code = "fini", "", "", ""
        _journal(f"{job.id} {job.compte} {job.service} fini en {now - job.debut:.0f} s : {job.nom}")
        log.info("vidéo générée (%s, compte %s) : %s", job.service, job.compte, job.nom)
        self.publish(job)
        if self.on_fini is not None:
            try:
                self.on_fini(job)
            except Exception:
                log.exception("après la vidéo %s", job.id)

    def _echec(self, job: Job, code: str, message: str, detail: str = "", capture: str = "") -> None:
        job.etat, job.code, job.erreur = "echec", code, message
        _journal(f"{job.id} {job.compte or '-'} {job.service or job.demande} échec {code} : {detail[:200]!r}"
                 f"{' capture=' + capture if capture else ''}")
        log.warning("vidéo %s en échec (%s) : %s", job.id, code, detail or message)
        self.publish(job)

    # --- worker réel ---

    @staticmethod
    def commande() -> list[str] | None:
        node = ia_web.node()
        script = ia_web.script(TYPE)
        if not node or not script.is_file() or not (config.IMAGES_WORKER_DIR / "node_modules" / "playwright-core").is_dir():
            return None
        return [node, str(script)]

    def _lancer_worker(self, entree: dict, ligne: Callable[[dict], None]) -> dict:
        argv = self.commande()
        if argv is None:
            return {"t": "echec", "code": "absent", "message": "imagegen/video.mjs ou node_modules absent"}
        job = self.en_cours
        return ia_web.executer_worker(
            argv, entree, ligne, delai_s=float(entree.get("delai_s") or DELAI_S) + WORKER_MARGE_S,
            arreter_si=lambda: bool(job and job.annule) or config.ESTOP_FILE.exists(), nom="worker vidéos")


def _journal(texte: str) -> None:
    """`logs/videos.log` : une ligne par évènement (mesure du renouvellement réel des crédits)."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {texte}\n")
    except OSError:
        log.warning("journal des vidéos illisible")


videos = Videos()
