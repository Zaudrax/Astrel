"""Données sensibles d'une demande (04/11/2026, lot 3 du moteur local, docs/chantiers/HYBRIDE-QWEN.md).

`scan(texte, pieces)` dit, SANS modèle et toujours pareil pour le même texte, pourquoi une demande ne doit pas
partir chez Claude (Anthropic) sans l'accord de l'utilisateur : un secret (motifs du filtre de la mémoire), un IBAN
(clé vérifiée), un numéro de carte bancaire (Luhn), un numéro de sécurité sociale (clé vérifiée), un fichier
secret du garde-fou, ou un chemin dans une « zone confidentielle » (Réglages › Confidentialité). Il rend des
RAISONS courtes, jamais la valeur trouvée : elles vont dans la carte, le journal et `logs/routage.jsonl`.

Le cœur décide avec (`routeur.decider`) ; le garde-fou lit les mêmes zones (`guard.zones_confidentielles`) pour
ce que Claude lirait lui-même (Read, Grep, `cat`…).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

from .tools import memory_scan

# IBAN : 2 lettres, 2 chiffres de clé, 11 à 30 caractères (espaces permis par groupes de 4).
_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30})\b")
# Carte : 13 à 19 chiffres, groupés par espaces ou tirets.
_CARTE = re.compile(r"(?<![\d-])(\d(?:[ -]?\d){12,18})(?![\d-])")
# Sécurité sociale (France) : sexe, année, mois, département (2A/2B), commune, ordre, CLÉ (15 caractères).
_NIR = re.compile(r"(?<!\d)([12])[ .]?(\d{2})[ .]?(0[1-9]|1[0-2]|[2-9]\d)[ .]?(\d{2}|2[AB])[ .]?(\d{3})[ .]?(\d{3})"
                  r"[ .]?(\d{2})(?!\d)")
# Ce qui ressemble à un chemin dans le texte : absolu, ~/…, ./…, ../…
_CHEMIN = re.compile(r"(?:(?<=\s)|^|(?<=[\"'(«]))((?:~|\.{1,2})?/[^\s\"'`)»,;]+|[A-Za-z]:\\[^\s\"'`)»,;]+)")


def chemins(texte: str) -> list[str]:
    """Chemins écrits dans le texte (absolus, ~/…, ./…), tels quels. 05/11 : aussi pour `outils_local.dossiers_cites`."""
    return [m.group(1) for m in _CHEMIN.finditer(str(texte or ""))]


def _iban_valide(brut: str) -> bool:
    s = brut.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    r = s[4:] + s[:4]
    try:
        n = int("".join(str(int(c, 36)) for c in r))
    except ValueError:
        return False
    return n % 97 == 1


def _luhn(chiffres: str) -> bool:
    total, double = 0, False
    for c in reversed(chiffres):
        d = int(c) * (2 if double else 1)
        total += d - 9 if d > 9 else d
        double = not double
    return total % 10 == 0


def _carte_valide(brut: str) -> bool:
    s = re.sub(r"[ -]", "", brut)
    if not 13 <= len(s) <= 19 or len(set(s)) == 1 or s[0] not in "2345679":
        return False
    return _luhn(s)


def _nir_valide(m: re.Match) -> bool:
    corps = "".join(m.groups()[:6])
    cle = int(m.group(7))
    corps = corps.replace("2A", "19").replace("2B", "18")  # départements corses : règle officielle de la clé
    try:
        return 97 - int(corps) % 97 == cle
    except ValueError:
        return False


def zones() -> list[Path]:
    """Zones confidentielles (dossiers absolus), lues comme le garde-fou les lit."""
    from . import guard
    return guard.zones_confidentielles()


def _dedans(p: Path, racines: Iterable[Path]) -> bool:
    for r in racines:
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def _chemin_sensible(brut: str, racines_secret: list[Path], zs: list[Path]) -> str:
    """Raison si `brut` (chemin cité ou pièce jointe) désigne un secret du garde-fou ou une zone confidentielle."""
    try:
        p = Path(os.path.expanduser(brut.strip().strip("'\"«» "))).resolve()
    except (OSError, ValueError, RuntimeError):
        return ""
    if not p.is_absolute():
        return ""
    if _dedans(p, racines_secret):
        return "fichier secret d'Astrel"
    if _dedans(p, zs):
        return "chemin dans une zone confidentielle"
    return ""


# --- 05/11 (essais réels de l'utilisateur) : faits EXACTS donnés au moteur LOCAL seulement ---------------------
# Qwen a dit « suspect » l'IBAN d'exemple, valide (« le 189 est trop long »). Le cœur fait le calcul et l'ajoute
# au message envoyé au LOCAL (`local_tour`), jamais à Claude ; le numéro n'y est pas recopié en entier.
# Longueur officielle de l'IBAN des pays les plus courants (registre SWIFT) : une clé fausse n'est signalée que
# si la longueur est celle du pays (sinon ce n'est sans doute pas un IBAN).
IBAN_LONGUEUR = {"FR": 27, "MC": 27, "DE": 22, "BE": 16, "ES": 24, "IT": 27, "CH": 21, "LU": 20, "NL": 18,
                 "GB": 22, "PT": 25, "IE": 22, "AT": 20, "PL": 28, "SE": 24, "DK": 18, "FI": 18, "NO": 15}
_RIB_LETTRES = {c: str(v) for v, lettres in enumerate(("", "AJ", "BKS", "CLT", "DMU", "ENV", "FOW", "GPX",
                                                        "HQY", "IRZ")) for c in lettres}
_FAITS = {
    "fr": {"tete": "Contrôle d'Astrel :", "iban_ok": "IBAN {m} : clé de contrôle valide (calcul mod 97)",
           "longueur": ", longueur conforme pour {pays} ({n} caractères)",
           "rib_ok": ", clé RIB {cle} valide", "rib_ko": ", clé RIB {cle} INVALIDE",
           "iban_ko": "IBAN {m} : clé de contrôle INVALIDE (calcul mod 97) : faute de frappe probable",
           "carte": "numéro de carte {m} : clé de Luhn valide",
           "nir": "numéro de sécurité sociale {m} : clé valide",
           "existe": "Ces calculs ne disent pas si le compte, la carte ou la personne existent."},
    "en": {"tete": "Astrel check:", "iban_ok": "IBAN {m}: valid check digits (mod 97 computation)",
           "longueur": ", correct length for {pays} ({n} characters)",
           "rib_ok": ", French RIB key {cle} valid", "rib_ko": ", French RIB key {cle} INVALID",
           "iban_ko": "IBAN {m}: INVALID check digits (mod 97 computation): probably a typo",
           "carte": "card number {m}: valid Luhn check digit",
           "nir": "French social security number {m}: valid key",
           "existe": "These computations do not tell whether the account, card or person exists."},
}


def _cle_rib(bban: str) -> tuple[str, bool] | None:
    """Clé RIB d'un BBAN français (banque 5, guichet 5, compte 11, clé 2) : (clé lue, valide ?)."""
    if len(bban) != 23:
        return None
    try:
        conv = "".join(_RIB_LETTRES.get(c, c) for c in bban.upper())
        banque, guichet, compte, cle = int(conv[:5]), int(conv[5:10]), int(conv[10:21]), int(conv[21:23])
    except ValueError:
        return None
    return bban[21:23], 97 - (89 * banque + 15 * guichet + 3 * compte) % 97 == cle


def faits(texte: str, langue: str = "fr") -> str:
    """Ligne « [Contrôle d'Astrel : …] » des numéros reconnus (IBAN, carte, NIR), "" si aucun. Pour le moteur
    LOCAL seulement : le numéro est masqué (début et fin), le modèle a la demande entière à côté."""
    t = _FAITS.get(langue, _FAITS["fr"])
    haut, out, pris = str(texte or "").upper(), [], []
    for m in _IBAN.finditer(haut):
        s = m.group(1).replace(" ", "")
        pays, attendu = s[:2], IBAN_LONGUEUR.get(s[:2])
        masque = f"{s[:4]}…{s[-3:]}"
        if _iban_valide(m.group(1)):
            f = t["iban_ok"].format(m=masque)
            if attendu == len(s):
                f += t["longueur"].format(pays=pays, n=attendu)
            rib = _cle_rib(s[4:]) if pays in ("FR", "MC") else None
            if rib:
                f += t["rib_ok" if rib[1] else "rib_ko"].format(cle=rib[0])
        elif attendu == len(s):
            f = t["iban_ko"].format(m=masque)
        else:
            continue
        out.append(f)
        pris.append(m.span(1))
    for m in _CARTE.finditer(haut):
        if any(a <= m.start(1) < b for a, b in pris):
            continue  # les chiffres d'un IBAN ne sont pas une carte
        s = re.sub(r"[ -]", "", m.group(1))
        if _carte_valide(m.group(1)):
            out.append(t["carte"].format(m=f"…{s[-4:]}"))
            pris.append(m.span(1))
    for m in _NIR.finditer(haut):
        if not any(a <= m.start() < b for a, b in pris) and _nir_valide(m):
            out.append(t["nir"].format(m=f"{m.group(1)} {m.group(2)}…"))
    if not out:
        return ""
    return f"[{t['tete']} " + " ; ".join(out) + ". " + t["existe"] + "]"


def scan(texte: str, pieces: Iterable[str] = (), zones_conf: list[Path] | None = None) -> list[str]:
    """Raisons (sans doublon, dans l'ordre) qui rendent la demande sensible ; [] si rien.
    `pieces` : chemins des pièces jointes (ou leurs noms, alors seul le texte qu'elles apportent compte)."""
    texte = str(texte or "")
    raisons: list[str] = []

    def ajoute(r: str) -> None:
        if r and r not in raisons:
            raisons.append(r)

    for nature in memory_scan.find_secrets(texte):
        ajoute(nature)
    for m in _IBAN.finditer(texte.upper()):
        if _iban_valide(m.group(1)):
            ajoute("IBAN")
            break
    for m in _CARTE.finditer(texte):
        if _carte_valide(m.group(1)):
            ajoute("numéro de carte bancaire")
            break
    for m in _NIR.finditer(texte.upper()):
        if _nir_valide(m):
            ajoute("numéro de sécurité sociale")
            break
    try:
        from . import guard
        secrets = list(guard.SECRET_PATHS)
    except Exception:  # garde-fou illisible : on ne voit pas les secrets, la suite du scan tient
        secrets = []
    zs = zones() if zones_conf is None else list(zones_conf)
    for m in _CHEMIN.finditer(texte):
        ajoute(_chemin_sensible(m.group(1), secrets, zs))
    for piece in pieces:
        r = _chemin_sensible(str(piece), secrets, zs)
        if r:
            ajoute(f"pièce jointe : {r}")
    return raisons
