"""Commandes AUTORISÉES du moteur local (05/11/2026, lot 5, docs/chantiers/HYBRIDE-QWEN.md).

Une liste blanche écrite par l'utilisateur SEUL : `config/commandes-locales.json`, protégée par le garde-fou
(l'écrire = action sur le garde-fou, `guard.PROTECTED_PATHS`), éditée depuis Réglages › Modèle local
(`settings_action` du HUD, refusée à Claude). Absente = liste vide (défaut). Exemples :
`config/commandes-locales.example.json`.

    {"commandes": {"<id>": {"argv": ["pytest", "-q"], "dossier": "/chemin/absolu/dans/WORKDIR",
                            "delai_s": 120, "sortie_max": 20000, "description": "…"}}}

Le serveur MCP du CLI local (`mcp_local.py`) ne lance QUE l'argv d'un id de cette liste : jamais de texte
libre, jamais d'argument ajouté par le modèle, jamais de shell. Environnement nettoyé (aucune variable
Claude, Anthropic, clé ni jeton), délai, sortie bornée, tout le groupe de process tué à la fin
(`platform.systeme.tuer_arbre`). Le hook juge aussi l'appel (`guard._refus_outil_local`)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

FICHIER = config.CONFIG_DIR / "commandes-locales.json"
EXEMPLE = config.CONFIG_DIR / "commandes-locales.example.json"
ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")  # même motif que `guard._ID_COMMANDE_LOCALE`
MAX_COMMANDES = 40
ARGV_MAX, MOT_MAX = 32, 400
DELAI_DEFAUT, DELAI_MAX = 60.0, 600.0
SORTIE_DEFAUT, SORTIE_MIN, SORTIE_MAX = 16_000, 1_000, 200_000
DESCRIPTION_MAX = 200
TEXTE_MAX = 64_000  # la liste en JSON, telle que tapée dans le HUD
CLES = ("argv", "dossier", "delai_s", "sortie_max", "description")
# Variables gardées pour la commande (rien qui identifie Claude, aucune clé) ; le reste est retiré.
_ENV_GARDE = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TZ", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
              "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC", "TEMP", "TMP", "USERPROFILE")

# Textes d'écran (Réglages, erreurs rendues au HUD) : traduits par astrel/locales/en.json.
E_JSON = "liste illisible : JSON attendu ({detail})"
E_FORME = "liste illisible : un objet « commandes » (identifiant → argv, dossier…) est attendu"
E_TROP = "trop de commandes ({n}, {max} au plus)"
E_ID = "identifiant « {id} » refusé : minuscules, chiffres, « - », « _ » ou « . », 40 caractères au plus"
E_CLE = "commande « {id} » : clé inconnue « {cle} » (argv, dossier, delai_s, sortie_max, description)"
E_ARGV = "commande « {id} » : argv doit être une LISTE de 1 à {max} mots (jamais une ligne de shell)"
E_MOT = "commande « {id} » : un mot d'argv vide, trop long ou illisible"
E_DOSSIER = "commande « {id} » : dossier absolu attendu"
E_HORS = "commande « {id} » : dossier hors de tes projets ({racine})"
E_ABSENT = "commande « {id} » : dossier introuvable ({dossier})"
E_DELAI = "commande « {id} » : delai_s entre 1 et {max} secondes"
E_SORTIE = "commande « {id} » : sortie_max entre {min} et {max} caractères"
E_DESC = "commande « {id} » : description de {max} caractères au plus"
E_INCONNUE = "commande inconnue : « {id} » n'est pas dans la liste de l'utilisateur"
E_PROGRAMME = "programme introuvable : {prog}"
E_HUD = "la liste des commandes se modifie depuis les Réglages du HUD seulement"


class ListeInvalide(ValueError):
    """La liste (ou une commande) ne respecte pas le format : rien n'est lancé ni écrit."""


@dataclass(frozen=True)
class Commande:
    id: str
    argv: tuple[str, ...]
    dossier: Path
    delai_s: float
    sortie_max: int
    description: str

    def resume(self) -> dict:
        return {"id": self.id, "description": self.description, "argv": list(self.argv),
                "dossier": str(self.dossier), "delai_s": self.delai_s, "sortie_max": self.sortie_max}


def _dans(p: Path, racines: list[Path]) -> bool:
    return any(p == r or r in p.parents for r in racines)


def valider(data: object, racines: list[Path] | None = None, verifier_dossier: bool = True) -> dict[str, Commande]:
    """Commandes valides de `data` (le JSON lu) ; lève `ListeInvalide` à la première faute. `racines` : où un
    dossier peut être (défaut : `config.WORKDIR`) ; `verifier_dossier` : il doit exister."""
    if not isinstance(data, dict):
        raise ListeInvalide(E_FORME)
    inconnues = [k for k in data if k != "commandes" and not str(k).startswith("_")]
    brut = data.get("commandes", {})
    if inconnues or not isinstance(brut, dict):
        raise ListeInvalide(E_FORME)
    if len(brut) > MAX_COMMANDES:
        raise ListeInvalide(E_TROP.format(n=len(brut), max=MAX_COMMANDES))
    racines = [Path(r).resolve() for r in (racines if racines is not None else [config.WORKDIR])]
    out: dict[str, Commande] = {}
    for cid, e in brut.items():
        if not isinstance(cid, str) or not ID.match(cid):
            raise ListeInvalide(E_ID.format(id=str(cid)[:60]))
        if not isinstance(e, dict):
            raise ListeInvalide(E_ARGV.format(id=cid, max=ARGV_MAX))
        for cle in e:
            if cle not in CLES:
                raise ListeInvalide(E_CLE.format(id=cid, cle=str(cle)[:40]))
        argv = e.get("argv")
        if not isinstance(argv, list) or not 1 <= len(argv) <= ARGV_MAX:
            raise ListeInvalide(E_ARGV.format(id=cid, max=ARGV_MAX))
        if not all(isinstance(m, str) and m and len(m) <= MOT_MAX and "\0" not in m for m in argv):
            raise ListeInvalide(E_MOT.format(id=cid))
        d = e.get("dossier")
        if not isinstance(d, str) or not os.path.isabs(os.path.expanduser(d)):
            raise ListeInvalide(E_DOSSIER.format(id=cid))
        dossier = Path(os.path.expanduser(d)).resolve()
        if not _dans(dossier, racines):
            raise ListeInvalide(E_HORS.format(id=cid, racine=", ".join(str(r) for r in racines)))
        if verifier_dossier and not dossier.is_dir():
            raise ListeInvalide(E_ABSENT.format(id=cid, dossier=dossier))
        delai = e.get("delai_s", DELAI_DEFAUT)
        if isinstance(delai, bool) or not isinstance(delai, (int, float)) or not 1 <= delai <= DELAI_MAX:
            raise ListeInvalide(E_DELAI.format(id=cid, max=int(DELAI_MAX)))
        sortie = e.get("sortie_max", SORTIE_DEFAUT)
        if isinstance(sortie, bool) or not isinstance(sortie, int) or not SORTIE_MIN <= sortie <= SORTIE_MAX:
            raise ListeInvalide(E_SORTIE.format(id=cid, min=SORTIE_MIN, max=SORTIE_MAX))
        desc = e.get("description", "")
        if not isinstance(desc, str) or len(desc) > DESCRIPTION_MAX:
            raise ListeInvalide(E_DESC.format(id=cid, max=DESCRIPTION_MAX))
        out[cid] = Commande(cid, tuple(argv), dossier, float(delai), int(sortie), " ".join(desc.split()))
    return out


def charger(fichier: Path | None = None, racines: list[Path] | None = None) -> dict[str, Commande]:
    """La liste de l'utilisateur ; absente = vide. Illisible ou fautive : `ListeInvalide` (rien ne se lance)."""
    fichier = Path(fichier or FICHIER)
    try:
        texte = fichier.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ListeInvalide(E_JSON.format(detail=exc.strerror or type(exc).__name__)) from exc
    try:
        data = json.loads(texte)
    except ValueError as exc:
        raise ListeInvalide(E_JSON.format(detail=str(exc)[:120])) from exc
    return valider(data, racines)


def ecrire(texte: str, fichier: Path | None = None) -> dict[str, Commande]:
    """Valide puis écrit la liste (atomique, 0600). Appelé par le CŒUR (Réglages du HUD), jamais par Claude :
    le fichier est protégé par le garde-fou. Lève `ListeInvalide` (rien n'est écrit)."""
    fichier = Path(fichier or FICHIER)
    texte = str(texte or "").strip() or '{"commandes": {}}'
    if len(texte) > TEXTE_MAX:
        raise ListeInvalide(E_JSON.format(detail=f"{len(texte)} caractères, {TEXTE_MAX} au plus"))
    try:
        data = json.loads(texte)
    except ValueError as exc:
        raise ListeInvalide(E_JSON.format(detail=str(exc)[:120])) from exc
    cmds = valider(data)
    propre = {"commandes": {c.id: {"argv": list(c.argv), "dossier": str(c.dossier), "delai_s": c.delai_s,
                                   "sortie_max": c.sortie_max, "description": c.description}
                            for c in cmds.values()}}
    fichier.parent.mkdir(parents=True, exist_ok=True)
    tmp = fichier.with_name(f".{fichier.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(propre, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, fichier)
    return cmds


# ------------------------------------------------------------------ exécution

def environnement(base: dict | None = None) -> dict[str, str]:
    """Environnement NETTOYÉ de la commande : quelques variables du système, aucune de Claude ni d'Astrel."""
    base = dict(os.environ if base is None else base)
    env = {k: base[k] for k in _ENV_GARDE if base.get(k)}
    env.setdefault("PATH", os.defpath)
    env.update(LC_ALL="C.UTF-8" if os.name != "nt" else "", TERM="dumb", NO_COLOR="1", PYTHONUNBUFFERED="1")
    return {k: v for k, v in env.items() if v}


def _programme(argv0: str, dossier: Path, env: dict) -> str:
    if os.sep not in argv0 and (os.altsep is None or os.altsep not in argv0):
        trouve = shutil.which(argv0, path=env.get("PATH"))
        if not trouve:
            raise ListeInvalide(E_PROGRAMME.format(prog=argv0))
        return trouve
    p = Path(os.path.expanduser(argv0))
    p = p if p.is_absolute() else dossier / p
    if not p.is_file():
        raise ListeInvalide(E_PROGRAMME.format(prog=argv0))
    return str(p)


def executer(cmd: Commande, racines: list[Path] | None = None, stop: threading.Event | None = None) -> dict:
    """Lance l'argv de `cmd` (sans shell) dans son dossier, rejugé ici ; rend le code de sortie et la sortie
    (stdout et stderr mêlés), BORNÉE à `sortie_max` caractères ; au-delà du délai, tout le groupe est tué."""
    from .platform import enfants, systeme

    racines = [Path(r).resolve() for r in (racines if racines is not None else [config.WORKDIR])]
    if not _dans(cmd.dossier.resolve(), racines) or not cmd.dossier.is_dir():
        raise ListeInvalide(E_HORS.format(id=cmd.id, racine=", ".join(str(r) for r in racines)))
    env = environnement()
    argv = [_programme(cmd.argv[0], cmd.dossier, env), *cmd.argv[1:]]
    t0 = time.monotonic()
    p = enfants.popen(argv, cwd=str(cmd.dossier), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, start_new_session=systeme.UNIX)
    gardes: list[bytes] = []
    vu = {"n": 0, "total": 0}
    limite = cmd.sortie_max * 4  # octets lus au plus pour `sortie_max` caractères (UTF-8 : 4 octets au pire)

    def lire() -> None:
        while True:
            try:
                bloc = p.stdout.read1(65536) if hasattr(p.stdout, "read1") else p.stdout.read(65536)
            except (OSError, ValueError):
                return
            if not bloc:
                return
            vu["total"] += len(bloc)
            if vu["n"] < limite:
                garde = bloc[:limite - vu["n"]]
                gardes.append(garde)
                vu["n"] += len(garde)

    lecteur = threading.Thread(target=lire, name=f"commande-{cmd.id}", daemon=True)
    lecteur.start()
    expiree = arretee = False
    fin = t0 + cmd.delai_s
    while p.poll() is None:
        if stop is not None and stop.is_set():
            arretee = True
            break
        if time.monotonic() >= fin:
            expiree = True
            break
        time.sleep(0.05)
    # Fini, expiré ou arrêté : tout le groupe part (un enfant resté en fond ne survit pas à la commande).
    systeme.tuer_arbre(p.pid, systeme.SIGKILL)
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    lecteur.join(timeout=2)
    texte = b"".join(gardes).decode("utf-8", errors="replace")
    tronquee = len(texte) > cmd.sortie_max or vu["total"] > vu["n"]
    texte = texte[:cmd.sortie_max]
    return {"id": cmd.id, "code": None if (expiree or arretee) else p.returncode, "sortie": texte,
            "tronquee": tronquee, "expiree": expiree, "arretee": arretee,
            "duree_s": round(time.monotonic() - t0, 2)}


def lancer(cid: str, fichier: Path | None = None, racines: list[Path] | None = None) -> dict:
    """La commande `cid` de la liste (relue à chaque appel) ; inconnue : `ListeInvalide`."""
    cmds = charger(fichier, racines)
    if not isinstance(cid, str) or cid not in cmds:
        raise ListeInvalide(E_INCONNUE.format(id=str(cid)[:60]))
    return executer(cmds[cid], racines)


# ------------------------------------------------------------------ Réglages › Modèle local (HUD)

L_TITRE = "Commandes autorisées"  # aide de la ligne : `settings.ITEMS["local.commandes"]`
L_VIDE = "Aucune commande : le modèle local ne peut rien lancer."
L_FAUTE = "Liste illisible, aucune commande ne se lance : {erreur}"
L_LIGNE = "{argv} · dans {dossier} · {delai} s au plus"
L_MODIFIER = "Modifier la liste"
L_CHAMP = "Liste (JSON, voir config/commandes-locales.example.json)"
L_VIDER = "Vider la liste"
L_VIDER_CONFIRME = "Le modèle local ne pourra plus lancer aucune commande."
L_ECRITE = "Liste enregistrée : {n} commande(s)."
L_VIDEE = "Liste vidée : le modèle local ne lance plus rien."


def _texte_actuel() -> str:
    try:
        return FICHIER.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _gabarit() -> str:
    return json.dumps({"commandes": {}}, ensure_ascii=False, indent=1)


def view() -> dict:
    """Ligne « Commandes autorisées » des Réglages : la liste, puis Modifier / Vider."""
    lignes = []
    try:
        cmds = charger()
    except ListeInvalide as exc:
        lignes.append({"label": L_TITRE, "text": L_FAUTE.format(erreur=str(exc)), "level": "error"})
        cmds = {}
    else:
        if not cmds:
            lignes.append({"label": L_TITRE, "text": L_VIDE, "level": "info"})
    for c in cmds.values():
        lignes.append({"label": c.id, "level": "ok",
                       "text": (c.description + " — " if c.description else "")
                       + L_LIGNE.format(argv=" ".join(c.argv), dossier=c.dossier, delai=int(c.delai_s))})
    texte = _texte_actuel()
    return {"value": {"lines": lignes, **({"block": texte} if texte else {})},
            "actions": [{"id": "modifier", "label": L_MODIFIER,
                         "input": {"label": L_CHAMP, "multiline": True, "value": texte or _gabarit()}},
                        {"id": "vider", "label": L_VIDER, "danger": True, "confirm": L_VIDER_CONFIRME,
                         **({"disabled": True} if not cmds and not texte else {})}]}


def run(action: str, cmd: dict) -> dict:
    """Boutons de la ligne : HUD de l'utilisateur seulement (le cœur refuse déjà `settings_action` à Claude)."""
    if cmd.get("_via") != "hud" or cmd.get("_from_claude") is True:
        return {"ok": False, "error": E_HUD}
    try:
        if action == "modifier":
            cmds = ecrire(str(cmd.get("value") or ""))
            return {"ok": True, "text": L_ECRITE.format(n=len(cmds))}
        if action == "vider":
            ecrire('{"commandes": {}}')
            return {"ok": True, "text": L_VIDEE}
    except ListeInvalide as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "action inconnue"}
