"""Le CONSERVATEUR du coffre de mémoire : il relit les notes et PROPOSE, sans
modèle et sans rien modifier.

Idée de Hermes Agent (conservateur, relecture de la mémoire), réduite à ce qui se
calcule sans modèle :

- liens `[[…]]` vers une note qui n'existe pas ;
- notes que rien ne relie (aucun lien entrant ; l'Accueil est exclu) ;
- doublons probables (même titre, ou au moins `DUP_JACCARD` de mots communs) ;
- notes trop longues (> `LONG_CHARS` caractères : chaque lecture coûte autant) ;
- notes vides ;
- états sans doute périmés : « en cours », « en file », « demain »… dans une
  note qui n'a pas bougé depuis `STALE_DAYS` jours ;
- notes faites d'ajouts empilés (≥ `APPENDS_MAX` blocs datés) : à consolider ;
- secrets écrits en clair.

Appliquer une proposition reste l'affaire de Claude avec l'accord de l'utilisateur
(`memory_write`, qui sauvegarde la version remplacée). Le rapport complet est
écrit dans `runtime/vault-review.md` ; les points déjà vus sont retenus dans
`runtime/vault-review.json`, pour ne signaler que les NOUVEAUX
(vérification `vault_review` des réflexes).

Ménage de nuit (plan P3, lot D) : `candidates()` (quelques notes à relire, une fois
par version) et `propose()` (outil MCP `memory_propose`, écrit seulement dans
`runtime/vault-propositions/`), voir plus bas.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from .. import config
from . import memory_scan

REPORT_MD = config.RUNTIME / "vault-review.md"
REPORT_JSON = config.RUNTIME / "vault-review.json"

LONG_CHARS = 8000
STALE_DAYS = 14
APPENDS_MAX = 8
DUP_JACCARD = 0.6
DUP_MIN_WORDS = 15
EMPTY_CHARS = 10

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_MDLINK = re.compile(r"\]\(([^)]+\.md)\)")
_APPEND = re.compile(r"^<!-- \d{4}-\d{2}-\d{2} \d{2}:\d{2} -->$", re.M)
_STALE = re.compile(r"(?i)\b(en cours|en file|en attente|à surveiller|a surveiller|demain|ce soir)\b")
_ROOTS = {"accueil"}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def _vault() -> Path:
    from . import memory

    return memory.VAULT


def _load(vault: Path) -> dict[str, dict]:
    notes: dict[str, dict] = {}
    if not vault.is_dir():
        return notes
    for path in sorted(vault.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(vault).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            mtime = path.stat().st_mtime
        except OSError:
            continue
        m = re.search(r"^#\s+(.+)$", text, re.M)
        body = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
        body = re.sub(r"^#.*$", "", body, flags=re.M)
        body = _APPEND.sub("", body).strip()
        notes[str(path.relative_to(vault))] = {
            "title": m.group(1).strip() if m else path.stem,
            "text": text,
            "body": body,
            "mtime": mtime,
            "links": _WIKILINK.findall(text) + [Path(x).stem for x in _MDLINK.findall(text)],
        }
    return notes


def review(vault: Path | None = None, now: float | None = None) -> dict:
    """Relit le coffre et rend les propositions (rien n'est modifié)."""
    vault = vault or _vault()
    now = now or time.time()
    notes = _load(vault)
    by_name: dict[str, str] = {}
    for rel, n in notes.items():
        by_name.setdefault(_fold(Path(rel).stem), rel)
        by_name.setdefault(_fold(n["title"]), rel)

    broken, incoming = [], {rel: 0 for rel in notes}
    for rel, n in notes.items():
        for target in n["links"]:
            key = _fold(target.strip())
            dest = by_name.get(key) or by_name.get(_fold(Path(key).stem))
            if dest is None:
                item = {"note": rel, "cible": target.strip()}
                if item not in broken:
                    broken.append(item)
            elif dest != rel:
                incoming[dest] += 1
    orphans = [rel for rel, count in incoming.items()
               if count == 0 and _fold(Path(rel).stem) not in _ROOTS]

    long_notes, empty, stale, appends, secrets = [], [], [], [], []
    for rel, n in notes.items():
        size = len(n["text"])
        if size > LONG_CHARS:
            long_notes.append({"note": rel, "caracteres": size, "tokens": round(size / 3.5)})
        if len(n["body"]) < EMPTY_CHARS:
            empty.append(rel)
        age_days = (now - n["mtime"]) / 86400
        if age_days >= STALE_DAYS:
            m = _STALE.search(n["title"] + "\n" + n["body"])
            if m:
                stale.append({"note": rel, "modifiee": datetime.fromtimestamp(n["mtime"]).strftime("%Y-%m-%d"),
                              "indice": m.group(0)})
        count = len(_APPEND.findall(n["text"]))
        if count >= APPENDS_MAX:
            appends.append({"note": rel, "ajouts": count})
        kinds = memory_scan.find_secrets(n["text"])
        if kinds:
            secrets.append({"note": rel, "natures": sorted(set(kinds))})

    dups = []
    words = {rel: set(w for w in re.findall(r"\w{4,}", _fold(n["body"]))) for rel, n in notes.items()}
    rels = sorted(notes)
    for i, a in enumerate(rels):
        for b in rels[i + 1:]:
            if _fold(notes[a]["title"]) == _fold(notes[b]["title"]):
                dups.append({"notes": [a, b], "raison": "même titre"})
                continue
            wa, wb = words[a], words[b]
            if len(wa) >= DUP_MIN_WORDS and len(wb) >= DUP_MIN_WORDS:
                jac = len(wa & wb) / len(wa | wb)
                if jac >= DUP_JACCARD:
                    dups.append({"notes": [a, b], "raison": f"{round(jac * 100)} % de mots communs"})

    report = {
        "date": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
        "notes": len(notes),
        "liens_casses": broken,
        "orphelines": orphans,
        "doublons_probables": dups,
        "trop_longues": long_notes,
        "vides": empty,
        "perimees": stale,
        "a_consolider": appends,
        "secrets": secrets,
    }
    report["points"] = sum(len(report[k]) for k in _CATEGORIES)
    return report


_CATEGORIES = {
    "secrets": ("secret en clair", "secrets en clair"),
    "liens_casses": ("lien cassé", "liens cassés"),
    "doublons_probables": ("doublon probable", "doublons probables"),
    "trop_longues": ("note trop longue", "notes trop longues"),
    "perimees": ("état peut-être périmé", "états peut-être périmés"),
    "a_consolider": ("note à consolider", "notes à consolider"),
    "vides": ("note vide", "notes vides"),
    "orphelines": ("note sans lien entrant", "notes sans lien entrant"),
}


def issue_keys(report: dict) -> set[str]:
    """Identifiants stables des points relevés (pour ne signaler que les nouveaux)."""
    keys = set()
    for cat in _CATEGORIES:
        for item in report.get(cat, []):
            if isinstance(item, str):
                ident = item
            elif "notes" in item:
                ident = "+".join(item["notes"])
            else:
                ident = item["note"] + ("→" + item["cible"] if "cible" in item else "")
            keys.add(f"{cat}:{ident}")
    return keys


def summary(report: dict) -> str:
    """Une phrase, lisible à voix haute ou dans une notification."""
    if not report["points"]:
        return f"Coffre de mémoire : {report['notes']} notes, rien à revoir."
    parts = []
    for cat, (one, many) in _CATEGORIES.items():
        n = len(report[cat])
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    longest = max(report["trop_longues"], key=lambda x: x["caracteres"], default=None)
    tail = ""
    if longest:
        tail = f" La plus longue, {Path(longest['note']).stem}, coûte environ {longest['tokens']} tokens à chaque lecture."
    return f"Coffre de mémoire : {report['notes']} notes, à revoir : " + ", ".join(parts) + "." + tail


def to_markdown(report: dict) -> str:
    lines = [f"# Revue du coffre — {report['date']}", "",
             "Propositions du conservateur : RIEN n'a été modifié. Les appliquer avec "
             "l'accord de l'utilisateur (memory_write garde la version remplacée).", "",
             summary(report), ""]

    def section(title: str, items: list[str], hint: str) -> None:
        if items:
            lines.extend([f"## {title}", f"_{hint}_", ""] + [f"- {x}" for x in items] + [""])

    section("Secrets en clair", [f"{x['note']} : {', '.join(x['natures'])}" for x in report["secrets"]],
            "les remplacer par « dans config/secrets.json » ou les retirer")
    section("Liens cassés", [f"{x['note']} → [[{x['cible']}]]" for x in report["liens_casses"]],
            "corriger le nom, ou créer la note visée")
    section("Doublons probables", [f"{' / '.join(x['notes'])} ({x['raison']})" for x in report["doublons_probables"]],
            "fusionner dans une seule note, puis faire pointer l'autre vers elle")
    section("Notes trop longues", [f"{x['note']} : {x['caracteres']} caractères ≈ {x['tokens']} tokens"
                                   for x in report["trop_longues"]],
            "résumer l'état actuel en tête, archiver le détail ancien dans une note à part")
    section("États peut-être périmés", [f"{x['note']} (« {x['indice']} », modifiée le {x['modifiee']})"
                                        for x in report["perimees"]],
            "vérifier si c'est toujours vrai ; sinon noter le résultat ou archiver")
    section("Notes à consolider", [f"{x['note']} : {x['ajouts']} ajouts datés" for x in report["a_consolider"]],
            "réécrire en un texte à jour (les faits, pas l'historique des ajouts)")
    section("Notes vides", report["vides"], "compléter ou supprimer (avec l'utilisateur)")
    section("Notes sans lien entrant", report["orphelines"], "les relier depuis une note sommaire (Accueil, Projets…)")
    return "\n".join(lines).rstrip() + "\n"


def run(vault: Path | None = None, save: bool = True) -> tuple[dict, set[str]]:
    """Relit, écrit le rapport, et rend (rapport, points NOUVEAUX depuis la passe précédente)."""
    report = review(vault)
    keys = issue_keys(report)
    try:
        before = set(json.loads(REPORT_JSON.read_text(encoding="utf-8")).get("keys", []))
    except (OSError, ValueError, AttributeError):
        before = set()
    if save:
        REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
        REPORT_MD.write_text(to_markdown(report), encoding="utf-8")
        REPORT_JSON.write_text(json.dumps({"date": report["date"], "points": report["points"],
                                           "keys": sorted(keys)}, ensure_ascii=False, indent=1), encoding="utf-8")
    return report, keys - before


# --- ménage de nuit (plan P3, lot D) ---
#
# `candidates()` choisit, sans modèle, quelques notes à faire relire par l'agent
# `archiviste` (lecture seule) : doublons, notes à consolider, états périmés. Une note
# n'est revue qu'une fois par version (mtime), au plus MENAGE_NOTES par nuit, et une
# seule sélection par nuit. L'archiviste dépose des PROPOSITIONS (`memory_propose`,
# `runtime/vault-propositions/`) ; rien n'est appliqué sans le « oui » de l'utilisateur.

MENAGE_FILE = config.RUNTIME / "vault-menage.json"
MENAGE_NOTES = 3
MENAGE_KINDS = ("doublons_probables", "a_consolider", "perimees")
# Notes de travail ou d'archive : périmées ou longues par nature, jamais proposées.
_MENAGE_SKIP = re.compile(r"(^|/)(CHANTIERS-EN-COURS|ARCHIVE-|GABARIT-|PHRASE-DE-REPRISE|Accueil\.md)", re.I)


def _load_menage(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def candidates(kinds: tuple[str, ...] | list[str] | None = None, vault: Path | None = None,
               now: float | None = None, limit: int = MENAGE_NOTES, mark: bool = False,
               state_file: Path | None = None) -> list[dict]:
    """Candidats NOUVEAUX au ménage : `[{kind, notes, raison}]`, `limit` notes au plus.

    Nouveau = au moins une de ses notes a changé (mtime) depuis sa dernière revue.
    `mark` (contrôle de nuit) : les retient comme revues, avec la nuit du jour ; une
    seconde sélection la même nuit rend []. Rien n'est modifié dans le coffre."""
    vault = vault or _vault()
    now = now or time.time()
    state_file = state_file or MENAGE_FILE
    state = _load_menage(state_file)
    night = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    if mark and state.get("nuit") == night:
        return []
    seen = state.get("vues") if isinstance(state.get("vues"), dict) else {}
    report = review(vault, now)
    out, used = [], 0
    for kind in (k for k in MENAGE_KINDS if k in (kinds or MENAGE_KINDS)):
        for item in report.get(kind, []):
            notes = list(item["notes"]) if "notes" in item else [item["note"]]
            if any(_MENAGE_SKIP.search(n) for n in notes):
                continue
            try:
                mtimes = {n: (vault / n).stat().st_mtime for n in notes}
            except OSError:
                continue
            if all(seen.get(n) == m for n, m in mtimes.items()):
                continue  # déjà revue dans cette version
            if used + len(notes) > limit:
                continue
            raison = item.get("raison") or (f"{item['ajouts']} ajouts datés" if "ajouts" in item else
                                            f"« {item.get('indice', '')} », modifiée le {item.get('modifiee', '')}")
            out.append({"kind": kind, "notes": notes, "raison": raison})
            used += len(notes)
    if mark:
        seen = {n: m for n, m in seen.items() if (vault / n).exists()}  # notes disparues : oubliées
        for c in out:
            for n in c["notes"]:
                try:
                    seen[n] = (vault / n).stat().st_mtime
                except OSError:
                    pass
        state.update(vues=seen, nuit=night, candidats=out, date=datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"))
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_name(state_file.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(state_file)
    return out


PROPOSALS_DIR = config.RUNTIME / "vault-propositions"
# `nouvelle` (20/10, plan P12, lot 5b, idée de Hermes Agent) : une note qui n'existe pas encore,
# proposée par la relève en fin de session ; l'utilisateur valide, Astrel la crée (memory_write create).
PROPOSAL_KINDS = ("fusion", "consolidation", "perimee", "nouvelle")
PROPOSAL_MAX_CHARS = 12_000
PROPOSALS_PER_DAY = 10


def _cibles(kind: str, notes: list) -> list[str]:
    """Notes visées par une proposition, contrôlées (dans le coffre, existantes ou non selon le type),
    relatives au coffre. Sert au dépôt ET à l'application (04/11, T49 : rejouées au moment d'écrire)."""
    from . import memory

    notes = [str(n).strip() for n in (notes or []) if str(n).strip()]
    if not notes or (kind == "fusion") != (len(notes) >= 2) or len(notes) > 4:
        raise ValueError("fusion : 2 à 4 notes (la gardée d'abord) ; consolidation, perimee, nouvelle : "
                         "une seule note")
    rels = []
    for n in notes:
        if kind == "nouvelle" and not n.endswith(".md"):
            raise ValueError("nouvelle : chemin de la note en .md (ex. projets/sujet.md)")
        path = memory._safe(n)
        if kind == "nouvelle" and path.exists():
            raise ValueError(f"la note {n} existe déjà : propose plutôt une consolidation")
        if kind != "nouvelle" and not path.exists():
            raise ValueError(f"pas de note {n}")
        rels.append(str(path.relative_to(memory.VAULT.resolve())))
    if len(set(rels)) != len(rels):
        raise ValueError("la même note deux fois")
    return rels


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def propose(kind: str, notes: list[str], text: str, reason: str, now: float | None = None,
            folder: Path | None = None) -> dict:
    """Dépose UNE proposition de ménage dans `runtime/vault-propositions/` (outil MCP
    `memory_propose`) : le coffre n'est PAS touché. `fusion` : `notes[0]` est la note
    gardée (`text` = son nouveau contenu), les autres deviennent un RENVOI vers elle,
    jamais supprimées. `consolidation` / `perimee` : une note, `text` = sa réécriture.
    `nouvelle` : une note qui N'EXISTE PAS encore (chemin `.md` dans le coffre), `text` = son contenu.
    Le texte passe par le filtre de la mémoire (consigne injectée refusée, secrets masqués).
    L'utilisateur valide, puis Astrel applique avec memory_write (sauvegarde, concurrence)."""
    from . import memory

    kind = str(kind or "").strip().lower()
    if kind not in PROPOSAL_KINDS:
        raise ValueError(f"type inconnu : {kind!r} (fusion, consolidation, perimee ou nouvelle ; "
                         "jamais de suppression)")
    rels = _cibles(kind, notes)
    text, remarks = memory_scan.clean_for_write(str(text or ""))
    reason, _ = memory_scan.clean_for_write(str(reason or ""))
    if not text.strip():
        raise ValueError("texte proposé vide")
    if len(text) > PROPOSAL_MAX_CHARS:
        raise ValueError(f"texte proposé trop long ({len(text)} caractères, {PROPOSAL_MAX_CHARS} au plus)")
    now = now or time.time()
    folder = folder or PROPOSALS_DIR
    day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    if len(list(folder.glob(f"{day}-*.json"))) >= PROPOSALS_PER_DAY:
        raise ValueError(f"{PROPOSALS_PER_DAY} propositions déjà déposées aujourd'hui : on s'arrête là")
    kept = Path(rels[0]).stem
    data = {
        "date": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"), "type": kind, "statut": "en attente",
        "note": rels[0], "texte_propose": text.strip(), "raison": " ".join(reason.split())[:600],
        "renvois": {r: f"# {Path(r).stem.replace('-', ' ')}\n\nFusionnée dans [[{kept}]] le {day} ; "
                       f"tout le contenu y est.\n" for r in rels[1:]},
        # 04/11 (T49) : contenu des notes au dépôt ; l'application refuse si elles ont changé depuis
        "empreinte_note": ({} if kind == "nouvelle" else
                           {r: _sha((memory.VAULT / r).read_text(encoding="utf-8")) for r in rels}),
        "appliquer": ("après un oui explicite de l'utilisateur seulement : memory_write (create) de la note."
                      if kind == "nouvelle" else
                      "après un oui explicite de l'utilisateur seulement : memory_read puis memory_write "
                      "(replace) de la note, puis des renvois ; aucune suppression."),
    }
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(kept))[:50].strip("-") or "note"
    path = folder / f"{day}-{slug}-{kind}.json"
    i = 2
    while path.exists():
        path = folder / f"{day}-{slug}-{kind}-{i}.json"
        i += 1
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return {"proposition": str(path), "note": rels[0], "renvois": rels[1:], "remarques": remarks,
            "message": "Proposition déposée, rien n'est modifié dans le coffre."}


def compact(report: dict) -> dict:
    """Le rapport sans les détails inutiles à Claude (pour l'outil MCP)."""
    out = {"resume": summary(report), "rapport_complet": str(REPORT_MD)}
    for cat in _CATEGORIES:
        if report[cat]:
            out[cat] = report[cat]
    return out


# --- 04/11 (T49) : écran « Propositions » du HUD (commandes du cœur, l'utilisateur seul) ---
#
# Les propositions attendaient sans écran depuis le 21/10. L'utilisateur les voit dans le HUD, texte EXACT qui
# sera écrit, et choisit Appliquer ou Refuser. L'empreinte lie ce qui est affiché à ce qui est appliqué :
# `runtime/vault-propositions/` n'est pas protégé, un fichier réécrit entre l'affichage et le clic est refusé.

PROPOSITION_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+$")
EN_ATTENTE = "en attente"
PROPOSITIONS_MAX = 50


def _empreinte(data: dict) -> str:
    vue = {k: data.get(k) for k in ("type", "note", "texte_propose", "renvois")}
    return _sha(json.dumps(vue, ensure_ascii=False, sort_keys=True))[:16]


def _fichier(pid: str, folder: Path | None = None) -> Path:
    pid = str(pid or "").strip()
    if not PROPOSITION_ID.match(pid):
        raise ValueError("proposition inconnue")
    base = (folder or PROPOSALS_DIR).resolve()
    path = (base / f"{pid}.json").resolve()
    if path.parent != base or not path.is_file():
        raise ValueError("proposition inconnue")
    return path


def _charger(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("type") not in PROPOSAL_KINDS or not isinstance(data.get("note"), str):
        return None
    if not isinstance(data.get("texte_propose"), str) or not isinstance(data.get("renvois") or {}, dict):
        return None
    return data


def _texte_ecrit(note: str, texte: str) -> str:
    """Ce que `memory.write` écrira vraiment (filtre de la mémoire, en-tête YAML en tête, titre)."""
    from . import memory

    clean, _ = memory_scan.clean_for_write(texte)
    return memory._avec_titre(memory.VAULT / note, clean)


def lister(folder: Path | None = None, toutes: bool = False) -> list[dict]:
    """Propositions (en attente seulement, sauf `toutes`), la plus récente d'abord. Un fichier abîmé est ignoré."""
    folder = folder or PROPOSALS_DIR
    out = []
    for path in sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []:
        pid = path.stem
        data = _charger(path) if PROPOSITION_ID.match(pid) else None
        if data is None or (not toutes and data.get("statut") != EN_ATTENTE):
            continue
        out.append({
            "proposition_id": pid, "date": str(data.get("date") or ""), "type": data["type"],
            "statut": str(data.get("statut") or ""), "note": data["note"],
            "renvois": sorted(data.get("renvois") or {}), "raison": str(data.get("raison") or ""),
            "texte": _texte_ecrit(data["note"], data["texte_propose"]), "empreinte": _empreinte(data),
            "traitee": data.get("traitee") or None,
        })
        if len(out) >= PROPOSITIONS_MAX:
            break
    return out


def en_attente(folder: Path | None = None) -> int:
    folder = folder or PROPOSALS_DIR
    n = 0
    for path in folder.glob("*.json") if folder.is_dir() else []:
        data = _charger(path) if PROPOSITION_ID.match(path.stem) else None
        n += bool(data and data.get("statut") == EN_ATTENTE)
    return n


def _ecrire(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _a_traiter(pid: str, folder: Path | None) -> tuple[Path, dict]:
    path = _fichier(pid, folder)
    data = _charger(path)
    if data is None:
        raise ValueError("proposition illisible")
    if data.get("statut") != EN_ATTENTE:
        raise ValueError(f"proposition déjà traitée ({data.get('statut')})")
    return path, data


def appliquer(pid: str, empreinte: str, par: str = "utilisateur", folder: Path | None = None) -> dict:
    """Écrit la proposition dans le coffre (par `memory.write` : versions gardées, texte refiltré), si elle est
    encore en attente, que son empreinte est celle qu'a vue l'utilisateur et que ses notes n'ont pas changé."""
    from . import memory

    path, data = _a_traiter(pid, folder)
    if str(empreinte or "") != _empreinte(data):
        raise ValueError("la proposition a changé depuis son affichage : relis-la")
    kind, renvois = data["type"], dict(data.get("renvois") or {})
    rels = _cibles(kind, [data["note"], *renvois])
    for rel, sha in (data.get("empreinte_note") or {}).items():
        try:
            courant = (memory.VAULT / rel).read_text(encoding="utf-8")
        except OSError:
            courant = ""
        if _sha(courant) != sha:
            raise ValueError(f"la note {rel} a changé depuis la proposition : elle n'est pas appliquée")
    ecrites = [memory.write(rels[0], data["texte_propose"], "create" if kind == "nouvelle" else "replace")]
    for rel in rels[1:]:
        ecrites.append(memory.write(rel, renvois.get(rel) or "", "replace"))
    data.update(statut="appliquée", traitee={"le": datetime.now().strftime("%Y-%m-%d %H:%M"), "par": par,
                                              "resultat": ecrites})
    _ecrire(path, data)
    return {"ok": True, "notes": ecrites}


def refuser(pid: str, raison: str = "", par: str = "utilisateur", folder: Path | None = None) -> dict:
    path, data = _a_traiter(pid, folder)
    raison, _ = memory_scan.clean_for_write(str(raison or ""))
    data.update(statut="refusée", traitee={"le": datetime.now().strftime("%Y-%m-%d %H:%M"), "par": par,
                                            "raison": " ".join(raison.split())[:600]})
    _ecrire(path, data)
    return {"ok": True}
