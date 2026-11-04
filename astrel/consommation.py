"""Consommation selon la connexion (22/10, plan P19 lot 1 ; demande du 21/10 : une clé
API, un abonnement Grok ou un autre n'affichent pas la même chose que l'abonnement Claude : tokens
utilisés, coût d'une clé API, fenêtre plus longue ou plus courte, selon l'IA et la connexion).

Fonctions PURES (données seules, aucun texte d'écran : la mise en mots est au HUD, lot 3) et un
`Compteur` persistant `runtime/consommation.json`.

- Mode d'après l'`Effectif` (`fournisseurs.Effectif`) : `fenetres` (abonnement Claude : fenêtres
  de 5 h et de la semaine, inchangé), `cle` (clé Claude ou fournisseur à clé par la passerelle :
  facturé à l'usage), `forfait` (abonnement Grok, `xaiabo` : jamais de dollars, la limite
  n'est pas lisible chez le fournisseur), `local` (mode léger : tokens, 0 $).
- Mesure : `modelUsage` du `result` du CLI (tokens et `costUSD` PAR MODÈLE, CUMULÉS pour le
  process : on n'ajoute que la différence, comme `fournisseurs.Depense.noter`). Repli : `usage`
  du tour (PAR TOUR, ajouté tel quel) quand `modelUsage` manque (jamais vu hors Claude), est
  inexploitable ou à cumul nul ; jamais sur un simple delta nul (double comptage).
- Coût : tarif de `astrel/tarifs_defaut.json`, surchargé par `config/tarifs.json` ; pour un
  modèle Claude sans tarif, le `costUSD` du CLI (prix catalogue Claude). Fiabilité :
  `mesure` (tokens du fournisseur + tarif vérifié de moins de 90 jours, ou coût Claude du CLI),
  `estime` (tarif non vérifié ou périmé, ou coût du CLI au prix Claude pour un modèle qui n'est
  pas Claude), `tokens_seuls` (aucun tarif). Jamais un coût inventé.

Appelé par le cœur (lot 2 : `core._note_depense` → `compteur.noter`, puis `vue` → évènement
`consommation`). Fichier de données : `{"version": 1, "jours": {"AAAA-MM-JJ": {"echanges": n,
"lignes": {"<fournisseur>|<modèle>": {fournisseur, modele, entree, sortie, cache_lecture,
cache_ecriture, cout_cli_usd}}}}}`, 62 jours gardés, écrit de façon atomique.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("astrel.consommation")

MODES = ("fenetres", "cle", "forfait", "local")
FIABILITES = ("mesure", "estime", "tokens_seuls")  # du plus sûr au moins sûr
TARIF_PERIME_JOURS = 90
JOURS_GARDES = 62
TOKENS = ("entree", "sortie", "cache_lecture", "cache_ecriture")
TARIFS_DEFAUT = Path(__file__).with_name("tarifs_defaut.json")
# Champs de `modelUsage` (CLI, cumulés par process) et de `usage` (par tour), dans l'ordre de TOKENS.
_MODEL_USAGE = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
_USAGE = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_CLAUDE = ("abonnement", "cle_claude")  # fournisseurs dont les modèles sont des modèles Claude


def fichier() -> Path:
    """`runtime/consommation.json` (`config.CONSOMMATION_FILE` s'il existe, pour les tests du lot 2)."""
    return getattr(config, "CONSOMMATION_FILE", config.RUNTIME / "consommation.json")


def tarifs_perso() -> Path:
    return config.CONFIG_DIR / "tarifs.json"


def aujourd_hui() -> str:
    return time.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- connexion

def _oauth(fid: str) -> bool:
    from . import fournisseurs

    f = fournisseurs.CATALOGUE.get(fid)
    return bool(f and f.oauth)


def facturation(fid: str) -> str:
    """`abonnement` (Claude, non facturé au token), `usage` (clé API), `forfait` (abonnement d'un
    autre fournisseur, Grok par abonnement), `local` (mode léger). Un id inconnu est compté à l'usage."""
    if fid == "abonnement":
        return "abonnement"
    if fid == "local":
        return "local"
    if _oauth(fid):
        return "forfait"
    return "usage"


def fournisseur_de(eff: Any, local: bool = False) -> str:
    """Id sous lequel le compteur range la consommation : celui du catalogue, ou `local`."""
    if local:
        return "local"
    return str(getattr(eff, "id", "") or "abonnement")


def mode_de(eff: Any, local: bool = False) -> str:
    """Mode du panneau d'après l'`Effectif` (None = abonnement)."""
    return {"abonnement": "fenetres", "usage": "cle", "forfait": "forfait",
            "local": "local"}[facturation(fournisseur_de(eff, local))]


# --------------------------------------------------------------------------- tarifs

def _prix(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return float(v)


def _date(v: Any) -> _dt.date | None:
    if not isinstance(v, str):
        return None
    try:
        return _dt.date.fromisoformat(v)
    except ValueError:
        return None


def _lire_table(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("tarifs illisibles (%s) : %s", type(exc).__name__, path)
        return {}
    modeles = data.get("modeles") if isinstance(data, dict) else None
    if not isinstance(modeles, dict):
        log.warning("tarifs sans objet « modeles » : %s", path)
        return {}
    return {str(k): v for k, v in modeles.items() if not str(k).startswith("_") and isinstance(v, dict)}


def _valide(nom: str, t: dict) -> dict | None:
    """Tarif utilisable : entrée et sortie obligatoires (nombres ≥ 0), cache facultatif."""
    out: dict[str, Any] = {}
    for k in ("entree", "sortie"):
        p = _prix(t.get(k))
        if p is None:
            log.warning("tarif ignoré (%s manquant ou invalide) : %s", k, nom)
            return None
        out[k] = p
    for k in ("cache_lecture", "cache_ecriture"):
        p = _prix(t.get(k))
        if p is not None:
            out[k] = p
    d = _date(t.get("verifie"))
    out["verifie"] = d.isoformat() if d else None
    out["source"] = t.get("source", "defaut")
    return out


def charger_tarifs(defaut: Path | None = None, perso: Path | None = None) -> dict[str, dict]:
    """Table des tarifs ($ par million de tokens) : celle livrée, surchargée modèle par modèle par
    `config/tarifs.json`. Un modèle surchargé garde les champs livrés qu'il ne redonne pas, mais
    PAS la date `verifie` livrée : sans la sienne, son tarif est « non vérifié »."""
    base = _lire_table(defaut or TARIFS_DEFAUT)
    for nom, t in _lire_table(perso or tarifs_perso()).items():
        base[nom] = {**base.get(nom, {}), **t, "verifie": t.get("verifie"), "source": "perso"}
    out = {}
    for nom, t in base.items():
        v = _valide(nom, t)
        if v is not None:
            out[nom] = v
    return out


def tarif_de(tarifs: dict[str, dict], fournisseur: str, modele: str) -> dict | None:
    """« fournisseur:modèle » d'abord (prix propre à un fournisseur), puis le modèle seul."""
    return tarifs.get(f"{fournisseur}:{modele}") or tarifs.get(modele)


def _age_jours(verifie: str | None, jour: str) -> int | None:
    d, j = _date(verifie), _date(jour)
    if d is None or j is None:
        return None
    return (j - d).days


def resume_tarifs(tarifs: dict[str, dict], jour: str | None = None) -> dict:
    """Ligne d'info des Réglages (« Tarifs : N modèles, vérifiés le … »), en données seules."""
    jour = jour or aujourd_hui()
    dates = sorted(t["verifie"] for t in tarifs.values() if t.get("verifie"))
    perimes = sum(1 for t in tarifs.values()
                  if (a := _age_jours(t.get("verifie"), jour)) is not None and a > TARIF_PERIME_JOURS)
    return {"modeles": len(tarifs), "verifies": len(dates), "non_verifies": len(tarifs) - len(dates),
            "perimes": perimes, "plus_ancien": dates[0] if dates else None,
            "plus_recent": dates[-1] if dates else None,
            "perso": sum(1 for t in tarifs.values() if t.get("source") == "perso")}


def _pire(a: str, b: str) -> str:
    return a if FIABILITES.index(a) >= FIABILITES.index(b) else b


def cout_ligne(ligne: dict, tarifs: dict[str, dict], jour: str | None = None) -> dict:
    """Coût d'une ligne du compteur : `{"cout_usd": float | None, "fiabilite", "perime"}`.
    Forfait : jamais de dollars. Local : 0 $. Sinon tarif de la table, puis coût Claude du CLI."""
    jour = jour or aujourd_hui()
    fid, modele = str(ligne.get("fournisseur") or ""), str(ligne.get("modele") or "")
    fact = facturation(fid)
    if fact == "forfait":
        return {"cout_usd": None, "fiabilite": "tokens_seuls", "perime": False}
    if fact == "local":
        return {"cout_usd": 0.0, "fiabilite": "mesure", "perime": False}
    t = tarif_de(tarifs, fid, modele)
    if t is not None:
        fiab, perime = "mesure", False
        age = _age_jours(t.get("verifie"), jour)
        if age is None:
            fiab = "estime"
        elif age > TARIF_PERIME_JOURS:
            fiab, perime = "estime", True
        cout = 0.0
        for k in TOKENS:
            n = _n(ligne.get(k))
            if not n:
                continue
            p = t.get(k)
            if p is None:  # prix du cache non donné : celui de l'entrée, et le coût n'est plus sûr
                p, fiab = t["entree"], _pire(fiab, "estime")
            cout += n * p / 1_000_000
        return {"cout_usd": round(cout, 9), "fiabilite": fiab, "perime": perime}
    cli = _n(ligne.get("cout_cli_usd"), entier=False)
    if cli and fid in _CLAUDE:
        return {"cout_usd": round(cli, 9), "fiabilite": "mesure", "perime": False}
    if cli:  # le CLI chiffre un modèle inconnu au prix Claude : un ordre de grandeur
        return {"cout_usd": round(cli, 9), "fiabilite": "estime", "perime": False}
    return {"cout_usd": None, "fiabilite": "tokens_seuls", "perime": False}


# --------------------------------------------------------------------------- mesure

def _n(v: Any, entier: bool = True) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return 0
    return int(v) if entier else float(v)


def _nom_modele(cle: str, v: dict) -> str:
    canon = v.get("canonicalModel")
    if isinstance(canon, str) and canon.strip():
        return canon.strip()
    return cle.split("[", 1)[0].strip() or cle


def lire_model_usage(model_usage: Any) -> dict[str, dict]:
    """`modelUsage` du CLI → {clé brute: {modele, entree, sortie, cache_lecture, cache_ecriture,
    cout_cli_usd}} ; ce qui n'a pas la bonne forme est ignoré."""
    out: dict[str, dict] = {}
    if not isinstance(model_usage, dict):
        return out
    for cle, v in model_usage.items():
        if not isinstance(v, dict):
            continue
        ligne = {"modele": _nom_modele(str(cle), v)}
        for k, src in zip(TOKENS, _MODEL_USAGE):
            ligne[k] = _n(v.get(src))
        ligne["cout_cli_usd"] = _n(v.get("costUSD"), entier=False)
        out[str(cle)] = ligne
    return out


def _vide(ligne: dict) -> bool:
    return not any(ligne.get(k) for k in (*TOKENS, "cout_cli_usd"))


class Compteur:
    """Consommation par jour, fournisseur et modèle (`runtime/consommation.json`). Un seul écrivain
    (le cœur) ; verrou entre ses fils, écriture atomique (un lecteur voit l'ancien ou le nouveau)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._process = 0
        self._last: dict[tuple[str, str], dict] = {}  # (fournisseur, clé brute) → cumul déjà compté

    @property
    def path(self) -> Path:
        return self._path or fichier()

    @staticmethod
    def _forme_juste(data: Any) -> bool:
        """`{"jours": {"AAAA-MM-JJ": {"echanges": n?, "lignes": {clé: {…}}?}}}` ; le reste (jour
        qui n'est pas un objet, `lignes` en liste, `echanges` non numérique…) ferait lever `noter`
        à chaque appel ou serait écrasé en silence."""
        if not isinstance(data, dict) or not isinstance(data.get("jours"), dict):
            return False
        for k, j in data["jours"].items():
            if _date(k) is None or not isinstance(j, dict):
                return False
            e = j.get("echanges", 0)
            if e is not None and (isinstance(e, bool) or not isinstance(e, (int, float))
                                  or not math.isfinite(e)):
                return False
            lignes = j.get("lignes", {})
            if not isinstance(lignes, dict) or not all(isinstance(l, dict) for l in lignes.values()):
                return False
        return True

    def _lire(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not self._forme_juste(data):
                raise ValueError("forme fausse")
        except FileNotFoundError:
            return {"version": 1, "jours": {}}
        except (OSError, ValueError) as exc:
            # Illisible ou de forme fausse : mis de côté (jamais écrasé en silence), on repart à zéro.
            log.warning("consommation illisible (%s), mise de côté : %s", exc, self.path)
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".illisible"))
            except OSError:
                pass
            return {"version": 1, "jours": {}}
        return data

    def _ecrire(self, data: dict) -> None:
        path = self.path
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.warning("consommation non retenue : %s", path)
            try:
                tmp.unlink()
            except OSError:
                pass

    @staticmethod
    def _elaguer(jours: dict, jour: str) -> None:
        """Garde `JOURS_GARDES` jours avant le jour écrit, mais jamais à partir d'un jour FUTUR :
        l'ancre est le plus tôt du jour écrit et de la date réelle (une horloge avancée une seule
        fois, en 2030, supprimait sinon tout l'historique)."""
        j, reel = _date(jour), _date(aujourd_hui())
        if j is None or reel is None:
            return
        limite = min(j, reel) - _dt.timedelta(days=JOURS_GARDES - 1)
        for k in list(jours):
            d = _date(k)
            if d is None or d < limite:
                del jours[k]

    def _deltas(self, fournisseur: str, lu: dict[str, dict], process: int) -> list[dict]:
        """`lu` = `lire_model_usage(modelUsage)` (cumuls du process)."""
        if process != self._process:  # CLI relancé : son cumul repart de zéro
            self._process, self._last = process, {}
        out = []
        for cle, cur in lu.items():
            prev = self._last.get((fournisseur, cle))
            self._last[(fournisseur, cle)] = cur
            champs = (*TOKENS, "cout_cli_usd")
            if prev is not None and all(cur[k] >= prev[k] for k in champs):
                d = {k: cur[k] - prev[k] for k in champs}
            else:  # premier vu dans ce process, ou cumul qui recule (remis à zéro) : tout compte
                d = {k: cur[k] for k in champs}
            d["modele"] = cur["modele"]
            if not _vide(d):
                out.append(d)
        return out

    def noter(self, fournisseur: str, model_usage: Any = None, process: int = 0, usage: Any = None,
              modele: str = "", jour: str | None = None) -> list[dict]:
        """Ajoute la consommation d'un `result` du CLI ; rend ce qui a été ajouté (une ligne par
        modèle, vide si rien). `model_usage` (cumulé par process) prime ; sinon `usage` du tour,
        rangé sous `modele` (le modèle choisi chez le fournisseur). Repli sur `usage` aussi quand
        `model_usage` ne donne aucune ligne exploitable ou un cumul total nul ; PAS sur un simple
        delta nul (le tour est déjà dans le cumul : le recompter serait un double comptage)."""
        fournisseur = str(fournisseur or "abonnement")
        jour = jour or aujourd_hui()
        with self._lock:
            lu = lire_model_usage(model_usage)
            if any(not _vide(l) for l in lu.values()):
                ajouts = self._deltas(fournisseur, lu, process)
            elif isinstance(usage, dict):
                d = {k: _n(usage.get(src)) for k, src in zip(TOKENS, _USAGE)}
                d.update(cout_cli_usd=0.0, modele=str(modele or "inconnu"))
                ajouts = [] if _vide(d) else [d]
            else:
                ajouts = []
            if not ajouts:
                return []
            data = self._lire()
            jours = data["jours"]
            j = jours.setdefault(jour, {"echanges": 0, "lignes": {}})
            j["echanges"] = int(j.get("echanges") or 0) + 1
            lignes = j.setdefault("lignes", {})
            for d in ajouts:
                row = lignes.setdefault(f"{fournisseur}|{d['modele']}", {
                    "fournisseur": fournisseur, "modele": d["modele"], **{k: 0 for k in TOKENS},
                    "cout_cli_usd": 0.0})
                for k in TOKENS:
                    row[k] = int(_n(row.get(k)) + d[k])
                row["cout_cli_usd"] = round(_n(row.get("cout_cli_usd"), entier=False) + d["cout_cli_usd"], 9)
            self._elaguer(jours, jour)
            data["version"] = 1
            self._ecrire(data)
            return [{"fournisseur": fournisseur, **d} for d in ajouts]

    def donnees(self) -> dict:
        with self._lock:
            return self._lire()


compteur = Compteur()


# --------------------------------------------------------------------------- vue

_DATE_CLAUDE = re.compile(r"^(claude-.+)-\d{8}$")


def modele_canonique(modele: str) -> str:
    """Nom d'un modèle Claude sans sa date de version (`-AAAAMMJJ`) ; les autres tels quels."""
    m = _DATE_CLAUDE.match(modele)
    return m.group(1) if m else modele


def _periode(jours: dict, prefixe: str, tarifs: dict[str, dict], jour: str) -> dict:
    """Lignes et totaux des jours dont la date commence par `prefixe` (un jour ou un mois)."""
    rows: dict[str, dict] = {}
    echanges = 0
    for k, j in jours.items():
        if not str(k).startswith(prefixe) or not isinstance(j, dict):
            continue
        echanges += int(_n(j.get("echanges")))
        for cle, l in (j.get("lignes") or {}).items():
            if not isinstance(l, dict):
                continue
            # 04/11 (T50) : « claude-haiku-4-5-20251001 » et « claude-haiku-4-5 » = une seule ligne (lecture
            # seulement, le fichier garde ce qu'a dit le CLI).
            modele = modele_canonique(str(l.get("modele", "")))
            fid = str(l.get("fournisseur", ""))
            r = rows.setdefault(f"{fid}|{modele}" if modele else cle, {"fournisseur": fid, "modele": modele,
                                **{t: 0 for t in TOKENS}, "cout_cli_usd": 0.0})
            for t in TOKENS:
                r[t] += int(_n(l.get(t)))
            r["cout_cli_usd"] += _n(l.get("cout_cli_usd"), entier=False)
    lignes = []
    tot = {"tokens": 0, "cout_usd": None, "fiabilite": None, "partiel": False, "equivalent_usd": None}
    for r in rows.values():
        r["tokens"] = sum(r[t] for t in TOKENS)
        r["cout_cli_usd"] = round(r["cout_cli_usd"], 9)
        r["facturation"] = facturation(str(r["fournisseur"]))
        r.update(cout_ligne(r, tarifs, jour))
        lignes.append(r)
        tot["tokens"] += r["tokens"]
        if r["facturation"] == "usage":  # ce qui est facturé
            if r["cout_usd"] is None:
                tot["partiel"] = True
            else:
                tot["cout_usd"] = round((tot["cout_usd"] or 0.0) + r["cout_usd"], 9)
            tot["fiabilite"] = _pire(tot["fiabilite"] or "mesure", r["fiabilite"])
        elif r["facturation"] == "abonnement" and r["cout_usd"] is not None:  # équivalent, non facturé
            tot["equivalent_usd"] = round((tot["equivalent_usd"] or 0.0) + r["cout_usd"], 9)
    if tot["cout_usd"] is None:
        tot["partiel"] = False  # rien de chiffré : « tokens seuls », pas un coût partiel
    lignes.sort(key=lambda r: (-r["tokens"], r["fournisseur"], r["modele"]))
    return {"echanges": echanges, "lignes": lignes, "total": tot}


def vue(mode: str, donnees: dict | None = None, tarifs: dict[str, dict] | None = None,
        jour: str | None = None, seuil_mois_usd: float = 0.0) -> dict:
    """Données de l'évènement `consommation` (lot 2) : mode, jour et mois (lignes par fournisseur
    et modèle, totaux, fiabilité), résumé des tarifs. Aucun texte : le HUD le met en mots."""
    jour = jour or aujourd_hui()
    tarifs = charger_tarifs() if tarifs is None else tarifs
    jours = (donnees if donnees is not None else compteur.donnees()).get("jours") or {}
    mois = _periode(jours, jour[:7], tarifs, jour)
    seuil = _prix(seuil_mois_usd) or 0.0
    cout_mois = mois["total"]["cout_usd"] or 0.0
    return {
        "mode": mode if mode in MODES else "fenetres",
        "date": jour,
        "jour": _periode(jours, jour, tarifs, jour),
        "mois": {"mois": jour[:7], **mois},
        "seuil_mois_usd": seuil,
        "alerte_mois": bool(seuil and cout_mois >= seuil),
        "tarifs": resume_tarifs(tarifs, jour),
    }
