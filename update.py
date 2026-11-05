#!/usr/bin/env python3
"""Met Astrel à jour depuis le dépôt Git — Linux, macOS, Windows.

    python3 update.py        (ou ./update.sh)
    py update.py             (Windows ; ou .\\update.ps1)

1. `git pull --ff-only` : n'avance que si ta copie n'a pas divergé (aucune
   fusion faite dans ton dos) ;
2. réinstalle les dépendances Python ou Node SEULEMENT si leur liste a changé ;
3. ajoute les nouveaux réglages d'exemple, sans toucher aux tiens ;
4. retient la nouvelle empreinte du garde-fou si (et seulement si) tes fichiers
   sont exactement ceux du dépôt — sinon le contrôle de santé t'alertera ;
5. relance Astrel s'il tourne en service (Linux).

Tes réglages (`config/*.json`), ta mémoire (`vault/`), `runtime/` et `logs/`
ne sont pas suivis par Git : une mise à jour n'y touche jamais.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WATCHED = {
    "python": ["requirements.txt"],
    "mcp": ["mcp-servers/package.json", "mcp-servers/package-lock.json"],
    "hud": ["hud/package.json", "hud/package-lock.json"],
    # 05/11 (T45) : pont WhatsApp et worker des images et vidéos IA, réinstallés à la version du package-lock
    "whatsapp": ["whatsapp/package.json", "whatsapp/package-lock.json"],
    "imagegen": ["imagegen/package.json", "imagegen/package-lock.json"],
}
# Dossiers Node installés par `npm ci` (versions FIXÉES du package-lock), comme dans install.py.
NPM_CI = ("whatsapp", "imagegen")
# Ce qui décide de laisser passer une action : son empreinte est retenue par le doctor.
GUARD_PREFIXES = ("astrel/", "hooks/")


def _load_install():
    import importlib.util

    spec = importlib.util.spec_from_file_location("astrel_install", ROOT / "install.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["astrel_install"] = mod
    spec.loader.exec_module(mod)
    return mod


def digest(root: Path, files: list[str]) -> str:
    h = hashlib.sha256()
    for rel in files:
        p = root / rel
        h.update(rel.encode())
        h.update(p.read_bytes() if p.exists() else b"<absent>")
    return h.hexdigest()


def git(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def local_changes(root: Path = ROOT) -> list[str]:
    """Fichiers SUIVIS modifiés localement (les non suivis ne gênent pas un pull)."""
    out = git("status", "--porcelain", "--untracked-files=no", root=root).stdout
    return [line[3:] for line in out.splitlines() if line.strip()]


def changed_files(before: str, after: str, root: Path = ROOT) -> list[str]:
    if before == after:
        return []
    return [f for f in git("diff", "--name-only", before, after, root=root).stdout.splitlines() if f]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Met Astrel à jour (git pull --ff-only, dépendances si besoin, relance).")
    ap.add_argument("--sans-relance", action="store_true", help="ne pas relancer le service")
    args = ap.parse_args(argv)
    inst = _load_install()
    py = inst.venv_python(ROOT)

    if not (ROOT / ".git").exists() or not shutil.which("git"):
        print("Ce dossier n'est pas un dépôt Git (ou git est absent) : la mise à jour passe par `git clone`.")
        return 1
    dirty = local_changes()
    if dirty:
        print("Des fichiers d'Astrel ont été modifiés sur cette machine :")
        for f in dirty[:20]:
            print(f"   {f}")
        print("La mise à jour les écraserait. Mets-les de côté (`git stash`) ou annule-les "
              "(`git checkout -- FICHIER`), puis relance.")
        return 1

    before_rev = git("rev-parse", "HEAD").stdout.strip()
    before = {k: digest(ROOT, v) for k, v in WATCHED.items()}
    print("1/4 Récupération (git pull --ff-only)")
    pull = git("pull", "--ff-only")
    print("   " + (pull.stdout.strip() or pull.stderr.strip()).replace("\n", "\n   "))
    if pull.returncode != 0:
        print("La mise à jour a échoué (copie divergente ou réseau). Rien n'a été changé.")
        return 1
    after_rev = git("rev-parse", "HEAD").stdout.strip()
    changed = changed_files(before_rev, after_rev)
    if not changed:
        print("Déjà à jour.")
        return 0

    print("2/4 Dépendances")
    after = {k: digest(ROOT, v) for k, v in WATCHED.items()}
    if before["python"] != after["python"]:
        inst.run([py, "-m", "pip", "install", "-q", "-r", ROOT / "requirements.txt"])
        print("   Python : réinstallées")
    if before["mcp"] != after["mcp"] and shutil.which("npm"):
        inst.run(["npm", "install", "--no-audit", "--no-fund", "--silent"], cwd=ROOT / "mcp-servers")
        print("   Node (MCP) : réinstallées")
    for nom in NPM_CI:
        if before[nom] != after[nom] and shutil.which("npm") and (ROOT / nom / "package-lock.json").exists():
            code = inst.run(["npm", "ci", "--no-audit", "--no-fund", "--silent"], cwd=ROOT / nom, check=False)
            print(f"   Node ({nom}) : " + ("réinstallées" if code == 0 else f"échec, relance « cd {nom} && npm ci »"))
    if all(before[k] == after[k] for k in ("python", "mcp", *NPM_CI)):
        print("   inchangées")

    print("3/4 Réglages et garde-fou")
    copied = inst.copy_examples(ROOT)
    if copied:
        print("   nouveaux réglages : " + ", ".join(copied))
    guard_touched = any(f.startswith(GUARD_PREFIXES) for f in changed)
    if guard_touched and inst.system() == "linux" and not local_changes():
        # Fichiers identiques à ceux du dépôt : la nouvelle empreinte est celle du dépôt.
        inst.run([py, "-m", "astrel", "--doctor", "--accept"], check=False)
    elif guard_touched:
        print("   garde-fou modifié : lance `./run.sh --doctor` pour vérifier.")

    print("4/4 Relance")
    if args.sans_relance:
        print("   passée (--sans-relance)")
    elif inst.system() == "linux" and inst.has_systemd() and subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", "astrel"]).returncode == 0:
        inst.run(["systemctl", "--user", "restart", "astrel"])
        print("   service relancé. La bulle et le HUD se reconnectent seuls.")
    else:
        print("   relance Astrel toi-même pour prendre la mise à jour.")
    print(f"\nÀ jour : {len(changed)} fichier(s) changé(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
