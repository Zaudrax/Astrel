"""Génération d'images IA par le navigateur (plan P14, 21/10/2026).

Demande de l'utilisateur : quand il demande une image, une carte animée s'affiche dans le fil du
HUD pendant que Gemini (ChatGPT au lot 5) la génère dans un Chrome SANS FENÊTRE ; une fois
téléchargée, l'image remplace l'animation, avec l'IA et le compte utilisés. Crédit gratuit
seulement (rien de payant), rythme humain, usage perso.

Le cœur pilote le navigateur SANS modèle : `imagegen/worker.mjs` (Node, playwright-core à
version FIXÉE) est lancé ici, une image par lancement, lignes JSON sur stdout. Ce module
tient la FILE (un job à la fois), l'ÉTAT des comptes (`runtime/imagegen/etat.json`,
écriture atomique : compteur, épuisement, reprise), le PLAFOND par moteur (Gemini 20 par jour,
ChatGPT 3 sur 24 h glissantes depuis la première image : `config.IMAGES_PLAFONDS`) et publie
l'évènement `image_job` :

    {job, etat: file|en_cours|fini|echec, moteur, compte (alias, jamais l'adresse), prompt,
     file (copie dans runtime/attachments, vignette par /api/attachment/<file>), nom
     (fichier dans Images-IA), etape, erreur, code, texte_ia, channel, position}

Quotas : un compte épuisé est sauté ; en `auto`, on passe au compte suivant (puis à l'autre
moteur, lot 5). Heure réelle du reset de Gemini et ChatGPT NON établie : chaque épuisement
et le premier succès qui suit sont journalisés dans `logs/images.log` pour la mesurer ;
repli provisoire : compte de nouveau essayé 24 h après l'épuisement (ou à l'heure lue dans
le message du site), une sonde toutes les 6 h au plus en attendant.

Pilote (lots 1 à 4) : Gemini seul, compte « amw » (ImWeek AMV), sans pièces jointes ni
panneau. Les champs `moteur`, `compte`, `fichiers` sont déjà là pour le lot 5.

P40 (A1, 27/10) : comptes, catalogue, état des quotas par (compte, service), choix auto/manuel
et verrou de Chrome sont dans le socle commun `ia_web.py` ; ce module garde la file, `submit`,
`retry`, `status`. Entrée du worker : + `service`, `modele`, `type`, `affichage` (masquee|
visible ; `fenetre` reste la fenêtre du quota, déjà lue par worker.mjs) ; sortie : +
`modele_utilise`, `credits_restants`.

P40 A4 (28/10, demande de l'utilisateur : « coder les étapes manquantes ») : Gemini, ChatGPT et Bing
pilotés ; pièces jointes (filtre du cœur `ia_web.pieces`, réappliqué par le worker) ; moteur ET
modèle : `engine` nommé = MANUEL pour cette demande ; `auto` suit les Réglages › Images
(`images_mode`, `images_ordre`, `images_manuel`). Manuel : repli sur un autre compte du MÊME service
et modèle seulement ; jamais un autre moteur sans l'utilisateur (échec + `proposition`, bouton
« Essayer avec X » = `retry(job, engine=X)`). Refus du worker `rythme`/`occupe`/`plafond` : compte
suivant ; `nuit` : échec avec l'heure de reprise. Rien ne part sous ESTOP ni mémoire du PC en
« blocage » (état mesuré, `memoire.lire_etat`). Comptes : `compte_commande` (ajouter, retirer, ordre,
connexion par `ia_connexion`), réservé à l'utilisateur par le cœur. État pour le HUD : évènement sticky
`ia_etat` (= `status()` sans `ok`).
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import attachments, config, ia_connexion, ia_web
from .ia_web import Etat, reprise_apres  # noqa: F401  (réexportés : appelants et tests d'avant P40)

log = logging.getLogger("astrel.images")

# Moteurs connus ; seuls ceux de MOTEURS_PRETS sont pilotés. P40 A4 : les trois (sites du lot A3,
# jamais essayés sur les vrais sites : pilote avec l'utilisateur). Bing : 4 variantes, sans compte.
MOTEURS = {"gemini": "Gemini", "chatgpt": "ChatGPT", "bing": "Bing Image Creator"}
MOTEURS_PRETS = ("gemini", "chatgpt", "bing")
IMAGES_PAR_JOB = 4  # Bing rend 4 variantes : toutes remontent dans la carte (grille)
DEMANDES = ("auto", *MOTEURS)
PROMPT_MAX = 2000
AFFICHAGE = "masquee"        # Chrome sans fenêtre (Q2 du plan P40) ; « visible » au lot A4
CAPTCHA_PAUSE_S = 6 * 3600   # compte laissé au repos après un défi anti-robot
WORKER_MARGE_S = 240         # au-delà du délai de génération, le worker est tué
JOBS_GARDES = 50
RELANCE_EN_COURS = "…"   # carte réservée par un clic « Réessayer » dont la demande part
ALIAS = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")

# Texte d'écran par code d'échec du worker (FR ; EN dans astrel/locales/en.json). Le texte de
# l'IA (refus de contenu, message de quota) est montré à part, tel quel (`texte_ia`).
MESSAGES = {
    "quota": "Crédit gratuit épuisé sur ce compte.",
    "captcha": "Le site demande de prouver qu'on n'est pas un robot : arrêt, rien n'est contourné.",
    "refus": "L'IA a refusé de créer cette image.",
    "session": "Compte déconnecté : à reconnecter une fois, à la main.",
    "page": "La page du site a changé : la génération n'a pas abouti.",
    "delai": "Pas d'image au bout du délai : abandon, sans nouvel essai.",
    "plafond": "Plafond d'images atteint sur ce compte.",
    "occupe": "Une image est déjà en cours sur ce compte.",
    "fichiers": "Pièce jointe refusée par le générateur d'images.",
    "entree": "Demande illisible par le générateur d'images.",
    "interne": "Le générateur d'images s'est arrêté sur une erreur.",
    "absent": "Générateur d'images pas installé (cd imagegen && npm ci).",
    "estop": "Arrêt d'urgence : image annulée.",
    "memoire": "Mémoire du PC presque pleine : aucun Chrome ne part tant qu'elle ne redescend pas.",
    "nuit": "Pas de génération la nuit (rythme humain).",
    "rythme": "Site utilisé il y a quelques secondes sur ces comptes (rythme humain) : réessaie plus tard.",
}
NUIT_REPRISE = "Pas de génération la nuit (rythme humain) : reprise à {heure}."
RYTHME_REPRISE = "Site utilisé il y a quelques secondes sur ces comptes (rythme humain) : nouvel essai après {heure}."
# Refus du worker qui ne disent rien du compte ni du moteur : on passe au compte suivant.
CODES_SAUT = ("rythme", "occupe", "plafond")
TOUS_EPUISES = "Crédit gratuit épuisé sur tous les comptes."
TOUS_PLAFOND = "Plafond d'images atteint sur tous les comptes : nouvel essai après la remise à zéro."
EN_PAUSE = "Compte au repos après un défi anti-robot : nouvel essai plus tard."
NON_CONNECTE = "Aucun compte connecté pour ce moteur : connexion à faire une fois, à la main."
COMPTES_ILLISIBLES = "Liste des comptes d'images illisible (runtime/imagegen/comptes.json) : à corriger."
# Gestion des comptes (P40 A4) : texte d'écran par code de `ia_web` / `ia_connexion`.
COMPTES_MESSAGES = {
    "alias": "Nom de compte invalide : lettres minuscules, chiffres, tiret ou souligné, 32 au plus.",
    "fournisseur": "Fournisseur inconnu : google ou openai.",
    "existe": "Ce compte existe déjà.",
    "inconnu": "Compte inconnu.",
    "max": "Nombre maximal de comptes atteint.",
    "ordre": "Ordre refusé : chaque compte une fois, et tous les comptes.",
    "comptes": COMPTES_ILLISIBLES,
    "occupe": "Ce compte est déjà ouvert (image en cours ou fenêtre de connexion) : réessaie quand il sera libre.",
    "absent": "Google Chrome introuvable : la connexion se fait dans Chrome.",
    "url": "Aucune page de connexion connue pour ce fournisseur.",
    "erreur": "Chrome n'a pas pu s'ouvrir.",
    "estop": "Arrêt d'urgence en cours : pas de fenêtre de connexion.",
    "memoire": "Mémoire du PC presque pleine : pas de fenêtre de connexion tant qu'elle ne redescend pas.",
    "profil": "Un profil Chrome porte déjà ce nom (compte retiré ?) : choisis un autre nom.",
}
CONNEXION_OUVERTE = ("Fenêtre Chrome ouverte sur le compte {alias} : connecte-toi toi-même, puis ferme la "
                     "fenêtre. Astrel ne voit ni ne garde ton mot de passe.")
COMMANDES_COMPTES = ("image_compte_ajouter", "image_compte_retirer", "image_compte_ordre", "image_compte_connecter",
                     "image_compte_renommer")


def memoire_bloquee() -> bool:
    """Mémoire du PC en « blocage » d'après la dernière mesure fraîche du cœur (`memoire.json`) :
    aucun Chrome ne part (images, connexion). Mesure absente ou périmée : on laisse passer, comme
    le hook des agents."""
    from . import memoire
    data = memoire.lire_etat()
    return bool(data and data.get("state") == "blocage")


def reglages_images() -> dict:
    """Réglages › Images (`settings.py`), relus à chaque demande : {mode, ordre, service, modele}.
    Illisibles : valeurs par défaut sûres (auto, ordre du catalogue, défaut du 1er service)."""
    from . import settings
    services = ia_web.services("image")
    out = {"mode": "auto", "ordre": list(services), "service": services[0] if services else "gemini",
           "modele": ia_web.MODELE_DEFAUT}
    try:
        mode = str(settings.reglages.value("images_mode") or "auto")
        ordre = [s for s in str(settings.reglages.value("images_ordre") or "").split(",") if s in services]
        service, _, modele = str(settings.reglages.value("images_manuel") or "").partition("/")
    except Exception as exc:  # noqa: BLE001 — un réglage illisible ne bloque pas les images
        log.warning("réglages des images illisibles (%s) : valeurs par défaut", exc)
        return out
    out["mode"] = mode if mode in ia_web.MODES else "auto"
    out["ordre"] = list(dict.fromkeys(ordre)) + [s for s in services if s not in ordre]
    if service in services and ia_web.modele_propose(service, modele):
        out["service"], out["modele"] = service, modele
    else:
        out["modele"] = (ia_web.modeles_proposes(out["service"]) or [{"id": ia_web.MODELE_DEFAUT}])[0]["id"]
    return out


# --------------------------------------------------------------------------- état des comptes
# Depuis P40 (A1), l'état, le catalogue, les comptes et le choix sont dans le socle commun
# `ia_web.py` (images et vidéo) ; `Etat` et `reprise_apres` restent importables d'ici.

def plafond(moteur: str) -> int:
    """Images permises par compte de ce moteur sur sa fenêtre (`config.IMAGES_PLAFONDS`)."""
    return ia_web.plafond(moteur)


def fenetre(moteur: str) -> str:
    """« jour » (remise à zéro à minuit) ou « 24h » (24 h après la première image)."""
    return ia_web.fenetre(moteur)


# --------------------------------------------------------------------------- jobs

@dataclass
class Job:
    id: str
    prompt: str
    demande: str                    # auto, gemini, chatgpt
    channel: str = ""
    fichiers: list[str] = field(default_factory=list)  # lot 5 (pièces jointes)
    etat: str = "file"
    moteur: str = ""
    compte: str = ""
    etape: str = ""
    erreur: str = ""
    code: str = ""
    texte_ia: str = ""
    file: str = ""                  # image PRINCIPALE : copie pour le HUD (runtime/attachments)
    nom: str = ""                   # image principale : fichier dans Images-IA
    chemin: str = ""
    # Toutes les images rendues, la principale en tête : [{file, nom}] (Bing : 4 variantes)
    images: list[dict] = field(default_factory=list)
    essais: list[str] = field(default_factory=list)
    sonde: bool = False
    relance: str = ""               # job lancé par « Réessayer » (un seul par carte)
    cree: float = field(default_factory=time.time)
    modele: str = ""                # P40 : modèle demandé (manuel) ou choisi (défaut du service)
    modele_utilise: str = ""        # P40 : modèle annoncé par le worker
    proposition: str = ""           # P40 : manuel en échec, autre service disponible (« Essayer avec X »)
    mode: str = "auto"              # P40 A4 : auto (ordre des Réglages) ou manuel (service + modèle fixés)
    ordre: list[str] = field(default_factory=list)   # P40 A4 : ordre des moteurs pris à la demande (auto)
    sauts: list[tuple[str, str]] = field(default_factory=list)  # (code, reprise) des comptes passés
    epuises: int = 0                # comptes épuisés pendant cette demande

    def event(self) -> dict:
        return {"job": self.id, "etat": self.etat, "moteur": self.moteur or self.demande,
                "moteur_libelle": MOTEURS.get(self.moteur, ""), "compte": self.compte,
                "prompt": self.prompt, "file": self.file, "nom": self.nom, "images": list(self.images),
                "etape": self.etape,
                "erreur": self.erreur, "code": self.code, "texte_ia": self.texte_ia, "channel": self.channel,
                "modele": self.modele_utilise or self.modele, "proposition": self.proposition,
                "proposition_libelle": MOTEURS.get(self.proposition, ""), "mode": self.mode,
                "pieces": len(self.fichiers)}


Runner = Callable[[dict, Callable[[dict], None]], dict]


# Lancement du worker commun aux images et aux vidéos (`ia_web`, P41 B3) ; noms gardés pour les appelants.
_node = ia_web.node
_env_worker = ia_web.env_worker


class Images:
    """File d'images : un job à la fois, dans un fil à part ; `submit` rend la main aussitôt."""

    def __init__(self, publish: Callable[..., Any] | None = None, etat: Etat | None = None,
                 runner: Runner | None = None, publish_etat: Callable[..., Any] | None = None) -> None:
        self._publish = publish
        self._publish_etat = publish_etat  # évènement sticky `ia_etat` (défaut : le bus)
        self.etat = etat or Etat()
        self.runner: Runner = runner or self._lancer_worker
        self._file: queue.Queue[Job | None] = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._fil: threading.Thread | None = None
        self.en_cours: Job | None = None

    # --- publication ---

    def publish(self, job: Job) -> None:
        pub = self._publish
        if pub is None:
            from .events import bus
            pub = bus.publish
        try:
            pub("image_job", **job.event(), position=self._position(job))
        except Exception:
            log.exception("publication image_job")

    def _position(self, job: Job) -> int:
        if job.etat != "file":
            return 0
        with self._lock:
            attente = [j for j in self._jobs.values() if j.etat == "file"]
        attente.sort(key=lambda j: j.cree)
        return 1 + attente.index(job) + (1 if self.en_cours else 0) if job in attente else 0

    # --- comptes ---

    @staticmethod
    def comptes() -> list[tuple[str, str]]:
        """(alias, moteur) des comptes d'images, dans l'ordre du repli (`ia_web.comptes`)."""
        return [(a, s) for a, s in ia_web.paires("image", MOTEURS_PRETS) if ALIAS.fullmatch(a) and s in MOTEURS]

    @staticmethod
    def profil(alias: str) -> Path:
        return config.IMAGES_PROFILS / alias

    def _choisir(self, job: Job, now: float | None = None) -> tuple[str, str, bool] | tuple[str, str]:
        """(alias, moteur, sonde) du prochain compte à essayer, ou (code, message) si aucun.
        `auto` : mode AUTO du socle ; un moteur nommé : mode MANUEL (jamais d'autre moteur)."""
        manuel = job.demande != "auto"
        # Un compte dont la fenêtre de connexion est ouverte n'est pas pris (son profil est à l'utilisateur).
        choix = ia_web.choisir(mode="manuel" if manuel else "auto", service=job.demande if manuel else "",
                               modele=job.modele, essais=job.essais, prets=MOTEURS_PRETS, etat=self.etat,
                               profil_existe=lambda alias: self.profil(alias).is_dir() and not ia_connexion.ouverte(alias),
                               now=now, ordre=job.ordre or None, pieces=bool(job.fichiers))
        if isinstance(choix, ia_web.Choix):
            job.modele = choix.modele
            return choix.alias, choix.service, choix.sonde
        job.proposition = choix.proposition
        message = {"quota": TOUS_EPUISES, "plafond": TOUS_PLAFOND, "captcha": EN_PAUSE,
                   "comptes": COMPTES_ILLISIBLES}.get(choix.code, NON_CONNECTE)
        return choix.code, message

    # --- demandes ---

    def submit(self, prompt: Any, engine: Any = "auto", files: Any = None, channel: str = "",
               model: Any = "") -> dict:
        """Outil MCP `generate_image` / commande `image_generate` : met l'image en file et rend
        la main aussitôt ; le résultat arrive au HUD par `image_job`. `model` (P40) : modèle du
        catalogue `config/ia-web.json`, seulement avec un moteur nommé (mode manuel).
        P40 A4 : `engine` nommé = MANUEL pour cette demande ; `auto` suit Réglages › Images (mode
        auto avec l'ordre des moteurs, ou manuel avec le moteur et le modèle choisis). `files` :
        chemins absolus (6 au plus), filtrés ici (`ia_web.pieces`) puis par le worker."""
        prompt = re.sub(r"\s+", " ", str(prompt or "")).strip()
        demande = str(engine or "auto").strip().lower() or "auto"
        modele = str(model or "").strip().lower()
        if not prompt:
            return {"ok": False, "error": "prompt vide : décris l'image voulue"}
        if len(prompt) > PROMPT_MAX:
            return {"ok": False, "error": f"prompt trop long ({PROMPT_MAX} caractères au plus)"}
        if demande not in DEMANDES:
            return {"ok": False, "error": f"moteur inconnu : {demande} (auto, gemini, chatgpt ou bing)"}
        if demande not in ("auto", *MOTEURS_PRETS):
            return {"ok": False, "error": f"moteur pas encore piloté : {demande}"}
        if modele and demande == "auto":
            return {"ok": False, "error": "un modèle se choisit avec un moteur nommé (mode manuel), pas en auto"}
        if memoire_bloquee():
            return {"ok": False, "error": "mémoire du PC presque pleine : pas d'image (aucun Chrome) tant qu'elle "
                                          "ne redescend pas"}
        if modele and not ia_web.modele_propose(demande, modele):
            noms = ", ".join(m["id"] for m in ia_web.modeles_proposes(demande))
            return {"ok": False, "error": f"modèle inconnu pour ce moteur : {modele} (connus : {noms})"}
        reg = reglages_images()
        mode, ordre = "manuel", []
        if demande == "auto":
            if reg["mode"] == "manuel":  # Réglages › Images en manuel : moteur et modèle choisis là
                demande, modele = reg["service"], reg["modele"]
                if demande not in MOTEURS_PRETS:
                    return {"ok": False, "error": f"moteur pas encore piloté : {demande}"}
            else:
                mode, ordre = "auto", [s for s in reg["ordre"] if s in MOTEURS_PRETS]
        if isinstance(files, str):  # un seul chemin donné tel quel : pas un caractère par élément
            files = [files]
        fichiers, refus = ia_web.pieces(files)
        if refus:
            return {"ok": False, "error": refus}
        if fichiers and mode == "manuel" and not ia_web.accepte_pieces(demande):
            nom_moteur = MOTEURS.get(demande, demande)
            return {"ok": False, "error": f"{nom_moteur} ne prend pas de pièce jointe : "
                                          "choisis un autre moteur ou retire la pièce"}
        with self._lock:
            attente = sum(1 for j in self._jobs.values() if j.etat in ("file", "en_cours"))
            if attente >= config.IMAGES_FILE_MAX:
                return {"ok": False, "error": f"file d'images pleine ({config.IMAGES_FILE_MAX} en attente) : réessaie plus tard"}
            job = Job(id=uuid.uuid4().hex[:12], prompt=prompt, demande=demande, channel=str(channel or ""),
                      fichiers=fichiers, modele=modele, mode=mode, ordre=ordre)
            self._jobs[job.id] = job
            self._oublier()
            if self._fil is None or not self._fil.is_alive():
                self._fil = threading.Thread(target=self._boucle, name="images-ia", daemon=True)
                self._fil.start()
        # Publié AVANT la mise en file : sinon le fil des images pourrait annoncer « en cours »
        # avant que la carte « en file » n'arrive au HUD, qui l'afficherait alors en attente.
        position = self._position(job)
        self.publish(job)
        _journal(f"{job.id} demandé ({demande}) : {prompt[:120]}")
        self._file.put(job)
        return {"ok": True, "job": job.id, "etat": "file", "moteur": demande, "mode": mode, "position": position,
                "message": "Image lancée en arrière-plan : elle s'affichera dans le HUD dès qu'elle sera prête "
                           "(une à deux minutes), avec une animation en attendant. N'attends pas, ne relance pas "
                           "et ne la décris pas : l'utilisateur la verra."}

    def retry(self, job_id: str, engine: str = "") -> dict:
        """Bouton « Réessayer » d'une carte en échec : une NOUVELLE demande, jamais automatique.
        `engine` (P40 A4) : bouton « Essayer avec X » d'un échec en manuel ; seul le moteur
        PROPOSÉ par la carte est accepté (modèle par défaut de ce moteur, mêmes pièces jointes)."""
        with self._lock:
            old = self._jobs.get(str(job_id or ""))
        if old is None:
            return {"ok": False, "error": "image inconnue (Astrel a peut-être redémarré)"}
        if old.etat != "echec":
            return {"ok": False, "error": "cette image n'est pas en échec"}
        autre = str(engine or "").strip().lower()
        if autre and autre != old.demande and autre != old.proposition:
            return {"ok": False, "error": "ce moteur n'est pas celui proposé par la carte"}
        # Relecture fiche 4 : la carte est RÉSERVÉE sous le verrou avant `submit` (qui le prend
        # lui-même) ; deux clics simultanés (deux HUD) ne lancent donc qu'une image.
        with self._lock:
            if old.relance:  # second clic (autre HUD, double clic) : ignoré, rien ne repart
                return {"ok": True, "job": "" if old.relance == RELANCE_EN_COURS else old.relance, "deja": True}
            old.relance = RELANCE_EN_COURS
        r: dict = {"ok": False, "error": "relance impossible"}
        try:
            if autre and autre != old.demande:
                r = self.submit(old.prompt, autre, old.fichiers, old.channel, "")
            else:
                r = self.submit(old.prompt, old.demande, old.fichiers, old.channel,
                                old.modele if old.demande != "auto" else "")
        finally:
            with self._lock:
                old.relance = r["job"] if r.get("ok") else ""  # refus : le bouton resservira
        return r

    def status(self, now: float | None = None) -> dict:
        """État pour Claude (`image_status`) et pour le HUD (évènement `ia_etat`, sans `ok`) :
        `comptes` par (compte, moteur) avec quotas ; P40 A4 : `declares` (comptes.json, ordre du
        repli), `services` (modèles PROPOSÉS, pièces jointes), `reglages`, `connexions` ouvertes."""
        now = time.time() if now is None else now
        connexions = ia_connexion.ouvertes()
        comptes = []
        for alias, moteur in self.comptes():
            c = self.etat.compte(alias, now, moteur)
            ok, raison, _ = self.etat.disponible(alias, moteur, now)
            sans_compte = ia_web.fournisseur(moteur) == "aucun"
            comptes.append({"compte": alias, "moteur": moteur, "connecte": sans_compte or self.profil(alias).is_dir(),
                            "sans_compte": sans_compte, "a_reconnecter": bool(c.get("a_reconnecter")),
                            "images": c["n"], "plafond": plafond(moteur), "fenetre": fenetre(moteur),
                            "disponible": ok, "raison": raison, "epuise_le": c.get("epuise_le"),
                            "reprise_apres": c.get("reprise_apres"), "connexion_ouverte": alias in connexions})
        services = [{"service": s, "libelle": ia_web.libelle(s), "fournisseur": ia_web.fournisseur(s),
                     "pret": s in MOTEURS_PRETS, "pieces": ia_web.accepte_pieces(s),
                     "modeles": [{"id": m["id"], "libelle": m["libelle"]} for m in ia_web.modeles_proposes(s)],
                     "plafond": plafond(s), "fenetre": fenetre(s)}
                    for s in ia_web.services("image")]
        with self._lock:
            attente = sum(1 for j in self._jobs.values() if j.etat == "file")
        return {"ok": True, "comptes": comptes, "en_attente": attente,
                "en_cours": self.en_cours.id if self.en_cours else "",
                "declares": ia_web.comptes(), "comptes_illisibles": ia_web.comptes_illisibles(),
                "services": services, "reglages": reglages_images(), "connexions": connexions,
                "pieces_max": ia_web.PIECES_MAX}

    def publier_etat(self) -> None:
        """Évènement sticky `ia_etat` (Réglages › Images et panneau du HUD, lot A5)."""
        pub = self._publish_etat
        if pub is None:
            from .events import bus
            pub = bus.publish
        try:
            etat = self.status()
            etat.pop("ok", None)
            pub("ia_etat", **etat)
        except Exception:
            log.exception("publication ia_etat")

    # --- comptes (P40 A4) : commandes réservées à l'utilisateur (refus `_from_claude` par le cœur) ---

    def compte_commande(self, name: str, cmd: dict) -> dict:
        """`image_compte_ajouter {alias, fournisseur}`, `image_compte_retirer {alias}`,
        `image_compte_ordre {ordre: [alias…]}`, `image_compte_connecter {alias}` (fenêtre Chrome
        VISIBLE sur le profil du compte, sans pilotage : `ia_connexion.ouvrir`)."""
        alias = str(cmd.get("alias") or "").strip().lower()
        if name == "image_compte_connecter":
            if config.ESTOP_FILE.exists():
                return {"ok": False, "error": COMPTES_MESSAGES["estop"]}
            if memoire_bloquee():
                return {"ok": False, "error": COMPTES_MESSAGES["memoire"]}
            en_cours = self.en_cours
            if en_cours is not None and en_cours.compte == alias and en_cours.etat == "en_cours":
                return {"ok": False, "error": COMPTES_MESSAGES["occupe"]}
            r = ia_connexion.ouvrir(alias)
            if not r["ok"]:
                return {"ok": False, "error": COMPTES_MESSAGES.get(r["code"], COMPTES_MESSAGES["erreur"])}
            _journal(f"connexion du compte {alias} : fenêtre ouverte à la demande de l'utilisateur")
            return {"ok": True, "alias": alias, "deja": bool(r.get("deja")),
                    "message": CONNEXION_OUVERTE.replace("{alias}", alias)}
        if name == "image_compte_ajouter":
            liste, code = ia_web.ajouter_compte(alias, str(cmd.get("fournisseur") or ""))
        elif name == "image_compte_retirer":
            liste, code = ia_web.retirer_compte(alias)
        elif name == "image_compte_ordre":
            liste, code = ia_web.ordonner_comptes(cmd.get("ordre"))
        elif name == "image_compte_renommer":  # 30/10 : nom mal tapé à l'ajout
            liste, code = ia_web.renommer_compte(alias, str(cmd.get("nouveau") or ""), self._deplacer_compte)
            if not code:
                from . import videos_ia
                videos_ia.videos.publier_etat()
        else:
            return {"ok": False, "error": f"commande inconnue : {name}"}
        if code:
            return {"ok": False, "error": COMPTES_MESSAGES.get(code, COMPTES_MESSAGES["erreur"])}
        _journal(f"comptes : {name.removeprefix('image_compte_')} {alias or ''} → "
                 f"{', '.join(c['alias'] + ':' + c['fournisseur'] for c in liste)}")
        self.publier_etat()
        return {"ok": True, "comptes": liste}

    def _deplacer_compte(self, ancien: str, nouveau: str) -> str:
        """Renommage : profil Chrome (la connexion suit), compteur du worker et état. Refusé si le
        profil sert (image ou vidéo en cours, fenêtre de connexion, Chrome ouvert dessus) ou si un
        profil porte déjà le nouveau nom (jamais écrasé)."""
        from . import videos_ia
        avant, apres = ia_connexion.profil(ancien), ia_connexion.profil(nouveau)
        cpt_avant, cpt_apres = Path(f"{avant}.compteur.json"), Path(f"{apres}.compteur.json")
        with ia_connexion._lock:
            sert = [j for j in (self.en_cours, videos_ia.videos.en_cours) if j is not None and j.compte == ancien
                    and j.etat == "en_cours"]
            # `_ouvertes` lu directement : `ouverte()` reprendrait `_lock` (non réentrant)
            if sert or ancien in ia_connexion._ouvertes or ia_connexion.verrou_tenu(avant) or ia_connexion.chrome_ouvert(avant):
                return "occupe"
            if apres.exists() or cpt_apres.exists() or Path(f"{apres}.lock").exists():
                return "profil"
            ia_connexion._effacer_verrou_mort(avant)
            try:
                if avant.exists():
                    avant.rename(apres)
                if cpt_avant.exists():
                    cpt_avant.rename(cpt_apres)
            except OSError as exc:
                log.warning("renommage du compte %s en %s impossible (%s)", ancien, nouveau, exc)
                return "erreur"
        self.etat.renommer(ancien, nouveau)
        return ""

    def _oublier(self) -> None:
        finis = [j for j in self._jobs.values() if j.etat in ("fini", "echec")]
        for j in sorted(finis, key=lambda j: j.cree)[:max(0, len(self._jobs) - JOBS_GARDES)]:
            self._jobs.pop(j.id, None)

    # --- traitement ---

    def _boucle(self) -> None:
        while True:
            job = self._file.get()
            if job is None:
                return
            self.en_cours = job
            try:
                self._traiter(job)
            except Exception as exc:
                log.exception("image %s", job.id)
                self._echec(job, "interne", MESSAGES["interne"], detail=str(exc))
            finally:
                self.en_cours = None

    def _traiter(self, job: Job) -> None:
        # Bornée par le nombre de comptes : un compte déjà essayé n'est jamais repris (`essais`).
        while True:
            if config.ESTOP_FILE.exists():  # « ⛔ Stop tout » : rien de nouveau ne part
                self._echec(job, "estop", MESSAGES["estop"])
                return
            if memoire_bloquee():  # P40 A4 : pas de Chrome quand la mémoire du PC est en blocage
                self._echec(job, "memoire", MESSAGES["memoire"])
                return
            if job.mode == "auto" and ia_web.comptes_illisibles():
                # Fiche 2 du testeur : sinon l'auto partait sur Bing sans compte, sans rien dire. Échec
                # clair : comptes.json est à corriger avant toute image en auto (manuel : moteur choisi).
                self._echec(job, "comptes", COMPTES_ILLISIBLES)
                return
            choix = self._choisir(job)
            if len(choix) == 2:
                code, message = choix  # type: ignore[misc]
                if job.sauts and not job.epuises:
                    # Comptes seulement passés (rythme, occupé, plafond du worker) : c'est ça qu'on dit.
                    code, reprise = job.sauts[0]
                    message = (RYTHME_REPRISE.replace("{heure}", reprise) if code == "rythme" and reprise
                               else TOUS_PLAFOND if code == "plafond" else MESSAGES[code])
                self._echec(job, code, message)
                return
            alias, moteur, sonde = choix  # type: ignore[misc]
            job.compte, job.moteur, job.sonde = alias, moteur, sonde
            job.essais.append(ia_web.cle_essai(alias, moteur))
            job.etat, job.etape, job.erreur, job.code = "en_cours", "ouverture", "", ""
            self.publish(job)
            # Contrat du worker (plan P40 étape 7) : + service, modele, type, affichage ; `fenetre`
            # reste la fenêtre du QUOTA (jour|24h), déjà lue par worker.mjs pour son compteur.
            entree = {"moteur": moteur, "service": moteur, "modele": job.modele, "type": "image",
                      "affichage": AFFICHAGE, "profil": str(self.profil(alias)), "prompt": job.prompt,
                      "fichiers": job.fichiers, "sortie": str(config.IMAGES_SORTIE),
                      "debug": str(config.IMAGES_DEBUG_DIR), "plafond": plafond(moteur),
                      "fenetre": fenetre(moteur), "delai_s": config.IMAGES_DELAI_S}
            unites = ia_web.cout(moteur, job.modele)

            def ligne(msg: dict, job: Job = job, alias: str = alias, moteur: str = moteur, unites: int = unites) -> None:
                if msg.get("t") == "etape" and msg.get("etape") in ("ouverture", "saisie", "generation", "telechargement"):
                    job.etape = msg["etape"]
                    self.publish(job)
                elif msg.get("t") == "envoye":
                    n = self.etat.compter(alias, moteur, unites=unites)
                    _journal(f"{job.id} {alias} {moteur} prompt envoyé ({n}/{plafond(moteur)}, fenêtre {fenetre(moteur)})")

            with ia_web.chrome_libre(config.ESTOP_FILE.exists) as pris:  # un seul Chrome, images et vidéos
                fin = self.runner(entree, ligne) if pris else {"t": "echec", "code": "estop"}
            if config.ESTOP_FILE.exists():  # « ⛔ Stop tout » pendant la génération : worker et Chrome arrêtés
                self._echec(job, "estop", MESSAGES["estop"])
                return
            if fin.get("t") == "fini":
                self._fini(job, fin)
                return
            code = str(fin.get("code") or "interne")
            message = str(fin.get("message") or "")
            if code == "quota":
                self.etat.epuise(alias, message, str(fin.get("reprise") or ""), job.sonde, moteur=moteur)
                _journal(f"ÉPUISEMENT {alias} {moteur}{' (sonde)' if job.sonde else ''} : "
                         f"{message[:200]!r} reprise={fin.get('reprise') or '?'}")
                job.texte_ia = message[:500]
                job.etape = "bascule"
                job.epuises += 1
                self.publish(job)
                continue  # compte suivant (auto) ; sinon `_choisir` dit « tous épuisés »
            if code in CODES_SAUT:
                # P40 A4 : le worker n'a rien envoyé (10 min pas écoulées, profil tenu, son propre
                # compteur au plafond) : compte suivant, sans rien noter contre ce compte.
                job.sauts.append((code, str(fin.get("reprise") or "")[:5]))
                _journal(f"{job.id} {alias} {moteur} passé ({code}) : {message[:160]!r}")
                job.etape = "bascule"
                self.publish(job)
                continue
            if code == "nuit":
                reprise = str(fin.get("reprise") or "")[:5]
                self._echec(job, code, NUIT_REPRISE.replace("{heure}", reprise) if reprise else MESSAGES["nuit"],
                            detail=message)
                return
            if code == "session":
                self.etat.noter(alias, moteur=moteur, a_reconnecter=True)
            if code == "captcha":
                self.etat.noter(alias, moteur=moteur, pause_jusqua=time.time() + CAPTCHA_PAUSE_S)
            if code == "refus":
                job.texte_ia = message[:500]
            self._echec(job, code, MESSAGES.get(code, MESSAGES["interne"]), detail=message,
                        capture=str(fin.get("capture") or ""))
            return

    def _fini(self, job: Job, fin: dict) -> None:
        """Le worker rend `fichier` (image principale) et, s'il y en a plusieurs (Bing : 4
        variantes), `fichiers` : [{fichier, nom}], la principale en tête. Chaque chemin doit
        être un fichier ordinaire du dossier Images-IA."""
        sortie = config.IMAGES_SORTIE.resolve()
        annonces = [f.get("fichier") for f in fin.get("fichiers") or [] if isinstance(f, dict)]
        if fin.get("fichier") and fin.get("fichier") not in annonces:
            annonces.insert(0, fin.get("fichier"))
        reels: list[Path] = []
        for brut in annonces[:IMAGES_PAR_JOB * 3]:
            if len(reels) >= IMAGES_PAR_JOB:
                break
            try:
                reel = Path(str(brut or "")).resolve(strict=True)
            except OSError:
                continue
            if reel.is_relative_to(sortie) and reel.is_file():
                reels.append(reel)
        if not reels:
            self._echec(job, "interne", MESSAGES["interne"], detail=f"aucune image dans {sortie} : {annonces!r}")
            return
        job.chemin, job.nom = str(reels[0]), reels[0].name
        for reel in reels:
            try:
                job.images.append({"file": copie_hud(reel), "nom": reel.name})
            except (OSError, ValueError) as exc:
                log.warning("copie de l'image pour le HUD impossible : %s", exc)
                job.images.append({"file": "", "nom": reel.name})
        job.file = job.images[0]["file"]
        # P40 étape 7 : le worker annonce le modèle réellement employé ; un autre que celui
        # demandé (hors « défaut du site ») est une substitution : échec `page`, jamais tu.
        job.modele_utilise = str(fin.get("modele_utilise") or "")[:40]
        if job.modele_utilise and job.modele not in ("", ia_web.MODELE_DEFAUT) and job.modele_utilise != job.modele:
            job.images, job.file, job.nom = [], "", ""
            self._echec(job, "page", MESSAGES["page"],
                        detail=f"modèle {job.modele_utilise!r} au lieu de {job.modele!r}")
            return
        if "credits_restants" in fin:
            _journal(f"{job.id} {job.compte} {job.moteur} crédits restants annoncés : {fin.get('credits_restants')!r}")
        depuis = self.etat.succes(job.compte, moteur=job.moteur)
        if depuis is not None:
            _journal(f"PREMIER SUCCÈS après épuisement {job.compte} {job.moteur} : {depuis / 3600:.1f} h")
        job.etat, job.etape, job.erreur, job.code = "fini", "", "", ""
        _journal(f"{job.id} {job.compte} {job.moteur} fini : {job.nom}")
        log.info("image générée (%s, compte %s) : %s", job.moteur, job.compte, job.nom)
        self.publish(job)
        self.publier_etat()  # compteurs et épuisements changés

    def _echec(self, job: Job, code: str, message: str, detail: str = "", capture: str = "") -> None:
        job.etat, job.code, job.erreur = "echec", code, message
        _journal(f"{job.id} {job.compte or '-'} {job.moteur or job.demande} échec {code} : {detail[:200]!r}"
                 f"{' capture=' + capture if capture else ''}")
        log.warning("image %s en échec (%s) : %s", job.id, code, detail or message)
        self.publish(job)
        self.publier_etat()

    # --- worker réel ---

    @staticmethod
    def commande() -> list[str] | None:
        node = _node()
        worker = config.IMAGES_WORKER_DIR / "worker.mjs"
        if not node or not worker.is_file() or not (config.IMAGES_WORKER_DIR / "node_modules" / "playwright-core").is_dir():
            return None
        return [node, str(worker)]

    def _lancer_worker(self, entree: dict, ligne: Callable[[dict], None]) -> dict:
        argv = self.commande()
        if argv is None:
            return {"t": "echec", "code": "absent", "message": "imagegen/node_modules absent"}
        # Délai dépassé ou « Stop tout » : le worker est arrêté AVEC son Chrome (avant : `proc.kill`
        # du seul worker, le Chrome détaché de Playwright pouvait rester).
        return ia_web.executer_worker(
            argv, entree, ligne, delai_s=float(entree.get("delai_s") or config.IMAGES_DELAI_S) + WORKER_MARGE_S,
            arreter_si=config.ESTOP_FILE.exists, nom="worker images")


def copie_hud(chemin: Path) -> str:
    """Copie de l'image dans runtime/attachments (servie au HUD par `/api/attachment/<file>`,
    5 Mo au plus) ; réduite en JPEG si elle est trop lourde. Rend le nom de la copie."""
    data = chemin.read_bytes()
    name = chemin.name
    if len(data) > 4 * 1024 * 1024:
        data, _media = attachments.shrink_image(data, max_bytes=1_500_000, max_side=2048)
        name = f"{chemin.stem}.jpg"
    return attachments.save_bytes(name, data).name


def _journal(texte: str) -> None:
    """`logs/images.log` : une ligne par évènement (mesure du reset réel des quotas)."""
    try:
        config.IMAGES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(config.IMAGES_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {texte}\n")
    except OSError:
        log.warning("journal des images illisible")


images = Images()


def _etats_publies() -> None:
    """Ouverture et fermeture d'une fenêtre de connexion : l'état des comptes du HUD suit (P40 A4),
    images ET vidéos (30/10 : un compte Google sert Gemini, Flow et Dreamina)."""
    images.publier_etat()
    from . import videos_ia
    videos_ia.videos.publier_etat()


def _session_vue(alias: str, ouverte: bool) -> None:
    """30/10 : fenêtre de connexion fermée, session lue dans le profil : « à reconnecter » suit
    aussitôt (avant : seule une génération réussie l'effaçait)."""
    images.etat.noter(alias, a_reconnecter=not ouverte)
    # Fenêtre de connexion fermée : l'utilisateur a pu se connecter aussi aux sites à connexion propre
    # (Dreamina) ; leur marque est levée, la prochaine génération dira si c'est vrai
    for service in ia_web.services("video"):
        if ia_web.connexion_propre(service):
            vu = ia_connexion.session_ouverte(ia_connexion.profil(alias), service)  # session du site, lue aussi
            images.etat.noter(alias, moteur=service, site_a_reconnecter=vu is False)
    _journal(f"connexion du compte {alias} : session {'ouverte' if ouverte else 'absente'} à la fermeture")


ia_connexion.on_change = _etats_publies
ia_connexion.on_session = _session_vue
