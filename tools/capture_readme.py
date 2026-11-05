#!/usr/bin/env python3
"""Captures du HUD pour les README du dépôt public (05/11) : la MÊME scène de la démo
(tools/hud_demo.py : fil, question à choix, carte de confirmation, agents en cours), en français
(docs/images/hud.png) et en anglais (docs/images/hud-en.png : interface `ui_language: en` ET contenu
de la démo en anglais, `hud_demo.py --langue en`).

    .venv/bin/python tools/capture_readme.py                  # les deux, dans tools/depot/docs/images/
    .venv/bin/python tools/capture_readme.py --langues en     # l'anglaise seule
    .venv/bin/python tools/capture_readme.py --sortie /tmp/x  # ailleurs (pour comparer)

Chrome HEADLESS (rien à l'écran, DISPLAY et WAYLAND_DISPLAY retirés), 1600 × 1000 à l'échelle 1, thème
Premium clair (« auto » en headless), ports à part (4796, CDP 9336). Un seul Chrome headless à la fois :
attend que les autres aient fini (sonde de /proc, qui ne se voit pas elle-même).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from hud_check_langue import COLLECT_JS, french  # noqa: E402  (relevé des textes visibles)
from hud_check_rendu import SETTLE_JS, Page  # noqa: E402  (même pilote DevTools)
from export_depot import PERSONAL_DEFAULT  # noqa: E402  (marques retirées du dépôt public)

PORT = 4796
CDP_PORT = 9336
SORTIE = ROOT / "tools" / "depot" / "docs" / "images"
NOMS = {"fr": "hud.png", "en": "hud-en.png"}
LARGEUR, HAUTEUR = 1600, 1000
CHROMES = {"chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome-headless-shell"}


def chromes_headless() -> list[int]:
    """pid des Chrome headless PRINCIPAUX en cours (ni leurs sous-processus `--type=…`, ni un programme qui
    lance Chrome sans en être un, comme le serveur MCP Playwright) ; lu dans /proc, la sonde ne se voit pas."""
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            argv = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [a.decode("utf-8", "replace") for a in argv if a]
        if args and Path(args[0]).name in CHROMES and any(a.startswith("--headless") for a in args) \
                and not any(a.startswith("--type=") for a in args):
            out.append(int(d.name))
    return out


def attendre_chrome(limite: float = 900.0) -> bool:
    """Un seul Chrome headless à la fois : attend que les autres aient fini, `limite` secondes au plus."""
    t0, dit = time.time(), False
    while time.time() - t0 < limite:
        autres = chromes_headless()
        if not autres:
            return True
        if not dit:
            print(f"  un autre Chrome headless tourne ({autres}) : j'attends qu'il finisse…", flush=True)
            dit = True
        time.sleep(2)
    return False


SCENE_JS = """(() => { const q = (s) => document.querySelector(s);
  return !q('#confirm').classList.contains('hidden') && !!q('.msg.choice .choice-question')
    && document.querySelectorAll('#tasks .task').length >= 3 && !q('.msg.astrel.live'); })()"""


async def scene(url: str, langue: str, fichier: Path) -> bool:
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
    ws_url = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
    import websockets
    async with websockets.connect(ws_url, max_size=50_000_000) as ws:
        p = Page(ws)
        pump = asyncio.create_task(p.pump())
        await p.send("Page.enable")
        await p.send("Runtime.enable")
        # langue posée AVANT le premier rendu (localStorage du HUD, comme après un choix dans les Réglages) ;
        # la démo publie le même `ui_language` : pas de rechargement
        await p.send("Page.addScriptToEvaluateOnNewDocument",
                     source=f"try {{ localStorage.setItem('astrel-lang', {json.dumps(langue)}); }} catch {{}}")
        await p.send("Emulation.setDeviceMetricsOverride", width=LARGEUR, height=HAUTEUR, deviceScaleFactor=1, mobile=False)
        await p.send("Page.navigate", url=url)
        if not await p.until("!!document.querySelector('.channel-tab')", 20):
            print("  le HUD ne s'est pas chargé")
            return False
        # séquence de la démo (question, outils, réponse dite) puis la carte de confirmation : ~ 20 s
        if not await p.until(SCENE_JS, 60):
            print("  la scène (confirmation, question à choix, trois agents) n'est pas venue")
            return False
        await asyncio.sleep(1.0)
        # la question du matin a ouvert le canal où elle est rangée : retour au canal par défaut, comme d'un clic
        # (comptes rendus « Blocage » et « Résultat », question à choix)
        await p.js("document.querySelector('.channel-tab[data-chan=\"general\"]')?.click(); true")
        if not await p.until("document.querySelector('.channel-tab.on')?.dataset.chan === 'general'", 5):
            print("  le canal par défaut ne s'est pas ouvert")
            return False
        await asyncio.sleep(1.0)
        lang = await p.js("document.documentElement.lang")
        if lang != langue:
            print(f"  langue de la page « {lang} », attendue « {langue} »")
            return False
        await p.js("document.querySelectorAll('.toast, .menu').forEach(e => e.remove()); document.activeElement?.blur(); "
                   "(() => { const t = document.querySelector('#transcript'); t.scrollTop = t.scrollHeight; })(); true")
        await asyncio.sleep(0.4)
        await p.js(SETTLE_JS)
        # rien de personnel à l'écran (marques du dépôt public, chemins d'une machine) ; en anglais, aucun texte
        # qui a l'air français (commandes vocales citées entre « » mises à part, comme hud_check_langue)
        texte = await p.js("document.body.innerText")
        perso = sorted({m.group(0) for rx in [*PERSONAL_DEFAULT, r"/home/(?!user\b)\w+", r"/data/\w+"]
                        for m in re.finditer(rx, texte)})
        if perso:
            print(f"  marques personnelles à l'écran : {perso}")
            return False
        if langue == "en":
            vus = await p.js(f"{COLLECT_JS}('#capture-readme-rien')")  # rien d'exclu : le contenu aussi
            restes = sorted({t for _k, _el, t in vus if french(t) and not re.fullmatch(r"[\w.:/-]+", t)})  # sauf piper:en_US-…
            if restes:
                print(f"  textes français dans la capture anglaise : {restes[:12]}")
                return False
        r = await p.send("Page.captureScreenshot", format="png")
        fichier.write_bytes(base64.b64decode(r["data"]))
        pump.cancel()
        return True


def capture(langue: str, fichier: Path, chrome: str) -> bool:
    env = dict(os.environ, ASTREL_HUD_PORT=str(PORT), PYTHONUNBUFFERED="1")
    for k in ("ASTREL_UI_LANGUAGE", "ASTREL_DEMO_LANGUE", "ASTREL_ASSISTANT_NAME", "DISPLAY", "WAYLAND_DISPLAY"):
        env.pop(k, None)
    demo = subprocess.Popen([sys.executable, str(ROOT / "tools" / "hud_demo.py"), "--once", "--langue", langue],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    profile = tempfile.mkdtemp(prefix="astrel-chrome-readme-")
    browser = None
    try:
        url, t0 = "", time.time()
        while time.time() - t0 < 30 and not url:
            line = demo.stdout.readline()
            if not line:
                break
            if "http://" in line:
                url = line[line.index("http://"):].strip()
        if not url:
            print("  la démo n'a pas donné d'adresse")
            return False
        threading.Thread(target=lambda: [None for _ in demo.stdout], daemon=True).start()  # tuyau vidé
        if not attendre_chrome():
            print("  un autre Chrome headless tourne toujours : rien de capturé")
            return False
        browser = subprocess.Popen([chrome, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
                                    f"--user-data-dir={profile}", "--no-first-run", "--disable-gpu",
                                    f"--window-size={LARGEUR},{HAUTEUR}", "--hide-scrollbars", "about:blank"],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(300):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        return asyncio.run(scene(url, langue, fichier))
    finally:
        if browser:
            browser.terminate()
            try:
                browser.wait(10)
            except subprocess.TimeoutExpired:
                browser.kill()
        demo.terminate()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--langues", default="fr,en", help="fr, en ou fr,en (défaut)")
    ap.add_argument("--sortie", type=Path, default=SORTIE, help="dossier des images")
    args = ap.parse_args()
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        print("Chrome introuvable")
        return 2
    langues = [x.strip() for x in args.langues.split(",") if x.strip()]
    if not langues or any(x not in NOMS for x in langues):
        print(f"langues possibles : {', '.join(NOMS)}")
        return 2
    args.sortie.mkdir(parents=True, exist_ok=True)
    ok = True
    for langue in langues:
        fichier = args.sortie / NOMS[langue]
        print(f"{langue} → {fichier}", flush=True)
        fait = capture(langue, fichier, chrome)
        print("  ok" if fait else "  ÉCHEC", flush=True)
        ok = ok and fait
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
