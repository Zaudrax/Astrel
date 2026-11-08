#!/usr/bin/env python3
"""Banc de QUALITÉ du moteur local (05/11/2026, lot 8, docs/chantiers/HYBRIDE-QWEN.md ; rapport
docs/chantiers/RECHERCHE-QWEN-2026-11-05.md, recommandation R0 / section 11).

    .venv/bin/python tools/banc_qualite_local.py --config base-sans --reflexion off
    .venv/bin/python tools/banc_qualite_local.py --config base-avec --reflexion on --plafond 8000
    .venv/bin/python tools/banc_qualite_local.py --simuler runtime/banc/qualite-…-avec.json runtime/banc/qualite-…-sans.json
    .venv/bin/python tools/banc_qualite_local.py --rescorer runtime/banc/qualite-….json [--filtre]

Questions : tests/fixtures/banc_qualite_local.json (~80, en français : faits, pièges sans réponse, vérificateurs,
cybersécurité, forme, brièveté, agent). Notation DÉTERMINISTE (motifs attendus / interdits, nombre de mots et de
phrases) : pas de juge par un modèle, qui aurait les mêmes défauts que celui qu'on note. 3 passages par question :
taux moyen ET pass^3 (réussi les trois fois, comme τ-bench). Mesures : premier mot, durée totale, jetons de
réflexion (lignes `thinking` du flux d'Ollama : une ligne = un jeton), relances.

Le chemin mesuré est celui de PRODUCTION : `moteur_local.MoteurLocal.repondre` (consignes, réflexion, budget,
filtre de sortie) avec le message que fabrique le tour local (faits exacts de `sensible`), et, pour l'agent, le
VRAI `outils_local.OutilsLocaux` (ligne du CLI, environnement, lecture du flux) — seul le hook est celui du banc
(`tools/banc_local_hook.py` : jamais de carte sur le HUD). Même nom de modèle et même contexte qu'Astrel : rien
n'est rechargé (Ollama 0.30.6 garde un seul exécutant pour qwen3.5:9b et astrel-qwen3.5-9b, mesuré le 05/11).

`--plafond N` (jetons) : le banc coupe une réflexion plus longue (sécurité contre les boucles) et la note
« coupée » ; `--simuler` rejoue alors n'importe quel budget de réflexion ≤ plafond à partir d'un passage AVEC et
d'un passage SANS réflexion (au-delà du budget, la production relance la même demande sans réflexion : on prend
le passage SANS de la même question, et le temps de réflexion jusqu'au budget).
Résultat : runtime/banc/qualite-<date>-<config>.json (écrit après chaque question).

06/11 (lot 9, tout tour local par le CLI avec outils quand le banc de tools/banc_local.py a réussi) :
    .venv/bin/python tools/banc_qualite_local.py --config lot9-cli --chemin cli --passages 1 [--verification]
`--chemin cli` : CHAQUE question (conversation comme agent) passe par le chemin de production du lot 9
(`outils_local.OutilsLocaux.repondre` : message avec faits, outils de lecture ET web, SearXNG de la machine, relecture
`MoteurLocal.relire` si `--verification`, filtre, racine d'Astrel ouverte) ; seul le hook est celui du banc.
`--cli-reflexion off` : réflexion du CLI coupée (MAX_THINKING_TOKENS=0), pour mesure."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astrel import config, moteur_local, outils_local, sensible  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "banc_qualite_local.json"
HOOK = ROOT / "tools" / "banc_local_hook.py"
SORTIE = config.RUNTIME / "banc"
PRECIS = {"temperature": 0.6, "presence_penalty": 0.0}  # R7 : jeu « précis » de la carte Qwen3.5 (réflexion, code)
# R7 : jeu officiel « sans réflexion, général » de la carte Qwen3.5 (top_k 20, min_p 0 : déjà ceux du Modelfile)
SANS_REFLEXION = {"temperature": 0.7, "top_p": 0.8, "presence_penalty": 1.5}
JEUX = {"defaut": None, "precis": PRECIS, "sans_reflexion": SANS_REFLEXION}

# Forme, vérifiée sur TOUTES les réponses (texte normalisé) : défauts vus le 05/11 par l'utilisateur.
FORME = {
    "salutation": re.compile(r"^\W*(bonjour|bonsoir|salut|coucou|hello|hey|hi)\b"),
    "presentation": re.compile(r"^\W*(je suis astrel|moi,? astrel|ici astrel|en tant qu)|en tant qu'(astrel|assistant|ia|modele)"),
    "chemin_interne": re.compile(r"/tmp/astrel-local|astrel-local-|outils-local|claude_config"),
    "prenom": re.compile(r"\butilisateur\b"),
}
INTERNET = re.compile(r"(sans|pas d'|pas d'acces a |aucun acces a |pas acces a )internet|hors ligne|\ben local\b|"
                      r"\bmode local\b|modele local|sur (ta|votre) machine")
URL = re.compile(r"\bhttps?://[^\s)\]»>\"']+|\bwww\.[a-z0-9-]+(?:\.[a-z0-9-]+)+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\."
                 r"(?:fr|com|org|net|io|eu|gouv\.fr|info|be|ch)\b")


# ------------------------------------------------------------------ notation

def normaliser(texte: str) -> str:
    t = unicodedata.normalize("NFKD", str(texte or "")).replace("’", "'").replace("ʼ", "'")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[  ]", " ", t).lower()


def _motif(brut: str, motifs: dict) -> re.Pattern:
    for _ in range(5):  # macros imbriquées (« @INVALIDE_ATTENDU » cite « @PAS_VALIDE »)
        if "@" not in brut:
            break
        brut = re.sub(r"@([A-Z_]+)", lambda m: "(?:" + motifs[m.group(1)] + ")", brut)
    return re.compile(brut)


def mots(texte: str) -> int:
    return len(re.findall(r"\w+", texte))


def phrases(texte: str) -> int:
    return sum(1 for p in re.split(r"(?<=[.!?…])\s+|\n+", texte.strip()) if re.search(r"\w", p))


def forme(texte: str, question: str = "") -> dict:
    n = normaliser(texte)
    d = {k: bool(r.search(n)) for k, r in FORME.items()}
    d["internet"] = bool(INTERNET.search(n)) and not INTERNET.search(normaliser(question))
    vus = normaliser(question)
    d["urls_non_vues"] = sorted({u for u in URL.findall(n) if u not in vus})
    d["ok"] = not any(d[k] for k in FORME)
    return d


def noter(item: dict, texte: str, motifs: dict, outils_lus: int = 0) -> dict:
    """Réussite de la réponse (déterministe) et la raison de l'échec."""
    n = normaliser(texte)
    echecs = []
    if not n.strip():
        echecs.append("réponse vide")
    for a in item.get("attendu") or []:
        brut, debut = (a["re"], a.get("debut")) if isinstance(a, dict) else (a, None)
        if not _motif(brut, motifs).search(n[:debut] if debut else n):
            echecs.append(f"attendu absent : {brut}")
    pts = item.get("points") or []
    if pts:
        trouves = sum(1 for p in pts if _motif(p, motifs).search(n))
        if trouves < int(item.get("min_points") or len(pts)):
            echecs.append(f"points {trouves}/{item.get('min_points')}")
    for i in item.get("interdit") or []:
        if _motif(i, motifs).search(n):
            echecs.append(f"interdit : {i}")
    if item.get("max_mots") and mots(texte) > item["max_mots"]:
        echecs.append(f"{mots(texte)} mots > {item['max_mots']}")
    if item.get("max_phrases") and phrases(texte) > item["max_phrases"]:
        echecs.append(f"{phrases(texte)} phrases > {item['max_phrases']}")
    f = forme(texte, item.get("q", ""))
    if item.get("cat") == "forme" and not f["ok"]:
        echecs.append("forme : " + ", ".join(k for k in FORME if f[k]))
    if item.get("lecture") and not outils_lus:
        echecs.append("aucun outil de lecture appelé")
    return {"ok": not echecs, "echecs": echecs, "forme": f}


# ------------------------------------------------------------------ conversation (moteur local direct)

class _Mesure(moteur_local.MoteurLocal):
    """MoteurLocal de production ; le flux est seulement OBSERVÉ (jetons et temps de réflexion), et coupé au
    plafond de sécurité du banc."""

    def __init__(self, plafond: int, options: dict | None, **kw) -> None:
        super().__init__(**kw)
        self.plafond, self.options_banc, self.essais = plafond, options or {}, []

    def corps(self, *a, **kw) -> dict:
        c = super().corps(*a, **kw)
        c["options"].update(self.options_banc)
        return c

    def _flux(self, http, corps):
        e = {"think": corps.get("think"), "jetons": 0, "cars": 0, "t0": time.monotonic(), "debut": None, "fin": None,
             "coupe": False, "contenu": 0}
        self.essais.append(e)
        for ligne in super()._flux(http, corps):
            msg = ligne.get("message") or {}
            th = str(msg.get("thinking") or "")
            if th:
                e["jetons"] += 1
                e["cars"] += len(th)
                now = time.monotonic() - e["t0"]
                e["debut"] = now if e["debut"] is None else e["debut"]
                e["fin"] = now
                if self.plafond and e["jetons"] > self.plafond:
                    e["coupe"] = True
                    return
            if msg.get("content"):
                e["contenu"] += 1
            if ligne.get("done"):
                e.update(eval_count=ligne.get("eval_count"), prompt_eval_count=ligne.get("prompt_eval_count"),
                         done_reason=ligne.get("done_reason"))
            yield ligne


FAITS = {"source": "production"}  # --faits sensible : seulement les faits du 05/11 (IBAN, carte, NIR), pour isoler R2


def message_local(demande: str, lang: str = "fr") -> str:
    """Le message que le tour local donne au modèle (production si elle l'expose, sinon la règle du 05/11)."""
    f = getattr(moteur_local, "message_local", None)
    if f and FAITS["source"] == "production":
        return f(demande, lang)
    faits = sensible.faits(demande, lang)
    return f"{demande}\n\n{faits}" if faits else demande


def tour_conversation(q: str, reflexion: bool, plafond: int, options: dict | None) -> dict:
    m = _Mesure(plafond, options, tag=moteur_local.modele())
    moteur_local.REFLEXION = reflexion
    t0, premier, morceaux, erreur = time.monotonic(), None, [], ""
    try:
        for x in m.repondre(message_local(q)):
            if premier is None:
                premier = time.monotonic() - t0
            morceaux.append(x)
    except moteur_local.ErreurLocale as exc:
        erreur = str(exc)
    texte = "".join(morceaux)
    marquer = getattr(moteur_local, "marquer_urls", None)
    if marquer:
        texte = marquer(texte, [q], "fr")
    return {"texte": texte, "premier_s": round(premier or 0.0, 2), "total_s": round(time.monotonic() - t0, 2),
            "essais": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in e.items() if k != "t0"}
                       for e in m.essais], "derniere": m.derniere, "erreur": erreur}


# ------------------------------------------------------------------ agent (CLI local, outils de lecture)

def preparer_projet() -> Path:
    racine = Path(tempfile.mkdtemp(prefix="astrel-banc-qualite-"))
    p = racine / "projet"
    (p / "config").mkdir(parents=True)
    (p / "src").mkdir()
    (p / "notes.txt").write_text("Rappel : le code du coffre est ZORGLUB-4217.\n", encoding="utf-8")
    (p / "config" / "app.ini").write_text("[serveur]\nport = 8443\nhote = 127.0.0.1\n", encoding="utf-8")
    (p / "journal.log").write_text("".join(
        f"2026-11-05 10:{i:02d} {'ERREUR disque plein' if i in (3, 7, 11) else 'INFO tâche finie'}\n"
        for i in range(14)), encoding="utf-8")
    (p / "src" / "calcul.py").write_text('def tva(prix):\n    """Prix TTC."""\n    return prix * 1.2\n',
                                         encoding="utf-8")
    (p / "README.md").write_text("# Projet Hirondelle\n\nOutils internes de démonstration.\n", encoding="utf-8")
    return p


def tag_precis(base: str) -> str:
    """Modèle dérivé « précis » (R7) : mêmes fichier, contexte et couches que le dérivé d'Astrel (rien n'est
    rechargé), seul l'échantillonnage change ; le CLI n'envoie pas de température (relevé le 05/11)."""
    import httpx

    tag = outils_local.tag_derive(base) + "-precis"
    r = httpx.post(config.OLLAMA_URL.rstrip("/") + "/api/create", timeout=60.0, json={
        "model": tag, "from": base, "stream": False,
        "parameters": {"num_ctx": moteur_local.NUM_CTX, "num_gpu": moteur_local.NUM_GPU_TOUT, **PRECIS}})
    if r.status_code >= 400:
        raise SystemExit(f"modèle {tag} non créé : {r.text}")
    return tag


CLI = {"chemin": "direct", "verification": False, "reflexion": "on"}  # 06/11 (lot 9) : --chemin, --cli-reflexion


def tag_contexte(base: str, num_ctx: int) -> str:
    """06/11 (lot 9) : dérivé du CLI à un AUTRE contexte (R5, 64 k) sous son propre nom : le dérivé d'Astrel
    (astrel-qwen3.5-9b, 32 k) n'est jamais recréé par le banc pendant qu'Astrel tourne."""
    import httpx

    tag = f"{outils_local.tag_derive(base)}-ctx{int(num_ctx)}"
    r = httpx.post(config.OLLAMA_URL.rstrip("/") + "/api/create", timeout=60.0, json={
        "model": tag, "from": base, "stream": False,
        "parameters": {"num_ctx": int(num_ctx), "num_gpu": moteur_local.NUM_GPU_TOUT, **outils_local.ECHANTILLONNAGE}})
    if r.status_code >= 400:
        raise SystemExit(f"modèle {tag} non créé : {r.text}")
    return tag


def tour_agent(q: str, projet: Path, tag: str) -> dict:
    journal = projet.parent / f"hook-{time.monotonic_ns()}.jsonl"

    def lancer(cmd, **kw):
        # le VRAI tour du CLI local, avec le hook du banc à la place du vrai (aucune carte sur le HUD)
        reglages = Path(cmd[cmd.index("--settings") + 1])
        reglages.write_text(json.dumps(outils_local.reglages_hook(config.hook_command(HOOK))), encoding="utf-8")
        kw["env"] = {**kw["env"], "BANC_LOCAL_JOURNAL": str(journal)}
        if CLI["reflexion"] == "off":
            kw["env"]["MAX_THINKING_TOKENS"] = "0"
        return subprocess.Popen(cmd, **kw)

    o = outils_local.OutilsLocaux(preparer=lambda b: tag, lancer=lancer)
    t0, premier, morceaux, erreur = time.monotonic(), None, [], ""
    kw: dict = {}
    if CLI["chemin"] == "cli":  # 06/11 (lot 9) : le chemin de production (`coeur/local_tour._local_flux`)
        q = message_local(q)
        kw["web"] = True
        if CLI["verification"]:
            m = moteur_local.MoteurLocal()

            def relire(d, brouillon, sources, web_tour, nav_tour, recherche=None):
                return m.relire(d, brouillon, "fr", sources=sources, web=web_tour, navigateur=nav_tour,
                                recherche=recherche)
            kw["relire"] = relire
    try:
        for x in o.repondre(q, "fr", projet=projet, **kw):
            if premier is None:
                premier = time.monotonic() - t0
            morceaux.append(x)
    except moteur_local.ErreurLocale as exc:
        erreur = str(exc)
    if erreur and not morceaux and CLI["chemin"] == "cli":
        # 06/11 (lot 9) : comme la production (`coeur/local_tour._local_repli`) : CLI en échec avant tout texte →
        # le moteur direct répond (sans outil, la raison dans ses consignes)
        try:
            for x in moteur_local.MoteurLocal().repondre(q, "fr", sans_web="panne"):
                if premier is None:
                    premier = time.monotonic() - t0
                morceaux.append(x)
            erreur = f"repli direct ({erreur[:120]})"
        except moteur_local.ErreurLocale as exc:
            erreur += f" ; repli impossible : {exc}"
    appels = []
    if journal.exists():
        appels = [json.loads(l).get("outil") for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()]
    texte = "".join(morceaux)
    marquer = getattr(moteur_local, "marquer_urls", None)
    if marquer:
        texte = marquer(texte, [q, *o.derniere.get("urls_vues", [])], "fr")
    return {"texte": texte, "premier_s": round(premier or 0.0, 2), "total_s": round(time.monotonic() - t0, 2),
            "outils": appels, "derniere": o.derniere, "erreur": erreur}


def tour(item: dict, projet: Path | None, tag: str, reflexion: bool, plafond: int, options: dict | None) -> tuple:
    """(résultat, outils de lecture appelés) d'une question, sur le chemin choisi."""
    if item["cat"] == "agent" or CLI["chemin"] == "cli":
        r = tour_agent(item["q"].replace("{projet}", str(projet)), projet, tag)
        return r, sum(1 for a in r["outils"] if a in ("Read", "Grep", "Glob"))
    return tour_conversation(item["q"], reflexion, plafond, options), 0


# ------------------------------------------------------------------ résumé

def _q(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(round(p * (len(v) - 1))))], 1)


def resumer(runs: list[dict]) -> dict:
    par_item: dict[str, list[dict]] = {}
    for r in runs:
        par_item.setdefault(r["id"], []).append(r)
    cats: dict[str, dict] = {}
    for iid, rs in par_item.items():
        c = cats.setdefault(rs[0]["cat"], {"items": 0, "runs": 0, "ok": 0, "pass3": 0})
        c["items"] += 1
        c["runs"] += len(rs)
        c["ok"] += sum(r["note"]["ok"] for r in rs)
        c["pass3"] += all(r["note"]["ok"] for r in rs) and len(rs) >= 3
    for c in cats.values():
        c["taux"] = round(100 * c["ok"] / max(1, c["runs"]), 1)
        c["pass3_pct"] = round(100 * c["pass3"] / max(1, c["items"]), 1)
    tous = [r for r in runs]
    pens = [sum(e.get("jetons", 0) for e in r.get("essais") or []) for r in tous]
    return {
        "categories": cats,
        "taux": round(100 * sum(r["note"]["ok"] for r in tous) / max(1, len(tous)), 1),
        "pass3_pct": round(100 * sum(c["pass3"] for c in cats.values()) / max(1, len(par_item)), 1),
        "forme_ok_pct": round(100 * sum(r["note"]["forme"]["ok"] for r in tous) / max(1, len(tous)), 1),
        "defauts": {k: sum(bool(r["note"]["forme"].get(k)) for r in tous) for k in (*FORME, "internet", "urls_non_vues")},
        "premier_s": {"med": _q([r["premier_s"] for r in tous], .5), "p90": _q([r["premier_s"] for r in tous], .9)},
        "total_s": {"med": _q([r["total_s"] for r in tous], .5), "p90": _q([r["total_s"] for r in tous], .9),
                    "somme": round(sum(r["total_s"] for r in tous))},
        "reflexion_jetons": {"med": _q(pens, .5), "p90": _q(pens, .9), "max": max(pens or [0])},
        "relances": sum(max(0, len(r.get("essais") or []) - 1) for r in tous),
        "coupes": sum(any(e.get("coupe") for e in r.get("essais") or []) for r in tous),
        "erreurs": sum(bool(r.get("erreur")) for r in tous),
        "runs": len(tous),
    }


def afficher(res: dict) -> None:
    s = res["resume"]
    print(f"\n{res['config']} : {s['taux']} % ({s['runs']} réponses), pass^3 {s['pass3_pct']} %, forme {s['forme_ok_pct']} %")
    for cat, c in s["categories"].items():
        print(f"  {cat:14} {c['taux']:5} %   pass^3 {c['pass3']}/{c['items']}")
    print(f"  premier mot {s['premier_s']} ; total {s['total_s']} ; réflexion {s['reflexion_jetons']} ; relances "
          f"{s['relances']} ; coupées {s['coupes']} ; erreurs {s['erreurs']} ; défauts {s['defauts']}")


def _meta(args: argparse.Namespace) -> dict:
    import httpx

    m: dict = {"args": vars(args), "modele": moteur_local.modele()}
    try:
        u = config.OLLAMA_URL.rstrip("/")
        m["ollama"] = httpx.get(u + "/api/version", timeout=5).json().get("version")
        m["parametres"] = httpx.post(u + "/api/show", json={"model": moteur_local.modele()}, timeout=10).json().get("parameters")
    except Exception as exc:
        m["ollama_erreur"] = str(exc)
    try:
        m["gpu"] = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,memory.used", "--format=csv,noheader"],
                                  capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        m["git"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                                  text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    m["empreintes"] = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()[:12]
                       for f in ("astrel/moteur_local.py", "astrel/outils_local.py", "astrel/sensible.py")}
    m["consignes"] = hashlib.sha256(moteur_local.SYSTEME["fr"].encode()).hexdigest()[:12]
    for k in ("REFLEXION_MAX_CARS", "REFLEXION_BUDGET", "REFLEXION_PLAFOND", "VERIFICATION", "EXEMPLES"):
        if hasattr(moteur_local, k):
            m[k] = getattr(moteur_local, k)
    return m


# ------------------------------------------------------------------ passages

def lancer_banc(args: argparse.Namespace) -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    motifs, items = data["motifs"], data["items"]
    cats = set(filter(None, (args.categories or "").split(",")))
    if cats:
        items = [i for i in items if i["cat"] in cats]
    if args.sans_agent:
        items = [i for i in items if i["cat"] != "agent"]
    if args.budget is not None and hasattr(moteur_local, "REFLEXION_BUDGET"):
        moteur_local.REFLEXION_BUDGET = args.budget
    if args.plafond:
        moteur_local.REFLEXION_MAX_CARS = 10**9  # le plafond du banc (en jetons) remplace la borne de production
        # (baseline du 05/11 : la borne de 12 000 caractères est REJOUÉE par --simuler, budget -12000)
        if hasattr(moteur_local, "REFLEXION_BUDGET") and args.budget is None:
            moteur_local.REFLEXION_BUDGET = args.plafond
    options = dict(JEUX[args.echantillonnage] or {}) or None
    FAITS["source"] = args.faits
    if args.consignes:  # A/B des consignes : SYSTEME d'une autre version de moteur_local (fichier .py)
        import importlib.util
        spec = importlib.util.spec_from_file_location("astrel._consignes_banc", args.consignes,
                                                      submodule_search_locations=None)
        autre = importlib.util.module_from_spec(spec)
        autre.__package__ = "astrel"
        spec.loader.exec_module(autre)
        moteur_local.SYSTEME = dict(autre.SYSTEME)
    if args.exemples:  # R3 : exemples vérifiés en fin de consignes (moteur_local.EXEMPLES)
        moteur_local.EXEMPLES = True
        moteur_local.SYSTEME = {lang: moteur_local.consignes(lang) for lang in ("fr", "en")}
    if args.num_ctx:  # R5 : 64 k avec le cache KV en q8_0 (réglage d'Ollama par l'utilisateur) ; recharge le modèle
        moteur_local.NUM_CTX = args.num_ctx
    if hasattr(moteur_local, "VERIFICATION"):
        moteur_local.VERIFICATION = args.verification
    CLI.update(chemin=args.chemin, verification=args.verification, reflexion=args.cli_reflexion)
    projet = preparer_projet() if args.chemin == "cli" or any(i["cat"] == "agent" for i in items) else None
    if args.chemin == "cli":
        from astrel import searxng_local
        print(f"SearXNG : {searxng_local.assurer() or 'prêt'}", flush=True)
    tag_agent = ""
    if projet:
        tag_agent = tag_precis(moteur_local.modele()) if args.echantillonnage == "precis" else \
            tag_contexte(moteur_local.modele(), args.num_ctx) if args.num_ctx else \
            outils_local.preparer_modele(moteur_local.modele())
    SORTIE.mkdir(parents=True, exist_ok=True)
    fichier = Path(args.sortie) if args.sortie else SORTIE / f"qualite-{time.strftime('%Y%m%d-%H%M')}-{args.config}.json"
    res = {"config": args.config, "fixture": data.get("version"), "debut": time.strftime("%Y-%m-%d %H:%M:%S"), "meta": _meta(args), "runs": []}
    print(f"banc de qualité {args.config} : {len(items)} questions × {args.passages} → {fichier}", flush=True)
    for passage in range(args.passages):
        for item in items:
            r, lus = tour(item, projet, tag_agent, args.reflexion == "on", args.plafond, options)
            r.update(id=item["id"], cat=item["cat"], passage=passage, note=noter(item, r["texte"], motifs, lus))
            res["runs"].append(r)
            print(f"  {'ok ' if r['note']['ok'] else 'NON'} {item['id']} p{passage} {r['premier_s']}/{r['total_s']} s "
                  f"{'; '.join(r['note']['echecs'])[:120]}", flush=True)
            res["resume"] = resumer(res["runs"])
            fichier.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    res["fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
    res["meta"]["gpu_fin"] = _meta(args).get("gpu")
    fichier.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    afficher(res)
    return res


def rescorer(chemin: str, filtre: bool, ecrire: bool = False) -> dict:
    """Renote un résultat (fixture modifiée, ou filtre de sortie de production appliqué après coup)."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items = {i["id"]: i for i in data["items"]}
    res = json.loads(Path(chemin).read_text(encoding="utf-8"))
    for r in res["runs"]:
        texte = r["texte"]
        if filtre:
            texte = moteur_local.filtrer_sortie(texte, items[r["id"]]["q"])
            r["texte_filtre"] = texte
        lus = sum(1 for a in r.get("outils") or [] if a in ("Read", "Grep", "Glob"))
        r["note"] = noter(items[r["id"]], texte, data["motifs"], lus)
    res["fixture"] = data.get("version")
    res["resume"] = resumer(res["runs"])
    if ecrire and not filtre:  # renoté avec la version courante de la fixture, en place
        Path(chemin).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    res["config"] += " (filtre appliqué)" if filtre else " (renoté)"
    afficher(res)
    return res


def simuler(avec: str, sans: str, budgets: list[int]) -> list[dict]:
    """Budget de réflexion rejoué : réflexion ≤ budget → réponse AVEC ; au-delà (ou coupée) → réponse SANS de la
    même question et du même passage, après le temps de réflexion jusqu'au budget. `cars` : borne en caractères
    (production du 05/11 : 12 000)."""
    A = {(r["id"], r["passage"]): r for r in json.loads(Path(avec).read_text(encoding="utf-8"))["runs"]}
    S = {(r["id"], r["passage"]): r for r in json.loads(Path(sans).read_text(encoding="utf-8"))["runs"]}
    lignes = []
    for b in budgets:
        runs = []
        for k, a in A.items():
            s = S.get(k)
            e = (a.get("essais") or [{}])[0]
            jetons, cars = e.get("jetons", 0), e.get("cars", 0)
            # réponse vide après réflexion : relancée par le budget en jetons (lot 8), PAS par la borne en caractères
            depasse = e.get("coupe") or (jetons > b if b > 0 else cars > -b) or (b > 0 and not a["texte"].strip())
            if not depasse or s is None:
                runs.append({**a})
                continue
            if b > 0:
                t_b = (e.get("debut") or 0) + ((e.get("fin") or 0) - (e.get("debut") or 0)) * min(1.0, b / max(1, jetons))
            else:
                t_b = (e.get("debut") or 0) + ((e.get("fin") or 0) - (e.get("debut") or 0)) * min(1.0, -b / max(1, cars))
            runs.append({**s, "premier_s": round(t_b + s["premier_s"], 2), "total_s": round(t_b + s["total_s"], 2),
                         "essais": [{**e, "jetons": min(jetons, b) if b > 0 else jetons}, *s.get("essais", [])]})
        r = resumer(runs)
        lignes.append({"budget": b, **r})
        nom = f"{-b} caractères" if b < 0 else f"{b} jetons"
        print(f"budget {nom:>16} : {r['taux']:5} % pass^3 {r['pass3_pct']:5} % | " +
              " ".join(f"{c[:5]} {v['taux']}" for c, v in r["categories"].items()) +
              f" | 1er mot méd {r['premier_s']['med']} p90 {r['premier_s']['p90']} s ; total méd "
              f"{r['total_s']['med']} p90 {r['total_s']['p90']} s ; relances {r['relances']}")
    return lignes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="essai", help="nom de la configuration (dans le nom du fichier)")
    ap.add_argument("--reflexion", choices=("on", "off"), default="on")
    ap.add_argument("--plafond", type=int, default=0, help="coupe une réflexion au-delà de N jetons (sécurité du banc)")
    ap.add_argument("--budget", type=int, default=None, help="budget de réflexion de production (jetons)")
    ap.add_argument("--echantillonnage", choices=tuple(JEUX), default="defaut",
                    help="conversation : options par requête ; agent : modèle dérivé (« precis » seulement)")
    ap.add_argument("--passages", type=int, default=3)
    ap.add_argument("--verification", action="store_true", help="relecture du brouillon (moteur_local.VERIFICATION)")
    ap.add_argument("--faits", choices=("production", "sensible"), default="production")
    ap.add_argument("--consignes", default="", help="fichier .py d'une autre version de moteur_local (A/B du prompt)")
    ap.add_argument("--exemples", action="store_true", help="exemples vérifiés en fin de consignes")
    ap.add_argument("--num-ctx", type=int, default=0, help="contexte (R5 : 65536 après OLLAMA_KV_CACHE_TYPE=q8_0)")
    ap.add_argument("--chemin", choices=("direct", "cli"), default="direct",
                    help="06/11 (lot 9) : cli = toutes les questions par le CLI local avec outils (production du lot 9)")
    ap.add_argument("--cli-reflexion", choices=("on", "off"), default="on", help="réflexion du CLI (MAX_THINKING_TOKENS)")
    ap.add_argument("--categories", default="")
    ap.add_argument("--sans-agent", action="store_true")
    ap.add_argument("--sortie", default="")
    ap.add_argument("--rescorer", default="")
    ap.add_argument("--filtre", action="store_true")
    ap.add_argument("--ecrire", action="store_true", help="avec --rescorer : réécrit le fichier renoté")
    ap.add_argument("--simuler", nargs=2, metavar=("AVEC", "SANS"))
    ap.add_argument("--budgets", default="-12000,256,512,1000,1500,2000,3000,4000,6000,8000")
    args = ap.parse_args()
    if args.simuler:
        simuler(*args.simuler, [int(x) for x in args.budgets.split(",")])
        return 0
    if args.rescorer:
        rescorer(args.rescorer, args.filtre, args.ecrire)
        return 0
    raison = moteur_local.refus_modele(moteur_local.modele())
    if raison:
        print(raison)
        return 2
    lancer_banc(args)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main())
