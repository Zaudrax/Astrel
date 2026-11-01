"""Écrans branchés au PC, pour regarder le BON écran (plan P24, demande du 21/10).

Avec deux écrans ou plus, la capture prenait le premier flux venu (le portable) sans que Claude
sache qu'un autre écran existait. Ce module dit quels écrans sont branchés et lequel montrer ;
la capture elle-même reste dans `platform/linux_capture.py` (portail de partage d'écran).

- `lister()` : écrans de GNOME, lus par `org.gnome.Mutter.DisplayConfig.GetCurrentState`
  (D-Bus synchrone, sans boucle GLib, lecture seule). Liste vide hors Linux, hors GNOME ou en
  panne : la capture fait alors comme avant (un seul écran, sans légende précise).
- Chaque écran est un dict {id, nom, w, h, x, y, principal, integre, echelle, pw, ph} :
  `id` = connecteur (« HDMI-1 »), `w`/`h`/`x`/`y` = taille et position LOGIQUES (celles du
  bureau de GNOME, échelle appliquée), `pw`/`ph` = pixels réels du mode en cours.
- `choisir()` : un nom donné par Claude ou par le réglage (« HDMI-1 », « LG », « portable »,
  « principal », « tous », « hdmi », « portable et LG », « en haut »…) → écrans à montrer, avec une remarque si l'écran
  demandé n'est pas là (repli sur le principal, plan P24 « branchement à chaud »).
- `associer_flux()` : quel flux du portail montre quel écran (position, sinon taille, sinon
  pixels de l'image). À l'échelle 1,5 le portail peut rendre des pixels réels alors que Mutter
  donne la taille logique : les deux sont essayés.
- `legende()` : la phrase rendue avec chaque image (« Écran vu : … Autre écran : … »).

Fonctions pures sauf `lister()` et `ecran_par_defaut()` : testées sur la disposition mesurée
le 22/10 (tests/test_ecrans.py).
"""
from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
from typing import Any, NamedTuple

log = logging.getLogger("astrel.ecrans")

MUTTER = "org.gnome.Mutter.DisplayConfig"
MUTTER_PATH = "/org/gnome/Mutter/DisplayConfig"

# Réglage « Écran regardé par défaut » (plan P24, lot 2, astrel/settings.py) : « auto » ou vide =
# écran principal de GNOME, sinon un nom accepté par `choisir`. Variable d'environnement prioritaire.
REGLAGE = "ecran_defaut"
ENV = "ASTREL_ECRAN"

_MOTS_TOUS = {"tous", "tout", "toutes", "all", "both", "deux", "lesdeux", "chacun"}
_MOTS_DEFAUT = {"", "auto", "defaut", "pardefaut", "default"}
_MOTS_PRINCIPAL = {"principal", "primaire", "primary", "main"}
_MOTS_INTEGRE = {"portable", "integre", "laptop", "builtin", "interne", "internal", "pcportable"}
_MOTS_EXTERNE = {"externe", "external", "exterieur", "secondaire", "second", "deuxieme"}
_MOTS_POSITION = {"haut": "en haut", "bas": "en bas", "gauche": "à gauche", "droite": "à droite"}
# Séparateurs de plusieurs écrans cités (« le portable et le LG », « HDMI-1, eDP-1 »).
_ET = re.compile(r"\s*(?:[,;+&]|\bet\b|\band\b)\s*", re.IGNORECASE)
# Mots qui ne désignent pas un écran (« l'écran LG » = « LG »).
_VIDES = {"l", "le", "la", "les", "de", "du", "des", "d", "en", "a", "au", "sur", "mon", "ma", "the", "ecran",
          "ecrans", "screen", "screens", "monitor", "moniteur", "display", "affichage", "celui", "un", "une"}


class Choix(NamedTuple):
    cibles: list[dict]       # écrans à montrer (un seul, ou tous)
    tous: bool               # « tous » ou plusieurs écrans cités : images assemblées comme sur le bureau
    remarques: list[str]     # repli, ambiguïté : à dire dans la légende


# ------------------------------------------------------------------ lecture de l'état de GNOME

def lister(timeout_ms: int = 2000) -> list[dict]:
    """Écrans actifs de GNOME, du haut à gauche au bas à droite. [] si illisible."""
    if not sys.platform.startswith("linux"):
        return []
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(MUTTER, MUTTER_PATH, MUTTER, "GetCurrentState", None, None,
                              Gio.DBusCallFlags.NONE, timeout_ms, None)
        return depuis_etat(reply.unpack())
    except Exception as exc:  # pas de GNOME, pas de session D-Bus, Mutter muet
        log.warning("liste des écrans illisible : %s", exc)
        return []


def _nom(connecteur: str, produit: str, nom_gnome: str, integre: bool) -> str:
    if integre:
        return nom_gnome or "Écran intégré"
    if produit and produit.lower() not in ("unknown", "inconnu") and not re.fullmatch(r"0x[0-9a-fA-F]+", produit):
        return produit
    return nom_gnome or connecteur


def depuis_etat(etat: Any) -> list[dict]:
    """Réponse de GetCurrentState (déballée) → liste d'écrans. Écrans éteints ignorés.

    Format D-Bus : (serial, monitors a((ssss)a(siiddada{sv})a{sv}),
    logical_monitors a(iiduba(ssss)a{sv}), properties a{sv}). En « layout-mode » 1 (logique, le
    défaut de GNOME), position et taille du bureau sont les pixels divisés par l'échelle."""
    _serial, moniteurs, logiques, proprietes = etat
    logique = int((proprietes or {}).get("layout-mode", 1)) != 2
    infos: dict[str, dict] = {}
    for spec, modes, props in moniteurs:
        connecteur, _vendeur, produit, _serie = spec
        props = props or {}
        courant = next((m for m in modes if (m[6] or {}).get("is-current")), None)
        integre = bool(props.get("is-builtin", False))
        infos[connecteur] = {
            "nom": _nom(connecteur, produit, str(props.get("display-name") or ""), integre),
            "integre": integre,
            "pw": int(courant[1]) if courant else 0,
            "ph": int(courant[2]) if courant else 0,
        }
    out = []
    for x, y, echelle, transformation, principal, specs, _props in logiques:
        ids = [s[0] for s in specs if s[0] in infos]
        if not ids:
            continue
        info = infos[ids[0]]
        pw, ph = info["pw"], info["ph"]
        if int(transformation) % 2 == 1:  # 90° ou 270° (retourné ou non) : largeur et hauteur échangées
            pw, ph = ph, pw
        echelle = float(echelle) or 1.0
        w, h = (round(pw / echelle), round(ph / echelle)) if logique else (pw, ph)
        noms = [infos[i]["nom"] for i in ids]
        out.append({
            "id": ids[0], "nom": " / ".join(dict.fromkeys(noms)), "w": w, "h": h, "x": int(x), "y": int(y),
            "principal": bool(principal), "integre": any(infos[i]["integre"] for i in ids),
            "echelle": round(echelle, 2), "pw": pw, "ph": ph,
        })
    out.sort(key=lambda e: (e["y"], e["x"]))
    return out


def ecran_par_defaut() -> str:
    """Écran du réglage « Écran regardé par défaut » ; "" = le principal de GNOME.

    Le réglage arrive au lot 2 (settings.py) : tant qu'il n'existe pas, seule la variable
    d'environnement compte."""
    try:
        from .settings import ITEMS, reglages

        if REGLAGE in ITEMS:
            valeur = str(reglages.value(REGLAGE) or "").strip()
            return "" if _norme(valeur) in _MOTS_DEFAUT else valeur
    except Exception as exc:
        log.warning("réglage de l'écran illisible : %s", exc)
    valeur = os.environ.get(ENV, "").strip()
    return "" if _norme(valeur) in _MOTS_DEFAUT else valeur


# ------------------------------------------------------------------ choix d'un écran

def _norme(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "", t)


def _mots(texte: str) -> list[str]:
    t = unicodedata.normalize("NFKD", texte or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return [m for m in re.split(r"[^a-z0-9]+", t) if m]


def principal(liste: list[dict]) -> dict | None:
    return next((e for e in liste if e["principal"]), liste[0] if liste else None)


def position(ecran: dict, liste: list[dict]) -> str:
    """« en haut », « à gauche », « en bas à droite », « au milieu » ; "" s'il est seul."""
    autres = [o for o in liste if o["id"] != ecran["id"]]
    if not autres:
        return ""
    x, y, w, h = ecran["x"], ecran["y"], ecran["w"], ecran["h"]
    dessus = any(o["y"] + o["h"] <= y for o in autres)
    dessous = any(o["y"] >= y + h for o in autres)
    gauche = any(o["x"] + o["w"] <= x for o in autres)
    droite = any(o["x"] >= x + w for o in autres)
    v = "en haut" if dessous and not dessus else "en bas" if dessus and not dessous else ""
    hz = "à gauche" if droite and not gauche else "à droite" if gauche and not droite else ""
    return " ".join(p for p in (v, hz) if p) or "au milieu"


def _liste_noms(liste: list[dict]) -> str:
    return ", ".join(f"{e['nom']} ({e['id']})" for e in liste)


def _candidats(liste: list[dict], demande: str) -> list[dict]:
    mots = _mots(demande)
    utiles = [m for m in mots if m not in _VIDES]
    compact = "".join(utiles)
    if set(utiles) & _MOTS_PRINCIPAL or compact in _MOTS_PRINCIPAL:
        return [e for e in liste if e["principal"]] or liste[:1]
    if set(utiles) & _MOTS_INTEGRE or compact in _MOTS_INTEGRE:
        return [e for e in liste if e["integre"]]
    if set(utiles) & _MOTS_EXTERNE:
        return [e for e in liste if not e["integre"]]
    if not compact:
        return []
    places = [m for m in utiles if m not in ("tout", "tres")]  # « tout en haut »
    if places and all(m in _MOTS_POSITION for m in places):  # « l'écran du haut », « en bas à gauche »
        voulus = [_MOTS_POSITION[m] for m in places]
        return [e for e in liste if all(p in position(e, liste) for p in voulus)]
    par_id = [e for e in liste if _norme(e["id"]) == compact]
    if par_id:
        return par_id
    exacts = [e for e in liste if _norme(e["nom"]) == compact]
    if exacts:
        return exacts
    if len(compact) >= 2 and compact.isalpha():  # préfixe de connecteur : « hdmi » → HDMI-1, « dp » → DP-1, DP-2
        prefixe = [e for e in liste if re.fullmatch(re.escape(compact) + r"[a-z]?\d+", _norme(e["id"]))]
        if prefixe:
            return prefixe
    par_nom = [e for e in liste if len(compact) >= 2 and (compact in _norme(e["nom"]) or _norme(e["nom"]) in compact)]
    return par_nom


def _plusieurs(liste: list[dict], morceaux: list[str]) -> Choix | None:
    """« portable et LG » : chaque morceau désigne un écran ; ensemble, ils sont montrés assemblés
    comme « tous » (plan P24). None si aucun morceau ne désigne un écran."""
    vus: list[dict] = []
    remarques: list[str] = []
    for m in morceaux:
        trouves = _candidats(liste, m)
        if not trouves:
            remarques.append(f"Écran « {m} » introuvable (débranché ?) parmi : {_liste_noms(liste)}.")
            continue
        if len(trouves) > 1:
            remarques.append(f"Plusieurs écrans répondent à « {m} » ({_liste_noms(trouves)}) : "
                             f"{trouves[0]['nom']} montré ; précise le connecteur.")
        if trouves[0] not in vus:
            vus.append(trouves[0])
    if not vus:
        return None
    cibles = [e for e in liste if e in vus]  # ordre du bureau
    return Choix(cibles, len(cibles) > 1, remarques)


def choisir(liste: list[dict], demande: str = "") -> Choix:
    """Écrans à montrer pour `demande` (vide = principal). Jamais d'exception : un nom inconnu
    montre le principal, avec une remarque pour la légende. Plusieurs écrans cités (« portable et
    LG ») : ceux-là, assemblés (`tous` vrai dès qu'il y en a plus d'un)."""
    if not liste:
        return Choix([], "".join(m for m in _mots(demande) if m not in _VIDES) in _MOTS_TOUS, [])
    demande = (demande or "").strip()
    compact = _norme(demande)
    if compact in _MOTS_DEFAUT:
        return Choix([principal(liste)], False, [])
    if compact in _MOTS_TOUS or "".join(m for m in _mots(demande) if m not in _VIDES) in _MOTS_TOUS:
        return Choix(list(liste), True, [])
    morceaux = [m.strip() for m in _ET.split(demande) if any(x not in _VIDES for x in _mots(m))]
    if len(morceaux) > 1 and not any(compact in (_norme(e["id"]), _norme(e["nom"])) for e in liste):
        multiple = _plusieurs(liste, morceaux)
        if multiple:
            return multiple
    trouves = _candidats(liste, demande)
    if not trouves:
        p = principal(liste)
        return Choix([p], False, [f"Écran « {demande} » introuvable (débranché ?) parmi : {_liste_noms(liste)}. "
                                  f"Écran principal montré à la place."])
    if len(trouves) > 1:
        return Choix([trouves[0]], False, [f"Plusieurs écrans répondent à « {demande} » ({_liste_noms(trouves)}) : "
                                           f"{trouves[0]['nom']} montré ; précise le connecteur."])
    return Choix(trouves, False, [])


# ------------------------------------------------------------------ flux du portail ↔ écrans

def _proche(a: tuple, b: tuple, marge: int = 2) -> bool:
    return abs(int(a[0]) - int(b[0])) <= marge and abs(int(a[1]) - int(b[1])) <= marge


def _meme_taille(dims: tuple, e: dict) -> bool:
    return _proche(dims, (e["w"], e["h"])) or _proche(dims, (e["pw"], e["ph"]))


def _meme_rapport(dims: tuple, e: dict) -> bool:
    if not dims[1] or not e["h"]:
        return False
    return abs(dims[0] / dims[1] - e["w"] / e["h"]) < 0.01


def associer_flux(flux: list[dict], liste: list[dict]) -> dict[str, int]:
    """{id d'écran: indice du flux}. Un flux = {"position": (x, y)?, "size": (w, h)?,
    "pixels": (w, h)?} (propriétés du portail, version 4+ ; `pixels` = taille de l'image tirée).

    Ordre : position exacte (coordonnées du bureau), puis taille logique ou pixels, puis rapport
    largeur/hauteur ; un flux n'est attribué que s'il désigne UN seul écran encore libre."""
    res: dict[str, int] = {}
    libres = list(range(len(flux)))

    def attribuer(test, cle: str) -> None:
        for i in list(libres):
            dims = flux[i].get(cle)
            if not dims:
                continue
            cands = [e for e in liste if e["id"] not in res and test(tuple(dims), e)]
            if len(cands) == 1:
                res[cands[0]["id"]] = i
                libres.remove(i)

    attribuer(lambda d, e: _proche(d, (e["x"], e["y"])), "position")
    for cle in ("size", "pixels"):
        attribuer(_meme_taille, cle)
    for cle in ("size", "pixels"):
        attribuer(_meme_rapport, cle)
    return res


# ------------------------------------------------------------------ légende rendue avec l'image

def _decrit(e: dict, liste: list[dict], taille: bool = True) -> str:
    details = [e["id"]]
    if taille:
        details.append(f"{e['pw']}x{e['ph']}")
    if e["principal"] and len(liste) > 1:
        details.append("principal")
    if e["integre"]:
        details.append("portable")
    pos = position(e, liste)
    if pos:
        details.append(pos)
    return f"{e['nom']} ({', '.join(details)})"


def legende(vus: list[dict], liste: list[dict], remarques: list[str] | None = None) -> str:
    """Une ligne pour Claude : écran(s) vu(s), écrans non montrés, remarques (repli, autorisation)."""
    parties: list[str] = []
    if vus:
        if len(vus) > 1:
            parties.append("Écrans vus, assemblés comme sur le bureau : " + " ; ".join(_decrit(e, liste) for e in vus) + ".")
        else:
            parties.append(f"Écran vu : {_decrit(vus[0], liste)}.")
        ids = {e["id"] for e in vus}
        autres = [e for e in liste if e["id"] not in ids]
        if autres:
            mot = "Autre écran" if len(autres) == 1 else "Autres écrans"
            parties.append(f"{mot} : " + " ; ".join(_decrit(e, liste, taille=False) for e in autres)
                           + " (screen_capture avec ecran=… pour le voir).")
    parties.extend(r for r in (remarques or []) if r)
    return " ".join(parties).replace("\n", " ")


def resume(liste: list[dict], defaut: str = "") -> dict:
    """Réponse de l'outil MCP `list_screens`."""
    choix = choisir(liste, defaut)
    return {
        "ecrans": [{**{k: e[k] for k in ("id", "nom", "w", "h", "x", "y", "principal", "integre", "pw", "ph")},
                    "position": position(e, liste)} for e in liste],
        "regarde_par_defaut": choix.cibles[0]["id"] if choix.cibles else "",
        "conseil": ("Un seul écran." if len(liste) == 1 else
                    "Liste des écrans illisible (pas de GNOME ?) : screen_capture montre le premier écran partagé."
                    if not liste else
                    "Plusieurs écrans : passe ecran=<id ou nom> à screen_capture (ou « tous ») quand la demande "
                    "vise un écran précis ; sans ecran, c'est l'écran par défaut."),
    }
