#!/usr/bin/env python3
"""Copie les icônes de services utiles au HUD depuis simple-icons (CC0), HORS LIGNE ensuite.

    .venv/bin/python tools/icones.py                       # remet à jour celles déjà là + connecteurs vus
    .venv/bin/python tools/icones.py Notion "Hugging Face" # en ajoute (nom du service ou slug)
    .venv/bin/python tools/icones.py --version 16.35.0 --dry-run Slack

19/10 (plan P10). Rien n'est écrit en dur ici : la liste des icônes est celle de
`hud/assets/icons/index.json` (ce qui a déjà été copié), plus les connecteurs claude.ai
déjà découverts (`runtime/connecteurs-vus.json`), plus les noms passés en argument.
Le paquet est téléchargé par `npm pack simple-icons@<version>` (version FIXÉE, comme les
serveurs MCP tiers) dans un dossier temporaire ; seuls les SVG retenus sont copiés.
Une icône dont simple-icons déclare une licence autre que CC0 est écartée (pastille à
lettre à la place). Le nom est résolu comme dans `astrel/icones.py` (slug, titre, alias,
puis sans le dernier mot). Aucun appel réseau pendant qu'Astrel tourne.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astrel import claudeai, icones  # noqa: E402

VERSION = icones.SIMPLE_ICONS_VERSION  # la même que la récupération automatique (astrel/icones.py)
ALLOWED_LICENSES = icones.ALLOWED_LICENSES


def _current() -> tuple[list[str], str]:
    try:
        data = json.loads((icones.ICON_DIR / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], ""
    icons = data.get("icons") if isinstance(data, dict) else None
    return (list(icons) if isinstance(icons, dict) else []), str(data.get("version") or "")


def _seen_connectors() -> list[str]:
    return claudeai.Connecteurs()._read_seen()


def _fetch(version: str, tmp: Path) -> tuple[list[dict], tarfile.TarFile]:
    subprocess.run(["npm", "pack", f"simple-icons@{version}", "--silent"], cwd=tmp, check=True,
                   stdout=subprocess.DEVNULL, timeout=300)
    tgz = next(tmp.glob("simple-icons-*.tgz"))
    tar = tarfile.open(tgz)
    data = json.load(tar.extractfile("package/data/simple-icons.json"))
    return data, tar


def _resolve(name: str, table: dict[str, dict]) -> dict | None:
    short = name.removeprefix(icones.PREFIX).strip()
    words = short.split()
    for key in [icones.slugify(short)] + ([icones.slugify(" ".join(words[:-1]))] if len(words) > 1 else []):
        if key and key in table:
            return table[key]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("names", nargs="*", help="services à ajouter (nom ou slug simple-icons)")
    ap.add_argument("--version", default=VERSION, help=f"version de simple-icons (défaut {VERSION})")
    ap.add_argument("--dry-run", action="store_true", help="dit ce qui serait copié, n'écrit rien")
    args = ap.parse_args(argv)

    kept, _ = _current()
    wanted = list(dict.fromkeys([*kept, *_seen_connectors(), *args.names]))
    with tempfile.TemporaryDirectory(prefix="astrel-icones-") as tmp:
        data, tar = _fetch(args.version, Path(tmp))
        table: dict[str, dict] = {}
        for icon in data:
            slug = icon.get("slug") or icones.slugify(icon["title"])
            icon = {**icon, "slug": slug}
            aka = (icon.get("aliases") or {}).get("aka") or []
            for key in (slug, icones.slugify(icon["title"]), *map(icones.slugify, aka)):
                table.setdefault(key, icon)
        index: dict[str, dict] = {}
        missing, refused = [], []
        for name in wanted:
            icon = _resolve(name, table)
            if icon is None:
                missing.append(name)
                continue
            lic = (icon.get("license") or {}).get("type")
            if lic and lic not in ALLOWED_LICENSES:
                refused.append(f"{name} ({lic})")
                continue
            index[icon["slug"]] = {"title": icon["title"], "hex": icon["hex"],
                                   "aliases": (icon.get("aliases") or {}).get("aka") or []}
        if not args.dry_run:
            icones.ICON_DIR.mkdir(parents=True, exist_ok=True)
            for slug in index:
                svg = tar.extractfile(f"package/icons/{slug}.svg").read()
                (icones.ICON_DIR / f"{slug}.svg").write_bytes(svg)
            for old in icones.ICON_DIR.glob("*.svg"):
                if old.stem not in index:
                    old.unlink()
            (icones.ICON_DIR / "index.json").write_text(json.dumps(
                {"source": "simple-icons (CC0-1.0), https://simpleicons.org", "version": args.version,
                 "icons": dict(sorted(index.items()))}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tar.close()
    size = sum(p.stat().st_size for p in icones.ICON_DIR.glob("*")) if icones.ICON_DIR.exists() else 0
    print(f"{len(index)} icônes{' (essai, rien écrit)' if args.dry_run else ''}, dossier {size // 1024} Ko")
    if missing:
        print("sans icône (pastille à lettre) : " + ", ".join(missing))
    if refused:
        print("écartées (licence) : " + ", ".join(refused))
    return 0


if __name__ == "__main__":
    sys.exit(main())
