"""Capacité de pentest (09/11/2026, branche cyber-local).

L'utilisateur dit ou tape « pentest <cible> », « teste <cible> » ou « scan <cible> » (voix ou HUD) ;
Astrel lance un test de sécurité contre la cible, DANS SON PÉRIMÈTRE AUTORISÉ, en réutilisant
l'outillage déjà construit hors du dépôt (`cyberlab/`), jamais réécrit ici :

- `cyberlab/pentest.sh <cible> [--outils]` : le lanceur. Il vérifie LUI AUSSI le périmètre
  (`cyberlab/perimetre.txt`) puis lance un pentest agentique Qwen. Mode HTTP (défaut, depuis
  l'hôte, playbook) ou `--outils` (nmap/sqlmap dans le conteneur ISOLÉ `cyberlab-toolbox`, jamais
  sur l'hôte) ; il écrit un JSON de résultat dans `cyberlab/resultats/` et imprime une ligne JSON
  résumé sur stdout.
- `cyberlab/perimetre.txt` : le périmètre autorisé (une cible par ligne : IP, CIDR, hôte ou URL).
  Une cible absente = refus net, rien n'est lancé (protège les tiers).

Ce module ne contient QUE la logique pure (lecture du périmètre, lecture d'une cible, motifs
vocaux, lecture du résultat) ; le mixin du cœur est dans `astrel/coeur/cyber.py`. Le périmètre et
le lanceur restent la source de vérité unique : on les réutilise, on ne les recopie pas.

INVARIANTS (voir aussi `docs/CLAUDE-DETAIL.md` § Cybersécurité autorisée) :
- la cible est TOUJOURS vérifiée contre le périmètre AVANT tout lancement (ici ET dans le lanceur) ;
- les outils offensifs ne tournent que dans le conteneur isolé (géré par le lanceur, jamais ici) ;
- la commande est refusée à Claude (le cerveau ne lance jamais de pentest) : voir le mixin.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.parse
from pathlib import Path

# Dossier de l'outillage hors dépôt (chemin absolu, réglable pour les tests et le portage).
CYBERLAB_DEFAUT = "~/Projets/Studio/cyberlab"


def dossier() -> Path:
    """Le dossier `cyberlab/` (réglage `ASTREL_CYBERLAB`, défaut `CYBERLAB_DEFAUT`)."""
    return Path(os.environ.get("ASTREL_CYBERLAB") or CYBERLAB_DEFAUT)


def lanceur() -> Path:
    """Le script `pentest.sh` qui vérifie le périmètre puis lance le pentest."""
    return dossier() / "pentest.sh"


def fichier_perimetre() -> Path:
    """Le fichier du périmètre autorisé (une cible par ligne)."""
    return dossier() / "perimetre.txt"


def dossier_resultats() -> Path:
    return dossier() / "resultats"


def fichier_resultat(outils: bool = False, modele: str = "qwen3.5:9b") -> Path:
    """Le JSON de résultat écrit par le lanceur (nommé d'après le modèle ; `shell_` en mode outils,
    `run_` en mode HTTP), pour le joindre au compte rendu."""
    nom = str(modele).replace("/", "_").replace(":", "_")
    return dossier_resultats() / f"{'shell_' if outils else 'run_'}{nom}.json"


def lire_resume(sortie: str) -> dict | None:
    """Le résumé JSON que le lanceur imprime sur stdout (dernière ligne qui est un objet JSON avec
    un `score`) : {modele, score, sur, flags, duree_s, …}. None si illisible."""
    for ligne in reversed(str(sortie or "").splitlines()):
        ligne = ligne.strip()
        if not ligne.startswith("{"):
            continue
        try:
            d = json.loads(ligne)
        except ValueError:
            continue
        if isinstance(d, dict) and "score" in d:
            return d
    return None


def hote(cible: str) -> str:
    """L'hôte d'une cible (IP ou URL), comme le fait `pentest.sh` : une cible sans schéma est
    préfixée par `http://`, puis `urlparse().hostname`. "" si illisible."""
    c = str(cible or "").strip()
    if not c:
        return ""
    if "://" not in c:
        c = "http://" + c
    try:
        return urllib.parse.urlparse(c).hostname or ""
    except ValueError:
        return ""


def _lignes_perimetre(fichier: Path | None = None) -> list[str]:
    """Les entrées du périmètre, commentaires (`#`) et lignes vides retirés."""
    fichier = fichier or fichier_perimetre()
    try:
        brut = fichier.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[str] = []
    for ligne in brut.splitlines():
        entree = ligne.split("#", 1)[0].strip()
        if entree:
            out.append(entree)
    return out


def dans_perimetre(host: str, fichier: Path | None = None) -> bool:
    """Vrai si `host` (un hôte ou une IP) est autorisé par le périmètre. MÊME logique que
    `pentest.sh` : égalité d'hôte (insensible à la casse), ou IP contenue dans un CIDR."""
    host = str(host or "").strip()
    if not host:
        return False
    for entree in _lignes_perimetre(fichier):
        if entree.lower() == host.lower():
            return True
        if "/" in entree:
            try:
                if ipaddress.ip_address(host) in ipaddress.ip_network(entree, strict=False):
                    return True
            except ValueError:
                pass
    return False


def verifier_cible(cible: str, fichier: Path | None = None) -> tuple[str, bool]:
    """(hôte, autorisée) pour une cible (IP ou URL). `hôte` vide = cible illisible."""
    host = hote(cible)
    if not host:
        return "", False
    return host, dans_perimetre(host, fichier)


# --- motifs vocaux « pentest <cible> », « teste <cible> », « scan <cible> » (FR et EN)
# La cible (IP ou URL) est capturée TELLE QUELLE (pas de pliage : une URL garde sa casse et son
# chemin). Le mot clé et l'éventuel « avec outils » sont, eux, lus sans casse ni accents.

_VOCATIF = re.compile(r"^\s*(?:(?:hey|ok|dis)\s+)?(?:astrel|jarvis)\s*[,:!.]?\s*", re.IGNORECASE)
# Mot clé : pentest / pen test, scan, teste / test (FR « teste », EN « test »).
_MOT = r"(?:pentest|pen[\s-]?test|scan|testes?|test)"
# Tournure facultative devant la cible : « la cible », « le site », « the target », « l'ip »…
_CIBLE_MOT = (r"(?:(?:la|le|l['’]|sur|de|du|des|the|a|an|my|mon|ma)\s*)*"
              r"(?:cible|target|site|machine|serveur|server|h[oô]te|host|ip|url|adresse)\s+")
# Fin facultative qui demande les VRAIS outils (nmap/sqlmap, conteneur isolé).
_OUTILS = (r"\s+(?:avec(?:\s+les)?(?:\s+vrais)?\s+outils|en\s+outils|with(?:\s+the)?\s+tools"
           r"|using\s+tools|--?outils|--?tools)")
_PENTEST = re.compile(
    r"^" + _MOT + r"\s+(?:" + _CIBLE_MOT + r")?(?P<cible>[^\s]+?)(?:" + _OUTILS + r")?\s*[.!?…]*$",
    re.IGNORECASE)

# Une cible plausible : une IPv4/IPv6, une URL http(s), ou un nom d'hôte pointé (au moins un point)
# ou « localhost ». Sert à ne PAS intercepter une phrase ordinaire (« teste cette fonction »).
_HOTE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def est_cible(mot: str) -> bool:
    """Le mot ressemble-t-il à une cible (IP, URL, ou nom d'hôte pointé) ? Évite les faux
    déclenchements sur une phrase courante."""
    m = str(mot or "").strip().rstrip(".,;:!?…")
    if not m:
        return False
    if "://" in m:
        return bool(hote(m))
    try:
        ipaddress.ip_address(m)
        return True
    except ValueError:
        pass
    if m.lower() == "localhost":
        return True
    return "." in m and _HOTE_RE.match(m) is not None


def commande(text: str) -> tuple[str, bool] | None:
    """Lit « pentest/teste/scan <cible> [avec outils] » dans une phrase (voix ou chat).
    Rend (cible, outils) si la cible ressemble à une IP ou une URL, sinon None (la phrase
    repart vers Claude). Le vocatif « Astrel, … » est retiré d'abord. La cible garde sa casse
    d'origine (l'URL n'est pas pliée) : seul le mot clé est reconnu sans casse ni accents."""
    # Vocatif retiré sur le texte d'origine (regex insensible à la casse) : la casse de l'URL reste.
    sans_vocatif = _VOCATIF.sub("", str(text or "").strip(), count=1).strip()
    m = _PENTEST.match(sans_vocatif)
    if not m:
        return None
    cible = m.group("cible").strip().rstrip(".,;:!?…")
    if not est_cible(cible):
        return None
    outils = bool(re.search(_OUTILS + r"\s*[.!?…]*$", sans_vocatif, re.IGNORECASE))
    return cible, outils
