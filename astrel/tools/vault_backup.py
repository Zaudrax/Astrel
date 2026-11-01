"""Sauvegardes du coffre de mémoire : rien de ce qui est écrasé n'est perdu.

Idée de Hermes Agent (`agent/curator_backup.py`, MIT), réécrite :

- VERSION d'une note : avant qu'une note existante soit écrasée (`memory_write`
  en `replace`, restauration), son contenu est copié dans
  `<sauvegardes>/versions/<note>/<horodatage>.md` ; `KEEP_VERSIONS` par note ;
- INSTANTANÉ du coffre : un `tar.gz` de tout `vault/`, au plus un par
  `SNAPSHOT_EVERY_S` et seulement si le coffre a changé (`snapshot_if_due`,
  appelé au démarrage du serveur MCP et par le conservateur) ; `KEEP_SNAPSHOTS`
  gardés, un manifeste JSON à côté de chacun ;
- RETOUR ARRIÈRE : commence toujours par sauvegarder l'état actuel, il est donc
  lui-même réversible ; il ne SUPPRIME jamais une note.

Les sauvegardes sont HORS du coffre (`runtime/vault-backups/`) : sinon la
recherche et le graphe verraient les anciennes versions. `ASTREL_VAULT_BACKUPS`
déplace le dossier.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import time
from datetime import datetime
from pathlib import Path

from .. import config

BACKUP_DIR = Path(os.environ.get("ASTREL_VAULT_BACKUPS") or config.RUNTIME / "vault-backups")
KEEP_VERSIONS = 20
KEEP_SNAPSHOTS = 14
SNAPSHOT_EVERY_S = 24 * 3600.0

_ID = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6}$")


def _new_id() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")


def _check_id(vid: str) -> str:
    vid = vid.strip().removesuffix(".md").removesuffix(".tar.gz")
    if not _ID.match(vid):
        raise ValueError(f"identifiant de sauvegarde invalide : {vid!r} (attendu : AAAA-MM-JJTHH-MM-SS-micro)")
    return vid


def _label(vid: str) -> str:
    d, t = vid.split("T")
    return f"{d} {t[:2]}:{t[3:5]}:{t[6:8]}"


def _versions_dir(vault: Path, note: Path) -> Path:
    rel = note.resolve().relative_to(vault.resolve())
    return BACKUP_DIR / "versions" / rel.with_suffix("")


# --- versions d'une note ---

def save_version(vault: Path, note: Path) -> str | None:
    """Copie la version actuelle de `note` ; rien si elle n'existe pas."""
    if not note.is_file():
        return None
    folder = _versions_dir(vault, note)
    folder.mkdir(parents=True, exist_ok=True)
    vid = _new_id()
    shutil.copy2(note, folder / f"{vid}.md")
    for old in sorted(folder.glob("*.md"))[:-KEEP_VERSIONS]:
        old.unlink(missing_ok=True)
    return vid


def list_versions(vault: Path, note: Path) -> list[dict]:
    """Versions gardées d'une note, de la plus récente à la plus ancienne."""
    folder = _versions_dir(vault, note)
    out = []
    for p in sorted(folder.glob("*.md"), reverse=True):
        if _ID.match(p.stem):
            out.append({"id": p.stem, "date": _label(p.stem), "caracteres": p.stat().st_size})
    return out


def read_version(vault: Path, note: Path, vid: str) -> str:
    path = _versions_dir(vault, note) / f"{_check_id(vid)}.md"
    if not path.is_file():
        raise FileNotFoundError(f"pas de version {vid} pour cette note")
    return path.read_text(encoding="utf-8")


def restore_version(vault: Path, note: Path, vid: str) -> str:
    """Rétablit une version ; la version actuelle est sauvegardée d'abord."""
    content = read_version(vault, note, vid)
    current = save_version(vault, note)
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(content, encoding="utf-8")
    msg = f"version du {_label(_check_id(vid))} rétablie"
    if current:
        msg += f" ; l'ancienne version actuelle est gardée sous l'identifiant {current}"
    return msg


# --- instantanés du coffre ---

def _files(vault: Path) -> list[Path]:
    return sorted(p for p in vault.rglob("*") if p.is_file())


def signature(vault: Path) -> str:
    h = hashlib.sha1()
    for p in _files(vault):
        st = p.stat()
        h.update(f"{p.relative_to(vault)}\0{st.st_mtime_ns}\0{st.st_size}\n".encode())
    return h.hexdigest()


def list_snapshots() -> list[dict]:
    folder = BACKUP_DIR / "snapshots"
    out = []
    for p in sorted(folder.glob("*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def snapshot(vault: Path, reason: str = "") -> str:
    folder = BACKUP_DIR / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    sid = _new_id()
    files = _files(vault)
    with tarfile.open(folder / f"{sid}.tar.gz", "w:gz") as tar:
        for p in files:
            tar.add(p, arcname=str(p.relative_to(vault)))
    manifest = {"id": sid, "date": _label(sid), "raison": reason, "fichiers": len(files),
                "octets": sum(p.stat().st_size for p in files), "signature": signature(vault)}
    (folder / f"{sid}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for old in sorted(folder.glob("*.tar.gz"))[:-KEEP_SNAPSHOTS]:
        old.unlink(missing_ok=True)
        old.with_name(old.name.removesuffix(".tar.gz") + ".json").unlink(missing_ok=True)
    return sid


def snapshot_if_due(vault: Path, reason: str = "quotidien", every_s: float = SNAPSHOT_EVERY_S) -> str | None:
    """Instantané si le dernier a plus de `every_s` ET que le coffre a changé depuis."""
    if not vault.is_dir():
        return None
    last = next(iter(list_snapshots()), None)
    if last:
        try:
            age = time.time() - datetime.strptime(last["id"], "%Y-%m-%dT%H-%M-%S-%f").timestamp()
        except (KeyError, ValueError):
            age = every_s
        if age < every_s or last.get("signature") == signature(vault):
            return None
    return snapshot(vault, reason)


def restore_snapshot(vault: Path, sid: str) -> str:
    """Remet les notes de l'instantané `sid` (instantané de l'état actuel d'abord).
    Les notes créées depuis restent : rien n'est supprimé."""
    sid = _check_id(sid)
    archive = BACKUP_DIR / "snapshots" / f"{sid}.tar.gz"
    if not archive.is_file():
        raise FileNotFoundError(f"pas d'instantané {sid}")
    before = snapshot(vault, f"avant retour à {sid}")
    vault.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(vault, filter="data")
    return f"coffre remis à l'instantané {_label(sid)} ; état précédent gardé sous {before}"
