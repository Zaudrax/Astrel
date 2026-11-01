#!/usr/bin/env python3
"""Captures du HUD par thème, en Chrome HEADLESS (rien ne s'affiche à l'écran), sur la démo
(tools/hud_demo.py, port à part). 21/10 (plan P17, thèmes Classique et Premium) ; 25/10 (plan P35) :
Premium est le SEUL thème (Classique abandonné), `--themes` n'accepte plus que « premium ».

    .venv/bin/python tools/hud_check_theme.py --out /tmp/captures-p35/avant
    .venv/bin/python tools/hud_check_theme.py --out /tmp/captures-p35/apres --compare /tmp/captures-p35/avant
    .venv/bin/python tools/hud_check_theme.py --out /tmp/captures-p35/themes --themes premium --modes dark,light

Scènes : fil (page principale), Réglages, Plans, Journal, boîte de dialogue (Stop tout ; Éteindre jusqu'au 28/10).
Pour comparer deux captures au pixel près, le décor animé est figé : orbe (et graphe 3D, retiré
le 23/10) masqués, animations et transitions coupées, curseur de saisie invisible. `--compare` rend
le nombre de pixels différents par scène.
22/10 (plan P23) : `--affichage simple` capture l'affichage Simple ; avec `--compare`, l'interrupteur
« Simple » et la ligne « Affichage » des Réglages (seuls ajouts visibles en Avancé) sont retirés.
23/10 (plan P28) : `--masque "#graph-tools,#brain-btn,#effort-badge"` retire ces éléments (display: none,
avant la première peinture) des DEUX côtés d'une comparaison : captures « avant » prises avec la même
option (elle pose aussi le masque P23). Sert à prouver qu'en retirant Recadrer/Orbite et en ajoutant
le sélecteur d'effort, rien d'autre n'a bougé.
Le mode est choisi comme le ferait l'utilisateur : commande `settings_set` du HUD (réglage
`theme_mode` ; plus de réglage `theme` depuis P35), sans rechargement de la page.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
PORT = 4797       # ni le vrai Astrel (4719) ni hud_check_rendu (4799)
CDP_PORT = 9337
SCENES = ("fil", "reglages", "plans", "journal", "modale")
KEEP_GRAPH = False
FREEZE = """(() => {
  const s = document.createElement('style'); s.id = 'freeze-for-capture';
  s.textContent = `*, *::before, *::after { animation: none !important; transition: none !important;
    caret-color: transparent !important; }
    #graph, #orb-premium { visibility: hidden !important; }
    #stats, .task .time, .grant .time, .alert-since, .mission-budget .time, .count, .audit-when, .plan-item-date,
    .plan-meta, .plan-entry-head, .plan-item-meta, #toasts { visibility: hidden !important; }`;
  document.head.append(s); if (document.activeElement) document.activeElement.blur(); return true; })()"""
# 22/10 (plan P23) : avec --compare, les DEUX seuls ajouts visibles en affichage Avancé (interrupteur
# « Simple » de la barre, ligne « Affichage » des Réglages › Apparence) sont retirés de la page
# (22/10 soir : et le ↻ du panneau Abonnement, la ligne « Suivre le quota hors d'Astrel »)
# (display: none, donc sans place gardée) : la comparaison prouve que RIEN d'autre n'a bougé.
# Posé AVANT la première peinture (script de début de document) : retiré après coup, l'interrupteur
# laissait ±1 niveau sur ≈ 700 pixels de la barre floutée derrière les modales (Plans, Éteindre),
# mesuré le 22/10 ; posé tôt, 0 pixel (artefact de repeinture, pas un changement du Classique).
MASK_P23 = """document.addEventListener('readystatechange', () => {
  if (document.getElementById('mask-p23')) return;
  const s = document.createElement('style'); s.id = 'mask-p23';
  s.textContent = `.setting[data-key="affichage"], #quota-refresh, .setting[data-key="quota_refresh"]
    { display: none !important; }`;
  (document.head || document.documentElement).append(s); });"""
MASK = False
MASQUE = ""  # 23/10 (plan P28) : sélecteurs retirés des deux côtés (option --masque)


def masque_js(selectors: str) -> str:
    """Script de début de document : `display: none` sur les sélecteurs donnés (séparés par des virgules)."""
    css = json.dumps(f"{selectors} {{ display: none !important; }}")
    return ("""document.addEventListener('readystatechange', () => {
  if (document.getElementById('mask-p28')) return;
  const s = document.createElement('style'); s.id = 'mask-p28'; s.textContent = """ + css + """;
  (document.head || document.documentElement).append(s); });""")


class Page:
    def __init__(self, ws):
        self.ws, self.n, self.waiters = ws, 0, {}

    async def pump(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.waiters:
                self.waiters.pop(msg["id"]).set_result(msg)

    async def send(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.waiters[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        msg = await asyncio.wait_for(fut, 20)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def js(self, expr):
        r = await self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS : {r['exceptionDetails']}")
        return r["result"].get("value")

    async def until(self, expr, timeout=8.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if await self.js(expr):
                return True
            await asyncio.sleep(0.05)
        return False

    async def shot(self, path: Path):
        r = await self.send("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(r["data"]))


async def settle(p: Page):
    await p.js("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    await asyncio.sleep(0.4)


async def close_panels(p: Page):
    for _ in range(3):
        await p.js("(() => { const b = document.querySelector('.panel-back, .modal-back'); "
                   "if (!b) return 0; document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); "
                   "const x = b.querySelector('.modal-x'); if (x) x.click(); return 1; })()")
        await asyncio.sleep(0.25)


async def set_theme(p: Page, theme: str, mode: str):
    """Comme un clic dans les Réglages : `settings_set theme_mode` par la connexion du HUD.
    25/10 (plan P35) : Premium seul, data-theme="premium" toujours posé (index.html)."""
    await p.js(f"""window.__ws.send(JSON.stringify({{cmd: 'settings_set', key: 'theme_mode',
        value: {json.dumps(mode)}, id: 'cap' + Math.random()}})), true""")
    await asyncio.sleep(0.4)
    want = "document.documentElement.dataset.theme === 'premium'"
    if mode in ("dark", "light"):
        want += f" && document.documentElement.dataset.mode === {json.dumps(mode)}"
    ok = await p.until(want, 5)
    print(f"  thème {theme}/{mode} posé : {ok}", flush=True)


async def scenes(p: Page, out: Path, tag: str, only: tuple[str, ...]):
    await close_panels(p)
    # mouvement réellement appliqué (avant le gel des captures) : nom d'animation d'un message,
    # propriétés en transition d'un bouton (jamais « all »)
    motion = await p.js("""(() => { const m = document.querySelector('.msg.astrel'), c = document.querySelector('.chip');
        return { msg: m && getComputedStyle(m).animationName, chip: c && getComputedStyle(c).transitionProperty }; })()""")
    print(f"  {tag or 'défaut-'}mouvement : {motion}", flush=True)
    await p.js(FREEZE if not KEEP_GRAPH else FREEZE.replace("#graph, #orb-premium { visibility: hidden !important; }", ""))
    await settle(p)
    if "fil" in only:
        await p.shot(out / f"{tag}fil.png")
    if "reglages" in only:
        await p.js("document.querySelector('#settings-chip').click()")
        await p.until("!!document.querySelector('.settings-group')", 8)
        # la section Apparence dépliée si elle existe (cartes d'aperçu), sinon Voix
        await p.js("""(() => { const g = document.querySelector('details[data-group="apparence"]')
            || document.querySelector('details[data-group="voix"]'); if (g) { g.open = true; g.scrollIntoView(); } })()""")
        await settle(p)
        await p.shot(out / f"{tag}reglages.png")
        await close_panels(p)
    if "plans" in only:
        await p.js("document.querySelector('#plans-chip').click()")
        await p.until("!!document.querySelector('.plan-item')", 8)
        await p.js("(() => { const i = document.querySelector('.plan-item'); if (i) i.click(); })()")
        await settle(p)
        await p.shot(out / f"{tag}plans.png")
        await close_panels(p)
    if "journal" in only:
        await p.js("document.querySelector('#audit-chip').click()")
        await p.until("!!document.querySelector('.audit-item')", 8)
        await settle(p)
        await p.shot(out / f"{tag}journal.png")
        await close_panels(p)
    if "modale" in only:
        # 28/10 (T10) : bouton Éteindre retiré de la barre ; boîte rouge de « Stop tout » à la place
        await p.js("document.querySelector('#estop-chip').click()")
        await p.until("!!document.querySelector('.modal')", 5)
        await settle(p)
        await p.shot(out / f"{tag}modale.png")
        await close_panels(p)


async def run(url, args):
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
    ws_url = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
    out = Path(args.out)
    async with websockets.connect(ws_url, max_size=80_000_000) as ws:
        p = Page(ws)
        pump = asyncio.create_task(p.pump())
        await p.send("Page.enable")
        await p.send("Runtime.enable")
        await p.send("Page.addScriptToEvaluateOnNewDocument", source="""
            const send = WebSocket.prototype.send;
            WebSocket.prototype.send = function (d) { window.__ws = this; return send.call(this, d); };""")
        if MASK:
            await p.send("Page.addScriptToEvaluateOnNewDocument", source=MASK_P23)
        if MASQUE:
            await p.send("Page.addScriptToEvaluateOnNewDocument", source=masque_js(MASQUE))
        if args.reduced_motion:
            await p.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        if args.scheme:
            await p.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": args.scheme}])
        await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
        await p.send("Page.navigate", url=url)
        if not await p.until("!!document.querySelector('#transcript') && !!document.querySelector('.channel-tab')", 20):
            raise SystemExit("HUD non chargé")
        # la séquence de la démo finit sur une carte de confirmation (≈ 16 s) : décor stable ensuite
        await p.until("!document.querySelector('#confirm').classList.contains('hidden')", 30)
        await asyncio.sleep(args.wait)
        only = tuple(s for s in args.scenes.split(",") if s in SCENES)
        if args.affichage:  # 22/10 (plan P23) : comme un clic sur l'interrupteur « Simple »
            await p.js(f"""window.__ws.send(JSON.stringify({{cmd: 'settings_set', key: 'affichage',
                value: {json.dumps(args.affichage)}, id: 'cap' + Math.random()}})), true""")
            want = "document.documentElement.dataset.affichage === 'simple'" if args.affichage == "simple" else \
                "!document.documentElement.dataset.affichage"
            print(f"  affichage {args.affichage} posé : {await p.until(want, 5)}", flush=True)
        if not args.themes:
            await scenes(p, out, "", only)
        else:
            for theme in args.themes.split(","):
                for mode in args.modes.split(","):
                    await p.js("document.getElementById('freeze-for-capture')?.remove()")
                    await set_theme(p, theme, mode)
                    await scenes(p, out, f"{theme}-{mode}-", only)
        pump.cancel()


def compare(out: Path, ref: Path) -> int:
    import numpy as np
    from PIL import Image, ImageChops

    worst = 0
    for img in sorted(ref.glob("*.png")):
        other = out / img.name
        if not other.exists():
            print(f"  {img.name} : non capturée ici (scène non demandée)")
            continue
        a, b = Image.open(img).convert("RGB"), Image.open(other).convert("RGB")
        if a.size != b.size:
            print(f"  {img.name} : tailles différentes {a.size} / {b.size}")
            worst = max(worst, 1)
            continue
        diff = ImageChops.difference(a, b)
        n = int(np.count_nonzero(np.asarray(diff).any(axis=2)))
        box = diff.getbbox()
        print(f"  {img.name} : {n} pixels différents" + (f" (zone {box})" if box else ""))
        if n:
            diff.point(lambda v: 255 if v else 0).save(out / f"diff-{img.name}")
            worst = max(worst, n)
    return worst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/captures-p17/capture")
    ap.add_argument("--compare", default="")
    ap.add_argument("--themes", default="")
    ap.add_argument("--modes", default="dark")
    ap.add_argument("--scenes", default=",".join(SCENES))
    ap.add_argument("--wait", type=float, default=2.0)
    ap.add_argument("--reduced-motion", action="store_true")
    ap.add_argument("--scheme", default="", help="prefers-color-scheme émulé (light/dark), pour le mode Auto")
    ap.add_argument("--keep-graph", action="store_true", help="garder l'orbe (captures non comparables)")
    ap.add_argument("--affichage", default="", choices=("", "simple", "avance"), help="affichage du HUD (plan P23)")
    ap.add_argument("--masque", default="", help="sélecteurs CSS retirés des deux côtés d'une comparaison (plan P28)")
    args = ap.parse_args()
    autres = [t for t in args.themes.split(",") if t and t != "premium"]
    if autres:  # 25/10 (plan P35) : thème Classique abandonné
        print(f"thème inconnu : {', '.join(autres)} (Premium est le seul thème depuis le plan P35)")
        return 2
    global KEEP_GRAPH, MASK, MASQUE
    KEEP_GRAPH = args.keep_graph
    MASQUE = ",".join(x.strip() for x in args.masque.split(",") if x.strip())
    MASK = bool(args.compare) or bool(MASQUE)
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        print("Chrome introuvable")
        return 2
    Path(args.out).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, ASTREL_HUD_PORT=str(PORT), PYTHONUNBUFFERED="1")
    demo = subprocess.Popen([sys.executable, str(ROOT / "tools" / "hud_demo.py"), "--once"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    profile = tempfile.mkdtemp(prefix="astrel-chrome-theme-")
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
            print("La démo n'a pas donné d'adresse")
            return 2
        browser = subprocess.Popen([chrome, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
                                    f"--user-data-dir={profile}", "--no-first-run", "--disable-gpu",
                                    "--hide-scrollbars", "--force-color-profile=srgb",
                                    "--window-size=1600,1000", "about:blank"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        asyncio.run(run(url, args))
    finally:
        if browser:
            browser.terminate()
        demo.terminate()
        shutil.rmtree(profile, ignore_errors=True)
    print(f"captures dans {args.out}")
    if args.compare:
        worst = compare(Path(args.out), Path(args.compare))
        return 1 if worst else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
