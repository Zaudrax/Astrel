#!/usr/bin/env python3
"""Vérifie en Chrome HEADLESS (rien ne s'affiche à l'écran) le rendu markdown du
fil et les boîtes de dialogue du HUD (15/10), sur la démo (tools/hud_demo.py).
15/10 : page « Plans » (`check_plans`, mode plan).

    .venv/bin/python tools/hud_check_rendu.py          # captures dans /tmp/astrel-hud-check/

Lance la démo sur un port à part (4799 : le vrai Astrel garde 4719), pilote la
page par le protocole DevTools, contrôle le DOM et rend 0 si tout passe.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
PORT = 4799
CDP_PORT = 9339
OUT = Path("/tmp/astrel-hud-check")


class Page:
    def __init__(self, ws):
        self.ws, self.n, self.events, self.waiters = ws, 0, [], {}

    async def pump(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if "id" in msg and msg["id"] in self.waiters:
                self.waiters.pop(msg["id"]).set_result(msg)
            elif "method" in msg:
                self.events.append(msg)

    async def send(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.waiters[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        msg = await asyncio.wait_for(fut, 15)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def js(self, expr):
        r = await self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS : {r['exceptionDetails']}")
        return r["result"].get("value")

    async def key(self, key, code=None, vk=0, text=None, shift=False):
        base = {"key": key, "code": code or key, "windowsVirtualKeyCode": vk, "modifiers": 8 if shift else 0}
        await self.send("Input.dispatchKeyEvent", type="keyDown", **base, **({"text": text} if text else {}))
        await self.send("Input.dispatchKeyEvent", type="keyUp", **base)

    async def until(self, expr, timeout=8.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if await self.js(expr):
                return True
            await asyncio.sleep(0.05)
        return False

    async def shot(self, name):
        r = await self.send("Page.captureScreenshot", format="png")
        (OUT / name).write_bytes(base64.b64decode(r["data"]))


CHECKS: list[tuple[str, bool]] = []
MISSION_START: dict = {}  # commande « mission start » envoyée par le HUD, rejouée pour la mise en page


def check(label, ok):
    CHECKS.append((label, bool(ok)))
    print(("  ok  " if ok else "  ÉCHEC ") + label, flush=True)


# 25/10 (plan P35) : les contrôles tournent désormais en Premium (seul thème ; « auto » = clair en Chrome
# headless, comme le HUD réel de l'utilisateur). Les couleurs attendues sont lues dans les JETONS du thème
# posé (plus de valeurs en dur du Classique), après la fin des transitions Premium (couleurs en
# transition 150 ms : lues trop tôt, on obtenait la couleur de départ).
TOKEN_JS = """(v => { const s = document.createElement('span'); s.style.color = `var(${v})`; document.body.append(s);
  const c = getComputedStyle(s).color; s.remove(); return c; })"""
RGB_JS = "(v => getComputedStyle(document.documentElement).getPropertyValue(v).trim())"
SETTLE_STMT = """document.getAnimations().forEach(a => { try { if (a instanceof CSSTransition
  || (a instanceof CSSAnimation && a.effect?.getComputedTiming().endTime !== Infinity)) a.finish(); } catch {} })"""
SETTLE_JS = f"({SETTLE_STMT}, true)"


async def premium_mode(p, mode: str = "auto") -> bool:
    """25/10 (plan P35) : Premium est le SEUL thème du HUD (Classique abandonné). Choisit Clair, Sombre
    ou Automatique comme un clic dans les Réglages (`settings_set theme_mode`) ; rend vrai quand
    <html> porte data-theme="premium" et le mode résolu (« auto » : celui du système émulé)."""
    await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'settings_set', 'key': 'theme_mode', 'value': mode}))})")
    await asyncio.sleep(0.3)
    want = mode if mode in ("dark", "light") else \
        "' + (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') + '"
    return await p.until(f"document.documentElement.dataset.theme === 'premium' "
                         f"&& document.documentElement.dataset.mode === ('{want}')", 5)


async def reglages_bouton(p, fkey: str) -> bool:
    """28/10 (hud-boutons-t10) : ouvre les Réglages et clique le bouton d'action `fkey` (« clé|act|id »),
    section dépliée ; rend vrai si le bouton existait."""
    await p.js("document.querySelector('#settings-chip').click()")
    sel = json.dumps(f'.modal.wide [data-fkey="{fkey}"]')
    found = await p.until(f"!!document.querySelector({sel})", 4)
    if found:
        await p.js(f"(() => {{ const b = document.querySelector({sel}); const d = b.closest('details'); "
                   "if (d) d.open = true; b.click(); return true; })()")
    return found


async def run(url):
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
    ws_url = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
    async with websockets.connect(ws_url, max_size=50_000_000) as ws:
        p = Page(ws)
        pump = asyncio.create_task(p.pump())
        await p.send("Page.enable")
        await p.send("Runtime.enable")
        # Toute commande envoyée au cœur est notée : Total ne doit partir qu'après « Activer ».
        await p.send("Page.addScriptToEvaluateOnNewDocument", source="""
            window.__sent = []; window.__pwned = 0;
            const send = WebSocket.prototype.send;
            WebSocket.prototype.send = function (d) { window.__ws = this; try { window.__sent.push(JSON.parse(d)); } catch {} return send.call(this, d); };
        """)
        await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
        await p.send("Page.navigate", url=url)
        check("HUD chargé", await p.until("!!document.querySelector('#transcript') && !!document.querySelector('.channel-tab')", 15))
        await asyncio.sleep(1.0)
        found = await p.js(EMOJI_DOM_JS)
        check(f"au chargement : aucun émoji dans le DOM du HUD ({found[:5]})", found == [])
        sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)}).length")

        # ---------------------------------------------------------- markdown en flux
        await p.js("""(() => { const a = document.querySelector('#ask'); a.value = 'démo markdown';
            a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
        check("réponse en cours d'écriture", await p.until("!!document.querySelector('.msg.astrel.live .md')", 8))
        samples = []
        for _ in range(60):
            v = await p.js("""(() => { const m = document.querySelector('.msg.astrel.live .md');
                return m ? m.querySelectorAll('*').length : -1; })()""")
            if v == -1:
                break
            samples.append(v)
            await asyncio.sleep(0.05)
        check(f"rendu progressif pendant le flux ({len(samples)} relevés, {samples[:1]}→{samples[-1:]} nœuds)",
              len(samples) > 3 and samples[-1] > samples[0])
        await p.until("!document.querySelector('.msg.astrel.live')", 10)
        info = await p.js("""(() => {
            const all = [...document.querySelectorAll('.msg.astrel .md')];
            const md = all.find(x => x.textContent.includes('Bilan de la journée'));
            if (!md) return null;
            const t = document.querySelector('#transcript');
            return {
              h3: md.querySelectorAll('h3').length, h4: md.querySelectorAll('h4').length,
              ol: md.querySelectorAll('ol > li').length, nested: md.querySelectorAll('ol li ul li').length,
              pre: md.querySelectorAll('pre code').length, quote: md.querySelectorAll('blockquote').length,
              hr: md.querySelectorAll('hr').length, strong: md.querySelectorAll('strong').length,
              code: md.querySelectorAll(':not(pre) > code').length,
              links: [...md.querySelectorAll('a')].map(a => [a.href, a.rel, a.target]),
              // les seuls <svg> permis dans le fil sont les icônes du HUD (svg.ico), jamais dans le texte rendu ;
              // 04/11 : sauf l'icône d'un lien (icons.js, tracés constants), enfant direct de a.md-link
              bad: t.querySelectorAll('script, img, iframe, svg:not(.ico), object, embed, .md svg:not(a.md-link > svg.ico)').length,
              onattr: [...t.querySelectorAll('*')].filter(e => [...e.attributes].some(a => /^on/i.test(a.name))).length,
              text: md.textContent,
              codeBg: getComputedStyle(md.querySelector('pre')).backgroundColor,
              codeFont: getComputedStyle(md.querySelector('pre code')).fontFamily,
              ulPad: getComputedStyle(md.querySelector('ol')).paddingLeft,
            };
        })()""")
        check("réponse finale trouvée", info)
        if info:
            check(f"intertitres h3={info['h3']} h4={info['h4']}", info["h3"] == 1 and info["h4"] == 1)
            check(f"liste numérotée (3) avec sous-liste (2) : {info['ol']} / {info['nested']}",
                  info["ol"] == 3 and info["nested"] == 2)
            check("bloc de code, citation, séparateur, gras, code en ligne",
                  info["pre"] == 1 and info["quote"] == 1 and info["hr"] == 1 and info["strong"] >= 1 and info["code"] >= 2)
            check(f"liens https seulement, noopener, nouvel onglet : {info['links']}",
                  len(info["links"]) == 2 and all(h.startswith("https://") and "noopener" in r and t == "_blank"
                                                   for h, r, t in info["links"]))
            check("aucune balise script/img/iframe/svg ni attribut on* dans le fil", info["bad"] == 0 and info["onattr"] == 0)
            check("pièges affichés en texte inerte",
                  "<script>alert(1)</script>" in info["text"] and "<img src=x onerror=alert(2)>" in info["text"]
                  and "[clique ici](javascript:alert(3))" in info["text"] and "[image : pisteur]" in info["text"])
            check(f"code en monospace sur fond léger ({info['codeFont'][:30]}…, {info['codeBg']})",
                  "Mono" in info["codeFont"] and info["codeBg"] != "rgba(0, 0, 0, 0)")
            check(f"listes en retrait ({info['ulPad']})", info["ulPad"] not in ("0px", ""))
        dialogs = [e for e in p.events if e["method"] == "Page.javascriptDialogOpening"]
        check("aucune boîte alert() déclenchée par le texte", not dialogs and not await p.js("window.__pwned"))
        await p.js("document.querySelector('#transcript').scrollTop = 1e9")
        await p.shot("1-fil-markdown.png")

        # ---------------------------------------------------------- copie brute
        raw_ok = await p.js("""(async () => {
            let got = null;
            navigator.clipboard.writeText = async (t) => { got = t; };
            const m = [...document.querySelectorAll('.msg.astrel')].find(x => x.textContent.includes('Bilan de la journée'));
            m.querySelector('.copy-btn').click();
            await new Promise(r => setTimeout(r, 100));
            return got && got.startsWith('## Bilan de la journée') && got.includes('1. La facture');
        })()""")
        check("« Copier » copie le texte brut (marques markdown comprises)", raw_ok)

        # ---------------------------------------------------------- tableau, schéma, copie d'un bloc (19/10)
        await p.js("""(() => { const a = document.querySelector('#ask'); a.value = 'démo tableau';
            a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
        await p.until("[...document.querySelectorAll('.msg.astrel:not(.live) .md')].some(x => x.textContent.includes('Comparatif'))", 10)
        tb = await p.js("""(async () => {
            const md = [...document.querySelectorAll('.msg.astrel .md')].find(x => x.textContent.includes('Comparatif'));
            const t = md.querySelector('.table-wrap > table');
            const codes = [...md.querySelectorAll('.code-block')];
            let got = null;
            navigator.clipboard.writeText = async (x) => { got = x; };
            codes[0].querySelector('.code-copy').click();
            await new Promise(r => setTimeout(r, 100));
            const tr = t.querySelectorAll('tbody tr');
            return { th: t.querySelectorAll('th').length, rows: tr.length,
                     align: [...t.querySelectorAll('thead th')].map(e => getComputedStyle(e).textAlign),
                     zebra: [getComputedStyle(tr[0]).backgroundColor, getComputedStyle(tr[1]).backgroundColor],
                     wrapScroll: getComputedStyle(md.querySelector('.table-wrap')).overflowX,
                     strong: t.querySelectorAll('td strong').length, code: t.querySelectorAll('td code').length,
                     blocks: codes.length, copies: md.querySelectorAll('.code-copy').length,
                     ascii: codes[0].querySelector('code').textContent, got, label: codes[0].querySelector('.code-copy').textContent,
                     ws: getComputedStyle(codes[0].querySelector('pre code')).whiteSpace,
                     links: [...md.querySelectorAll('a')].map(a => a.href) };
        })()""")
        check(f"tableau : {tb['th']} colonnes, {tb['rows']} rangées, alignements {tb['align']}, gras et code dans les cellules",
              tb["th"] == 4 and tb["rows"] == 3 and tb["align"] == ["left", "center", "left", "right"]
              and tb["strong"] == 1 and tb["code"] == 1)
        check(f"tableau : lignes alternées {tb['zebra']}, défilement horizontal ({tb['wrapScroll']})",
              tb["zebra"][0] != tb["zebra"][1] and tb["wrapScroll"] == "auto")
        check(f"schéma ASCII : espaces gardés ({tb['ws']}), bouton Copier par bloc ({tb['copies']}/{tb['blocks']}) → « {tb['label']} »",
              tb["ws"] == "pre" and tb["blocks"] == 2 and tb["copies"] == 2 and tb["label"] == "Copié"
              and " maison ──> gare ──> train ──> Bordeaux\n                                 │" in tb["ascii"])
        check("Copier d'un bloc : le code brut exact", tb["got"] == tb["ascii"])
        check(f"tableau : seul le lien https est cliquable ({tb['links']})",
              tb["links"] == ["https://fr.wikipedia.org/wiki/Bordeaux"])
        await p.js("""(() => { const m = [...document.querySelectorAll('.msg.astrel')].find(x => x.textContent.includes('Comparatif'));
            m.scrollIntoView({ block: 'start' }); })()""")
        await p.shot("1b-fil-tableau-schema.png")

        # ---------------------------------------------------------- Autonome TOTAL
        before = await sent("total")
        await p.js("document.querySelector('#total-chip').click()")
        check("Total : boîte du HUD ouverte (pas de confirm natif)", await p.until("!!document.querySelector('.modal.danger')", 3))
        await p.js(SETTLE_JS)
        t = await p.js("""(() => { const m = document.querySelector('.modal');
            return { title: m.querySelector('.modal-title')?.textContent, items: m.querySelectorAll('.modal-items li').length,
                     ok: m.querySelector('.modal-ok').textContent, okBg: getComputedStyle(m.querySelector('.modal-ok')).backgroundColor,
                     focus: document.activeElement?.textContent }; })()""")
        danger = await p.js(f"{RGB_JS}('--rgb-danger')")  # P35 : rouge du thème posé
        check(f"Total : titre, liste de {t['items']}, bouton rouge « {t['ok']} » ({t['okBg']}, jeton {danger}), focus sur « {t['focus']} »",
              t["title"] == "Activer le mode Sans confirmation ?" and t["items"] == 4 and t["okBg"].startswith(f"rgba({danger}")
              and t["focus"] == "Annuler")
        await p.shot("2-total.png")
        for _ in range(5):
            await p.key("Tab", vk=9)
        check("focus piégé dans la boîte (5 × Tab)", await p.js("document.querySelector('.modal').contains(document.activeElement)"))
        await p.key("Escape", vk=27)
        check("Échap annule, aucune commande total envoyée",
              await p.until("!document.querySelector('.modal')", 2) and await sent("total") == before)
        await p.js("document.querySelector('#total-chip').click()")
        await p.until("!!document.querySelector('.modal')", 2)
        await p.js("document.querySelector('.modal-ok').click()")
        await asyncio.sleep(0.3)
        last = await p.js("window.__sent.filter(m => m.cmd === 'total').pop()")
        check(f"« Activer TOTAL » envoie la commande HUD total ({last})", last and last.get("mode") == "on")

        # ---------------------------------------------------------- arrêt d'urgence : annulé
        # 28/10 (T10, décision de l'utilisateur) : bouton Éteindre (#power-chip, `quit`) retiré de la barre
        for chip, cmd in (("#estop-chip", "estop"),):
            n = await sent(cmd)
            await p.js(f"document.querySelector('{chip}').click()")
            opened = await p.until("!!document.querySelector('.modal.danger')", 2)
            await p.js("document.querySelector('.modal-cancel').click()")
            closed = await p.until("!document.querySelector('.modal')", 2)
            check(f"{chip} : boîte rouge, Annuler n'envoie rien", opened and closed and await sent(cmd) == n)

        # ---------------------------------------------------------- accès repris dans les Réglages (28/10)
        # hud-boutons-t10 : Lire aussitôt, Mission et Éteindre quittent la barre ; lignes des Réglages, icônes,
        # nom affiché « Astrel » (jamais l'ancien nom).
        await p.js("document.querySelector('#settings-chip').click()")
        await p.until("!!document.querySelector('.modal.wide .setting[data-key=\"systeme.eteindre\"]')", 4)
        rows = await p.js("""(() => ['reports', 'mission', 'systeme.eteindre'].map(k => {
            const r = document.querySelector(`.modal.wide .setting[data-key="${k}"]`); if (!r) return null;
            return [r.querySelector('.setting-label')?.textContent, r.querySelector('svg.setting-ico')?.dataset.icon,
                    r.closest('details')?.dataset.group, [...r.querySelectorAll('button.setting-action')].map(b => b.textContent)]; }))()""")
        check(f"Réglages : Comptes rendus (Voix), Mission (Garde-fou), Éteindre (Système), icônes et boutons ({rows})",
              rows == [["Comptes rendus des tâches de fond", "volume", "voix", []],
                       ["Mission autonome", "target", "garde_fou", ["Lancer une mission"]],
                       ["Éteindre Astrel", "power", "systeme", ["Éteindre"]]])
        n = await sent("settings_set")
        for value in ("direct", "demande"):
            await p.js(f"""(() => {{ const s = document.querySelector('.modal.wide select[data-fkey="reports"]');
                s.closest('details').open = true; s.value = {json.dumps(value)};
                s.dispatchEvent(new Event('change', {{ bubbles: true }})); return true; }})()""")
            await p.until(f"window.__sent.filter(m => m.cmd === 'settings_set').length > {n}", 3)
            n = await sent("settings_set")
        last = await p.js("window.__sent.filter(m => m.cmd === 'settings_set').slice(-2)")
        shown = await p.js("""(() => { const s = document.querySelector('.modal.wide select[data-fkey="reports"]');
            return [s.value, [...s.options].map(o => o.textContent)]; })()""")
        check(f"Comptes rendus : Lus aussitôt puis À la demande envoient settings_set reports ({last}), liste {shown}",
              [(m.get("key"), m.get("value")) for m in last] == [("reports", "direct"), ("reports", "demande")]
              and shown == ["demande", ["À la demande", "Lus aussitôt"]])
        await p.js("document.querySelector('.modal.wide .modal-x')?.click()")
        await p.until("!document.querySelector('.modal-back')", 2)
        n = await sent("quit")
        found = await reglages_bouton(p, "systeme.eteindre|act|eteindre")
        opened = await p.until("!!document.querySelector('.modal.danger') && !document.querySelector('.modal.wide')", 2)
        title = await p.js("document.querySelector('.modal-title')?.textContent")
        await p.js("document.querySelector('.modal-cancel').click()")
        closed = await p.until("!document.querySelector('.modal')", 2)
        check(f"Réglages › Système › Éteindre : Réglages refermés, boîte rouge « {title} », Annuler n'envoie rien",
              found and opened and title == "Éteindre Astrel ?" and closed and await sent("quit") == n)
        await reglages_bouton(p, "systeme.eteindre|act|eteindre")
        await p.until("!!document.querySelector('.modal.danger')", 2)
        await p.js("document.querySelector('.modal-ok').click()")
        check("Éteindre confirmé : commande quit envoyée (celle de l'ancien bouton)",
              await p.until(f"window.__sent.filter(m => m.cmd === 'quit').length === {n + 1}", 3))

        # ---------------------------------------------------------- canal : créer, supprimer
        await p.js("document.querySelector('.channel-tab.add').click()")
        check("« + » : fenêtre Nouveau canal", await p.until("document.querySelector('.modal-title')?.textContent === 'Nouveau canal'", 2))
        f = await p.js("""(() => ({ n: document.querySelectorAll('.modal input').length,
                                    focus: document.activeElement?.name }))()""")
        check(f"3 champs (nom, dossier, mots-clés), focus sur le nom : {f}", f["n"] == 3 and f["focus"] == "label")
        await p.key("Enter", vk=13, text="\r")
        check("Entrée sur un nom vide : message, la fenêtre reste",
              await p.js("!!document.querySelector('.modal') && !document.querySelector('.modal-error').classList.contains('hidden')"))
        await p.send("Input.insertText", text="Essai rendu")
        await p.key("Tab", vk=9)
        await p.key("Tab", vk=9)
        await p.send("Input.insertText", text="essai, démo")
        await p.shot("3-nouveau-canal.png")
        await p.key("Enter", vk=13, text="\r")
        created = await p.until("[...document.querySelectorAll('.channel-tab')].some(t => t.textContent.includes('Essai rendu'))", 4)
        last = await p.js("window.__sent.filter(m => m.cmd === 'channel_create').pop()")
        check(f"Entrée crée le canal ({last})", created and last.get("keywords") == "essai, démo")
        await p.js("""(() => { const t = [...document.querySelectorAll('.channel-tab')].find(t => t.textContent.includes('Essai rendu'));
            const r = t.getBoundingClientRect();
            t.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 5, clientY: r.top + 5 })); })()""")
        await p.until("!!document.querySelector('.menu')", 2)
        await p.js("[...document.querySelectorAll('.menu .item')].find(i => i.textContent.startsWith('Modifier')).click()")
        pre = await p.js("[...document.querySelectorAll('.modal input')].map(i => i.value)")
        check(f"Modifier : champs préremplis {pre}", pre and pre[0] == "Essai rendu" and pre[2] == "essai, démo")
        await p.key("Escape", vk=27)
        await p.until("!document.querySelector('.modal')", 2)
        await p.js("""(() => { const t = [...document.querySelectorAll('.channel-tab')].find(t => t.textContent.includes('Essai rendu'));
            t.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 300, clientY: 700 })); })()""")
        await p.until("!!document.querySelector('.menu')", 2)
        await p.js("[...document.querySelectorAll('.menu .item')].find(i => i.textContent.startsWith('Supprimer')).click()")
        check("Supprimer : boîte rouge", await p.until("!!document.querySelector('.modal.danger')", 2))
        await p.js("document.querySelector('.modal-ok').click()")
        check("canal supprimé",
              await p.until("![...document.querySelectorAll('.channel-tab')].some(t => t.textContent.includes('Essai rendu'))", 4))

        # ---------------------------------------------------------- barre : Maj+Entrée, composition (15/10)
        says = await sent("say")
        await p.js("document.querySelector('#ask').value = ''; document.querySelector('#ask').focus()")
        await p.send("Input.insertText", text="ligne 1")
        await p.key("Enter", vk=13, text="\r", shift=True)
        await p.send("Input.insertText", text="ligne 2")
        v = await p.js("document.querySelector('#ask').value")
        check(f"Maj+Entrée passe à la ligne sans envoyer ({v!r})", v == "ligne 1\nligne 2" and await sent("say") == says)
        h = await p.js("document.querySelector('#ask').offsetHeight")
        check(f"la barre grandit avec le texte ({h} px)", h > 34)
        await p.js("""(() => { const a = document.querySelector('#ask');
            a.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
            a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
            a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true }));
            a.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })); })()""")
        await asyncio.sleep(0.3)
        check("Entrée pendant une composition (IME, dictée) n'envoie rien", await sent("say") == says)
        await p.key("Enter", vk=13, text="\r")
        await p.until(f"window.__sent.filter(m => m.cmd === 'say').length > {says}", 3)
        last = await p.js("window.__sent.filter(m => m.cmd === 'say').pop()")
        check(f"Entrée envoie les deux lignes ({last and last.get('text')!r})", last and last.get("text") == "ligne 1\nligne 2")
        check("barre vidée et repliée après l'envoi", await p.until(
            "document.querySelector('#ask').value === '' && document.querySelector('#ask').offsetHeight <= 34", 3))

        # ---------------------------------------------------------- mission autonome (15/10)
        # 28/10 (hud-boutons-t10, décision de l'utilisateur) : plus de bouton « Mission » dans la barre ; une
        # PREMIÈRE mission se lance depuis Réglages › Garde-fou › « Lancer une mission » (les Réglages se
        # referment, la fenêtre de mission est seule à l'écran).
        seeded = await sent("mission")
        found = await reglages_bouton(p, "mission|act|lancer")
        check("Réglages › Garde-fou › « Lancer une mission » (pas de bouton dans la barre) : Réglages refermés, "
              "fenêtre « Lancer une mission autonome »",
              found and await p.js("!document.querySelector('#mission-chip')") and await p.until(
                  "document.querySelectorAll('.modal-back').length === 1 && !document.querySelector('.modal.wide')"
                  " && document.querySelector('.modal-title')?.textContent === 'Lancer une mission autonome'", 3))
        f = await p.js("""(() => ({ fields: document.querySelectorAll('.modal .modal-field').length,
            numbers: [...document.querySelectorAll('.modal input[type=number]')].map(i => i.value),
            areas: document.querySelectorAll('.modal textarea').length, focus: document.activeElement?.name }))()""")
        # 02/11 (plan P43) : + « Preuve exigée » (zone multiligne), le critère en clair devient facultatif.
        check(f"9 champs, budget borné par défaut {f['numbers']}, 3 zones multilignes, focus sur l'objectif",
              f["fields"] == 9 and f["numbers"] == ["20", "60", "3000"] and f["areas"] == 3 and f["focus"] == "objective")
        await p.key("Enter", vk=13, text="\r")
        check("sans objectif : message, rien d'envoyé",
              await p.js("!!document.querySelector('.modal') && !document.querySelector('.modal-error').classList.contains('hidden')")
              and await sent("mission") == seeded)
        await p.send("Input.insertText", text="Faire passer les tests")
        await p.js("document.querySelector('.modal [name=done_when]').value = 'pytest -q rend 0'")
        await p.js("document.querySelector('.modal [name=scope]').focus()")
        await p.send("Input.insertText", text="/home/user/Projets/Boutique")
        await p.key("Enter", vk=13, text="\r")
        await p.send("Input.insertText", text="docs")
        scope = await p.js("document.querySelector('.modal [name=scope]').value")
        check(f"Entrée dans le périmètre passe à la ligne ({scope!r})",
              scope == "/home/user/Projets/Boutique\ndocs" and await p.js("!!document.querySelector('.modal')"))
        await p.js("document.querySelector('.modal [name=max_turns]').value = '500'")
        await p.js("document.querySelector('.modal-ok').click()")
        check("budget hors bornes (500 tours) : refusé dans la fenêtre",
              await p.js("!!document.querySelector('.modal') && document.querySelector('.modal-error').textContent.includes('entre 1 et 200')")
              and await sent("mission") == seeded)
        await p.js("document.querySelector('.modal [name=max_turns]').value = '5'")
        await p.shot("4-mission-fenetre.png")
        await p.js("document.querySelector('.modal-ok').click()")
        await p.until("window.__sent.some(m => m.cmd === 'mission')", 3)
        last = await p.js("window.__sent.filter(m => m.cmd === 'mission').pop()")
        MISSION_START.update({k: v for k, v in (last or {}).items() if k != "id"})  # relancée par check_mise_en_page
        spec = (last or {}).get("spec") or {}
        check(f"« Lancer la mission » envoie start ({spec.get('max_turns')} tours, {spec.get('max_tokens')} tokens)",
              last and last.get("action") == "start" and spec.get("max_turns") == 5 and spec.get("max_tokens") == 3_000_000
              and spec.get("scope") == "/home/user/Projets/Boutique\ndocs")
        check("bandeau « MISSION EN COURS » visible",
              await p.until("!document.querySelector('#mission-banner').hidden", 3))
        panel = await p.js("""(() => { const m = document.querySelector('#mission');
            return { shown: !m.classList.contains('hidden'), text: m.textContent, bars: m.querySelectorAll('.bar').length }; })()""")
        check(f"panneau : objectif, étape, 3 budgets, périmètre absolu ({panel['text'][:90]}…)",
              panel["shown"] and "Faire passer les tests" in panel["text"] and "Tour 1 en cours" in panel["text"]
              # « docs » : relatif au dossier de travail (le canal Général n'a pas de dossier relié)
              and panel["bars"] == 3 and "/home/user/Projets/Boutique, /home/user/Projets/docs" in panel["text"])
        await p.shot("5-mission-active.png")
        await p.js("document.querySelector('#mission-banner-pause').click()")
        check("Pause : bandeau en pause, « Reprendre »",
              await p.until("document.querySelector('#mission-banner').classList.contains('paused') "
                            "&& document.querySelector('#mission-banner-pause').textContent === 'Reprendre'", 3))
        await p.js("document.querySelector('#mission-banner-pause').click()")
        check("Reprendre : de nouveau en cours", await p.until(
            "!document.querySelector('#mission-banner').classList.contains('paused')", 3))
        n = await sent("mission")
        await p.js("document.querySelector('#mission-banner-stop').click()")
        check("Arrêter : boîte rouge", await p.until("!!document.querySelector('.modal.danger')", 2))
        await p.key("Escape", vk=27)
        check("Échap : la mission continue", await p.until("!document.querySelector('.modal')", 2)
              and await sent("mission") == n and not await p.js("document.querySelector('#mission-banner').hidden"))
        await p.js("document.querySelector('#mission-banner-stop').click()")
        await p.until("!!document.querySelector('.modal.danger')", 2)
        await p.js("document.querySelector('.modal-ok').click()")
        check("arrêtée : bandeau retiré, panneau « arrêtée »", await p.until(
            "document.querySelector('#mission-banner').hidden && document.querySelector('#mission .pill')?.textContent === 'arrêtée'", 3))
        # 04/11 (demande : une mission d'essai finie revenait après relance) : la croix FERME la mission
        # côté cœur (`mission_close`, clé `mission_id`), le navigateur ne retient plus rien quand c'est fait.
        await p.js("localStorage.removeItem('astrel-mission-masquee'), true")
        closes = await sent("mission_close")
        await p.js("document.querySelector('#mission-hide').click()")
        check("la croix masque la mission finie", await p.until("document.querySelector('#mission').classList.contains('hidden')", 2))
        await p.until(f"window.__sent.filter(m => m.cmd === 'mission_close').length > {closes}", 3)
        close = await p.js("window.__sent.filter(m => m.cmd === 'mission_close').pop()") or {}
        await asyncio.sleep(0.3)  # réponse du cœur
        check(f"la croix envoie mission_close avec mission_id ({close.get('mission_id')!r}), rien en localStorage",
              str(close.get("mission_id") or "").startswith("m-") and close.get("mission_id") != close.get("id")
              and await p.js("localStorage.getItem('astrel-mission-masquee') === null"))
        # Une autre mission affichée, puis l'état de la vraie : close, elle est publiée comme absente.
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'demo_mission', 'etat': 'verifiee'}))})")
        await p.until("!document.querySelector('#mission').classList.contains('hidden')", 3)
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'mission'}))})")
        check("mission close : republiée comme absente, panneau caché sans souvenir du navigateur", await p.until(
            "document.querySelector('#mission').classList.contains('hidden') "
            "&& document.querySelector('#mission-title').classList.contains('hidden') "
            "&& document.querySelector('#mission-banner').hidden", 3))

        await check_lots_15_10(p)
        await check_detail_agent(p)  # 21/10 (plan P18)
        await check_effort(p)  # 23/10 (plan P28)
        await check_memoire(p)  # 21/10 (plan P20)
        await check_courrier(p)  # 26/10 (plan P33 lot 3)
        await check_mission_preuve(p)  # 02/11 (plan P43)
        await check_todo(p)  # 26/10 (plan P38 lot 2)
        await check_couleurs_semantiques(p)  # 21/10 (relecture P17)
        await check_reglages(p)
        await check_reglages_actions(p)
        await check_calibrage(p)  # 02/11 : assistant de calibrage du micro
        await check_levee(p)  # 18/10 (plan P7)
        await check_reglages_icones(p)  # 19/10
        await check_reglages_connecteurs_icones(p)  # 19/10 (plan P10, lot 3)
        await check_whatsapp_et_guide(p)
        await check_capacites(p)  # 23/10 (plan P22, lot 2)
        await check_heure_messages(p)  # 23/10 : heure sur chaque message
        await check_fournisseur(p)  # 20/10 (plan P11)
        await check_plans(p)
        await check_audit(p)
        await check_propositions(p)  # 04/11 (T49)
        await check_hors_ligne(p)  # 04/11 (lot 2 du moteur local)
        await check_barre_du_haut(p)  # 19/10
        await check_mise_en_page(p)
        await check_composer(p)  # 19/10
        await check_bouton_envoyer(p)  # 04/11 : bouton « Envoyer » (↑), même chemin qu'Entrée
        await check_icones(p)
        await check_fil_coupures_pieces(p)
        await check_messages_non_envoyes(p)
        await check_collage_instantane(p)  # 22/10 : vignette immédiate, préparation derrière
        await check_collage_fragilites(p)  # 22/10 : délai, échec, phrase dite, visionneuse
        await check_visionneuse(p)  # 19/10
        # 28/10 (plan P41 B3) : AVANT les images, dont la dernière, « lente », garde le verrou du Chrome commun
        await check_videos_ia(p)
        await check_videos_panneau(p)  # 28/10 (plan P41 B4) : referme le panneau, réglage remis au défaut
        # 28/10 (plan P40 lot A5) : AVANT check_images_ia, qui finit sur une image « lente » qui tient la file
        await check_images_panneau(p)
        await check_images_ia(p)  # 21/10 (plan P14)
        await check_medias(p)  # 22/10 (plan P26)
        await check_liens_et_barre(p)  # 04/11 : liens cliquables, Copier, ↑, « / », point du bouton ↓
        await check_recherche_chemins_echap(p)  # 04/11 : chemins copiés, Échap = ■, recherche Ctrl+K
        await check_moteur_local(p)  # 04/11 : badge « Local », réponse locale à côté de celle de Claude
        await check_sans_graphe(p)  # 23/10 (plan P28) : graphe 3D, Recadrer, Orbite retirés
        await check_affichage_simple(p)  # 22/10 (plan P23, lots A et B) : recharge la page
        await check_theme_a_la_connexion(p)  # 21/10 (relecture P17) : recharge la page
        await check_nom_affiche(p)  # 23/10 (plan P31) : recharge la page deux fois
        await check_non_lus(p)  # 27/10 (plan P41) : pastille des non-lus, recharge la page deux fois
        await check_ordre_canaux(p)  # 19/10 : en dernier, il recharge la page

        errors =[e for e in p.events if e["method"] == "Runtime.exceptionThrown"]
        errors += [e for e in p.events if e["method"] == "Runtime.consoleAPICalled" and e["params"]["type"] == "error"]
        check(f"aucune erreur JS ({len(errors)})", not errors)
        for e in errors[:5]:
            print("     ", json.dumps(e["params"])[:300])
        pump.cancel()


async def check_calibrage(p: Page) -> None:
    """02/11 (demande de l'utilisateur) : Réglages › « Calibrer ce micro » ouvre l'assistant (fenêtre du HUD,
    jamais confirm()), rien ne démarre avant « Commencer » ; vumètre en direct ; résultat à côté de
    l'actuel ; Enregistrer / Refaire / Annuler ; fermer envoie `calibrage_cancel` ; un micro sans profil
    fait proposer l'assistant (notice non bloquante). Captures : calibrage-*.png."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)}).length")

    async def demo(**args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'demo_calibrage', **args}))})")

    await demo(pause_at="parole")
    found = await reglages_bouton(p, "audio.calibrage|act|ouvrir")
    check("Réglages › « Calibrer ce micro » ouvre l'assistant (Réglages refermés)",
          found and await p.until("!!document.querySelector('.modal.wide.calibrage') "
                                  "&& !document.querySelector('.modal.wide.settings')", 3))
    intro = await p.js("""(() => { const m = document.querySelector('.modal.wide.calibrage');
        return { title: m.querySelector('.modal-title')?.textContent, steps: m.querySelectorAll('.calib-steps li').length,
                 buttons: [...m.querySelectorAll('.calib-actions .btn')].map(b => b.textContent) }; })()""")
    check(f"présentation : 3 étapes, {intro['buttons']}, rien d'envoyé avant « Commencer »",
          intro["steps"] == 3 and intro["buttons"] == ["Annuler", "Commencer"] and await sent("calibrage_start") == 0)
    await p.js(SETTLE_JS)
    await p.shot("calibrage-1-presentation.png")
    await p.js("document.querySelector('.calib-actions .ok').click()")
    check("Commencer : étape 1 « silence » avec vumètre",
          await p.until("!!document.querySelector('.calib-meter') && document.querySelector('.calib-big')"
                        "?.textContent.includes('Silence')", 4) and await sent("calibrage_start") == 1)
    check("étape 2 : « Dis « Astrel » trois fois », vumètre qui bouge",
          await p.until("document.querySelector('.calib-big')?.textContent.includes('Astrel') && "
                        "document.querySelector('.calib-bar').style.transform !== 'scaleX(0.000)'", 5))
    await asyncio.sleep(0.3)
    await p.js(SETTLE_JS)
    await p.shot("calibrage-2-parole.png")
    await demo(pause_at="")
    await p.js("document.querySelector('.calib-actions .btn').click()")  # Annuler pendant la mesure
    check("Annuler pendant la mesure : fenêtre fermée, `calibrage_cancel` envoyé",
          await p.until("!document.querySelector('.modal.wide.calibrage')", 3)
          and await p.until("window.__sent.some(m => m.cmd === 'calibrage_cancel')", 3))
    await p.until("!!document.querySelector('.modal.wide.settings')", 3)  # les Réglages se rouvrent
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal-back')", 3)
    await reglages_bouton(p, "audio.calibrage|act|ouvrir")
    await p.until("!!document.querySelector('.modal.wide.calibrage .calib-actions .ok')", 3)
    await p.js("document.querySelector('.calib-actions .ok').click()")
    ok = await p.until("!!document.querySelector('.calib-table')", 8)
    res = await p.js("""(() => { const m = document.querySelector('.modal.wide.calibrage');
        return { rows: [...m.querySelectorAll('.calib-table tr')].map(r => [...r.children].map(c => c.textContent)),
                 buttons: [...m.querySelectorAll('.calib-actions .btn')].map(b => b.textContent),
                 facts: m.querySelector('.calib-facts')?.textContent || '', focus: document.activeElement?.textContent }; })()""")
    check(f"résultat : actuel à côté du proposé ({res['rows'][1:3] if ok else '?'})",
          ok and res["rows"][1] == ["Gain", "×2", "×1,4"] and res["rows"][2] == ["Seuil de parole (VAD)", "0,35", "0,32"]
          and res["rows"][3][2].endswith("inchangé") and res["rows"][4] == ["Canaux", "tous", "0 + 1"])
    check(f"boutons Annuler / Refaire / Enregistrer ({res['buttons']}), nom reconnu 3 sur 3",
          res["buttons"] == ["Annuler", "Refaire", "Enregistrer"] and "3 fois sur 3" in res["facts"]
          and res["focus"] == "Enregistrer")
    await p.js(SETTLE_JS)
    await p.shot("calibrage-3-resultat.png")
    await p.js("[...document.querySelectorAll('.calib-actions .btn')].find(b => b.textContent === 'Enregistrer').click()")
    check("Enregistrer : `calibrage_save`, profil enregistré",
          await p.until("document.querySelector('.calib-big')?.textContent === 'Profil enregistré.'", 3)
          and await sent("calibrage_save") == 1)
    await p.shot("calibrage-4-enregistre.png")
    await p.js("document.querySelector('.calib-actions .ok').click()")
    await p.until("!!document.querySelector('.modal.wide.settings')", 3)
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal-back')", 3)
    await demo(propose=True)
    check("micro sans profil : notice non bloquante « Calibrer », rien de lancé",
          await p.until("!!document.querySelector('.toast.calib-propose button.ok')", 3)
          and not await p.js("!!document.querySelector('.modal-back')"))
    await p.shot("calibrage-5-proposition.png")
    await p.js("document.querySelector('.toast.calib-propose button.ok').click()")
    check("« Calibrer » ouvre l'assistant", await p.until("!!document.querySelector('.modal.wide.calibrage')", 3))
    await p.key("Escape", vk=27)
    check("Échap ferme (présentation : aucun cancel de plus)", await p.until(
        "!document.querySelector('.modal.wide.calibrage')", 3) and await sent("calibrage_cancel") == 1)


async def check_lots_15_10(p: Page) -> None:
    """Contrat des lots du 15/10 : comptes rendus typés, question à choix, coût par agent, quota."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")

    async def demo(cmd, **args):  # commande de la démo, sans passer par l'interface
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    # ---------------------------------------------------------- comptes rendus typés (idée 3)
    r = await p.js("""(() => {
        const msg = (cls) => document.querySelector(`.msg.astrel.${cls}`);
        const d = msg('report-decision'), b = msg('report-blocage'), ok = msg('report-resultat');
        const plain = [...document.querySelectorAll('.msg.astrel')].find(x => x.textContent.includes('Bilan de la journée'));
        return d && b && ok && {
          decision: d.querySelector('.report-badge.decision')?.textContent,
          icons: ['decision', 'blocage', 'resultat'].map(k => document.querySelector(`.report-badge.${k} svg.ico`)?.dataset.icon),
          decisionBg: getComputedStyle(d.querySelector('.report-badge')).backgroundColor,
          decisionBorder: getComputedStyle(d).borderLeftColor,
          blocage: b.querySelector('.report-badge.blocage')?.textContent,
          resultat: ok.querySelector('.report-badge.resultat')?.textContent,
          files: [...d.querySelectorAll('.report-file')].map(f => f.textContent),
          noFiles: b.querySelectorAll('.report-file').length,
          plainBadge: plain ? plain.querySelectorAll('.report-badge').length : -1,
        };
    })()""")
    check("comptes rendus typés présents (décision, blocage, résultat)", r)
    if r:
        amber = await p.js(f"{TOKEN_JS}('--amber')")  # P35 : ambre du thème posé
        check(f"badge ambre « {r['decision']} » ({r['decisionBg']}, jeton {amber})",
              r["decision"] == "Décision requise" and r["decisionBg"] == amber)
        # P35 : contrôle gardé strict. En Premium, `.msg.astrel { border-color: transparent }` (0,4,0)
        # efface le liseré ambre de `.msg.astrel.report-decision` (0,3,0) : défaut Premium d'avant P35.
        check(f"liseré ambre du compte rendu « décision » ({r['decisionBorder']}, jeton {amber})",
              r["decisionBorder"] == amber)
        check(f"badges blocage « {r['blocage']} » et résultat « {r['resultat']} », aucun sur une réponse ordinaire",
              r["blocage"] == "Blocage" and r["resultat"] == "Résultat" and r["plainBadge"] == 0)
        check(f"badges : icônes vectorielles {r['icons']}", r["icons"] == ["alert", "no-entry", "check"])
        check(f"fichiers du compte rendu listés en texte : {r['files']}",
              r["files"] == ["/home/user/Documents/Déménagement/devis-comparés.pdf", "/tmp/astrel-demo/devis-demenageurs.txt"]
              and r["noFiles"] == 0)
    copied = await p.js("""(async () => {
        let got = null;
        navigator.clipboard.writeText = async (t) => { got = t; };
        document.querySelector('.msg.report-decision .report-file').click();
        await new Promise(r => setTimeout(r, 100));
        return got; })()""")
    check(f"clic sur un fichier : chemin copié ({copied})", copied == "/home/user/Documents/Déménagement/devis-comparés.pdf")

    # ---------------------------------------------------------- question à choix (idée 9)
    # Le canal de la question posée au démarrage (rejouée à l'ouverture de la page).
    shown = await p.js("""(() => {
        for (const t of document.querySelectorAll('.channel-tab:not(.add)')) {
          t.click();
          const c = document.querySelector('.msg.choice.active');
          if (c && !c.classList.contains('hidden')) return true;
        }
        return false; })()""")
    check("question à choix rejouée et visible dans son canal", shown)
    card = await p.js("""(() => { const c = document.querySelector('.msg.choice.active');
        const btns = [...c.querySelectorAll('.choice-btn')];
        const row = c.querySelector('.choice-options'), ta = c.querySelector('textarea.choice-other');
        return { q: c.querySelector('.choice-question').textContent, labels: btns.map(b => b.querySelector('.choice-label').textContent),
                 rec: btns.map(b => b.classList.contains('recommended')), other: btns.at(-1).classList.contains('other'),
                 keys: btns.map(b => b.querySelector('.choice-key').textContent),
                 group: row.getAttribute('role'), named: document.getElementById(row.getAttribute('aria-labelledby')) === c.querySelector('.choice-question'),
                 radios: btns.map(b => b.getAttribute('role') + ':' + b.getAttribute('aria-checked')),
                 tabs: btns.map(b => b.tabIndex), folded: !!ta && ta.classList.contains('hidden'),
                 status: c.querySelector('.choice-status').getAttribute('role'), focusIn: c.contains(document.activeElement),
                 mark: document.querySelector('.channel-tab.on .decision-mark') !== null }; })()""")
    check(f"carte : question, 3 réponses + « Autre » en dernier, la recommandée en premier ({card['labels']})",
          card["q"].startswith("Le train de 8 h 12") and len(card["labels"]) == 4
          and card["labels"][0] == "Prendre celui de 9 h 40" and card["rec"] == [True, False, False, False]
          and card["other"] and card["labels"][3] == "Autre…")
    check(f"P21 : badges a) b) c) d) ({card['keys']})", card["keys"] == ["a)", "b)", "c)", "d)"])
    check(f"P21 : ARIA radiogroup nommé par la question, radios non cochées {card['radios']}, un arrêt de tabulation "
          f"{card['tabs']}, statut role=status ({card['status']})",
          card["group"] == "radiogroup" and card["named"] and card["radios"] == ["radio:false"] * 4
          and card["tabs"] == [0, -1, -1, -1] and card["status"] == "status")
    check("P21 : champ « Autre » replié tant qu'on ne le choisit pas, focus pas pris par la carte",
          card["folded"] and not card["focusIn"])
    check("point ambre sur l'onglet tant que la question attend", card["mark"])
    await p.js("document.querySelector('.msg.choice.active').scrollIntoView({ block: 'center' })")
    await p.shot("6-choix.png")
    await p.js("document.querySelector('.msg.choice.active .choice-btn').click()")
    await p.until("window.__sent.some(m => m.cmd === 'choice_answer')", 3)
    first = (await sent("choice_answer"))[-1:] or [{}]
    check(f"clic : choice_answer envoyé avec l'id de la question ({first[0]})",
          first[0].get("value") == "Prendre celui de 9 h 40" and isinstance(first[0].get("choice_id"), str) and len(first[0]["choice_id"]) >= 6)
    check("carte passée en « Répondu : … »", await p.until(
        "[...document.querySelectorAll('.msg.choice.answered .choice-status')]"
        ".some(s => s.textContent === 'Répondu : Prendre celui de 9 h 40')", 3))

    # La séquence de la démo (réponses, confirmation finale) doit être passée : sinon
    # un message peut arriver sous la carte et le raccourci ne vaut plus.
    await p.until("!document.querySelector('#confirm').classList.contains('hidden')", 25)
    await p.js("document.querySelector('#ask').value = ''; document.querySelector('#ask').focus()")
    await demo("demo_choice")
    check("question arrivée en direct : carte active, dernière du fil", await p.until(
        "(() => { const c = document.querySelectorAll('.msg.choice.active'); const l = c[c.length - 1];"
        " return !!l && l.querySelector('.choice-question').textContent.startsWith('Quel nom') && !l.classList.contains('hidden'); })()", 4))
    check("P21 : la carte arrivée en direct ne vole pas le focus (il reste dans la barre)",
          await p.js("document.activeElement === document.querySelector('#ask')"))
    n = len(await sent("choice_answer"))
    await p.js("document.querySelector('#ask').value = ''; document.querySelector('#ask').focus()")
    await p.key("2", code="Digit2", vk=50, text="2")
    await p.until(f"window.__sent.filter(m => m.cmd === 'choice_answer').length > {n}", 3)
    last = (await sent("choice_answer"))[-1]
    ask = await p.js("document.querySelector('#ask').value")
    check(f"touche 2, barre vide : 2e réponse envoyée ({last.get('value')}), rien tapé dans la barre ({ask!r})",
          last.get("value") == "Potager" and ask == "")

    await demo("demo_choice")
    await p.until("[...document.querySelectorAll('.msg.choice.active .choice-question')].some(q => q.textContent.startsWith('On publie'))", 4)
    n = len(await sent("choice_answer"))
    await p.js("document.querySelector('#ask').value = 'x'; document.querySelector('#ask').focus()")
    await p.key("1", code="Digit1", vk=49, text="1")
    await asyncio.sleep(0.3)
    check("barre non vide : la touche 1 s'écrit dans la barre, aucune réponse envoyée",
          len(await sent("choice_answer")) == n and await p.js("document.querySelector('#ask').value") == "x1")
    await p.js("document.querySelector('#ask').value = ''")
    # P21 : clavier dans la carte (une carte de confirmation est ouverte : Entrée ne doit pas y répondre).
    confirm_open = await p.js("!document.querySelector('#confirm').classList.contains('hidden')")
    n_conf = len(await sent("confirm"))
    await p.js("document.querySelector('.msg.choice.active .choice-btn').focus()")
    await p.key("ArrowDown", code="ArrowDown", vk=40)
    k1 = await p.js("""(() => { const b = [...document.querySelectorAll('.msg.choice.active .choice-btn')];
        return { checked: b.map(x => x.getAttribute('aria-checked')), tabs: b.map(x => x.tabIndex),
                 focus: b.indexOf(document.activeElement) }; })()""")
    check(f"P21 : flèche bas : 2e entrée cochée et focalisée {k1}",
          k1["checked"] == ["false", "true", "false", "false", "false"] and k1["tabs"] == [-1, 0, -1, -1, -1] and k1["focus"] == 1)
    await p.key("ArrowUp", code="ArrowUp", vk=38)
    await p.key("ArrowUp", code="ArrowUp", vk=38)
    k2 = await p.js("""(() => { const b = [...document.querySelectorAll('.msg.choice.active .choice-btn')];
        return { focus: b.indexOf(document.activeElement), other: document.activeElement.classList.contains('other') }; })()""")
    check(f"P21 : flèche haut deux fois : en boucle jusqu'à « Autre » (e) {k2}", k2["focus"] == 4 and k2["other"])
    await p.key("Enter", code="Enter", vk=13, text="\r")
    k3 = await p.js("""(() => { const c = document.querySelector('.msg.choice.active'), ta = c.querySelector('textarea.choice-other');
        return { open: !ta.classList.contains('hidden'), focus: document.activeElement === ta }; })()""")
    check(f"P21 : Entrée sur « Autre » déplie le champ et y met le focus {k3}", k3["open"] and k3["focus"])
    await p.js("document.querySelector('.msg.choice.active').scrollIntoView({ block: 'center' })")
    await p.shot("6b-choix-autre.png")
    await p.send("Input.insertText", text="Plutôt")
    await p.key("Enter", code="Enter", vk=13, text="\r", shift=True)
    await asyncio.sleep(0.2)
    check("P21 : Maj+Entrée dans « Autre » n'envoie rien", len(await sent("choice_answer")) == n)
    await p.key("Escape", code="Escape", vk=27)
    k4 = await p.js("""(() => { const c = document.querySelector('.msg.choice.active'), ta = c.querySelector('textarea.choice-other');
        return { folded: ta.classList.contains('hidden'), back: document.activeElement.classList.contains('other'), kept: ta.value }; })()""")
    check(f"P21 : Échap replie « Autre », focus revenu sur l'entrée, texte gardé {k4}",
          k4["folded"] and k4["back"] and k4["kept"].startswith("Plutôt"))
    check(f"P21 : Entrée et Échap dans la carte ne répondent pas à la carte de confirmation (ouverte : {confirm_open})",
          len(await sent("confirm")) == n_conf and (not confirm_open or await p.js(
              "!document.querySelector('#confirm').classList.contains('hidden')")))
    await p.key("e", code="KeyE", vk=69, text="e")
    check("P21 : lettre « e » dans la carte : « Autre » rouvert", await p.until(
        "document.activeElement === document.querySelector('.msg.choice.active textarea.choice-other')", 2))
    await p.js("(() => { const ta = document.querySelector('.msg.choice.active textarea.choice-other'); ta.value = 'Plutôt lundi'; })()")
    await p.key("Enter", vk=13, text="\r")
    await p.until(f"window.__sent.filter(m => m.cmd === 'choice_answer').length > {n}", 3)
    last = (await sent("choice_answer"))[-1]
    check(f"« Autre… » + Entrée envoie le texte libre ({last.get('value')})", last.get("value") == "Plutôt lundi")
    check("plus aucune carte active, les 3 questions répondues", await p.until(
        "!document.querySelector('.msg.choice.active') && document.querySelectorAll('.msg.choice.answered').length === 3", 3))

    # Question remplacée : le cœur (astrel.choices) annule celle qui attendait dans le canal
    # (`choice_answered` avec `cancelled: true`, valeur vide) : la carte dit « Annulée ».
    await demo("demo_choice")
    await p.until("document.querySelectorAll('.msg.choice.active').length === 1", 4)
    await demo("demo_choice")
    canc = await p.until(
        "(() => { const c = document.querySelectorAll('.msg.choice.cancelled');"
        " const a = document.querySelector('.msg.choice.active');"
        " return c.length === 1 && c[0].querySelector('.choice-status').textContent.startsWith('Annulée')"
        " && c[0].querySelectorAll('.choice-btn:not(.hidden)').length === 0"
        " && !!a && a.querySelector('.choice-question').textContent.startsWith('Quel nom'); })()", 4)
    check("question remplacée : l'ancienne carte « Annulée », la nouvelle active", canc)
    n = len(await sent("choice_answer"))
    await p.js("document.querySelector('.msg.choice.active .choice-btn').focus()")
    await p.key("b", code="KeyB", vk=66, text="b")
    await p.until(f"window.__sent.filter(m => m.cmd === 'choice_answer').length > {n}", 3)
    last = (await sent("choice_answer"))[-1]
    check(f"P21 : lettre « b » dans la carte : 2e réponse envoyée ({last.get('value')})", last.get("value") == "Potager")
    await p.until("!document.querySelector('.msg.choice.active')", 3)

    # P21 (testeur, fiche 3) : un libellé de 110 caractères SANS espace (URL, chemin) faisait
    # déborder le bouton et la carte. Carte injectée dans la page (comme si elle venait du cœur),
    # mesurée, puis fermée côté page (« annulée ») : elle disparaît au rechargement plus bas.
    long_label = "https://exemple.fr/" + "abcdefghij" * 9 + "z"  # 110 caractères, ni espace ni tiret
    long_ev = {"type": "choice", "id": "p21long0", "question": "Quel fichier je garde ?",
               "options": [{"label": long_label, "recommended": True}, {"label": "Aucun", "recommended": False}],
               "allow_other": True, "answered": False}
    await p.js(f"window.__ws.onmessage({{ data: {json.dumps(json.dumps(long_ev))} }})")
    await p.js("""(() => { for (const t of document.querySelectorAll('.channel-tab:not(.add)')) {
          t.click(); const q = document.getElementById('choice-q-p21long0');
          if (q && q.offsetWidth > 0) return true; } return false; })()""")
    await p.until("(() => { const q = document.getElementById('choice-q-p21long0'); return !!q && q.offsetWidth > 0; })()", 3)
    lg = await p.js("""(() => { const c = document.getElementById('choice-q-p21long0').closest('.msg.choice');
        const cr = c.getBoundingClientRect(), fil = c.parentElement.getBoundingClientRect();
        const btns = [...c.querySelectorAll('.choice-btn')];
        return { len: btns[0].querySelector('.choice-label').textContent.length,
                 card: [c.scrollWidth, c.clientWidth], inFil: cr.right <= fil.right + 1,
                 btns: btns.map(b => [b.scrollWidth, b.clientWidth, b.getBoundingClientRect().right <= cr.right + 1]),
                 lines: [...c.querySelectorAll('.choice-btn .choice-key, .choice-btn .choice-rec')].map(x => {
                   const r = document.createRange(); r.selectNodeContents(x);
                   return new Set([...r.getClientRects()].map(q => Math.round(q.top))).size; }) }; })()""")
    check(f"P21 : libellé de {lg['len']} caractères sans espace : carte sans débordement "
          f"(scrollWidth/clientWidth {lg['card']}, dans le fil : {lg['inFil']})",
          lg["len"] == 110 and lg["card"][0] <= lg["card"][1] and lg["inFil"])
    check(f"P21 : libellé de 110 caractères sans espace : boutons sans débordement {lg['btns']}",
          all(sw <= cw and inside for sw, cw, inside in lg["btns"]))
    check(f"P21 : libellé long : lettres a) b) c) et « recommandé » sur une seule ligne chacun {lg['lines']}",
          len(lg["lines"]) == 4 and all(n == 1 for n in lg["lines"]))
    await p.js("document.getElementById('choice-q-p21long0').closest('.msg.choice').scrollIntoView({ block: 'center' })")
    await p.shot("6c-choix-libelle-long.png")

    # P21 (testeur, fiche 5) : une réponse libre de 200 caractères sans espace (URL collée dans
    # « Autre », VALUE_MAX = 500) affichée dans « Répondu : … » faisait déborder la carte et
    # défiler le fil en largeur ; à 700 px, le min-width 320 de la carte faisait défiler le fil
    # même sans lien. Mesuré à 1600 puis 700 px, carte encore active puis répondue.
    async def mesure_p21(largeur):
        if largeur != 1600:
            await p.send("Emulation.setDeviceMetricsOverride", width=largeur, height=900, deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(0.4)
        await p.js("document.getElementById('choice-q-p21long0').closest('.msg.choice').scrollIntoView({ block: 'center' })")
        return await p.js("""(() => { const c = document.getElementById('choice-q-p21long0').closest('.msg.choice');
            const s = c.querySelector('.choice-status'), f = document.getElementById('transcript');
            return { txt: s.textContent.length, status: [s.scrollWidth, s.clientWidth], card: [c.scrollWidth, c.clientWidth],
                     fil: [f.scrollWidth, f.clientWidth],
                     cartes: [...f.querySelectorAll('.msg.choice')].filter(x => x.offsetWidth > 0)
                       .map(x => [x.scrollWidth, x.clientWidth]) }; })()""")

    def sans_debordement(m):
        return (m["status"][0] <= m["status"][1] and m["card"][0] <= m["card"][1] and m["fil"][0] <= m["fil"][1]
                and all(sw <= cw for sw, cw in m["cartes"]))

    m700 = await mesure_p21(700)
    check(f"P21 : à 700 px, carte active au libellé long : ni carte ni fil ne débordent "
          f"(carte {m700['card']}, fil {m700['fil']}, cartes visibles {m700['cartes']})", sans_debordement(m700))
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(0.4)
    libre = "https://exemple.fr/" + "abcdefghij" * 18 + "z"  # 200 caractères, ni espace ni tiret
    await p.js(f"window.__ws.onmessage({{ data: {json.dumps(json.dumps({'type': 'choice_answered', 'id': 'p21long0', 'value': libre, 'libre': True}))} }})")
    await p.until("document.getElementById('choice-q-p21long0').closest('.msg.choice').classList.contains('answered')", 3)
    for largeur in (1600, 700):
        m = await mesure_p21(largeur)
        check(f"P21 : à {largeur} px, réponse libre de {len(libre)} caractères sans espace (« Répondu : … », "
              f"{m['txt']} car.) : statut {m['status']}, carte {m['card']}, fil {m['fil']} sans débordement",
              len(libre) == 200 and m["txt"] > 200 and sans_debordement(m))
        await p.shot(f"6d-choix-reponse-libre-longue-{largeur}.png")
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(0.4)
    await p.until("!document.querySelector('.msg.choice.active')", 3)

    # ---------------------------------------------------------- coût par agent (idée 10)
    t = await p.js("""(() => [...document.querySelectorAll('#tasks .task')].map(r => ({
        title: r.querySelector('b').textContent, cost: r.querySelector('.cost').textContent,
        costShown: !r.querySelector('.cost').classList.contains('hidden'), alert: r.classList.contains('alert'),
        dot: !r.querySelector('.alert-dot').classList.contains('hidden'), time: r.querySelector('.time').textContent,
        stop: !!r.querySelector('.task-stop') })))()""")
    check(f"3 tâches dans « en cours » ({[x['title'] for x in t]})", len(t) == 3)
    if len(t) == 3:
        check(f"ligne de coût : « {t[0]['cost']} » et « {t[2]['cost']} », rien sans coût",
              t[0]["cost"] == "12 requêtes · 1,2 M tokens" and t[2]["cost"] == "162 requêtes · 1,4 M tokens"
              and not t[1]["costShown"])
        check(f"alerte (pastille rouge) sur l'agent qui s'emballe seulement, durée {t[2]['time']}",
              t[2]["alert"] and t[2]["dot"] and not t[0]["alert"] and not t[0]["dot"] and " min " in t[2]["time"])
        check("bouton Stop sur chaque tâche", all(x["stop"] for x in t))
    await p.js("window.__stopBtn = document.querySelectorAll('#tasks .task-stop')[2]")
    await asyncio.sleep(1.3)
    check("bouton Stop gardé d'un rafraîchissement à l'autre (le clic n'est pas perdu)",
          await p.js("window.__stopBtn.isConnected && document.querySelectorAll('#tasks .task-stop')[2] === window.__stopBtn"))
    await p.shot("7-en-cours-cout.png")
    await p.js("window.__stopBtn.click()")
    check("Stop : boîte rouge de confirmation", await p.until("!!document.querySelector('.modal.danger')", 2))
    await p.js("document.querySelector('.modal-ok').click()")
    await p.until("window.__sent.some(m => m.cmd === 'stop_task')", 3)
    last = (await sent("stop_task"))[-1:] or [{}]
    check(f"stop_task envoyé avec l'id de la tâche ({last[0]})", last[0].get("task_id") == "d3")
    check("tâche retirée du panneau", await p.until("document.querySelectorAll('#tasks .task').length === 2", 3))

    alert = await p.js("[...document.querySelectorAll('#activity-list .act')].some(r => r.textContent.includes(\"Agent qui s'emballe\"))")
    check("alerte d'agent (notice task_alert) notée dans l'activité, sans rien de bloquant", alert)

    # ---------------------------------------------------------- quota : prévision, fenêtres séparées
    await demo("demo_quota")
    q = await p.js("""(async () => { await new Promise(r => setTimeout(r, 300)); const b = document.querySelector('#quota');
        return { text: b.textContent, bars: b.querySelectorAll('.bar').length,
                 forecast: b.querySelector('.quota-forecast')?.textContent }; })()""")
    check(f"quota : 3 fenêtres en liste ({q['bars']}) et prévision « {q['forecast']} »",
          q["bars"] == 3 and "Semaine · Opus" in q["text"] and "62 %" in q["text"]
          and q["forecast"] == "À ce rythme : limite vers 17 h 40")
    await p.shot("8-quota.png")
    # 22/10 : ↻ du panneau Abonnement (Claude Code utilisé ailleurs) → `quota_refresh`, tourne pendant le relevé
    n = len(await sent("quota_refresh"))
    b = await p.js("""(() => { const b = document.querySelector('#quota-refresh'); b.click();
        return { busy: b.getAttribute('aria-busy'), label: b.getAttribute('aria-label'), ico: !!b.querySelector('svg'),
                 inTitle: b.parentElement.id === 'quota-title', title: b.parentElement.querySelector('[data-i18n]').textContent }; })()""")
    done = await p.until("!document.querySelector('#quota-refresh').hasAttribute('aria-busy')", 5)
    q = await p.js("document.querySelector('#quota').textContent")
    check(f"quota : ↻ « {b['label']} » dans l'intertitre « {b['title']} », en cours pendant le relevé "
          f"(aria-busy={b['busy']}), quota_refresh envoyé, fenêtres remplacées ({q[:60]}…)",
          b["busy"] == "true" and b["ico"] and b["inTitle"] and b["label"] == "Actualiser l'utilisation"
          and b["title"] == "Abonnement" and done and len(await sent("quota_refresh")) == n + 1
          and "92 %" in q and "Semaine" in q and "Opus" not in q)
    await p.shot("8-quota-actualise.png")

    # ---------------------------------------------------------- réouverture : état rejoué
    await p.send("Page.reload")
    await p.until("!!document.querySelector('.channel-tab')", 15)
    check("page rouverte : questions rejouées « répondu » et « annulée », aucune active", await p.until(
        "document.querySelectorAll('.msg.choice.answered').length === 5 && !document.querySelector('.msg.choice.active')"
        " && document.querySelectorAll('.msg.choice.cancelled').length === 1"
        " && [...document.querySelectorAll('.choice-status')].some(s => s.textContent === 'Répondu : Plutôt lundi')", 8))
    check("page rouverte : badge « Décision requise » rejoué",
          await p.until("!!document.querySelector('.msg.report-decision .report-badge.decision')", 4))


async def check_memoire(p: Page) -> None:
    """21/10 (plan P20) : ligne « Mémoire » sous « Contexte » (évènement `memory`), ambre en
    attention, rouge en blocage (jetons, Premium sombre et clair), segment de la ligne d'alerte en
    blocage, clic ou Entrée = classement (`memory_top`) en TEXTE, sans bouton pour fermer un processus."""
    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    async def theme(name, mode="dark"):  # 25/10 (plan P35) : Premium seul, `name` ignoré
        return await premium_mode(p, mode)

    color_of = "(v => { const s = document.createElement('span'); s.style.color = `var(${v})`; document.body.append(s); " \
               "const c = getComputedStyle(s).color; s.remove(); return c; })"
    mem = "document.querySelector('#memory')"
    panel_open = "!!document.querySelector('.modal.memory-top .mem-list')"
    check("ligne « Mémoire » affichée sous « Contexte » (démo : ok)", await p.until(
        f"!{mem}.classList.contains('hidden') && {mem}.textContent === 'RAM 9,4/15,3 Go · swap 62 %' "
        f"&& {mem}.previousElementSibling?.id === 'context'", 4))
    check("ok : discrète (muted), pas de bandeau", await p.js(
        f"{mem}.classList.contains('muted') && !{mem}.classList.contains('warn') && document.querySelector('#memory-banner').hidden"))
    await demo("demo_memory", state="attention")
    check("attention : ambre (--amber)", await p.until(
        f"{mem}.classList.contains('warn') && getComputedStyle({mem}).color === {color_of}('--amber') "
        f"&& document.querySelector('#memory-banner').hidden", 3))
    await demo("demo_memory", state="blocage", active=True)
    check("blocage : rouge (--red), segment de la ligne d'alerte, « nouveaux agents en pause »", await p.until(
        f"{mem}.classList.contains('crit') && getComputedStyle({mem}).color === {color_of}('--red') "
        "&& !document.querySelector('#memory-banner').hidden && !document.querySelector('#alert-line').hidden "
        "&& document.querySelector('#memory-detail').textContent.includes('nouveaux agents en pause')", 3))
    n0 = await p.js("window.__sent.filter(m => m.cmd === 'memory_top').length")
    await p.js(f"{mem}.click()")
    check("clic : classement des gros consommateurs (commande memory_top)", await p.until(panel_open, 4)
          and await p.js("window.__sent.filter(m => m.cmd === 'memory_top').length") == n0 + 1)
    check("classement : 7 applications, libellés traduits ou tels quels, en texte seulement", await p.js(
        "(() => { const b = document.querySelector('.modal.memory-top'); const rows = [...b.querySelectorAll('.mem-row')];"
        " return rows.length === 7 && rows[0].querySelector('.mem-name').textContent === 'LibreOffice'"
        " && rows[0].textContent.includes('3,2 Go') && rows[0].textContent.includes('+0,4 Go de swap')"
        " && rows[6].querySelector('.mem-name').textContent === '<img src=x onerror=alert(1)>' && !b.querySelector('img'); })()"))
    check("aucun bouton pour fermer un processus (seulement la croix de la fenêtre)", await p.js(
        "[...document.querySelectorAll('.modal.memory-top button')].every(b => b.classList.contains('modal-x'))"))
    await p.key("Escape", vk=27)
    check("Échap ferme le classement", await p.until("!document.querySelector('.modal.memory-top')", 2))
    await p.js("document.querySelector('#memory-show').click()")
    check("« Voir » du bandeau ouvre aussi le classement", await p.until(panel_open, 4))
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal.memory-top')", 2)
    await p.js(f"{mem}.focus()")
    c0 = await p.js("window.__sent.filter(m => m.cmd === 'confirm').length")
    await p.key("Enter", vk=13)
    check("Entrée sur la ligne ouvre le classement (clavier), sans répondre à la carte de confirmation",
          await p.until(panel_open, 4) and await p.js("window.__sent.filter(m => m.cmd === 'confirm').length") == c0)
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal.memory-top')", 2)
    ok_premium = await theme("premium", "dark")
    check("Premium sombre : rouge du thème (jeton), pas une couleur en dur", ok_premium and await p.js(
        f"getComputedStyle({mem}).color === {color_of}('--red') && getComputedStyle(document.querySelector('#memory-banner')).borderTopColor === {color_of}('--red')"))
    ok_light = await theme("premium", "light")
    check("Premium clair : rouge du thème", ok_light and await p.js(f"getComputedStyle({mem}).color === {color_of}('--red')"))
    await demo("demo_memory", state="blocage", active=False)
    check("blocage en observation : « rien n'est refusé »", await p.until(
        "document.querySelector('#memory-detail').textContent.includes('observation, rien n')", 3))
    await theme("premium", "auto")  # retour au défaut de la démo (P35 : plus de Classique)
    await demo("demo_memory", state="ok")
    check("retour à ok : bandeau retiré", await p.until(
        f"document.querySelector('#memory-banner').hidden && {mem}.classList.contains('muted')", 3))


async def check_courrier(p: Page) -> None:
    """26/10 (plan P33 lot 3) : volet « Courrier » des agents (évènement `courrier`) : caché sans
    mission, une ligne par mission, clic ou Entrée = derniers messages EN TEXTE (extrait hostile non
    interprété), aucun bouton d'action, caché en Simple."""
    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    box = "document.querySelector('#courrier')"
    title = "document.querySelector('#courrier-title')"
    panel_open = "!!document.querySelector('.modal.courrier-panel .courrier-list')"
    check("courrier : caché tant qu'aucune mission", await p.js(
        f"{box}.classList.contains('hidden') && {title}.classList.contains('hidden')"))
    await p.shot("p33-courrier-avant.png")
    await demo("demo_courrier")
    check("courrier : une ligne par mission, compteurs en mots", await p.until(
        f"!{box}.classList.contains('hidden') && !{title}.classList.contains('hidden') "
        f"&& {box}.querySelectorAll('.courrier-row').length === 2 "
        f"&& {box}.querySelector('.courrier-row span').textContent === 'p2' "
        f"&& {box}.querySelector('.courrier-row span').getBoundingClientRect().width >= 18 "
        f"&& {box}.querySelector('.courrier-row').getBoundingClientRect().height < 40 "
        f"&& {box}.querySelector('.courrier-row .time').textContent === '4 messages · 2 non lus · 1 question ouverte' "
        f"&& {box}.querySelectorAll('.courrier-row')[1].querySelector('.time').textContent === '1 message'", 4))
    await p.shot("p33-courrier-ligne.png")
    await p.js(f"{box}.querySelector('.courrier-row').click()")
    check("clic : derniers messages, extrait hostile en texte, aucun bouton d'action", await p.until(panel_open, 3)
          and await p.js(
              "(() => { const b = document.querySelector('.modal.courrier-panel'); const li = [...b.querySelectorAll('.courrier-msg')];"
              " return li.length === 3 && li[0].querySelector('.courrier-text').textContent === '<img src=x onerror=alert(1)>'"
              " && !b.querySelector('.courrier-list img') && li[0].querySelector('.courrier-head').textContent.startsWith('n° 4 · message de chercheur à planificateur')"
              " && li[2].querySelector('.courrier-head').textContent.includes('réponse au n° 1')"
              " && b.querySelector('.ad-note').textContent === 'planificateur : 2 non lu(s)'"
              " && [...b.querySelectorAll('button')].every(x => x.classList.contains('modal-x')); })()"))
    await p.shot("p33-courrier-volet.png")
    await p.key("Escape", vk=27)
    check("Échap ferme le volet", await p.until("!document.querySelector('.modal.courrier-panel')", 2))
    await p.js(f"{box}.querySelector('.courrier-row').focus()")
    c0 = await p.js("window.__sent.filter(m => m.cmd === 'confirm').length")
    await p.key("Enter", vk=13)
    check("Entrée sur la ligne ouvre le volet, sans répondre à la carte de confirmation",
          await p.until(panel_open, 3) and await p.js("window.__sent.filter(m => m.cmd === 'confirm').length") == c0)
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal.courrier-panel')", 2)
    await p.js("document.documentElement.dataset.affichage = 'simple', true")
    await asyncio.sleep(0.3)
    check("Simple : volet courrier masqué", await p.js(
        f"{box}.getClientRects().length === 0 && {title}.getClientRects().length === 0"))
    await p.js("delete document.documentElement.dataset.affichage, true")
    await demo("demo_courrier", off=True)
    check("courrier vide : volet de nouveau caché", await p.until(f"{box}.classList.contains('hidden')", 3))


async def check_mission_preuve(p: Page) -> None:
    """02/11 (plan P43) : preuve de fin de mission. Trois fins impossibles à confondre (vérifiée,
    acceptée sans preuve, vérification échouée), attente d'une décision (boutons, bandeau, origine
    du critère), rapport de contrôles rendu EN TEXTE (déclaration hostile non interprétée)."""
    async def demo(etat):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'demo_mission', 'etat': etat}))})")

    box = "document.querySelector('#mission')"
    fin = f"({box}.querySelector('.mission-fin') || {{}})"
    await demo("verifiee")
    check("mission vérifiée : fin verte « VÉRIFIÉE », preuve et contrôles en texte", await p.until(
        f"{box}.classList.contains('done') && {fin}.classList?.contains('ok') "
        f"&& {fin}.textContent.includes('VÉRIFIÉE') && {box}.querySelectorAll('.mission-preuve-list li.etat-ok').length === 4 "
        f"&& {box}.querySelector('.mission-preuve-head').textContent.startsWith('Vérifiée') "
        f"&& {box}.querySelectorAll('.mission-critere li').length === 4 "
        f"&& {box}.querySelector('.mission-critere li').textContent === 'docs/RAPPORT.md existe'", 4))
    check("déclaration hostile rendue en texte, aucune image", await p.js(
        f"!{box}.querySelector('img') && [...{box}.querySelectorAll('.mission-last')].some("
        "e => e.textContent.includes('<img src=x onerror=alert(1)>'))"))
    await p.shot("p43-mission-verifiee.png")
    await demo("accepted")
    check("acceptée sans preuve : fin ambre en pointillés, jamais « vérifiée »", await p.until(
        f"{box}.classList.contains('accepted_unverified') && {fin}.classList?.contains('accepted') "
        f"&& {fin}.textContent.includes('SANS preuve') && !{box}.textContent.includes('VÉRIFIÉE') "
        f"&& !{box}.querySelector('.mission-preuve') && getComputedStyle({box}.querySelector('.mission-fin')).borderStyle === 'dashed'", 4))
    await p.shot("p43-mission-acceptee.png")
    await demo("failed")
    check("vérification échouée : fin rouge, contrôles en échec marqués", await p.until(
        f"{box}.classList.contains('verification_failed') && {fin}.classList?.contains('failed') "
        f"&& {box}.querySelectorAll('.mission-preuve-list li.etat-echec').length === 2 "
        f"&& {box}.querySelector('.mission-preuve-head').textContent.startsWith('Critère non rempli')", 4))
    await p.shot("p43-mission-echouee.png")
    await demo("decide")
    check("en attente de décision : bandeau, origine d'Astrel non approuvée, quatre décisions", await p.until(
        f"{box}.classList.contains('decide') && !document.querySelector('#mission-banner').hidden "
        "&& document.querySelector('#mission-banner').classList.contains('decide') "
        "&& document.querySelector('#mission-banner-head').textContent.includes('DÉCIDER') "
        f"&& {box}.querySelector('.mission-origine.astrel') "
        f"&& ['Revérifier', 'Remettre au travail', 'Accepter sans preuve', 'Approuver le critère'].every("
        f"x => [...{box}.querySelectorAll('.mission-actions button')].some(b => b.textContent.includes(x))) "
        f"&& {box}.querySelectorAll('.mission-preuve-list li.etat-bloque').length === 1 && !{fin}.classList", 4))
    await p.shot("p43-mission-decision.png")
    # Accepter sans preuve passe par une boîte qui le dit, et n'envoie rien si on annule.
    n0 = await p.js("window.__sent.filter(m => m.cmd === 'mission').length")
    await p.js(f"[...{box}.querySelectorAll('.mission-actions button')].find(b => b.textContent.includes('Accepter')).click()")
    check("accepter : boîte « jamais vérifiée » avant d'agir", await p.until(
        "!!document.querySelector('.modal') && document.querySelector('.modal').textContent.includes('jamais « vérifiée »')", 3))
    await p.key("Escape", vk=27)
    await asyncio.sleep(0.2)
    check("annulée : aucune commande envoyée", await p.js("window.__sent.filter(m => m.cmd === 'mission').length") == n0)
    await p.js(f"document.querySelector('#mission-banner').hidden = true; {box}.replaceChildren(), true")
    await demo("verifiee")
    await p.until(f"{box}.classList.contains('done')", 3)
    # 04/11 : mission de démo (« m-demo », inconnue du cœur) : mission_close refusé → repli du navigateur.
    await p.js("localStorage.removeItem('astrel-mission-masquee'), true")
    await p.js("document.querySelector('#mission-hide').click(), true")
    check("mission_close refusé par le cœur : masquée quand même, retenue par le navigateur (repli)", await p.until(
        f"{box}.classList.contains('hidden') && !!localStorage.getItem('astrel-mission-masquee')", 3))
    await p.js("localStorage.removeItem('astrel-mission-masquee'), true")


async def check_todo(p: Page) -> None:
    """26/10 (plan P38 lot 2) : vue « Tâches » (démo : `seed_demo_todo`, vrai module astrel.todo).
    Aperçu du panneau droit en mots, pastille « attend toi », 4 colonnes, badges (plan et son statut,
    attend toi, agent en cours lu dans `tasks.refs`), titre hostile en texte, abandonnées repliées,
    détail (note, historique), Déplacer / Abandonner avec raison, carte nommée `todo_id` (jamais
    `id`), Ajouter et doublon, flèches et Entrée, 2 × 2 sous 900 px, Échap, caché en Simple."""
    modal = "document.querySelector('.modal.wide.todo')"
    card = lambda cid: f"{modal}.querySelector('.todo-card[data-todo=\"{cid}\"]')"
    small = "document.querySelector('.modal:not(.wide)')"
    ap = "document.querySelector('#todo-apercu')"
    # tâches de la démo republiées telles quelles : d1 porte `refs: ["p2"]` (badge « agent en cours » de T1)
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_tasks_nodone' })), true")
    await asyncio.sleep(0.3)
    projets = await p.js("[...document.querySelectorAll('#todo-apercu .todo-apercu-row')].map(r => r.dataset.projet)")
    check(f"todo : aperçu, une ligne par projet qui a des tâches ouvertes ({projets})", await p.until(
        f"!{ap}.classList.contains('hidden') && !document.querySelector('#todo-title').classList.contains('hidden') "
        f"&& {ap}.querySelectorAll('.todo-apercu-row').length === 3 "
        f"&& {ap}.querySelector('.todo-apercu-row .time').textContent === '1 à planifier · 1 à lancer · 1 en cours' "
        f"&& {ap}.querySelectorAll('.todo-apercu-row')[1].querySelector('.time').textContent === '1 à lancer · 1 en cours'", 4))
    check("todo : pastille = 2 tâches qui attendent l'utilisateur", await p.js(
        "!document.querySelector('#todo-badge').classList.contains('hidden') && document.querySelector('#todo-badge').textContent === '2'"
        " && document.querySelector('#todo-chip').title.includes('2 t\\'attendent')"))
    await p.shot("p38-todo-apercu.png")

    await p.js("document.querySelector('#todo-chip').click()")
    ok = await p.until(f"!!{modal} && {modal}.querySelectorAll('.todo-col').length === 4", 4)
    heads = await p.js(f"[...{modal}.querySelectorAll('.todo-col-head')].map(h => h.firstChild.textContent)") if ok else []
    check(f"todo : 4 colonnes ({heads})", ok and heads == ["À planifier", "À lancer", "En cours", "Fini"])
    check("todo : badges plan validé + agent en cours (refs p2), attend toi, projet affiché sous « Tous »", await p.js(
        f"{card('T1')}.querySelector('.todo-plan.s-validated')?.textContent === 'P2'"
        f" && {card('T1')}.querySelector('.todo-agent')?.textContent.trim() === 'agent en cours'"
        f" && {card('T1')}.querySelector('.todo-agent').title === 'Au travail : Trier les mails de la semaine'"
        f" && !!{card('T2')}.querySelector('.todo-wait') && {card('T2')}.querySelector('.todo-plan.s-pending')?.title === 'Plan P1 : à relire'"
        f" && !{card('T3')}.querySelector('.todo-agent') && !!{card('T3')}.querySelector('.todo-projet')"))
    # Lot 3 (27/10) : fiche de relecteur sans réserve sur « p2 » (hud_demo) → T1 seule porte « prêt à clore ».
    check("todo : badge prêt à clore sur T1 seule, info-bulle mission et fiche", await p.js(
        f"{card('T1')}.querySelector('.todo-ready')?.textContent === 'prêt à clore'"
        f" && {card('T1')}.querySelector('.todo-ready').title.includes('mission p2, fiche 3')"
        f" && {modal}.querySelectorAll('.todo-ready').length === 1"))
    check("todo : titre hostile en texte, aucune image ; titre long coupé à 120", await p.js(
        f"{card('T8')}.querySelector('.todo-card-title').textContent === '<img src=x onerror=alert(1)>'"
        f" && !{modal}.querySelector('img') && {card('T9')}.querySelector('.todo-card-title').textContent.length === 120"
        f" && {card('T9')}.scrollWidth <= {card('T9')}.clientWidth"))
    check("todo : Fini dans sa colonne, abandonnée repliée hors des colonnes", await p.js(
        f"{modal}.querySelector('.todo-col.c-fini').contains({card('T4')})"
        f" && !{modal}.querySelector('.todo-board').contains({card('T5')})"
        f" && {modal}.querySelector('.todo-dropped > summary').textContent === 'Abandonnée (1)'"
        f" && !{modal}.querySelector('.todo-dropped').open"))
    await p.shot("p38-todo-tableau.png")

    # clavier : flèche droite de T3 (À planifier) vers la colonne « À lancer », Entrée ouvre
    await p.js(f"{card('T3')}.focus()")
    await p.key("ArrowRight", vk=39)
    check("todo : flèche droite = colonne suivante", await p.js(
        f"document.activeElement?.closest('.todo-col')?.dataset.etat === 'lancer'"))
    await p.key("ArrowDown", vk=40)
    await p.key("ArrowUp", vk=38)
    focused = await p.js("document.activeElement?.dataset.todo || ''")
    n_get = await p.js("window.__sent.filter(m => m.cmd === 'todo_get').length")
    await p.key("Enter", vk=13, text="\r")  # sans text, Chrome headless n'active pas le bouton (fiche 2)
    ok = await p.until(f"!!{modal}.querySelector('.todo-detail .todo-hist-row')", 4)
    last_get = await p.js("window.__sent.filter(m => m.cmd === 'todo_get').at(-1) || {}")
    check(f"todo : Entrée ouvre le détail de {focused}, demandé par todo_id (requête {last_get.get('id')})",
          ok and last_get.get("todo_id") == focused and await p.js("window.__sent.filter(m => m.cmd === 'todo_get').length") > n_get
          and await p.js(f"{modal}.querySelector('.todo-detail').dataset.todo === {json.dumps(focused)}"
                         " && document.activeElement?.dataset.fkey === 'back'"))
    await p.js(f"{modal}.querySelector('.todo-back').click()")
    check("todo : Retour = tableau, focus rendu à la carte", await p.until(
        f"!!{modal}.querySelector('.todo-board') && document.activeElement?.dataset.todo === {json.dumps(focused)}", 3))
    # fiche 2 du lot 2 : Échap dans le détail ne referme QUE le détail ; la vue reste ouverte
    await p.key("Enter", vk=13, text="\r")
    await p.until(f"!!{modal}.querySelector('.todo-detail')", 3)
    await p.key("Escape", vk=27)
    check("todo : Échap dans le détail = retour au tableau (vue ouverte, focus sur la carte)", await p.until(
        f"!!{modal} && !!{modal}.querySelector('.todo-board') && !{modal}.querySelector('.todo-detail')"
        f" && document.activeElement?.dataset.todo === {json.dumps(focused)}", 3))

    # détail de T2 : note absente, déplacer vers « En cours », puis abandonner avec raison
    await p.js(f"{card('T2')}.click()")
    await p.until(f"!!{modal}.querySelector('.todo-detail .todo-hist-row')", 4)
    check("todo : détail (titre, état, pas de note, historique « créée dans À lancer »)", await p.js(
        f"{modal}.querySelector('.todo-detail-title').textContent === 'Relire le plan du déménagement'"
        f" && {modal}.querySelector('.todo-state').textContent === 'À lancer'"
        f" && {modal}.querySelector('.todo-section .todo-empty').textContent === 'Pas de note.'"
        f" && {modal}.querySelector('.todo-hist-row .todo-hist-what').textContent === 'créée dans « À lancer » · par toi'"))
    await p.shot("p38-todo-detail.png")
    await p.js(f"{modal}.querySelector('.todo-move[data-etat=\"cours\"]').click()")
    ok = await p.until(f"{modal}.querySelector('.todo-state')?.classList.contains('e-cours')"
                       f" && {modal}.querySelectorAll('.todo-hist-row').length === 2", 4)
    mv = await p.js("window.__sent.filter(m => m.cmd === 'todo_move').at(-1) || {}")
    what = await p.js(f"{modal}.querySelector('.todo-hist-row .todo-hist-what')?.textContent || ''")
    # fiche 2 du lot 2 : l'acteur dépend de qui lance le contrôle. Le serveur marque la connexion du HUD
    # « vient de Claude » si Chrome descend d'un process Claude ou du cgroup astrel.service (état réel,
    # todo.py `commande`) : « par Astrel » alors, « par toi » depuis un terminal de l'utilisateur. Les deux
    # sont justes ; le contrôle dit lequel il a vu.
    who = "toi" if what.endswith("· par toi") else "Astrel (connexion vue comme Claude)" if what.endswith("· par Astrel") else "?"
    check(f"todo : Déplacer vers En cours (todo_move todo_id={mv.get('todo_id')}, historique « {what} », acteur {who})",
          ok and mv.get("todo_id") == "T2" and mv.get("etat") == "cours"
          and what in ("À lancer → En cours · par toi", "À lancer → En cours · par Astrel"))
    await p.js(f"{modal}.querySelector('.todo-drop').click()")
    await p.until(f"!!{small}?.querySelector('textarea[name=raison]')", 3)
    await p.js(f"{small}.querySelector('.modal-ok').click()")
    check("todo : abandon sans raison refusé par la boîte", await p.until(
        f"!{small}.querySelector('.modal-error').classList.contains('hidden')", 2)
        and await p.js("window.__sent.filter(m => m.cmd === 'todo_drop').length") == 0)
    await p.js(f"(() => {{ const t = {small}.querySelector('textarea[name=raison]'); t.value = 'Fait à la main hier.';"
               " t.dispatchEvent(new Event('input', {bubbles: true})); })()")
    await p.js(f"{small}.querySelector('.modal-ok').click()")
    ok = await p.until(f"{modal}.querySelector('.todo-state')?.classList.contains('e-abandonne')", 4)
    check("todo : Abandonner avec raison (raison affichée, plus de Fini ni d'Abandonner, Rouvrir dans)", ok and await p.js(
        f"{modal}.querySelector('.todo-reason').textContent === 'Raison de l\\'abandon : Fait à la main hier.'"
        f" && !{modal}.querySelector('.todo-done') && !{modal}.querySelector('.todo-drop')"
        f" && {modal}.querySelector('.todo-actions .todo-section-title').textContent === 'Rouvrir dans'"
        f" && {modal}.querySelectorAll('.todo-move').length === 3"))
    check("todo : pastille suit le cœur (1 tâche attend l'utilisateur)", await p.until(
        "document.querySelector('#todo-badge').textContent === '1'", 3))
    await p.js(f"{modal}.querySelector('.todo-back').click()")
    await p.until(f"!!{modal}.querySelector('.todo-board')", 2)

    # filtre par projet, puis Ajouter (et doublon)
    p1 = projets[1] if len(projets) > 1 else ""
    await p.js(f"{modal}.querySelector('.todo-filter[data-projet={json.dumps(p1)}]').click()")
    check(f"todo : filtre {p1} (2 cartes, compteur 2, pas d'étiquette de projet)", await p.until(
        f"{modal}.querySelectorAll('.todo-board .todo-card').length === 2"
        f" && {modal}.querySelector('.todo-filter.on .todo-filter-n').textContent === '2'"
        f" && !{modal}.querySelector('.todo-projet')", 3))
    await p.js(f"{modal}.querySelector('.todo-add').click()")
    ok = await p.until(f"!!{small}?.querySelector('input[name=titre]') && !{small}.querySelector('input[name=projet]')", 3)
    await p.js(f"(() => {{ const i = {small}.querySelector('input[name=titre]'); i.value = 'Écrire la liste des invités'; }})()")
    await p.js(f"{small}.querySelector('.modal-ok').click()")
    ok = ok and await p.until(f"[...{modal}.querySelectorAll('.todo-col.c-planifier .todo-card-title')]"
                              ".some(x => x.textContent === 'Écrire la liste des invités')", 4)
    add = await p.js("window.__sent.filter(m => m.cmd === 'todo_add').at(-1) || {}")
    check(f"todo : Ajouter dans le projet filtré ({add.get('projet')}), arrive dans À planifier", ok and add.get("projet") == p1)
    await p.js(f"{modal}.querySelector('.todo-add').click()")
    await p.until(f"!!{small}?.querySelector('input[name=titre]')", 3)
    await p.js(f"(() => {{ const i = {small}.querySelector('input[name=titre]'); i.value = 'Ecrire la liste des invites'; }})()")
    await p.js(f"{small}.querySelector('.modal-ok').click()")
    ok = await p.until(f"{small}?.querySelector('.modal-ok')?.textContent === 'Ajouter quand même'", 3)
    dup = await p.js(f"{small}?.querySelector('.modal-text')?.textContent || ''")
    check(f"todo : doublon proche proposé ({dup[:60]})", ok and "« Écrire la liste des invités »" in dup)
    await p.key("Escape", vk=27)
    await p.until(f"!{small}", 2)
    check("todo : doublon annulé, rien d'ajouté", await p.js(
        f"[...{modal}.querySelectorAll('.todo-card-title')].filter(x => /crire la liste des invit/.test(x.textContent)).length === 1"
        " && window.__sent.filter(m => m.cmd === 'todo_add' && m.force).length === 0"))

    # fiche 2 du lot 2 : un Ajouter refusé par le cœur (projet inconnu, Général) garde la boîte ouverte,
    # avec le message et le texte tapé
    await p.js(f"{modal}.querySelector('.todo-filter[data-projet=\"\"]').click()")
    await p.until(f"{modal}.querySelector('.todo-filter.on')?.dataset.projet === ''", 2)
    await p.js(f"{modal}.querySelector('.todo-add').click()")
    await p.until(f"!!{small}?.querySelector('input[name=projet]')", 3)
    fill = lambda name, v: p.js(f"(() => {{ const i = {small}.querySelector('[name={name}]'); i.value = {json.dumps(v)};"
                                " i.dispatchEvent(new Event('input', {bubbles: true})); })()")
    await fill("projet", "Projet fantôme")
    await fill("titre", "Tâche refusée par le cœur")
    await fill("note", "Note tapée à garder.")
    n_add = await p.js("window.__sent.filter(m => m.cmd === 'todo_add').length")
    await p.js(f"{small}.querySelector('.modal-ok').click()")
    err_vis = f"!!{small} && !{small}.querySelector('.modal-error').classList.contains('hidden')"
    ok = await p.until(f"{err_vis} && window.__sent.filter(m => m.cmd === 'todo_add').length === {n_add + 1}", 4)
    err = await p.js(f"{small}?.querySelector('.modal-error')?.textContent || ''")
    kept = await p.js(f"!!{small} && {small}.querySelector('[name=titre]').value === 'Tâche refusée par le cœur'"
                      f" && {small}.querySelector('[name=note]').value === 'Note tapée à garder.'"
                      f" && !{small}.querySelector('.modal-ok').disabled")
    check(f"todo : Ajouter refusé (projet inconnu) : boîte ouverte, « {err[:50]}… », texte gardé",
          ok and kept and err.startswith("Refusé : projet inconnu"))
    await fill("projet", "Général")
    hidden = await p.js(f"{small}.querySelector('.modal-error').classList.contains('hidden')")
    await p.js(f"{small}.querySelector('.modal-ok').click()")
    ok = await p.until(f"{err_vis} && window.__sent.filter(m => m.cmd === 'todo_add').length === {n_add + 2}", 4)
    err = await p.js(f"{small}?.querySelector('.modal-error')?.textContent || ''")
    check(f"todo : Ajouter dans Général refusé dans la boîte (« {err} »), message effacé à la frappe ({hidden})",
          ok and hidden and err == "Refusé : une carte va dans un projet, pas dans Général")
    await p.key("Escape", vk=27)
    await p.until(f"!{small}", 2)
    check("todo : refus annulé, aucune carte ni toast d'ajout", await p.js(
        f"!!{modal} && ![...{modal}.querySelectorAll('.todo-card-title')].some(x => x.textContent === 'Tâche refusée par le cœur')"
        " && ![...document.querySelectorAll('.toast')].some(x => /refusée par le cœur/.test(x.textContent))"))

    # fiches 4 et 5 du lot 2 (echap_en_vol) : pendant un todo_add en vol (retardé de 1,5 s, technique du
    # testeur), Échap, Annuler et un clic à côté ne ferment pas la boîte ; la réponse arrivée, elle se ferme
    # et le toast d'ajout apparaît (avant : boîte fermée, carte créée sans toast, refus invisible).
    await p.js("(() => { const s = WebSocket.prototype.send; window.__ws.send = function (d) { try { if (JSON.parse(d).cmd"
               " === 'todo_add') { setTimeout(() => s.call(this, d), 1500); return; } } catch {} return s.call(this, d); }; })(), true")
    await p.js(f"{modal}.querySelector('.todo-add').click()")
    await p.until(f"!!{small}?.querySelector('input[name=projet]')", 3)
    await fill("projet", projets[0])
    await fill("titre", "Ajout en vol")
    await p.js(f"{small}.querySelector('.modal-ok').click()")
    await asyncio.sleep(0.3)
    grise = await p.js(f"!!{small} && {small}.querySelector('.modal-ok').disabled")
    await p.key("Escape", vk=27)
    await asyncio.sleep(0.15)
    apres_echap = await p.js(f"!!{small} && !!{modal}")
    await p.js(f"{small}?.querySelector('.modal-cancel')?.click(), true")
    await asyncio.sleep(0.15)
    apres_annuler = await p.js(f"!!{small}")
    await p.js("document.querySelector('.modal-back:not(.panel-back)')?.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})), true")
    await asyncio.sleep(0.15)
    apres_clic = await p.js(f"!!{small}")
    en_vol = await p.js("window.__sent.filter(m => m.cmd === 'todo_add' && m.titre === 'Ajout en vol').length") == 0
    check(f"todo : echap_en_vol, bouton grisé ({grise}), Échap / Annuler / clic à côté ignorés pendant l'ajout "
          f"({apres_echap} / {apres_annuler} / {apres_clic}, requête encore retenue : {en_vol})",
          grise and apres_echap and apres_annuler and apres_clic and en_vol)
    cid_js = (f"[...{modal}.querySelectorAll('.todo-card')].find(c => c.querySelector('.todo-card-title')"
              ".textContent === 'Ajout en vol')?.dataset.todo || ''")
    ok = await p.until(f"!{small} && !!({cid_js}) && [...document.querySelectorAll('.toast')]"
                       f".some(x => x.textContent.includes(({cid_js}) + ' ajoutée à'))", 5)
    cid = await p.js(cid_js)
    check(f"todo : echap_en_vol, réponse arrivée : boîte fermée, carte {cid} et toast d'ajout", ok)
    await p.js("delete window.__ws.send, true")

    # 2 × 2 sous 900 px
    await p.send("Emulation.setDeviceMetricsOverride", width=860, height=900, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(0.3)
    cols = await p.js(f"getComputedStyle({modal}.querySelector('.todo-board')).gridTemplateColumns.split(' ').length")
    check(f"todo : sous 900 px, colonnes en 2 × 2 ({cols} par ligne)", cols == 2)
    await p.shot("p38-todo-etroit.png")
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(0.3)
    await p.key("Escape", vk=27)
    check("todo : Échap ferme la vue", await p.until(f"!{modal}", 2))

    # T26 (29/10) : priorité dans une colonne, projet choisi : Alt+↓ (ou glisser) = `todo_ordre`, la colonne suit ;
    # vue « Tous » : rien à glisser (la priorité est celle de chaque projet).
    await p.js("document.querySelector('#todo-chip').click()")
    await p.until(f"!!{modal}", 3)
    tous = await p.js(f"[...{modal}.querySelectorAll('.todo-card')].some(c => c.draggable)")
    col = await p.js("""(() => {
        for (const id of [...document.querySelectorAll('.modal.wide.todo .todo-filter')].map(f => f.dataset.projet).filter(Boolean)) {
          [...document.querySelectorAll('.modal.wide.todo .todo-filter')].find(f => f.dataset.projet === id).click();
          const cards = [...document.querySelectorAll('.modal.wide.todo .c-planifier .todo-card')];
          if (cards.length >= 2) return { projet: id, ids: cards.map(c => c.dataset.todo), drag: cards.every(c => c.draggable) };
        }
        return null; })()""")
    ok = bool(col)
    if ok:
        await p.js(f"""(() => {{ const c = document.querySelector('.modal.wide.todo .todo-card[data-todo="{col['ids'][0]}"]');
            c.focus(); c.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'ArrowDown', altKey: true, bubbles: true }})); }})()""")
        want = json.dumps([col["ids"][1], col["ids"][0], *col["ids"][2:]], separators=(",", ":"))  # = JSON.stringify
        ok = await p.until(f"JSON.stringify(window.__sent.filter(m => m.cmd === 'todo_ordre').at(-1)?.ids) === {json.dumps(want)}"
                           f" && JSON.stringify([...document.querySelectorAll('.modal.wide.todo .c-planifier .todo-card')].map(c => c.dataset.todo))"
                           f" === {json.dumps(want)}", 4)
        ok = ok and await p.until(f"document.activeElement?.dataset?.todo === {json.dumps(col['ids'][0])}", 2)
    check(f"todo T26 : Alt+↓ classe la carte (projet {col and col['projet']}, {col and col['ids'][:2]}), glissable "
          f"seulement avec un projet choisi ({col and col['drag']} / vue Tous {tous})", ok and col["drag"] and not tous)
    await p.key("Escape", vk=27)
    await p.until(f"!{modal}", 2)

    # aperçu : clic = vue filtrée sur ce projet ; Simple : pastille et aperçu cachés
    await p.js(f"{ap}.querySelector('.todo-apercu-row').click()")
    check("todo : clic sur l'aperçu = vue filtrée sur ce projet", await p.until(
        f"!!{modal} && {modal}.querySelector('.todo-filter.on')?.dataset.projet === {json.dumps(projets[0])}", 3))
    await p.key("Escape", vk=27)
    await p.until(f"!{modal}", 2)
    await p.js("document.documentElement.dataset.affichage = 'simple', true")
    await asyncio.sleep(0.3)
    check("Simple : pastille Tâches et aperçu masqués", await p.js(
        "['#todo-chip', '#todo-apercu', '#todo-title'].every(s => document.querySelector(s).getClientRects().length === 0)"))
    await p.js("delete document.documentElement.dataset.affichage, true")

    # fiche 2 du lot 2 : avec « Tâches », la barre passait sur deux lignes de 1440 à 1556 px. Sous 1600 px,
    # Tâches et Journal en icône (+ compteur), nom gardé pour le lecteur d'écran et dans l'infobulle.
    rows_js = ("(() => { const b = document.querySelector('#topbar').getBoundingClientRect();"
               " const lab = (s) => { const x = document.querySelector(s + ' > span[data-i18n]'); return x.getBoundingClientRect().width; };"
               " return { h: Math.round(b.height), rows: new Set([...document.querySelectorAll('#topbar .tgroup')]"
               ".map(g => { const r = g.getBoundingClientRect(); return Math.round((r.top + r.bottom) / 20); })).size,"
               " todo: Math.round(lab('#todo-chip')), audit: Math.round(lab('#audit-chip')),"
               " names: ['#todo-chip', '#audit-chip'].map(s => document.querySelector(s).textContent.trim()),"
               " titles: ['#todo-chip', '#audit-chip'].every(s => !!document.querySelector(s).title) }; })()")
    for w in (1440, 1540, 1600):
        await p.send("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(0.4)
        r = await p.js(rows_js)
        compact = w < 1600
        check(f"{w} px : barre du haut sur une ligne ({r['h']} px, {r['rows']} rang), Tâches et Journal "
              f"{'en icône' if compact else 'avec leur nom'} ({r['todo']} / {r['audit']} px de libellé), noms {r['names']}",
              r["rows"] == 1 and r["titles"] and r["names"][0].startswith("Tâches") and r["names"][1] == "Journal"
              and ((r["todo"] <= 1 and r["audit"] <= 1) if compact else (r["todo"] > 20 and r["audit"] > 20)))
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(0.3)


async def check_detail_agent(p: Page) -> None:
    """21/10 (plan P18) : clic sur une ligne du panneau « en cours » → détail de l'agent (commande
    `task_detail`), tâches finies grisées 10 min, `done` absent toléré, fiche expirée sans erreur,
    données d'agent en TEXTE (balise, U+202E, caractères de contrôle), rafraîchi toutes les 2 s,
    Premium automatique puis sombre et clair (P35 : plus de Classique). Passe après check_lots_15_10 (d3 arrêtée : d1, d2)."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")

    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    async def theme(name, mode="auto"):  # 25/10 (plan P35) : Premium seul, `name` ignoré
        return await premium_mode(p, mode)

    rows = "[...document.querySelectorAll('#tasks .task')]"
    row_of = lambda desc: f"{rows}.find(r => r.querySelector('b').textContent.includes({json.dumps(desc)}))"
    detail_open = "!!document.querySelector('.modal.agent-detail .ad-meta')"

    async def open_row(desc):
        await p.js(f"{row_of(desc)}.click()")
        return await p.until(detail_open, 4)

    async def close():
        await p.key("Escape", vk=27)
        await p.until("!document.querySelector('.modal.agent-detail')", 2)

    # ---------------------------------------------------------- `done` absent puis présent
    await demo("demo_tasks_nodone")
    await asyncio.sleep(0.3)
    check("évènement tasks sans `done` (démo, cœur d'avant P18) : 2 lignes, aucune finie, pas d'erreur",
          await p.js(f"{rows}.length === 2 && !document.querySelector('#tasks .task.done')"))
    await demo("demo_tasks_done")
    await p.until(f"{rows}.length === 4", 3)
    t = await p.js(f"""{rows}.map(r => ({{ title: r.querySelector('b').textContent, done: r.classList.contains('done'),
        failed: r.classList.contains('failed'), step: r.querySelector('.step').textContent,
        stop: !!r.querySelector('.task-stop:not(.hidden)'), role: r.querySelector('b').getAttribute('role'),
        tab: r.querySelector('b').tabIndex, rowRole: r.getAttribute('role'), rowTab: r.getAttribute('tabindex'),
        nested: !!r.querySelector('[role=button] .task-stop, [role=button] button'),
        label: r.querySelector('b').getAttribute('aria-label'),
        anim: getComputedStyle(r).animationName }}))""")
    check(f"tâches finies grisées sous les vivantes ({[(x['title'], x['step']) for x in t]})",
          len(t) == 4 and [x["done"] for x in t] == [False, False, True, True]
          and t[2]["step"].endswith("Terminé · 61 actions") and t[3]["step"].endswith("Échec · 24 actions")
          and t[3]["failed"])  # le canal précède l'étape quand il y en a plusieurs
    check("tâche finie : ni Stop ni pulsation ; toutes les lignes cliquables au clavier (role=button, tabindex 0)",
          not t[2]["stop"] and not t[3]["stop"] and t[0]["stop"] and t[2]["anim"] == "none"
          and all(x["role"] == "button" and x["tab"] == 0 for x in t))
    # Relecture P18 : le rôle est sur le TITRE, la ligne qui contient Stop n'en a plus (interactif imbriqué).
    check(f"rôle « button » sur le titre seul : ligne sans rôle ni tabindex, Stop hors du bouton ({t[0]['label']!r})",
          all(x["rowRole"] is None and x["rowTab"] is None and not x["nested"] for x in t)
          and t[0]["label"] == "Détail de l'agent : " + t[0]["title"].strip())
    check("compteur : agents vivants seulement (2)", await p.js("document.querySelector('#tasks-count').textContent === '2'"))
    await demo("demo_tasks_nodone")
    await asyncio.sleep(0.3)
    check("évènement suivant sans `done` : les tâches finies connues restent", await p.js(f"{rows}.length === 4"))

    # ---------------------------------------------------------- agent en cours (d1, avec pièges)
    n0 = len(await sent("task_detail"))
    check("clic sur la ligne : détail ouvert", await open_row("Trier les mails"))
    last = (await sent("task_detail"))[-1:] or [{}]
    check(f"task_detail envoyé avec l'id de la tâche ({last[0].get('task_id')})", last[0].get("task_id") == "d1")
    trap = "<img src=x onerror=\"window.__pwned=1\">‮evil\x1b[31m rouge\x00 fin"
    d = await p.js(f"""(() => {{ const m = document.querySelector('.modal.agent-detail');
        const blocks = [...m.querySelectorAll('.ad-block')].map(b => b.textContent);
        return {{ title: m.querySelector('.modal-title').textContent,
          state: m.querySelector('.ad-state').textContent, chips: [...m.querySelectorAll('.ad-chip')].map(c => c.textContent),
          consigne: blocks[0], trap: blocks[0].includes({json.dumps(trap)}),
          todoTrap: [...m.querySelectorAll('.ad-todo li')].some(li => li.textContent.includes({json.dumps(trap)})),
          errTrap: [...m.querySelectorAll('.ad-error')].some(e => e.textContent.includes({json.dumps(trap)})),
          bad: m.querySelectorAll('img, script, iframe, object, embed').length,
          onattr: [...m.querySelectorAll('*')].filter(e => [...e.attributes].some(a => /^on/i.test(a.name))).length,
          pwned: window.__pwned,
          titles: [...m.querySelectorAll('.ad-title')].map(h => h.textContent),
          count: m.querySelector('.ad-count')?.textContent,
          todo: [...m.querySelectorAll('.ad-todo')].map(u => [u.classList[2], u.children.length]),
          acts: [...m.querySelectorAll('.ad-actions .ad-act')].map(a => [a.classList[1], a.querySelector('.ad-mark').title]),
          doing: m.querySelectorAll('.ad-doing li').length, foot: m.querySelector('.ad-foot')?.textContent,
          bidi: getComputedStyle(m.querySelector('.ad-block')).unicodeBidi }}; }})()""")
    check(f"titre « {d['title']} », état « {d['state']} », puces {d['chips']}",
          d["title"] == "Trier les mails de la semaine" and d["state"] == "En cours" and "chercheur" in d["chips"]
          and any(c.startswith("durée ") for c in d["chips"]) and "12 requêtes" in d["chips"])
    check("pièges (balise, U+202E, \\x1b, \\x00) affichés en TEXTE dans la mission, les étapes et les échecs",
          d["trap"] and d["todoTrap"] and d["errTrap"])
    check(f"aucune balise img/script ni attribut on*, rien d'exécuté (__pwned={d['pwned']}) ; texte isolé (bidi {d['bidi']})",
          d["bad"] == 0 and d["onattr"] == 0 and d["pwned"] == 0 and d["bidi"] == "isolate")
    check(f"sections {d['titles']}",
          # 23/10 (plan P28) : « Effort de réflexion » en tête (gardé d'un rafraîchissement à l'autre)
          d["titles"] == ["Effort de réflexion", "Mission", "Étape en cours", "Étapes", "Dernières actions", "Échecs (1)",
                          "Passation"])
    check(f"étapes : « {d['count']} », en cours / reste à faire / faites {d['todo']}",
          d["count"] == "1 sur 3 faites" and d["todo"] == [["in_progress", 1], ["pending", 1], ["completed", 1]])
    check(f"dernières actions, la plus récente en haut : {d['acts']}",
          [a[0] for a in d["acts"]] == ["running", "fail", "ok"] and d["acts"][1][1] == "échouée" and d["doing"] == 1)
    check("pied : mémoire 10 min, perdue au redémarrage, rafraîchi toutes les 2 s",
          d["foot"] and "10 minutes" in d["foot"] and "2 secondes" in d["foot"])
    await p.js("window.__blk = document.querySelector('.modal.agent-detail .ad-block')")
    await asyncio.sleep(2.4)
    n1 = len(await sent("task_detail"))
    check(f"rafraîchi toutes les 2 s tant qu'il est ouvert ({n1 - n0} demandes en ≈ 2,4 s)", n1 - n0 >= 2)
    check("rafraîchissement sans changement : corps gardé (sélection et défilement ne sautent pas)",
          await p.js("window.__blk.isConnected"))
    await asyncio.sleep(0.4)  # fin du fondu d'ouverture
    await p.shot("p18-detail-en-cours-auto.png")
    await p.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
    check("mouvement réduit : barre de progression sans transition",
          await p.js("getComputedStyle(document.querySelector('.ad-progress > div')).transitionDuration === '0s'"))
    await p.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": ""}])
    await close()
    n2 = len(await sent("task_detail"))
    await asyncio.sleep(2.3)
    check("Échap ferme ; plus aucune demande ensuite", len(await sent("task_detail")) == n2)

    # ---------------------------------------------------------- Stop ne l'ouvre pas ; clavier
    await p.js(f"{row_of('Trier les mails')}.querySelector('.task-stop').click()")
    check("clic sur Stop : boîte d'arrêt, PAS le détail", await p.until("!!document.querySelector('.modal.danger')", 2)
          and not await p.js("!!document.querySelector('.modal.agent-detail')"))
    await p.js("document.querySelector('.modal.danger .modal-cancel').click()")
    await asyncio.sleep(0.2)
    await p.js(f"{row_of('Copier les photos')}.querySelector('.task-open').focus()")
    ring = await p.js(f"getComputedStyle({row_of('Copier les photos')}).outlineStyle")
    await p.key("Enter", vk=13, text="\r")
    check(f"Entrée sur une ligne : détail ouvert (contour de focus de la ligne : {ring})",
          await p.until(detail_open, 4) and ring == "solid")
    c = await p.js("""(() => { const m = document.querySelector('.modal.agent-detail');
        return { chips: [...m.querySelectorAll('.ad-chip')].map(c => c.textContent),
                 titles: [...m.querySelectorAll('.ad-title')].map(h => h.textContent) }; })()""")
    check(f"commande de fond : « commande de fond », ni étapes ni passation ({c['titles']})",
          "commande de fond" in c["chips"] and "Étapes" not in c["titles"] and "Passation" not in c["titles"])
    await close()

    # ---------------------------------------------------------- tâches finies
    check("clic sur une tâche finie en échec : détail ouvert", await open_row("Comparer les billets"))
    f = await p.js("""(() => { const m = document.querySelector('.modal.agent-detail');
        return { state: m.querySelector('.ad-state').textContent, fail: m.querySelector('.ad-state').classList.contains('fail'),
          titles: [...m.querySelectorAll('.ad-title')].map(h => h.textContent),
          acts: [...m.querySelectorAll('.ad-actions .ad-act')].map(a => [a.classList[1], a.querySelector('.ad-mark').title]),
          notes: [...m.querySelectorAll('.ad-note')].map(n => n.textContent) }; })()""")
    check(f"échec : état « {f['state']} », pas d'« étape en cours », action sans réponse « {f['acts'][0][1] if f['acts'] else ''} »",
          f["state"] == "Échec" and f["fail"] and "Étape en cours" not in f["titles"]
          and f["acts"] and f["acts"][0][0] == "none" and f["acts"][0][1].startswith("sans résultat"))
    check("passation ambiguë (deux agents du même métier en même temps) : rien de joint, c'est dit",
          any("autre agent du même métier" in n for n in f["notes"]) and "Résumé final" in f["titles"])
    await asyncio.sleep(0.4)  # fin du fondu d'ouverture
    await p.shot("p18-detail-echec-auto.png")
    check("clic sur la tâche finie avec passation", await open_row("Liste des cartons"))
    g = await p.js("""(() => { const m = document.querySelector('.modal.agent-detail');
        return { head: m.querySelector('.ad-passation .ad-sub')?.textContent,
          dt: [...m.querySelectorAll('.ad-fields dt')].map(x => x.textContent),
          state: m.querySelector('.ad-state').textContent }; })()""")
    check(f"passation : « {g['head']} », champs {g['dt']}",
          g["head"] == "Fiche 1 · p1 · vers chercheur" and g["dt"] == ["Fait", "À tester", "Risques", "Preuves"]
          and g["state"] == "Terminé")
    errors_before = await p.js("document.querySelectorAll('#toasts .toast.error').length")
    await demo("demo_expire", task_id="d5")
    check("détail oublié par le cœur (unknown_task) : « plus disponible », pas une erreur",
          await p.until("(document.querySelector('.modal.agent-detail .ad-expired')?.textContent || '').includes('10 minutes')", 4)
          and await p.js(f"document.querySelectorAll('#toasts .toast.error').length === {errors_before}"))
    n3 = len(await sent("task_detail"))
    await asyncio.sleep(2.3)
    check("fiche expirée : on ne redemande plus", len(await sent("task_detail")) == n3)
    await close()

    # ---------------------------------------------------------- Premium sombre et clair
    await open_row("Trier les mails")
    for mode in ("dark", "light"):
        ok = await theme("premium", mode)
        await asyncio.sleep(0.6)
        bg = await p.js("getComputedStyle(document.querySelector('.modal.agent-detail .ad-block')).backgroundColor")
        check(f"Premium {mode} : thème posé à chaud, détail toujours ouvert, fond de bloc {bg}",
              ok and await p.js(detail_open))
        await p.shot(f"p18-detail-premium-{mode}.png")
    await theme("premium", "auto")  # retour au défaut de la démo (P35 : plus de Classique)
    await close()
    await check_detail_agent_relecture(p, rows)
    await demo("demo_tasks_done")  # état de la démo rétabli (les lignes injectées partent)
    await p.until(f"{rows}.length === 4", 3)
    await demo("demo_tasks_nodone")


async def check_effort(p: Page) -> None:
    """23/10 (plan P28, demande de l'utilisateur) : sélecteur d'effort à côté du modèle (évènement `effort`, commande
    `settings_set brain_effort`), grisé en Haiku ; fiche d'agent : effort reçu au lancement et choix des PROCHAINS
    agents du métier (`agent_effort`), liste qui ne se referme pas au rafraîchissement ; rien pour une commande."""
    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    last = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)}).pop() || null")
    badge = """(() => { const b = document.querySelector('#effort-badge');
        return { text: b.textContent.trim(), title: b.title, prev: b.previousElementSibling?.id || '', off: b.classList.contains('off'),
                 icon: b.querySelector('svg')?.dataset.icon || '' }; })()"""
    b = await p.js(badge)
    check(f"effort : badge « {b['text']} » juste après le modèle ({b['prev']}), icône {b['icon']}, « {b['title'][:60]}… »",
          b["text"] == "Effort Élevé" and b["prev"] == "model-badge" and not b["off"] and "cliquer pour changer" in b["title"])
    await p.js("document.querySelector('#effort-badge').click()")
    await p.until("!!document.querySelector('.menu.effort-menu')", 2)
    m = await p.js("""(() => { const m = document.querySelector('.menu.effort-menu');
        return { items: [...m.querySelectorAll('.item')].map(i => i.firstChild.textContent),
                 active: m.querySelector('.item.active')?.firstChild.textContent || '',
                 desc: [...m.querySelectorAll('.item .small')].map(d => d.textContent),
                 note: m.querySelector('.effort-note')?.textContent || '' }; })()""")
    check(f"effort : menu {m['items']}, actif « {m['active']} », quota dit pour Très élevé et Maximal, note « {m['note']} »",
          m["items"] == ["Faible", "Moyen", "Élevé", "Très élevé", "Maximal"] and m["active"] == "Élevé"
          and "quota" in m["desc"][3] and "quota" in m["desc"][4] and "sans cache" in m["note"])
    await p.shot("p28-effort-menu.png")
    await p.js("[...document.querySelectorAll('.menu.effort-menu .item')].find(i => i.firstChild.textContent === 'Maximal').click()")
    ok = await p.until("document.querySelector('#effort-badge').textContent.includes('Maximal')", 3)
    sent = await last("settings_set")
    toast = await p.js("[...document.querySelectorAll('.toast')].map(t => t.textContent).find(t => t.includes('Effort de réflexion')) || ''")
    check(f"effort : clic « Maximal » → settings_set {sent and (sent.get('key'), sent.get('value'))}, badge suivi, toast « {toast[:45]} »",
          ok and sent and sent.get("key") == "brain_effort" and sent.get("value") == "max" and "Maximal" in toast
          and not await p.js("!!document.querySelector('.menu.effort-menu')"))
    await demo("model", model="haiku")
    haiku = await p.until("document.querySelector('#effort-badge').classList.contains('off')", 3)
    b = await p.js(badge)
    check(f"effort en Haiku : badge grisé, « {b['title'][:70]} »", haiku and "Haiku" in b["title"])
    await demo("model", model="sonnet")
    back = await p.until("!document.querySelector('#effort-badge').classList.contains('off')", 3)
    await demo("settings_set", key="brain_effort", value="high")
    back = back and await p.until("document.querySelector('#effort-badge').textContent.includes('Élevé')", 3)
    check("effort : retour sur Sonnet, badge rallumé, Élevé rétabli", back)

    # ---------------------------------------------------------- fiche d'agent
    rows = "[...document.querySelectorAll('#tasks .task')]"
    row_of = lambda desc: f"{rows}.find(r => r.querySelector('b').textContent.includes({json.dumps(desc)}))"
    await p.js(f"{row_of('Trier les mails')}.click()")
    await p.until("!!document.querySelector('.modal.agent-detail .ad-effort-select')", 4)
    fiche = """(() => { const m = document.querySelector('.modal.agent-detail'), box = m.querySelector('.ad-effort');
        const sel = box?.querySelector('select');
        return { first: m.querySelector('.ad-title')?.textContent || '', now: box?.querySelector('.ad-effort-now').textContent || '',
                 label: box?.querySelector('label').textContent || '', labelFor: box?.querySelector('label').htmlFor === sel?.id,
                 options: sel ? [...sel.options].map(o => o.textContent) : [], value: sel?.value ?? null,
                 state: box?.querySelector('.ad-effort-state').textContent || '', help: box?.querySelector('.ad-effort-help').textContent || '' }; })()"""
    e = await p.js(fiche)
    check(f"fiche d'agent : « {e['now']} » ; « {e['label']} » {e['options']} = « {e['value']} » ; « {e['state']} »",
          e["first"] == "Effort de réflexion" and e["now"].startswith("Cet agent : Moyen") and e["label"] == "Prochains agents « chercheur » :"
          and e["labelFor"] and e["options"] == ["Par défaut (Élevé)", "Faible", "Moyen", "Élevé", "Très élevé", "Maximal"]
          and e["value"] == "" and "Haiku" in e["state"] and "Déjà pris" in e["state"] and "en cours garde" in e["help"])
    await p.js("window.__sel = document.querySelector('.modal.agent-detail .ad-effort-select')")
    await p.js("(() => { const s = window.__sel; s.value = 'xhigh'; s.dispatchEvent(new Event('change', { bubbles: true })); })()")
    await asyncio.sleep(2.5)  # au moins un rafraîchissement de la fiche (2 s)
    sent = await last("agent_effort")
    kept = await p.js("({ connected: window.__sel.isConnected, value: window.__sel.value })")
    toast = await p.js("[...document.querySelectorAll('.toast')].map(t => t.textContent).find(t => t.includes(\"Effort de l'agent\")) || ''")
    check(f"fiche : choix « Très élevé » → agent_effort {sent and (sent.get('agent'), sent.get('effort'))}, liste gardée au "
          f"rafraîchissement ({kept}), toast « {toast} »",
          sent and sent.get("agent") == "chercheur" and sent.get("effort") == "xhigh" and kept["connected"] and kept["value"] == "xhigh"
          and toast == "Effort de l'agent chercheur : Très élevé, pour ses prochains lancements.")
    await p.shot("p28-fiche-effort.png")
    await demo("agent_effort", agent="chercheur", effort="")  # état de la démo rétabli
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal.agent-detail')", 2)
    await p.js(f"{row_of('Copier les photos')}.click()")
    await p.until("!!document.querySelector('.modal.agent-detail .ad-meta')", 4)
    none = await p.js("!document.querySelector('.modal.agent-detail .ad-effort')")
    check("fiche d'une commande de fond : pas de bloc d'effort", none)
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal.agent-detail')", 2)
    await p.js("document.querySelectorAll('.toast').forEach(t => t.remove()), true")


async def check_detail_agent_relecture(p: Page, rows: str) -> None:
    """21/10 (relecture P18, fiches 5 et 6) : évènements `tasks` injectés dans la page (comme s'ils
    venaient du cœur). (1) un agent fini fait passer sa ligne aux grisées : la ligne focalisée
    qu'on déplace garde le focus (avant : body) ; (2) description vide ou nulle : titre, nom
    accessible et titre du détail lisibles ; (3) passation aux champs tous vides : en-tête lisible."""
    now = time.time()

    async def tasks(items, done):
        ev = {"type": "tasks", "items": items, "done": done, "ts": now}
        await p.js(f"window.__ws.onmessage({{ data: {json.dumps(json.dumps(ev))} }})")
        await asyncio.sleep(0.15)

    live = lambda tid, desc: {"id": tid, "kind": "agent", "desc": desc, "background": True, "started": now - 30,
                              "elapsed_s": 30, "channel": "general"}
    await tasks([live("fa", "Agent A"), live("fb", "Agent B"), live("fc", "Agent C")], [])
    order = await p.js(f"{rows}.map(r => r.querySelector('b').textContent.trim())")
    await p.js(f"{rows}.find(r => r.textContent.includes('Agent C')).querySelector('.task-open').focus()")
    await tasks([live("fb", "Agent B"), live("fc", "Agent C")],
                [{**live("fa", "Agent A"), "ended": now, "status": "completed"}])
    f = await p.js(f"""(() => {{ const a = document.activeElement;
        return {{ order: {rows}.map(r => r.querySelector('b').textContent.trim()),
          tag: a.tagName, text: a.textContent.trim(), open: a.classList.contains('task-open') }}; }})()""")
    check(f"agent fini, lignes réordonnées {order} → {f['order']} : le focus reste sur « Agent C » ({f['tag']})",
          order == ["Agent A", "Agent B", "Agent C"] and f["order"] == ["Agent B", "Agent C", "Agent A"]
          and f["open"] and f["text"] == "Agent C")

    await tasks([live("fe", ""), {**live("fn", None), "kind": "bash"}, live("fs", "   ")], [])
    e = await p.js(f"""{rows}.map(r => ({{ title: r.querySelector('b').textContent.trim(),
        label: r.querySelector('b').getAttribute('aria-label'), tip: r.title }}))""")
    check(f"description vide, nulle ou blanche : titre et nom accessible lisibles ({e})",
          [x["title"] for x in e] == ["Agent sans description", "Tâche sans description", "Agent sans description"]
          and [x["label"] for x in e] == ["Détail de l'agent : Agent sans description",
                                          "Détail de l'agent : Tâche sans description",
                                          "Détail de l'agent : Agent sans description"]
          and e[0]["tip"] == "Agent sans description")

    # Détail de « fe » : réponse du cœur simulée dans la page (passation aux champs tous vides).
    detail = {"ok": True, "task_id": "fe", "live": False, "state": "completed", "kind": "agent", "type": "codeur",
              "desc": "", "channel": "general", "background": True, "started": now - 30, "ended": now,
              "elapsed_s": 30, "tokens": 0, "requests": 0, "tool_uses": 0, "step": "", "consigne": "",
              "todos": None, "doing": [], "actions": [], "errors": 0, "last_errors": [], "files": [],
              "summary": "", "passation": {"n": None, "mission": "", "a": ""}, "passation_ambiguous": False,
              "truncated": False}
    await p.js(f"""(() => {{ const fake = {json.dumps(detail)}; window.__origSend = WebSocket.prototype.send;
        WebSocket.prototype.send = function (d) {{
          let m = null; try {{ m = JSON.parse(d); }} catch {{}}
          if (m && m.cmd === 'task_detail' && m.task_id === 'fe') {{
            window.__sent.push(m);
            setTimeout(() => this.onmessage({{ data: JSON.stringify({{ type: 'ack', id: m.id, result: fake }}) }}), 0);
            return;
          }}
          return window.__origSend.call(this, d);
        }}; }})()""")
    await p.js(f"{rows}[0].click()")
    ok = await p.until("!!document.querySelector('.modal.agent-detail .ad-passation')", 4)
    g = await p.js("""(() => { const m = document.querySelector('.modal.agent-detail');
        return { title: m.querySelector('.modal-title').textContent.trim(),
                 head: m.querySelector('.ad-passation .ad-sub')?.textContent }; })()""")
    await p.js("WebSocket.prototype.send = window.__origSend")
    check(f"détail d'un agent sans description : titre « {g['title']} », passation vide : « {g['head']} »",
          ok and g["title"] == "Agent sans description" and g["head"] == "Fiche sans numéro, mission ni destinataire")
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal.agent-detail')", 2)


async def check_reglages(p: Page) -> None:
    """Page « Réglages » (15/10, docs/CONTRAT-REGLAGES-2026-10-15.md), sur l'état en
    mémoire de la démo (`DemoSettings`). Passe après la réouverture de la page : la carte de
    confirmation de la démo est affichée, ce qui permet de vérifier qu'Entrée ne lui répond pas."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")
    await p.js("""window.__row = (k) => [...document.querySelectorAll('.modal.wide .setting')].find(r => r.dataset.key === k);
        window.__flag = (n) => [...window.__row('connectors').querySelectorAll('input[type=checkbox]')].find(c => c.dataset.name === n);
        window.__tri = (n, s) => [...window.__row('skills').querySelectorAll('.tri-line')].find(l => l.dataset.name === n)
                                   .querySelector(`input[value="${s}"]`);
        window.__open = (g) => { const d = document.querySelector(`.modal.wide details[data-group="${g}"]`);
                                 if (!d.open) d.querySelector('summary').click(); };""")

    async def click(expr):  # vrai clic de souris (le focus suit, comme pour l'utilisateur)
        r = await p.js(f"(() => {{ const e = {expr}; e.scrollIntoView({{block: 'center'}}); const b = e.getBoundingClientRect();"
                       " return [b.left + b.width / 2, b.top + b.height / 2]; })()")
        for kind in ("mousePressed", "mouseReleased"):
            await p.send("Input.dispatchMouseEvent", type=kind, x=r[0], y=r[1], button="left", clickCount=1)

    n_get = len(await sent("settings_get"))
    await p.js("document.querySelector('#settings-chip').click()")
    opened = await p.until("document.querySelectorAll('.modal.wide .settings-group').length === 15", 4)
    o = await p.js("""(() => ({ title: document.querySelector('.modal.wide .modal-title').textContent,
        icon: document.querySelector('.modal.wide .modal-title svg.ico')?.dataset.icon,
        open: [...document.querySelectorAll('.modal.wide details')].filter(d => d.open).map(d => d.dataset.group) }))()""")
    check(f"Réglages : fenêtre large, settings_get envoyé, 15 groupes (fournisseur 20/10, apparence 21/10, système 28/10, images 28/10), « compte » seul déplié (open du cœur, 19/10 : tout le reste replié) ({o['open']})",
          opened and o["title"] == "Réglages d'Astrel" and o["icon"] == "settings" and len(await sent("settings_get")) > n_get
          and o["open"] == ["compte"])

    # ---------------------------------------------------------- interrupteur (bool, applies mcp)
    flip = await p.js("""(() => { const b = __row('browser.extra_visible').querySelector('[role=switch]');
        const before = b.getAttribute('aria-checked'); b.click();
        const now = __row('browser.extra_visible').querySelector('[role=switch]');
        return [before, now.getAttribute('aria-checked'), now.querySelector('.val').textContent]; })()""")
    await p.until("!!__row('browser.extra_visible').querySelector('.setting-pending')", 3)
    last = (await sent("settings_set"))[-1:] or [{}]
    check(f"interrupteur : {flip[0]}→{flip[1]} « {flip[2]} » aussitôt, settings_set {last[0].get('key')}={last[0].get('value')}",
          flip == ["false", "true", "OUI"] and last[0].get("key") == "browser.extra_visible" and last[0].get("value") is True
          and "id" in last[0])
    check("réglage « mcp » changé : pastille « en attente de redémarrage » et bandeau « Appliquer maintenant »",
          await p.until("__row('browser.extra_visible').querySelector('.setting-pending')?.textContent === 'en attente de redémarrage'"
                        " && !document.querySelector('.settings-banner').classList.contains('hidden')", 3))

    # ---------------------------------------------------------- connecteurs (flags) : deux clics rapides
    await p.js("__open('connecteurs')")
    labels = await p.js("[...__row('connectors').querySelectorAll('.setting-flag')].map(l => l.textContent)")
    n_set = len(await sent("settings_set"))
    await p.js("__flag('gmail').click(); __flag('google_calendar').click()")
    await p.until(f"window.__sent.filter(m => m.cmd === 'settings_set').length >= {n_set + 2}", 3)
    last = (await sent("settings_set"))[-1]
    v = last.get("value") or {}
    shown = await p.until("!__flag('gmail').checked && __flag('google_calendar').checked", 3)
    check(f"connecteurs : 7 cases ({labels[:3]}…), deux clics rapides → objet complet "
          f"(gmail={v.get('gmail')}, google_calendar={v.get('google_calendar')}, {len(v)} noms)",
          len(labels) == 7 and "Gmail" in labels and last.get("key") == "connectors" and len(v) == 7
          and v.get("gmail") is False and v.get("google_calendar") is True and shown)
    await click("__flag('canva')")
    await p.until("__flag('canva').checked", 3)
    await asyncio.sleep(0.3)
    check("clic à la souris : la case garde le focus après le nouveau rendu",
          await p.js("document.activeElement?.dataset.fkey === 'connectors|canva' && __flag('canva').checked"))

    # ---------------------------------------------------------- skills (tri) : trois états
    await p.js("__open('skills')")
    await p.js("__tri('deep-research', 'name-only').click()")
    await p.until("__tri('deep-research', 'name-only').checked", 3)
    last = (await sent("settings_set"))[-1]
    await p.js("__tri('dataviz', 'off').click()")
    await p.until("__tri('dataviz', 'off').checked", 3)
    t = await p.js("""(() => { const r = __row('skills');
        return { on: r.querySelectorAll('.tri-opt.on.s-on').length, name: r.querySelectorAll('.tri-opt.on.s-name-only').length,
                 off: r.querySelectorAll('.tri-opt.on.s-off').length, lines: r.querySelectorAll('.tri-line').length,
                 labels: [...r.querySelector('.tri-seg').querySelectorAll('label')].map(l => l.textContent) }; })()""")
    check(f"skills : 3 états « {' / '.join(t['labels'])} » ({t['on']} actifs, {t['name']} nom seul, {t['off']} coupés), "
          f"envoi complet ({len(last.get('value') or {})} noms, deep-research={(last.get('value') or {}).get('deep-research')})",
          t["labels"] == ["actif", "nom seul", "coupé"] and t["lines"] == 7 and t["on"] and t["name"] and t["off"]
          and last.get("key") == "skills" and len(last.get("value") or {}) == 7
          and last["value"].get("deep-research") == "name-only")
    await p.shot("9-reglages.png")

    # ---------------------------------------------------------- réglage imposé, grisé
    await p.js("__open('seuils')")
    lk = await p.js("""(() => { const r = __row('quota_alert_pct');
        return { locked: r.classList.contains('locked'), disabled: r.querySelector('input').disabled,
                 reason: r.querySelector('.setting-lock')?.textContent, opacity: getComputedStyle(r.querySelector('input')).opacity }; })()""")
    check(f"réglage imposé grisé, champ désactivé, raison « {lk['reason']} » (opacité {lk['opacity']})",
          lk["locked"] and lk["disabled"] and "ASTREL_QUOTA_ALERT_PCT" in (lk["reason"] or "") and float(lk["opacity"]) < 1)

    # ---------------------------------------------------------- nombres : contrôle local, puis envoi
    n_set = len(await sent("settings_set"))
    await p.js("(() => { const i = __row('agent_alert_s').querySelector('input'); i.focus(); i.value = ''; })()")
    await p.send("Input.insertText", text="5")
    await p.key("Enter", vk=13, text="\r")
    err = await p.js("__row('agent_alert_s').querySelector('.setting-error:not(.hidden)')?.textContent")
    check(f"nombre hors bornes : message sous le champ ({err}), rien d'envoyé",
          err and "entre 60 et 86400" in err and len(await sent("settings_set")) == n_set)
    await p.js("__row('agent_alert_s').querySelector('input').value = ''")
    await p.send("Input.insertText", text="900")
    await p.key("Enter", vk=13, text="\r")
    await p.until(f"window.__sent.filter(m => m.cmd === 'settings_set').length > {n_set}", 3)
    last = (await sent("settings_set"))[-1]
    await asyncio.sleep(0.3)
    st = await p.js("""(() => { const r = __row('agent_alert_s'); return { v: r.querySelector('input').value,
        err: r.querySelector('.setting-error').classList.contains('hidden'),
        focus: document.activeElement?.dataset.fkey }; })()""")
    check(f"Entrée envoie la valeur ({last}), message effacé, focus gardé ({st})",
          last.get("key") == "agent_alert_s" and last.get("value") == 900 and st["v"] == "900" and st["err"]
          and st["focus"] == "agent_alert_s" and len(await sent("settings_set")) == n_set + 1)

    # ---------------------------------------------------------- refus du cœur : notice portant setting_error
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'settings_set', key: 'context_relief_tokens', value: 10 }))")
    ok = await p.until("(__row('context_relief_tokens').querySelector('.setting-error:not(.hidden)')?.textContent || '')"
                       ".includes('entre 50000 et 900000')", 3)
    check("refus du cœur (notice setting_error) : message sous le bon champ, gardé après le nouveau rendu", ok)

    # ---------------------------------------------------------- clavier : rien ne remonte au HUD
    card = await p.js("!document.querySelector('#confirm').classList.contains('hidden')")
    n_conf = len(await sent("confirm"))
    await p.js("__flag('gmail').focus()")
    await p.key("Enter", vk=13, text="\r")
    await p.js("document.activeElement.blur()")
    await p.key("Enter", vk=13, text="\r")
    await asyncio.sleep(0.3)
    check(f"Entrée dans la fenêtre ou sur la page : la carte de confirmation ({'affichée' if card else 'absente'}) "
          "ne répond pas, la fenêtre reste",
          len(await sent("confirm")) == n_conf and await p.js("!!document.querySelector('.modal.wide')")
          and (not card or await p.js("!document.querySelector('#confirm').classList.contains('hidden')")))

    # ---------------------------------------------------------- défilement gardé d'un rendu à l'autre
    top = await p.js("(() => { const b = document.querySelector('.modal.wide .modal-body'); b.scrollTop = 150; return b.scrollTop; })()")
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'settings_get' }))")
    await asyncio.sleep(0.4)
    after = await p.js("document.querySelector('.modal.wide .modal-body').scrollTop")
    check(f"défilement gardé au nouveau rendu ({top} → {after})", top > 0 and after == top)

    # ---------------------------------------------------------- bandeau : Appliquer maintenant
    n_apply = len(await sent("settings_apply"))
    await p.js("document.querySelector('.settings-apply').click()")
    conf = await p.until("document.querySelectorAll('.modal').length === 2 && !!document.querySelector('.modal.danger')", 3)
    c = await p.js("""(() => { const m = document.querySelector('.modal.danger');
        return { title: m.querySelector('.modal-title').textContent, text: m.querySelector('.modal-text').textContent,
                 items: m.querySelectorAll('.modal-items li').length, focus: document.activeElement?.textContent }; })()""")
    check(f"« Appliquer maintenant » : confirmation rouge PAR-DESSUS les réglages, {c['items']} réglages listés, focus « {c['focus']} »",
          conf and c["title"] == "Appliquer les réglages maintenant ?" and "Le cerveau va redémarrer" in c["text"]
          and c["items"] >= 3 and c["focus"] == "Annuler")
    await p.shot("10-reglages-appliquer.png")
    await p.key("Escape", vk=27)
    check("Échap ferme la confirmation seule, rien d'appliqué",
          await p.until("!document.querySelector('.modal.danger') && !!document.querySelector('.modal.wide')", 2)
          and len(await sent("settings_apply")) == n_apply)
    await p.js("document.querySelector('.settings-apply').click()")
    await p.until("!!document.querySelector('.modal.danger')", 2)
    await p.js("document.querySelector('.modal.danger .modal-ok').click()")
    check("confirmé : settings_apply envoyé, bandeau et pastilles retirés", await p.until(
        "document.querySelector('.settings-banner').classList.contains('hidden')"
        " && !document.querySelector('.modal.wide .setting .setting-pending')", 3)
          and len(await sent("settings_apply")) == n_apply + 1)

    # ---------------------------------------------------------- fermer, rouvrir
    await p.js("__flag('gmail').focus()")
    await p.key("Escape", vk=27)
    closed = await p.until("!document.querySelector('.modal-back')", 2)
    await p.js("document.querySelector('#settings-chip').click()")
    # 04/11 (CI, machine plus lente) : les valeurs du cœur arrivent après l'ouverture ; on les attend
    await p.until("document.querySelectorAll('.modal.wide .settings-group').length === 15"
                  " && __flag('google_calendar')?.checked === true", 5)
    again = await p.js("""(() => ({ open: [...document.querySelectorAll('.modal.wide details')].filter(d => d.open).map(d => d.dataset.group),
        agenda: __flag('google_calendar').checked }))()""")
    check(f"Échap ferme les réglages ({closed}) ; rouverts : groupes dépliés gardés {again['open']}, valeurs du cœur"
          f" (agenda {again['agenda']})", closed and again["open"] == ["seuils"] and again["agenda"])
    await p.js("document.querySelector('.modal.wide .modal-x').click()")
    check("la croix ferme les réglages", await p.until("!document.querySelector('.modal-back')", 2))


async def check_reglages_actions(p: Page) -> None:
    """Lignes `info`/`action` de la page « Réglages » (17/10, plan P4, lot 1) sur la démo :
    lignes d'état en textContent, confirmation rouge, mot à saisir, bloc copiable, bouton grisé."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")
    await p.js("document.querySelector('#settings-chip').click()")
    await p.until("document.querySelectorAll('.modal.wide .settings-group').length === 15", 3)
    c = await p.js("""(() => { const r = __row('compte');
        return { lines: [...r.querySelectorAll('.info-line')].map(l => l.querySelector('dt').textContent + '=' + l.querySelector('dd').textContent),
                 ok: !!r.querySelector('.info-line.lv-ok'), html: !!r.querySelector('b'),
                 buttons: [...r.querySelectorAll('.setting-action')].map(b => b.textContent) }; })()""")
    check(f"Compte : lignes d'état ({c['lines'][:2]}), balise restée du texte, bouton {c['buttons']}",
          len(c["lines"]) == 3 and c["ok"] and not c["html"] and "<b>pas du html</b>" in c["lines"][2]
          and c["buttons"] == ["Se déconnecter"])
    # Se déconnecter : confirmation rouge, Échap = rien ; OK = settings_action {key, action}, jamais `id` écrasé
    n = len(await sent("settings_action"))
    await p.js("__row('compte').querySelector('.setting-action').click()")
    conf = await p.until("document.querySelectorAll('.modal').length === 2 && !!document.querySelector('.modal.danger')", 3)
    focus = await p.js("document.activeElement?.textContent")
    await p.key("Escape", vk=27)
    check(f"Se déconnecter : confirmation rouge par-dessus (focus « {focus} »), Échap n'envoie rien",
          conf and focus == "Annuler" and await p.until("!document.querySelector('.modal.danger')", 2)
          and len(await sent("settings_action")) == n)
    await p.js("__row('compte').querySelector('.setting-action').click()")
    await p.until("!!document.querySelector('.modal.danger')", 2)
    await p.js("document.querySelector('.modal.danger .modal-ok').click()")
    await p.until(f"window.__sent.filter(m => m.cmd === 'settings_action').length > {n}", 3)
    last = (await sent("settings_action"))[-1]
    ok = await p.until("__row('compte').querySelector('.setting-action')?.textContent === 'Se connecter'"
                       " && !!__row('compte').querySelector('a.setting-link')", 3)
    link = await p.js("(() => { const a = __row('compte').querySelector('a.setting-link'); return [a.href, a.target, a.rel]; })()")
    res = await p.js("__row('compte').querySelector('.setting-result')?.textContent")
    check(f"déconnecté : settings_action {last.get('key')}/{last.get('action')}, résultat « {res} », lien https {link[1]}",
          ok and last.get("key") == "compte" and last.get("action") == "logout" and isinstance(last.get("id"), int)
          and res == "(démo) Déconnecté." and link[0].startswith("https://claude.ai/") and link[1] == "_blank"
          and "noopener" in link[2])
    await p.js("__row('compte').querySelector('.setting-action').click()")  # Se connecter : sans confirmation
    check("Se connecter : envoyé sans confirmation, l'état revient « connecté »",
          await p.until("__row('compte').querySelector('.setting-action')?.textContent === 'Se déconnecter'", 3)
          and (await sent("settings_action"))[-1].get("action") == "login")

    # Diagnostic : bloc copiable (texte brut), bouton Copier
    await p.js("__open('diagnostic')")
    await p.js("__row('diagnostic').querySelector('.setting-action').click()")
    got = await p.until("!!__row('diagnostic').querySelector('.setting-result pre')", 3)
    d = await p.js("""(() => { const r = __row('diagnostic').querySelector('.setting-result');
        return { pre: r.querySelector('pre').textContent, script: !!r.querySelector('script'),
                 copy: r.querySelector('.setting-copy')?.textContent }; })()""")
    check(f"Diagnostic : rapport dans un bloc ({len(d['pre'])} car.), balise en texte, bouton « {d['copy']} »",
          got and "Rapport Astrel" in d["pre"] and "<script>" in d["pre"] and not d["script"] and d["copy"] == "Copier")
    await p.shot("11-reglages-actions.png")

    # Action destructrice : mot à saisir ; un mauvais mot n'envoie rien
    n = len(await sent("settings_action"))
    grey = await p.js("""(() => { const b = [...__row('effacer').querySelectorAll('.setting-action')].find(x => x.dataset.action === 'grise');
        return [b.disabled, b.title, __row('effacer').querySelector('.setting-action-why')?.textContent]; })()""")
    await p.js("[...__row('effacer').querySelectorAll('.setting-action')].find(x => x.dataset.action === 'historique').click()")
    field = await p.until("!!document.querySelector('.modal.danger input[name=word]')", 3)
    label = await p.js("document.querySelector('.modal.danger .modal-label').textContent")
    await p.js("document.querySelector('.modal.danger input[name=word]').value = 'oui'")
    await p.js("document.querySelector('.modal.danger .modal-ok').click()")
    wrong = await p.until("(__row('effacer').querySelector('.setting-result')?.textContent || '').includes('EFFACER')", 3)
    check(f"mot à saisir ({label}) : « oui » refusé par le HUD, rien d'envoyé ; bouton grisé avec sa raison {grey}",
          field and "EFFACER" in label and wrong and len(await sent("settings_action")) == n
          and grey[0] and "à la main" in (grey[2] or ""))
    await p.js("[...__row('effacer').querySelectorAll('.setting-action')].find(x => x.dataset.action === 'historique').click()")
    await p.until("!!document.querySelector('.modal.danger input[name=word]')", 3)
    await p.js("document.querySelector('.modal.danger input[name=word]').value = 'effacer'")
    await p.js("document.querySelector('.modal.danger .modal-ok').click()")
    await p.until(f"window.__sent.filter(m => m.cmd === 'settings_action').length > {n}", 3)
    last = (await sent("settings_action"))[-1]
    check(f"bon mot (casse libre) : envoyé avec confirm={last.get('confirm')}, résultat affiché",
          last.get("action") == "historique" and last.get("confirm") == "effacer"
          and await p.until("(__row('effacer').querySelector('.setting-result')?.textContent || '').includes('Historique effacé')", 3))
    await p.js("document.querySelector('.modal.wide .modal-x').click()")
    await p.until("!document.querySelector('.modal-back')", 2)
    # Premier lancement : le GUIDE s'ouvre seul (plan P5), une fois par page
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_first_run' }))")
    auto = await p.until("!!document.querySelector('.modal.wide.guide .guide-topic')", 4)
    await p.shot("13-guide-premier-lancement.png")
    await p.js("document.querySelector('.modal.wide .modal-x').click()")
    await p.until("!document.querySelector('.modal-back')", 2)
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_first_run' }))")
    await asyncio.sleep(0.5)
    check("premier lancement : guide de démarrage ouvert seul, une seule fois par page",
          auto and await p.js("!document.querySelector('.modal-back')"))


SEMANTIQUE_JS = """(() => {
  // couleur attendue = la règle d'origine, résolue dans le thème posé (var(--red) du Premium…)
  const probe = (css) => { const d = document.createElement('div'); d.style.cssText = css; document.body.append(d);
    const s = getComputedStyle(d), r = { border: s.borderTopColor, bg: s.backgroundColor }; d.remove(); return r; };
  const got = (sel, sub) => { const e = document.querySelector(sel), s = getComputedStyle(sub ? e.querySelector(sub) : e);
    return { border: s.borderTopColor, bg: s.backgroundColor }; };
  return {
    total: got('#total-chip'), total_sw: got('#total-chip', '.sw').bg, lift: got('#lift-chip'),
    estop: got('#estop-chip'),
    want_red: probe('border: 1px solid var(--red); background: rgba(var(--rgb-danger), 0.25)'),
    plain: got('#settings-chip'),
  };
})()"""


async def check_couleurs_semantiques(p: Page) -> None:
    """Relecture P17 (demande : couleurs d'alerte dans les DEUX thèmes) : pastilles Total (orange),
    garde-fou levé (rouge), Stop tout (rouge, survol rouge), en
    Premium sombre et Premium clair (25/10, plan P35 : plus de Classique). Les règles génériques Premium des pastilles les
    écrasaient par spécificité (bordure transparente, fond gris). États posés à la main, puis rendus.
    28/10 (T10, décision de l'utilisateur) : pastilles Mission et Éteindre retirées de la barre (le bandeau
    de mission violet reste, contrôlé par check_mise_en_page et check_affichage_simple)."""
    async def theme(name, mode="auto"):  # 25/10 (plan P35) : Premium seul, `name` ignoré
        return await premium_mode(p, mode)

    async def hover(sel):
        box = await p.js(f"(() => {{ const r = document.querySelector({json.dumps(sel)}).getBoundingClientRect(); "
                         "return [r.left + r.width / 2, r.top + r.height / 2]; })()")
        await p.send("Input.dispatchMouseEvent", type="mouseMoved", x=box[0], y=box[1])
        await asyncio.sleep(0.4)
        # état FINAL des transitions (headless n'avance pas toujours les images)
        return await p.js("document.getAnimations().forEach(a => { if (a instanceof CSSTransition) a.finish(); }), "
                          f"getComputedStyle(document.querySelector({json.dumps(sel)})).borderTopColor")

    await p.js("""(() => { window.__sem = ['#total-chip', '#lift-chip', '#estop-chip']
        .map(s => { const e = document.querySelector(s); return [e, e.className, e.hidden]; }); return true; })()""")
    transparent = "rgba(0, 0, 0, 0)"
    try:
        for name, mode in (("premium", "dark"), ("premium", "light")):  # P35 : plus de Classique
            posed = await theme(name, mode)
            await p.js("""(() => { document.querySelector('#total-chip').classList.add('on');
                document.querySelector('#lift-chip').hidden = false;
                document.querySelector('#estop-chip').classList.add('on'); return true; })()""")
            await asyncio.sleep(0.5)  # transitions Premium (150 ms) finies
            await p.js("document.getAnimations().forEach(a => { if (a instanceof CSSTransition) a.finish(); }), true")
            c = await p.js(SEMANTIQUE_JS)
            tag = f"Premium {mode}"
            check(f"{tag} : Total orange ({c['total']['border']}, fond {c['total']['bg']}, interrupteur {c['total_sw']})",
                  posed and c["total"]["border"] == "rgb(255, 140, 0)" and c["total"]["bg"] == "rgba(255, 140, 0, 0.28)"
                  and c["total_sw"] == "rgba(255, 140, 0, 0.6)")
            check(f"{tag} : garde-fou levé rouge ({c['lift']['border']} = {c['want_red']['border']})",
                  c["lift"] == c["want_red"] and c["lift"]["border"] != transparent)
            check(f"{tag} : Stop tout rouge ({c['estop']['border']}, fond {c['estop']['bg']})",
                  c["estop"] == c["want_red"])
            await p.shot(f"p17-semantique-{name}-{mode}.png")
            await p.js("document.querySelector('#estop-chip').classList.remove('on'), true")
            await asyncio.sleep(0.3)
            h_estop, h_lift = await hover("#estop-chip"), await hover("#lift-chip")
            await p.send("Input.dispatchMouseEvent", type="mouseMoved", x=800, y=990)
            check(f"{tag} : survol de Stop tout et du garde-fou levé rouge ({h_estop}, {h_lift})",
                  h_estop == h_lift == c["want_red"]["border"])
            # une pastille ordinaire garde le style Premium (sans bordure)
            check(f"{tag} : pastille ordinaire inchangée ({c['plain']['border']})", c["plain"]["border"] == transparent)
    finally:
        await p.js("""(() => { for (const [e, cls, hidden] of window.__sem || []) { e.className = cls; e.hidden = hidden; }
            return true; })()""")
        await theme("premium", "auto")  # retour au défaut de la démo (P35 : plus de Classique)


SIMPLE_JS = """(() => {
  const q = (s) => document.querySelector(s);
  const shown = (s) => { const e = q(s); return !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden'; };
  const box = (s) => { const e = q(s); if (!e || !e.getClientRects().length) return null;
    const r = e.getBoundingClientRect(); return { t: r.top, b: r.bottom, l: r.left, r: r.right }; };
  // 28/10 (T10, décision de l'utilisateur) : #reports-chip, #mission-chip, #affichage-chip et #power-chip
  // retirés du DOM ; la mission en cours reste montrée par son bandeau
  const hide = ['#autonomy-chip', '#plans-chip', '#audit-chip', '#channels',
                '#right .badges', '#sensors', '#quota', '#context', '#memory', '#total-frame', '#lift-frame'];
  const keep = ['#conn', '.brand-name', '#total-chip', '#lift-chip', '#settings-chip',
                '#estop-chip', '#total-banner', '#lift-banner', '#memory-banner', '#mission-banner',
                '#transcript', '#ask', '#orb-wrap'];
  const seg = (s) => q(s + ' b')?.innerText.trim() || '';
  const st = (e) => { if (!e) return null; const c = getComputedStyle(e); return { border: c.borderTopColor, color: c.color, anim: c.animationName }; };
  const probe = (css) => { const d = document.createElement('div'); d.style.cssText = css; document.body.append(d);
    const c = getComputedStyle(d), r = { border: c.borderTopColor, color: c.color }; d.remove(); return r; };
  return { attr: document.documentElement.dataset.affichage || '', hidden: hide.filter(shown), missing: keep.filter(s => !shown(s)),
           brand: q('.brand').innerText.trim(), brandTT: getComputedStyle(q('.brand-name')).textTransform,
           brandSub: !!q('.brand-sub'), title: document.title,
           segs: ['#total-banner', '#lift-banner', '#memory-banner'].map(b => [getComputedStyle(q(b + ' .seg-full')).display,
             getComputedStyle(q(b + ' .seg-simple')).display]), titles: [...document.querySelectorAll('#right > .panel-title')]
             .filter(e => e.getClientRects().length).map(e => e.dataset.i18n || e.id),
           total: seg('#total-banner'), lift: seg('#lift-banner'), mem: seg('#memory-banner'),
           totalSt: st(q('#total-banner')), totalIco: st(q('#total-banner .ico.lead'))?.color,
           liftSt: st(q('#lift-banner')), liftIco: st(q('#lift-banner .ico.lead'))?.color, memSt: st(q('#memory-banner')),
           totalChip: st(q('#total-chip')), liftChip: st(q('#lift-chip')),
           want: probe('border: 1px solid rgba(var(--rgb-danger), 0.55); color: var(--red)'), ink: probe('color: var(--ink)').color,
           // P26 étape 0 : les pastilles passent à la ligne ; chevauchement = intersection des deux boîtes
           segOver: (() => { const r = [...document.querySelectorAll('.alert-seg')].filter(e => e.getClientRects().length)
             .map(e => e.getBoundingClientRect()); let n = 0;
             for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++)
               if (Math.min(r[i].right, r[j].right) - Math.max(r[i].left, r[j].left) > 0.5
                   && Math.min(r[i].bottom, r[j].bottom) - Math.max(r[i].top, r[j].top) > 0.5) n++;
             return n; })(),
           // boutons des pastilles (Couper, Rétablir, Voir) entiers dans leur pastille et cliquables
           segBtns: ['#total-off', '#lift-off', '#memory-show'].filter(s => q(s).getClientRects().length).filter(s => {
             const b = q(s).getBoundingClientRect(), seg = q(s).closest('.alert-seg').getBoundingClientRect();
             const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
             return !(b.left >= seg.left - 0.5 && b.right <= seg.right + 0.5 && b.right <= innerWidth && hit && q(s).contains(hit)); }),
           // la dernière rangée de pastilles reste au-dessus du fil (place réservée par --alert-h)
           segBelow: (() => { const segs = [...document.querySelectorAll('.alert-seg')].filter(e => e.getClientRects().length);
             if (!segs.length) return 0; const bot = Math.max(...segs.map(e => e.getBoundingClientRect().bottom));
             return Math.max(0, Math.round(bot - q('#dialog').getBoundingClientRect().top)); })(),
           segClip: [...document.querySelectorAll('.alert-seg')].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map(e => e.id),
           chan: q('.channel-tab.on')?.dataset.chan || '', dialog: box('#dialog'), right: box('#right'), w: innerWidth,
           gone: ['#reports-chip', '#mission-chip', '#affichage-chip', '#power-chip'].filter(q),
           stored: localStorage.getItem('astrel-affichage'),
           // lot B : orbe réduite ; 23/10 (plan P28) : plus de graphe ni de « Voir le cerveau » ;
           // 25/10 (plan P35) : seule orbe, #orb-premium
           orb: q('#orb-premium').getClientRects().length ? q('#orb-premium').clientWidth : 0,
           graphGone: ['#graph', '#graph-tools', '#brain-btn', '#stats', '[data-action]'].filter(q) };
})()"""


async def check_affichage_simple(p: Page) -> None:
    """22/10 (plan P23, lot A ; l'utilisateur : montrer Astrel à quelqu'un qui le découvre, « le graphe,
    les forces, les capteurs, les bandeaux rouge/orange et Astrel OS font trop geek ») : réglage
    `affichage` (Réglages › Apparence ; l'interrupteur « Simple » de la barre est retiré le 28/10, T10,
    décision de l'utilisateur) ; en Simple, panneau gauche, compteurs, pastilles de
    modes, capteurs, abonnement, filtres, badges, onglets cachés, le fil se centre sur le canal par
    défaut. SÉCURITÉ : Total, garde-fou levé, mémoire et mission restent affichés (pastilles sobres,
    Total orange, levée rouge), seuls les cadres colorés partent. Persistance après rechargement,
    retour à l'Avancé. Recharge la page."""
    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    async def par_reglages(value: str) -> bool:
        """Choisit l'affichage dans Réglages › Apparence (liste « Affichage »), referme les Réglages."""
        await p.js("document.querySelector('#settings-chip').click()")
        found = await p.until("!!document.querySelector('.modal.wide select[data-fkey=\"affichage\"]')", 4)
        await p.js(f"""(() => {{ const s = document.querySelector('.modal.wide select[data-fkey="affichage"]');
            s.closest('details').open = true; s.value = {json.dumps(value)};
            s.dispatchEvent(new Event('change', {{ bubbles: true }})); return true; }})()""")
        want = "=== 'simple'" if value == "simple" else "=== undefined"
        done = await p.until(f"document.documentElement.dataset.affichage {want}", 4)
        await p.js("document.querySelector('.modal.wide .modal-x')?.click()")
        await p.until("!document.querySelector('.modal-back')", 2)
        return found and done

    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await p.js("document.querySelectorAll('.modal-back').forEach(b => b.remove())")
    a = await p.js(SIMPLE_JS)
    check(f"P23 Avancé au départ : aucun attribut, ni « Lire aussitôt », « Mission », « Simple », « Éteindre » "
          f"dans la barre (restent {a['gone']}), absents ({a['missing']} : levée et mémoire inactives à ce moment)",
          a["attr"] == "" and a["gone"] == [])
    # sécurité active : Total, levée, mémoire pleine, mission ; un canal de projet ouvert
    await demo("total", mode="on")
    await demo("settings_action", key="garde_fou_levee", action="lever", confirm="lever")
    await demo("demo_memory", state="blocage", active=True)
    if MISSION_START:
        await p.js(f"window.__ws.send({json.dumps(json.dumps(MISSION_START))})")
    await p.until("['#total-banner', '#lift-banner', '#memory-banner'].every(s => !document.querySelector(s).hidden)", 4)
    await p.js("""(() => { const t = [...document.querySelectorAll('.channel-tab:not(.add)')].find(t => t.dataset.chan !== 'general');
        if (t) t.click(); return true; })()""")
    await asyncio.sleep(0.3)
    before_chan = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || ''")
    adv_dialog = (await p.js(SIMPLE_JS))["dialog"]
    n = len(await sent("settings_set"))
    on = await par_reglages("simple")
    await asyncio.sleep(0.5)
    await p.js("document.getAnimations().forEach(a => { if (a instanceof CSSTransition) a.finish(); }), true")
    last = (await sent("settings_set"))[-1:] or [{}]
    check(f"P23 Réglages › Apparence › Affichage = Simple : settings_set affichage={last[0].get('value')}, attribut posé",
          on and len(await sent("settings_set")) == n + 1 and last[0].get("key") == "affichage"
          and last[0].get("value") == "simple")
    s = await p.js(SIMPLE_JS)
    check(f"P23 Simple : gardé en local ({s['stored']}), boutons retirés absents ({s['gone']})",
          s["stored"] == "simple" and s["gone"] == [])
    check(f"P23 Simple : panneau gauche, compteurs, « OS », pastilles de modes, Plans, Journal, onglets, badges, capteurs, "
          f"abonnement, filtres, cadres cachés (encore visibles : {s['hidden']})", s["hidden"] == [])
    # 23/10 (plan P31) : nom affiché « Astrel », écrit tel quel en Simple (text-transform none), plus de « OS »
    check(f"P23 Simple : marque « {s['brand']} » ({s['brandTT']}), titre « {s['title']} », « OS » absent du DOM, "
          f"intertitres du panneau droit {s['titles']}",
          s["brand"] == "Astrel" and s["brandTT"] == "none" and not s["brandSub"] and s["title"] == "Astrel"
          and not set(s["titles"]) & {"right.sensors", "right.subscription", "quota-title"})
    check(f"P23 Simple : sécurité et commandes visibles (manquent : {s['missing']}) : Sans confirmation, garde-fou levé, "
          f"Stop tout, Réglages, bandeaux (dont la mission)", s["missing"] == [])
    check(f"P23 Simple : bandeaux sobres « {s['total']} » / « {s['lift']} » / « {s['mem']} » (texte {s['totalSt']['color']})",
          s["total"] == "Sans confirmation" and s["lift"] == "Garde-fou levé" and s["mem"] == "Mémoire presque pleine"
          and s["totalSt"]["color"] == s["liftSt"]["color"] == s["ink"])
    check(f"P23 Simple : Total ORANGE (filet {s['totalSt']['border']}, icône {s['totalIco']}, pastille {s['totalChip']['border']}), "
          f"levée ROUGE (filet {s['liftSt']['border']}, icône {s['liftIco']}, pastille {s['liftChip']['border']})",
          s["totalSt"]["border"] == "rgba(255, 140, 0, 0.55)" and s["totalIco"] == "rgb(255, 140, 0)"
          and s["totalChip"]["border"] == "rgb(255, 140, 0)" and s["liftSt"]["border"] == s["want"]["border"]
          and s["liftIco"] == s["want"]["color"] and s["liftChip"]["border"] == s["want"]["color"]
          and s["memSt"]["border"] == s["want"]["border"])
    check(f"P23 Simple : pastilles Total et levée sans pulsation ({s['totalChip']['anim']}, {s['liftChip']['anim']})",
          s["totalChip"]["anim"] == "none" and s["liftChip"]["anim"] == "none")
    d, w = s["dialog"], s["w"]
    check(f"P23 Simple : fil ramené sur Général (était {before_chan}, maintenant {s['chan']}), centré à {w} px "
          f"(marges {d['l']:.0f} / {w - d['r']:.0f} ; en Avancé {adv_dialog['l']:.0f} / {w - adv_dialog['r']:.0f})",
          s["chan"] == "general" and before_chan != "general" and abs(d["l"] - (w - d["r"])) <= 2)
    await p.js("""window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Digit2', key: 'é', altKey: true, bubbles: true })), true""")
    await asyncio.sleep(0.2)
    check("P23 Simple : Alt+2 ne change pas de canal",
          await p.js("document.querySelector('.channel-tab.on')?.dataset.chan") == "general")
    # 04/11 : recherche dans tous les canaux : ni bouton loupe ni Ctrl+K en Simple (noms de projets cachés)
    loupe = await p.js("document.querySelector('#search-btn').getClientRects().length")
    for kind in ("keyDown", "keyUp"):
        await p.send("Input.dispatchKeyEvent", type=kind, key="k", code="KeyK", windowsVirtualKeyCode=75, modifiers=2)
    await asyncio.sleep(0.3)
    check(f"P23 Simple : ni bouton loupe ({loupe}) ni Ctrl+K pour la recherche",
          loupe == 0 and await p.js("!document.querySelector('.modal.search-panel')"))
    check("P23 Simple : bouton « Envoyer » visible au bout de la barre",
          await p.js("document.querySelector('#send-btn').getClientRects().length > 0"))
    await p.js("document.querySelectorAll('.toast').forEach(t => t.remove()), true")
    for w2, h2 in ((1280, 800), (1600, 1000)):
        await p.send("Emulation.setDeviceMetricsOverride", width=w2, height=h2, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(0.6)
        m = await p.js(MESURE_JS)
        sg = await p.js(SIMPLE_JS)
        # relecture P23 (fiche 3) : une pastille ROGNÉE (« Rétablir » coupé de 8 px à 1280) échoue aussi
        check(f"P23 Simple {w2}×{h2} : barre sur {m['rows']} ligne(s), boutons ni chevauchés ni hors de la barre "
              f"({m['overlap']} / {m['outside']}), panneau droit sans débordement ({m['wide']}), trois pastilles "
              f"d'alerte côte à côte ({sg['segOver']} chevauchement, rognées {sg['segClip']})",
              m["overlap"] == 0 and m["outside"] == 0 and not m["wide"] and sg["segOver"] == 0 and not sg["segClip"]
              and not sg["segBtns"] and sg["segBelow"] == 0)
        await p.shot(f"p23-simple-securite-{w2}.png")
    # 700 px (fiches 2 et 5) : les pastilles se chevauchaient, « Sans confi » coupé, Couper / Rétablir
    # masqués (déjà en Avancé avant P23). P26 étape 0 : elles passent à la ligne, dans les DEUX affichages ;
    # aucune ne chevauche, aucune n'est rognée, chaque bouton est entier et cliquable, le fil reste dessous.
    await p.send("Emulation.setDeviceMetricsOverride", width=700, height=900, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(0.6)
    m7, s7 = await p.js(MESURE_JS), await p.js(SIMPLE_JS)
    await p.shot("p23-simple-securite-700.png")
    await p.js("delete document.documentElement.dataset.affichage, true")  # Avancé le temps d'une mesure, sans le cœur
    await asyncio.sleep(0.6)
    a7 = await p.js(SIMPLE_JS)
    await p.shot("p26-avance-securite-700.png")
    await p.js("document.documentElement.dataset.affichage = 'simple', true")
    await asyncio.sleep(0.6)
    for nom, x in (("Simple", s7), ("Avancé", a7)):
        check(f"P26 étape 0, {nom} 700×900, trois alertes : pastilles à la ligne sans chevauchement ({x['segOver']}), "
              f"rognées {x['segClip']}, boutons masqués {x['segBtns']}, sous le fil de {x['segBelow']} px, "
              f"sécurité visible (manquent {x['missing']})",
              x["segOver"] == 0 and not x["segClip"] and not x["segBtns"] and x["segBelow"] == 0
              and (nom == "Avancé" or x["missing"] == []))
    check(f"P23 Simple 700×900 : barre propre ({m7['overlap']} chevauchement, {m7['outside']} hors barre)",
          m7["overlap"] == 0 and m7["outside"] == 0)
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(0.4)
    await check_simple_lot_b(p)
    # thèmes : la sémantique tient en Premium sombre et clair
    for theme, mode in (("premium", "dark"), ("premium", "light"), ("premium", "auto")):  # P35 : Premium seul
        await premium_mode(p, mode)
        await asyncio.sleep(0.4)
        await p.js("document.getAnimations().forEach(a => { if (a instanceof CSSTransition) a.finish(); }), true")
        t = await p.js(SIMPLE_JS)
        check(f"P23 Simple {theme}/{mode} : Total orange ({t['totalSt']['border']}), levée rouge ({t['liftSt']['border']}), "
              f"rien de caché en trop ({t['missing']}), rien de geek ({t['hidden']}), orbe {t['orb']} px, "
              f"plus de graphe ({t['graphGone']})",
              t["attr"] == "simple" and t["totalSt"]["border"] == "rgba(255, 140, 0, 0.55)" and t["totalIco"] == "rgb(255, 140, 0)"
              and t["liftSt"]["border"] == t["want"]["border"] and t["liftIco"] == t["want"]["color"]
              and t["missing"] == [] and t["hidden"] == [] and t["orb"] == 110 and t["graphGone"] == [])
        await p.shot(f"p23-simple-{theme}-{mode}.png")
    # persistance : rechargement, attribut posé avant la première peinture (theme-boot.js)
    await p.send("Page.addScriptToEvaluateOnNewDocument", source="""
        new MutationObserver((ms, o) => { if (document.body) { window.__affAtBody = document.documentElement.dataset.affichage || '';
          o.disconnect(); } }).observe(document, { childList: true, subtree: true });""")
    await p.js("location.reload(), true")
    await asyncio.sleep(0.5)
    loaded = await p.until("!!document.querySelector('#transcript') && !!document.querySelector('.channel-tab')", 15)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await asyncio.sleep(0.8)
    r = await p.js(SIMPLE_JS)
    early = await p.js("window.__affAtBody")
    check(f"P23 rechargement : Simple dès le <body> ({early!r}), canal {r['chan']}, rien de geek ({r['hidden']})",
          loaded and early == "simple" and r["attr"] == "simple" and r["chan"] == "general" and r["hidden"] == [])
    # retour à l'Avancé (Réglages › Apparence) : tout revient, rien n'a été retiré du DOM
    off = await par_reglages("avance")
    await asyncio.sleep(0.4)
    b = await p.js(SIMPLE_JS)
    back = await p.js("""['#autonomy-chip', '#plans-chip', '#audit-chip', '#channels',
        '#right .badges', '#sensors', '#total-frame'].filter(s => !document.querySelector(s)?.getClientRects().length)""")
    check(f"P23 retour à l'Avancé : attribut retiré, tout revient (manquent : {back}), libellés d'origine des bandeaux "
          f"{b['segs']}, marque « {' '.join(b['brand'].split())} », local {b['stored']}, "
          f"orbe {b['orb']} px, plus de graphe ni de « Voir le cerveau » ({b['graphGone']})",
          off and back == [] and all(full != "none" and simple == "none" for full, simple in b["segs"])
          and " ".join(b["brand"].split()) == "ASTREL" and b["stored"] == "avance"
          and b["orb"] == 260 and b["graphGone"] == []
          and not await p.js("'cerveau' in document.documentElement.dataset"))
    await check_avance_lot_b(p)
    # Réglages › Apparence : la ligne « Affichage » existe (icône œil, Simple / Avancé)
    await p.js("document.querySelector('#settings-chip').click()")
    await p.until("!!document.querySelector('.modal.wide .settings-group')", 4)
    row = await p.js("""(() => { const d = document.querySelector('.modal.wide details[data-group="apparence"]'); if (d) d.open = true;
        const r = [...document.querySelectorAll('.modal.wide .setting')].find(r => r.dataset.key === 'affichage');
        return r ? { label: r.querySelector('.setting-label')?.textContent, icon: r.querySelector('svg.setting-ico')?.dataset.icon,
                     text: r.textContent } : null; })()""")
    check(f"P23 Réglages › Apparence : ligne « {row and row['label']} », icône {row and row['icon']}",
          row and row["label"] == "Affichage" and row["icon"] == "eye" and "Simple" in row["text"] and "Avancé" in row["text"])
    # 25/10 (plan P35) : plus de cartes de thème. Un cœur d'avant P35 envoie encore `theme` (cartes,
    # « classique ») : la ligne n'est pas affichée et le HUD reste en Premium.
    apparence = {"type": "settings", "groups": [{"id": "apparence", "title": "Apparence", "open": True, "items": [
        {"key": "theme", "label": "Thème", "kind": "choice", "value": "classique", "display": "cards", "applies": "hot",
         "choices": [{"value": "classique", "label": "Classique"}, {"value": "premium", "label": "Premium"}]},
        {"key": "theme_mode", "label": "Clair ou sombre", "kind": "choice", "value": "dark", "applies": "hot",
         "choices": [{"value": "auto", "label": "Automatique"}, {"value": "light", "label": "Clair"},
                     {"value": "dark", "label": "Sombre"}]},
        {"key": "theme_accent", "label": "Couleur d'accent", "kind": "choice", "value": "vert", "display": "swatches",
         "applies": "hot", "choices": [{"value": v, "label": v} for v in ("bleu", "graphite", "vert", "orange")]}]}]}
    # 04/11 (CI de main) : une republication des réglages de la démo pouvait écraser l'évènement injecté
    # après 0,3 s fixes ; on attend les lignes injectées, et on réinjecte une fois si elles ont été remplacées.
    injecte = ("[...document.querySelectorAll('.modal.wide details[data-group=\"apparence\"] .setting')]"
               ".map(r => r.dataset.key).join() === 'theme_mode,theme_accent'")
    for _ in range(2):
        await fake_event(p, apparence)
        if await p.until(injecte, 3):
            break
    ap = await p.js("""(() => { const m = document.querySelector('.modal.wide');
        const keys = [...m.querySelectorAll('details[data-group="apparence"] .setting')].map(r => r.dataset.key);
        const d = document.documentElement.dataset;
        return { keys, cards: m.querySelectorAll('.theme-card, .theme-cards, .theme-preview').length,
                 swatches: m.querySelectorAll('.accent-swatch').length, html: [d.theme, d.mode, d.accent] }; })()""")
    await p.shot("p35-reglages-apparence.png")
    check(f"P35 Réglages › Apparence sans cartes de thème : lignes {ap['keys']}, {ap['cards']} carte, "
          f"{ap['swatches']} pastilles d'accent, <html> {ap['html']} malgré « classique » d'un ancien cœur",
          ap["keys"] == ["theme_mode", "theme_accent"] and ap["cards"] == 0 and ap["swatches"] == 4
          and ap["html"] == ["premium", "dark", "vert"])
    await demo("settings_get")  # réglages de la démo republiés (l'évènement injecté est remplacé)
    await asyncio.sleep(0.3)
    await premium_mode(p, "auto")
    await p.js("document.querySelector('.modal.wide .modal-x')?.click(), true")
    await p.until("!document.querySelector('.modal-back')", 2)
    # état rendu : levée rétablie, mémoire normale (Total et mission comme avant)
    await demo("settings_action", key="garde_fou_levee", action="retablir")
    await demo("demo_memory", state="ok")
    await asyncio.sleep(0.3)


async def check_hors_ligne(p: Page) -> None:
    """04/11 (lot 2 du moteur local) : mode « rien ne sort » : pastille « Hors ligne » en haut quand le cœur
    l'annonce (évènement `model`, `offline`), visible en Avancé ET en Simple (la sécurité n'est jamais cachée),
    cachée au retour en ligne ; un clic ouvre le réglage."""
    vu = """(() => { const c = document.querySelector('#offline-chip');
        return { hidden: c.hidden, shown: c.getClientRects().length > 0, text: c.innerText.trim(),
                 badge: document.querySelector('#model-badge').innerText.trim() }; })()"""
    avant = await p.js(vu)
    await fake_event(p, {"type": "model", "active": "sonnet", "label": "Hors ligne · qwen3.5:9b", "id": "qwen3.5:9b",
                         "offline": True})
    avance = await p.js(vu)
    simple = await p.js("""(() => { const h = document.documentElement, was = h.dataset.affichage;
        h.dataset.affichage = 'simple'; const c = document.querySelector('#offline-chip');
        const r = c.getClientRects().length > 0; if (was === undefined) delete h.dataset.affichage; else h.dataset.affichage = was;
        return r; })()""")
    check(f"hors ligne : pastille « {avance['text']} » affichée (Avancé, Simple : {simple}), badge « {avance['badge']} »",
          avant["hidden"] and not avance["hidden"] and avance["shown"] and simple
          and avance["text"] == "Hors ligne" and avance["badge"].startswith("Hors ligne"))
    await p.js("document.querySelector('#offline-chip').click(), true")
    ouvert = await p.until("!!document.querySelector('.modal.wide .setting[data-key=\"hors_ligne\"]')", 3)
    await p.js("document.querySelector('.modal.wide .modal-x')?.click(), true")  # fermée comme l'utilisateur (état du module)
    await p.until("!document.querySelector('.modal-back')", 2)
    await fake_event(p, {"type": "model", "active": "sonnet", "label": "Sonnet", "id": ""})
    retour = await p.js(vu)
    check(f"hors ligne : un clic ouvre le réglage ({ouvert}), pastille cachée au retour en ligne", ouvert and retour["hidden"])
    # 04/11 : activé pendant une tâche de Claude → refus, et le VRAI « Tout arrêter » proposé (commande estop)
    # (réglage rouvert par la pastille, comme l'utilisateur : la page s'ouvre sur la ligne « Rien ne sort »)
    await fake_event(p, {"type": "model", "active": "sonnet", "label": "Hors ligne · qwen3.5:9b", "id": "qwen3.5:9b",
                         "offline": True})
    await p.js("document.querySelector('#offline-chip').click(), true")
    await p.until("!!document.querySelector('.modal.wide [data-fkey=\"hors_ligne\"]')", 3)
    n_estop = await p.js("window.__sent.filter(m => m.cmd === 'estop').length")
    await p.js("(() => { const i = document.querySelector('.modal.wide [data-fkey=\"hors_ligne\"]');"
               " i.scrollIntoView({block: 'center'}); i.click(); return true; })()")
    offre = await p.until("[...document.querySelectorAll('.modal.danger')].some(m => "
                          "m.querySelector('.modal-ok')?.textContent.trim() === 'Tout arrêter')", 3)
    titre = await p.js("document.querySelector('.modal.danger h2, .modal.danger .modal-title')?.textContent.trim() || ''")
    await p.js("document.querySelector('.modal.danger .modal-ok').click(), true")
    envoye = await p.until(f"window.__sent.filter(m => m.cmd === 'estop' && m.mode === 'on').length > {n_estop}", 3)
    check(f"hors ligne refusé pendant une tâche : « {titre} », bouton « Tout arrêter » ({offre}) → estop envoyé ({envoye})",
          offre and envoye and titre == "Une tâche de Claude tourne")
    await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'estop', 'mode': 'off'}))})")  # démo remise en route
    await asyncio.sleep(0.3)
    await p.js("document.querySelectorAll('.modal-back .modal-x, .modal-back .modal-cancel').forEach(b => b.click()), true")  # vraie fermeture : retirer la fenêtre laisserait son écouteur clavier (Entrée, Échap bloqués)
    await fake_event(p, {"type": "model", "active": "sonnet", "label": "Sonnet", "id": ""})


async def fake_event(p: Page, ev: dict) -> None:
    """Évènement injecté dans la page comme s'il venait du cœur."""
    await p.js(f"window.__ws.onmessage({{ data: {json.dumps(json.dumps(ev))} }}), true")
    await asyncio.sleep(0.15)


def lot_b_tasks(now: float) -> dict:
    """« En cours » du lot B : un agent vivant (étape technique, coût), un fini il y a 30 s (reste dans
    En cours), un fini il y a 400 s (Historique), une COMMANDE finie il y a 20 s (Historique aussitôt :
    seuls les agents finis restent 2 min)."""
    base = {"kind": "agent", "background": True, "channel": "general"}
    return {"type": "tasks", "ts": now,
            "items": [{**base, "id": "b1", "desc": "Agent du lot B", "started": now - 50, "elapsed_s": 50,
                       "step": "Read meeting-notes.md", "tool_uses": 4, "requests": 3, "tokens": 5000}],
            "done": [{**base, "id": "b2", "desc": "Fini récent", "started": now - 90, "ended": now - 30,
                      "elapsed_s": 60, "status": "completed", "tool_uses": 7, "requests": 2, "tokens": 900},
                     {**base, "id": "b3", "desc": "Fini ancien", "started": now - 500, "ended": now - 400,
                      "elapsed_s": 100, "status": "failed", "tool_uses": 2, "requests": 1, "tokens": 300},
                     {**base, "kind": "local_bash", "id": "b4", "desc": "Commande finie", "started": now - 25,
                      "ended": now - 20, "elapsed_s": 5, "status": "completed"}]}


TASKS_JS = """(() => { const box = document.querySelector('#tasks'), h = box.querySelector('.tasks-history');
  // étape SANS l'étiquette du canal (« Général », span .chan, cachée en Simple) : relevée à part, visible ou non
  const step = (r) => [...r.querySelector('.step').childNodes].filter(n => !n.classList?.contains('chan')).map(n => n.textContent).join('');
  const chan = (r) => { const c = r.querySelector('.step .chan'); return c && c.checkVisibility() ? c.textContent : ''; };
  const row = (r) => ({ title: r.querySelector('b').textContent.trim(), step: step(r), chan: chan(r),
    cost: r.querySelector('.cost').getClientRects().length ? r.querySelector('.cost').textContent : '',
    shown: r.checkVisibility() });  // checkVisibility : faux dans un <details> fermé (content-visibility)
  return { current: [...box.children].filter(e => e.classList.contains('task')).map(row),
           history: h ? [...h.querySelectorAll('.task')].map(row) : [], open: !!h?.open, head: h?.querySelector('summary').textContent || '',
           headShown: !!h && h.getClientRects().length > 0, last: box.lastElementChild === h,
           count: document.querySelector('#tasks-count').textContent }; })()"""


async def check_simple_lot_b(p: Page) -> None:
    """22/10 (plan P23, lot B ; décisions de l'utilisateur, questions 4, 5, 6 « Recommandé ») en affichage
    Simple : orbe réduite « Je travaille » / « Prêt » sans nom d'outil ; plus de graphe (retiré le 23/10,
    plan P28) ; « En cours » en phrases (ni étape technique,
    ni actions, ni requêtes, ni tokens), finies depuis plus de 2 min dans « Historique » replié ;
    relecture lot A : question à choix et compte rendu d'un canal de projet VISIBLES dans le fil (le
    reste du projet caché), toast cliquable."""
    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    # ---------------------------------------------------------- orbe (proposition 4)
    await fake_event(p, {"type": "state", "state": "working", "detail": "Bash · npm test", "muted": False})
    o = await p.js("({ label: document.querySelector('#state-label').textContent, detail: document.querySelector('#state-detail').textContent })")
    await fake_event(p, {"type": "state", "state": "sleeping", "detail": "", "muted": False})
    ready = await p.js("document.querySelector('#state-label').textContent")
    await fake_event(p, {"type": "state", "state": "thinking", "detail": "", "muted": False})
    think = await p.js("document.querySelector('#state-label').textContent")
    check(f"P23 lot B Simple : orbe « {o['label']} » (détail « {o['detail']} », sans nom d'outil), réflexion « {think} », "
          f"veille « {ready} »", o["label"] == "Je travaille" and o["detail"] == "" and think == "Je travaille" and ready == "Prêt")
    await fake_event(p, {"type": "state", "state": "sleeping", "detail": "", "muted": False})

    # ---------------------------------------------------------- graphe (proposition 6), retiré le 23/10 (plan P28)
    s = await p.js(SIMPLE_JS)
    check(f"P28 Simple : ni graphe, ni « Voir le cerveau », ni outils du graphe ({s['graphGone']})", s["graphGone"] == [])

    # ---------------------------------------------------------- « En cours » (proposition 5, point 15)
    await fake_event(p, lot_b_tasks(time.time()))
    k = await p.js(TASKS_JS)
    check(f"P23 lot B Simple « En cours » : {[(x['title'], x['step']) for x in k['current']]} ; finies depuis plus de 2 min "
          f"repliées « {k['head']} » ({[x['title'] for x in k['history']]}, ouvert {k['open']}) ; coût affiché "
          f"{[x['cost'] for x in k['current']]} ; repliées visibles {[x['shown'] for x in k['history']]}, titre "
          f"visible {k['headShown']}, dernière {k['last']}, compteur « {k['count']} »",
          [x["title"] for x in k["current"]] == ["Agent du lot B", "Fini récent"]
          and k["current"][0]["step"] == "en arrière-plan" and k["current"][1]["step"] == "Terminé"
          and all(not x["cost"] and not x["chan"] for x in k["current"] + k["history"])  # ni coût ni nom de canal
          and [x["title"] for x in k["history"]] == ["Commande finie", "Fini ancien"] and k["head"] == "Historique (2)"
          and not k["open"] and k["headShown"] and not k["history"][0]["shown"] and k["last"] and k["count"] == "1")
    await p.shot("p23b-simple-en-cours.png")
    await p.js("document.querySelector('#tasks .tasks-history > summary').click()")
    await asyncio.sleep(0.2)
    k2 = await p.js(TASKS_JS)
    old = next((x for x in k2["history"] if x["title"] == "Fini ancien"), {})
    check(f"P23 lot B Historique déplié : « {old.get('title', '')} » « {old.get('step', '')} » visible, "
          f"commande finie {[(x['title'], x['step']) for x in k2['history'] if x['title'] != 'Fini ancien']}",
          k2["open"] and old.get("shown") and old.get("step") == "Échec"
          and all(x["shown"] for x in k2["history"]) and k2["history"][0]["step"] == "Terminé")
    await p.shot("p23b-simple-historique.png")
    await p.js("document.querySelector('#tasks .tasks-history').open = false, true")

    # ---------------------------------------------------------- question et compte rendu d'un projet (relecture lot A)
    proj = await p.js("[...document.querySelectorAll('.channel-tab:not(.add)')].map(t => t.dataset.chan).find(c => c && c !== 'general') || ''")
    await p.js("document.querySelectorAll('.toast').forEach(t => t.remove()), true")
    n_before = await p.js("document.querySelectorAll('.msg.choice').length")
    await demo("demo_choice", channel=proj)
    await p.until(f"document.querySelectorAll('.msg.choice').length > {n_before}", 3)
    await fake_event(p, {"type": "reply", "mid": "p23b-m1", "channel": proj, "text": "Message du projet, caché en Simple."})
    await fake_event(p, {"type": "report", "mid": "p23b-r1", "channel": proj, "text": "Compte rendu du projet, à lire.",
                         "pending_read": True, "report_kind": "resultat"})
    c = await p.js("""(() => { const cards = [...document.querySelectorAll('.msg.choice')]; const card = cards[cards.length - 1];
        const msg = (t) => [...document.querySelectorAll('.msg.astrel')].find(m => m.textContent.includes(t));
        const vis = (e) => !!e && !e.classList.contains('hidden') && e.getClientRects().length > 0;
        const toasts = [...document.querySelectorAll('.toast')];
        const q = toasts.find(t => t.textContent.includes('attend'));
        return { card: vis(card), active: !!card?.classList.contains('active'), report: vis(msg('Compte rendu du projet')),
                 plain: vis(msg('Message du projet')), toasts: toasts.map(t => t.textContent),
                 clickable: toasts.filter(t => t.classList.contains('clickable')).length,
                 labels: [...document.querySelectorAll('.channel-tab:not(.add) span')].map(t => t.textContent.trim()).filter(t => t.length > 2) }; })()""")
    named = [l for l in c["labels"] if l not in ("Général",) and any(l in t for t in c["toasts"])]
    check(f"P23 Simple (relecture lot A) : question du canal « {proj} » visible dans le fil ({c['card']}, active "
          f"{c['active']}), compte rendu visible ({c['report']}), message ordinaire du projet caché ({not c['plain']}), "
          f"toasts cliquables {c['clickable']}/{len(c['toasts'])} sans nom de projet ({named})",
          proj and c["card"] and c["active"] and c["report"] and not c["plain"] and c["clickable"] >= 2 and not named)
    await p.shot("p23b-simple-question-projet.png")
    # répondue (« b) ») : la démo garde un état propre pour la suite
    await p.js("(() => { const cards = [...document.querySelectorAll('.msg.choice')]; cards[cards.length - 1].querySelectorAll('.choice-btn')[1].click(); })()")
    await asyncio.sleep(0.4)
    await p.js("document.querySelectorAll('.toast').forEach(t => t.remove()), true")


async def check_avance_lot_b(p: Page) -> None:
    """22/10 (plan P23, lot B) en affichage AVANCÉ : capteurs en mots (question 10 : aussi en Avancé, nom
    brut au survol), « En cours » technique comme avant (étape, actions, requêtes, tokens) mais finies
    depuis plus de 2 min dans « Historique » (point 15 de la note du 21/10), orbe « J'agis » + outil."""
    await fake_event(p, {"type": "state", "state": "working", "detail": "Bash · npm test", "muted": False})
    o = await p.js("({ label: document.querySelector('#state-label').textContent, detail: document.querySelector('#state-detail').textContent })")
    await fake_event(p, {"type": "state", "state": "sleeping", "detail": "", "muted": False})
    check(f"P23 lot B Avancé : orbe « {o['label']} » « {o['detail']} » (libellés d'origine)",
          o["label"] == "J'agis" and o["detail"] == "Bash · npm test")
    sens = await p.js("[...document.querySelectorAll('#sensors .sensor')].map(c => [c.dataset.sensor, c.querySelector('b').textContent, c.querySelector('.desc').textContent, c.title])")
    names = {raw: name for raw, name, _, _ in sens}
    # 23/10 (demande de l'utilisateur) : le panneau ne garde que WATCH ; les autres dans Réglages › Capteurs.
    check(f"capteurs du panneau : WATCH seul ({names}), en mots, nom brut au survol",
          names == {"WATCH": "Vue de l'écran"} and all(title.endswith(f"({raw})") for raw, _, _, title in sens))
    await fake_event(p, lot_b_tasks(time.time()))
    k = await p.js(TASKS_JS)
    check(f"P23 lot B Avancé « En cours » : {[(x['title'], x['step'], x['cost']) for x in k['current']]}, historique "
          f"« {k['head']} » {[x['title'] for x in k['history']]}",
          [x["title"] for x in k["current"]] == ["Agent du lot B", "Fini récent"]
          and k["current"][0]["step"] == "Read meeting-notes.md · 4 actions" and k["current"][0]["cost"] == "3 requêtes · 5 k tokens"
          and k["current"][1]["step"] == "Terminé · 7 actions"
          and [x["title"] for x in k["history"]] == ["Commande finie", "Fini ancien"]
          and k["head"] == "Historique (2)" and not k["open"])
    await p.shot("p23b-avance-en-cours.png")
    # la démo republie ses tâches : sans `done`, les finies injectées restent jusqu'au rechargement
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_tasks_nodone' })), true")
    await asyncio.sleep(0.2)


async def check_sans_graphe(p: Page) -> None:
    """23/10 (plan P28, demande de l'utilisateur) : plus de schéma 3D en fond du HUD, ni Recadrer ni Orbite, ni
    compteurs « N nœuds · liens · notes ». Les évènements d'outils et de geste qui l'animaient (`tool_activity`,
    `tool`, `tool_result`, `hand`, `graph`) n'ont plus rien à dessiner : l'activité suit, aucune erreur JS.
    Seule toile restante : l'orbe Premium (25/10, plan P35 : l'orbe WebGL du Classique est retirée)."""
    gone = await p.js("""(() => ({ els: ['#graph', '#graph-tools', '#stats', '#brain-btn', '[data-action]', '.graph-btn',
        '.float-tooltip-kap'].filter(s => document.querySelector(s)),
      canvases: [...document.querySelectorAll('canvas')].map(c => c.id || c.className).sort() }))()""")
    await fake_event(p, {"type": "graph", "nodes": [{"id": "core", "group": "core", "label": "Astrel"}], "links": [],
                         "stats": {"nodes": 1, "links": 0, "notes": 0}})
    await fake_event(p, {"type": "tool_activity", "id": "p28-a", "node": "tool:Bash", "tool": "Bash"})
    await fake_event(p, {"type": "tool", "id": "p28-a", "node": "tool:Bash", "tool": "Bash", "desc": "npm test P28"})
    await fake_event(p, {"type": "hand", "action": "rotate", "dx": 0.01, "dy": 0.0})
    await fake_event(p, {"type": "tool_result", "id": "p28-a", "ok": True})
    await asyncio.sleep(0.3)
    row = await p.js("""(() => { const r = [...document.querySelectorAll('#activity-list > *')].find(e => e.textContent.includes('npm test P28'));
        return r ? r.className : ''; })()""")
    check(f"P28 : plus de graphe 3D ni de Recadrer / Orbite / compteurs (restent : {gone['els']}), toiles {gone['canvases']}",
          gone["els"] == [] and gone["canvases"] == ["orb-premium"])
    check(f"P28 : évènements graph / tool / hand sans graphe, l'activité suit (ligne « {row} »)", "ok" in row.split())


async def check_theme_a_la_connexion(p: Page) -> None:
    """Relecture P17 : profil Chrome neuf (cache `astrel-theme` vide) et aucun instantané `settings`
    rejoué à la connexion (cœur relancé, settings.json édité à la main) : le HUD demande le thème au
    cœur (`settings_get`). 25/10 (plan P35, Premium seul) : Premium dès le <body> sans cache (défaut,
    sans settings.json), et un ANCIEN cache « classique » ne ramène plus le Classique. Recharge la page."""
    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    # attributs de <html> au moment où <body> apparaît (avant dist/app.js) : theme-boot.js seul
    await p.send("Page.addScriptToEvaluateOnNewDocument", source="""
        new MutationObserver((ms, o) => { if (document.body) { const d = document.documentElement.dataset;
          window.__themeAtBody = [d.theme || '', d.mode || '', d.accent || '']; o.disconnect(); } })
          .observe(document, { childList: true, subtree: true });""")
    system = await p.js("matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'")

    async def reload(cache):
        await demo("demo_settings_forget")
        await asyncio.sleep(0.3)
        await p.js("localStorage.removeItem('astrel-theme'), true" if cache is None else
                   f"localStorage.setItem('astrel-theme', {json.dumps(json.dumps(cache))}), true")
        await p.js("location.reload(), true")
        await asyncio.sleep(0.5)
        return await p.until("!!document.querySelector('#transcript') && !!document.querySelector('.channel-tab')", 15)

    # 1. défaut (démo = réglages par défaut, comme sans settings.json), aucun cache : Premium automatique
    await premium_mode(p, "auto")
    loaded = await reload(None)
    early = await p.js("window.__themeAtBody")
    themed = await p.until(f"document.documentElement.dataset.theme === 'premium' "
                           f"&& document.documentElement.dataset.mode === '{system}'", 6)
    check(f"P35 défaut sans cache : Premium dès le <body> ({early}), mode du système ({system}) après settings_get",
          loaded and themed and early == ["premium", system, "bleu"])

    # 2. profil neuf, cœur en sombre : le HUD le demande (settings_get) et le garde en cache
    await premium_mode(p, "dark")
    loaded = await reload(None)
    themed = await p.until("document.documentElement.dataset.theme === 'premium' "
                           "&& document.documentElement.dataset.mode === 'dark'", 6)
    asked = await p.js("window.__sent.filter(m => m.cmd === 'settings_get').length")
    cached = await p.js("JSON.parse(localStorage.getItem('astrel-theme') || '{}')")
    check(f"profil neuf, sans instantané settings : HUD Premium sombre après settings_get ({asked} envoyé, cache {cached})",
          loaded and themed and asked >= 1 and cached.get("theme") == "premium" and cached.get("mode") == "dark")

    # 3. ancien cache d'un HUD Classique : ignoré pour le thème (son mode et son accent seuls comptent)
    loaded = await reload({"theme": "classique", "mode": "light", "accent": "vert"})
    early = await p.js("window.__themeAtBody")
    themed = await p.until("document.documentElement.dataset.theme === 'premium' "
                           "&& document.documentElement.dataset.mode === 'dark'", 6)
    orb = await p.js("(() => { const c = document.querySelector('#orb-premium'); return { id: !!c, w: c ? c.clientWidth : 0, "
                     "webgl: !!document.querySelector('canvas#orb') }; })()")
    check(f"P35 ancien cache « classique » : Premium dès le <body> ({early}), puis réglages du cœur (sombre) ; "
          f"orbe 2D seule ({orb})",
          loaded and themed and early == ["premium", "light", "vert"] and orb["id"] and orb["w"] == 260 and not orb["webgl"])
    await premium_mode(p, "auto")  # retour au défaut de la démo


async def check_levee(p: Page) -> None:
    """18/10 (plan P7) sur la démo : Réglages › Garde-fou, mot « lever » exigé, bandeau et
    cadre ORANGE, bouton en barre, « Rétablir » depuis le bandeau. (Éteinte à la relance du
    vrai cœur : tests/test_levee.py.)"""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")
    hidden = "['#lift-banner', '#lift-frame', '#lift-chip'].every(s => document.querySelector(s).hidden)"
    check("levée : bandeau, cadre et bouton en barre cachés au départ", await p.js(hidden))
    await p.shot("40-levee-avant.png")
    await p.js("document.querySelector('#settings-chip').click()")
    await p.until("document.querySelectorAll('.modal.wide .settings-group').length === 15", 3)
    await p.js("__open('garde_fou')")
    label = await p.js("__row('garde_fou_levee').querySelector('.setting-action')?.textContent")
    n = len(await sent("settings_action"))
    await p.js("__row('garde_fou_levee').querySelector('.setting-action').click()")
    field = await p.until("!!document.querySelector('.modal.danger input[name=word]')", 3)
    await p.js("document.querySelector('.modal.danger input[name=word]').value = 'oui'")
    await p.js("document.querySelector('.modal.danger .modal-ok').click()")
    await asyncio.sleep(0.3)
    check(f"levée : bouton « {label} », mot à taper, « oui » refusé sans rien envoyer",
          label == "Lever le garde-fou" and field and len(await sent("settings_action")) == n and await p.js(hidden))
    await p.js("__row('garde_fou_levee').querySelector('.setting-action').click()")
    await p.until("!!document.querySelector('.modal.danger input[name=word]')", 3)
    await p.shot("41-levee-mot-a-taper.png")
    await p.js("document.querySelector('.modal.danger input[name=word]').value = 'lever'")
    await p.js("document.querySelector('.modal.danger .modal-ok').click()")
    shown = await p.until("!document.querySelector('#lift-banner').hidden", 3)
    last = (await sent("settings_action"))[-1]
    check(f"levée : settings_action {last.get('key')}/{last.get('action')} confirm={last.get('confirm')}, bandeau affiché",
          shown and last.get("key") == "garde_fou_levee" and last.get("action") == "lever" and last.get("confirm") == "lever")
    await p.js("document.querySelector('.modal.wide .modal-x').click()")
    await p.until("!document.querySelector('.modal-back')", 2)
    # 19/10 : la ligne Total / levée flotte SOUS la tête sans la grandir (rien ne bouge)
    await asyncio.sleep(0.3)
    await p.js(SETTLE_JS)
    red = await p.js(f"{TOKEN_JS}('--red')")  # P35 : rouge du thème posé
    b = await p.js("""(() => { const b = document.querySelector('#lift-banner'), f = document.querySelector('#lift-frame'),
        c = document.querySelector('#lift-chip'), r = b.getBoundingClientRect(), bar = document.querySelector('#topbar').getBoundingClientRect(),
        mis = document.querySelector('#mission-banner'), headH = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--head-h')),
        files = document.querySelector('#lift-files');
        const headBottom = Math.max(bar.bottom, mis.hidden ? 0 : mis.getBoundingClientRect().bottom);
        return { text: b.textContent, frame: !f.hidden, events: getComputedStyle(f).pointerEvents,
                 chip: !c.hidden && c.getBoundingClientRect().width > 0, color: getComputedStyle(b).borderTopColor,
                 below: r.top >= headH - 1, headSame: Math.abs(headH - Math.ceil(headBottom)) <= 1, h: r.height,
                 files: files.hidden ? null : [files.querySelector('summary').textContent, files.querySelectorAll('li').length],
                 body: document.body.classList.contains('lift-on') }; })()""")
    check(f"levée : « {' '.join(b['text'].split())[:90]} », cadre {b['frame']} ({b['events']}), bouton en barre, rouge {b['color']}",
          "GARDE-FOU LEVÉ" in b["text"] and re.search(r"· \d\d:\d\d", b["text"]) and "hud/src/main.js" in b["text"] and b["frame"]
          and b["events"] == "none" and b["chip"] and b["color"] == red and b["body"])
    check(f"levée : ligne fine ({b['h']:.0f} px) sous la tête, tête non grandie, fichiers en dépliant {b['files']}",
          b["below"] and b["headSame"] and b["h"] <= 28 and b["files"] and b["files"][1] >= 1)
    await p.js("document.querySelector('#lift-files > summary').click()")
    opened = await p.until("document.querySelector('#lift-files').open && "
                           "document.querySelector('#lift-files > ul').getBoundingClientRect().height > 0", 2)
    await p.shot("42-levee-active.png")
    await p.js("document.body.click()")
    closed = await p.until("!document.querySelector('#lift-files').open", 2)
    check("levée : le dépliant des fichiers s'ouvre d'un clic, se ferme d'un clic ailleurs", opened and closed)
    n = len(await sent("settings_action"))
    await p.js("document.querySelector('#lift-off').click()")
    back = await p.until(hidden, 3)
    last = (await sent("settings_action"))[-1]
    check(f"levée : « Rétablir » du bandeau → settings_action {last.get('action')}, tout disparaît",
          back and len(await sent("settings_action")) == n + 1 and last.get("action") == "retablir"
          and last.get("key") == "garde_fou_levee" and not await p.js("document.body.classList.contains('lift-on')"))
    await p.shot("43-levee-retablie.png")


REGLAGES_ICONES_JS = """(() => {
    const box = document.querySelector('.modal.wide.settings'); if (!box) return null;
    document.getAnimations().forEach(a => { try { a.finish(); } catch {} }); // état FINAL du chevron (headless n'avance pas toujours)
    const groups = [...box.querySelectorAll('details.settings-group')];
    const w = (e) => e ? Math.round(e.getBoundingClientRect().width) : 0;
    const rows = [...box.querySelectorAll('details[open] .setting')];
    // aide (et contrôle large) alignée sur le LIBELLÉ, pas sur l'icône
    const misaligned = rows.filter(r => r.classList.contains('with-ico')).filter(r => {
        const lab = r.querySelector('.setting-label').getBoundingClientRect().left;
        return [...r.children].filter(c => c.matches('.setting-help, .setting-info, .setting-flags, .setting-tri'))
          .some(c => Math.abs(c.getBoundingClientRect().left - lab) > 1.5); }).map(r => r.dataset.key);
    const vmis = rows.filter(r => r.querySelector('.setting-ico')).filter(r => {
        const i = r.querySelector('.setting-ico').getBoundingClientRect(), l = r.querySelector('.setting-label').getBoundingClientRect();
        return Math.abs((i.top + i.bottom) / 2 - (l.top + l.bottom) / 2) > 2; }).map(r => r.dataset.key);
    return {
      groups: groups.map(d => ({ id: d.dataset.group, icon: d.querySelector('summary .settings-group-ico svg.ico')?.dataset.icon,
                                 chevron: !!d.querySelector('summary svg.settings-group-chevron'), size: w(d.querySelector('summary .settings-group-ico svg')),
                                 tile: w(d.querySelector('summary .settings-group-ico')), name: d.querySelector('.settings-group-name')?.textContent })),
      items: [...box.querySelectorAll('.setting')].map(r => [r.dataset.key, r.querySelector('.setting-head > svg.setting-ico')?.dataset.icon || '']),
      sizes: [...new Set([...box.querySelectorAll('svg.setting-ico')].map(w))], misaligned, vmis,
      marker: getComputedStyle(box.querySelector('summary')).listStyleType,
      rot: groups.filter(d => d.open).map(d => getComputedStyle(d.querySelector('.settings-group-chevron')).transform),
      closedRot: groups.filter(d => !d.open).map(d => getComputedStyle(d.querySelector('.settings-group-chevron')).transform),
    };
})()"""


async def check_reglages_icones(p: Page) -> None:
    """19/10 (demande : « des icônes dans les Réglages permettraient d'avoir une interface plus
    lisible et claire ») : une icône devant chaque section et chaque réglage connu, même taille,
    centrée sur le libellé ; aide alignée sous le libellé ; chevron qui tourne à l'ouverture.
    Ids et clés inchangés (les contrôles de check_reglages passent toujours)."""
    while await p.js("!!document.querySelector('.modal-back')"):
        await p.key("Escape", vk=27)
        await asyncio.sleep(0.2)
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await p.js("document.querySelector('#settings-chip').click()")
    await p.until("document.querySelectorAll('.modal.wide .settings-group').length === 15", 3)
    for g in ("compte", "voix", "whatsapp", "navigateurs", "connecteurs", "skills", "seuils", "cerveau", "garde_fou"):
        await p.js(f"__open('{g}')")
    await asyncio.sleep(0.3)
    r = await p.js(REGLAGES_ICONES_JS)
    want = {"compte": "user", "fournisseur": "cloud", "voix": "volume", "apparence": "palette", "systeme": "monitor", "whatsapp": "smartphone", "navigateurs": "globe", "images": "image", "connecteurs": "plug",
            "skills": "sparkles", "plugins": "puzzle", "seuils": "bell", "cerveau": "brain", "diagnostic": "activity",
            "garde_fou": "shield-check"}
    got = {g["id"]: g["icon"] for g in r["groups"]}
    check(f"Réglages : icône devant chaque section ({got})", got == want)
    check(f"Réglages : pastille {r['groups'][0]['tile']} px, icône {r['groups'][0]['size']} px, chevron à droite, plus de triangle ({r['marker']})",
          all(g["chevron"] and g["tile"] == 28 and g["size"] == 16 for g in r["groups"]) and r["marker"] == "none"
          and r["groups"][0]["name"] == "Compte Claude")
    bare = [k for k, i in r["items"] if not i]
    check(f"Réglages : icône devant chaque réglage ({len(r['items']) - len(bare)}/{len(r['items'])}, sans : {bare})", not bare)
    check(f"Réglages : icônes de réglage de taille homogène {r['sizes']} px, centrées sur le libellé ({r['vmis']})",
          r["sizes"] == [16] and not r["vmis"])
    check(f"Réglages : aide et contrôles larges alignés sous le libellé ({r['misaligned']})", not r["misaligned"])
    check(f"Réglages : chevron tourné sur les sections ouvertes ({r['rot'][:1]}), droit sur les fermées ({r['closedRot'][:1]})",
          r["rot"] and all(t.startswith("matrix(0, 1,") or "e-17, 1," in t for t in r["rot"])
          and all(t == "none" for t in r["closedRot"]))
    found = await p.js(EMOJI_DOM_JS)
    check(f"Réglages : aucun émoji ({found[:3]})", found == [])
    await p.js("document.querySelector('.modal.wide details[data-group=\"voix\"]').scrollIntoView({block: 'start'})")
    await asyncio.sleep(0.2)
    await p.shot("44-reglages-icones.png")
    await p.js("[...document.querySelectorAll('.modal.wide details')].forEach(d => { if (d.open) d.querySelector('summary').click(); })")
    await asyncio.sleep(0.3)
    await p.shot("45-reglages-sections.png")
    await p.js("document.querySelector('.modal.wide .modal-x').click()")
    await p.until("!document.querySelector('.modal-back')", 2)


ICONES_CONNECTEURS = Path("/tmp/captures-icones-connecteurs")


async def check_reglages_connecteurs_icones(p: Page) -> None:
    """19/10 (plan P10, lot 3) : devant chaque connecteur, l'icône locale de son service
    (masque teint, image servie par le HUD) ou, s'il n'en a pas, une pastille à lettre."""
    while await p.js("!!document.querySelector('.modal-back')"):
        await p.key("Escape", vk=27)
        await asyncio.sleep(0.2)
    await p.js("document.querySelector('#settings-chip').click()")
    await p.until("document.querySelectorAll('.modal.wide .settings-group').length === 15", 3)
    await p.js("""(() => { const d = document.querySelector('.modal.wide details[data-group="connecteurs"]');
        if (!d.open) d.querySelector('summary').click(); d.scrollIntoView({block: 'start'}); })()""")
    await asyncio.sleep(0.4)
    r = await p.js("""(async () => {
      const row = [...document.querySelectorAll('.modal.wide .setting')].find(r => r.dataset.key === 'connectors');
      const flags = [...row.querySelectorAll('.setting-flag')].map(l => {
        const ico = l.querySelector('.setting-flag-ico'), cs = ico && getComputedStyle(ico);
        return { name: l.querySelector('input').dataset.name, text: l.textContent, letter: !!ico?.classList.contains('letter'),
                 mask: cs ? (cs.maskImage || cs.webkitMaskImage || '') : '', bg: cs?.backgroundColor || '',
                 w: ico ? Math.round(ico.getBoundingClientRect().width) : 0, hidden: ico?.getAttribute('aria-hidden') };
      });
      const urls = flags.map(f => (f.mask.match(/url\\("?([^")]+)"?\\)/) || [])[1]).filter(Boolean);
      const status = await Promise.all(urls.map(u => fetch(u).then(x => x.status + ' ' + x.headers.get('content-type')).catch(e => String(e))));
      return { flags, status };
    })()""")
    by = {f["name"]: f for f in r["flags"]}
    known = ["google_calendar", "figma", "claude_docs", "gmail", "google_drive", "dropbox"]
    check(f"Connecteurs : une icône (masque) par service connu ({[n for n in known if 'url(' in by.get(n, {}).get('mask', '')]})",
          all("url(" in by[n]["mask"] and not by[n]["letter"] for n in known))
    check(f"Connecteurs : Canva sans icône locale → pastille à lettre ({by['canva']['text']!r}, {by['canva']['mask']!r})",
          by["canva"]["letter"] and by["canva"]["text"].startswith("C") and "url(" not in by["canva"]["mask"])
    check(f"Connecteurs : icônes servies par le HUD en SVG ({sorted(set(r['status']))})",
          r["status"] and all(s.startswith("200 image/svg+xml") for s in r["status"]))
    check(f"Connecteurs : pastilles de 14 px, teintées, cachées aux lecteurs d'écran ({sorted({f['w'] for f in r['flags']})})",
          all(f["w"] == 14 and f["bg"] not in ("", "rgba(0, 0, 0, 0)") and f["hidden"] == "true" for f in r["flags"]))
    ICONES_CONNECTEURS.mkdir(exist_ok=True)
    shot = await p.send("Page.captureScreenshot", format="png")
    (ICONES_CONNECTEURS / "apres-reglages-connecteurs.png").write_bytes(base64.b64decode(shot["data"]))
    box = await p.js("""(() => { const b = [...document.querySelectorAll('.modal.wide .setting')]
        .find(r => r.dataset.key === 'connectors').getBoundingClientRect();
        return { x: b.x, y: b.y, width: b.width, height: b.height }; })()""")
    shot = await p.send("Page.captureScreenshot", format="png", clip={**box, "scale": 2})
    (ICONES_CONNECTEURS / "apres-connecteurs-zoom.png").write_bytes(base64.b64decode(shot["data"]))
    await p.js("document.querySelector('.modal.wide .modal-x').click()")
    await p.until("!document.querySelector('.modal-back')", 2)


FOURNISSEUR_OUT = Path("/tmp/captures-fournisseur")


async def check_fournisseur(p: Page) -> None:
    """20/10 (plan P11) sur la démo : Réglages › Fournisseur, la clé se colle dans un champ MASQUÉ
    (type password), part sous `value`, et n'apparaît plus nulle part dans la page ensuite."""
    import base64

    FOURNISSEUR_OUT.mkdir(exist_ok=True)

    async def shot(name):
        r = await p.send("Page.captureScreenshot", format="png")
        (FOURNISSEUR_OUT / name).write_bytes(base64.b64decode(r["data"]))

    cle = "sk-proj-CLE-FACTICE-DU-RENDU-0123456789"
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")
    row = "__row('fournisseur.cles')"
    if not await p.js("!!document.querySelector('.modal.wide')"):
        await p.js("document.querySelector('#settings-chip').click()")
    await p.until("document.querySelectorAll('.modal.wide .settings-group').length === 15", 3)
    await p.js("__open('fournisseur')")
    await p.until(f"!!{row}", 3)
    await p.js(f"{row}.scrollIntoView({{block: 'center'}})")
    await shot("apres-1-groupe-fournisseur.png")
    sel = await p.js("[...__row('fournisseur').querySelectorAll('option')].map(o => o.value)")
    check(f"Fournisseur : groupe présent, choix abonnement / clé Claude / OpenAI ({sel})",
          sel == ["abonnement", "cle_claude", "openai:gpt-5"])
    await p.js(f"[...{row}.querySelectorAll('.setting-action')].find(b => b.dataset.action === 'enregistrer_openai').click()")
    await p.until("!!document.querySelector('.modal:not(.wide) input[name=value]')", 3)
    kind = await p.js("document.querySelector('.modal:not(.wide) input[name=value]').type")
    await p.js(f"document.querySelector('.modal:not(.wide) input[name=value]').value = {json.dumps(cle)}")
    await shot("apres-2-saisie-masquee.png")
    n = len(await sent("settings_action"))
    await p.js("document.querySelector('.modal:not(.wide) .modal-ok').click()")
    await p.until(f"window.__sent.filter(m => m.cmd === 'settings_action').length > {n}", 3)
    last = (await sent("settings_action"))[-1]
    saved = await p.until(f"({row}.textContent || '').includes('enregistrée sur cette machine')", 3)
    leak = await p.js(f"""(() => document.body.innerText.includes({json.dumps(cle)})
        || [...document.querySelectorAll('input, textarea')].some(i => i.value.includes({json.dumps(cle)}))
        || JSON.stringify(sessionStorage).includes({json.dumps(cle)}) || JSON.stringify(localStorage).includes({json.dumps(cle)}))()""")
    await shot("apres-3-cle-enregistree.png")
    check(f"clé : champ « {kind} », envoyée sous value, jamais réaffichée ni gardée par la page",
          kind == "password" and last.get("action") == "enregistrer_openai" and last.get("value") == cle and saved and not leak)
    await p.js("document.querySelector('.modal.wide .modal-x').click()")


async def check_whatsapp_et_guide(p: Page) -> None:
    """18/10 (plan P5) sur la démo : appairage WhatsApp dans les Réglages (QR dessiné puis
    renouvelé, QR périmé non dessiné, compte confirmé, délier avec le mot, code à 8
    caractères) et guide de démarrage (pastille, aide « ? », sujets au clavier, Tester,
    étape cochée, réglage monté, Suivant, Terminé)."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")
    wa = "__row('whatsapp')"
    act = lambda a: p.js(f"[...{wa}.querySelectorAll('.setting-action')].find(b => b.dataset.action === '{a}')?.click()")
    acts = lambda: p.js(f"[...{wa}.querySelectorAll('.setting-action')].map(b => b.dataset.action)")
    await p.js("document.querySelector('#settings-chip').click()")
    await p.until("document.querySelectorAll('.modal.wide .settings-group').length === 15", 3)
    await p.js("__open('whatsapp')")
    help_ = await p.js(f"{wa}.querySelector('.setting-guide')?.getAttribute('aria-label')")
    check(f"WhatsApp non relié : Relier par QR ou par code, aide « ? » ({help_})",
          await acts() == ["link", "link_phone"] and "guide" in (help_ or ""))
    await p.shot("14-whatsapp-avant.png")
    await act("link")
    drawn = await p.until(f"{wa}.querySelector('canvas.wa-qr')?.width > 100 "
                          f"&& !{wa}.querySelector('canvas.wa-qr').classList.contains('hidden')", 4)
    info = await p.js(f"""(() => {{ const r = {wa};
        return {{ warn: r.querySelector('.wa-link-warn')?.textContent, text: r.querySelector('.wa-link-text')?.textContent,
                  role: r.querySelector('canvas.wa-qr')?.getAttribute('role'),
                  acts: [...r.querySelectorAll('.setting-action')].map(b => b.dataset.action) }}; }})()""")
    check(f"« Relier avec un QR » : QR dessiné, avertissement, image nommée ({(info['text'] or '')[:40]}…)",
          drawn and "accès à ton compte" in (info["warn"] or "") and info["role"] == "img"
          and (info["text"] or "").startswith("QR n°1") and info["acts"] == ["link_cancel"])
    await p.shot("15-whatsapp-qr.png")
    check("QR renouvelé sur place (n°2)",
          await p.until(f"({wa}.querySelector('.wa-link-text')?.textContent || '').startsWith('QR n°2')", 6))
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_wa_old_qr' }))")
    check("QR de plus de 60 s : plus dessiné, message d'expiration",
          await p.until(f"{wa}.querySelector('canvas.wa-qr').width === 0 "
                        f"&& {wa}.querySelector('.wa-link-text').textContent.includes('expiré')", 3))
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_wa_scan' }))")
    got = await p.until(f"({wa}.querySelector('.wa-link-text')?.textContent || '').includes('+33600000000')", 3)
    check(f"compte détecté : numéro à confirmer, QR effacé, boutons {await acts()}",
          got and await acts() == ["link_confirm", "link_cancel"]
          and await p.js(f"{wa}.querySelector('canvas.wa-qr').width === 0"))
    await act("link_confirm")
    ok = await p.until(f"({wa}.querySelector('.setting-result')?.textContent || '').includes('WhatsApp relié')", 3)
    check(f"confirmé : résultat affiché, encadré masqué, boutons {await acts()}",
          ok and await p.js(f"!{wa}.querySelector('.wa-link') || {wa}.querySelector('.wa-link').classList.contains('hidden')")
          and "unlink" in await acts())
    await p.shot("16-whatsapp-relie.png")
    n = len(await sent("settings_action"))
    await act("unlink")
    await p.until("!!document.querySelector('.modal.danger input[name=word]')", 3)
    label = await p.js("document.querySelector('.modal.danger .modal-label').textContent")
    await p.js("document.querySelector('.modal.danger input[name=word]').value = 'Délier'")
    await p.js("document.querySelector('.modal.danger .modal-ok').click()")
    await p.until(f"window.__sent.filter(m => m.cmd === 'settings_action').length > {n}", 3)
    last = (await sent("settings_action"))[-1]
    check(f"Délier : mot demandé ({label}), envoyé avec confirm, retour à « Relier »",
          "délier" in label and last.get("confirm") == "Délier"
          and await p.until(f"[...{wa}.querySelectorAll('.setting-action')].map(b => b.dataset.action).join() === 'link,link_phone'", 3))
    await act("link_phone")
    await p.until("!!document.querySelector('.modal input[name=value]')", 3)
    await p.js("document.querySelector('.modal input[name=value]').value = '+33 6 00 00 00 00'")
    await p.js("document.querySelector('.modal .modal-ok').click()")
    code = await p.until(f"{wa}.querySelector('.wa-code')?.textContent === 'DEMO-1234'", 3)
    last = (await sent("settings_action"))[-1]
    aria = await p.js(f"{wa}.querySelector('.wa-code')?.getAttribute('aria-label') || ''")
    check(f"« Relier avec un code » : numéro envoyé sous value, code lisible ({aria})",
          code and last.get("action") == "link_phone" and last.get("value") == "+33 6 00 00 00 00"
          and aria.startswith("Code d'appairage : D E M O"))
    await p.shot("16b-whatsapp-code.png")
    await act("link_cancel")
    check("annulé : code effacé, motif affiché",
          await p.until(f"({wa}.querySelector('.setting-result')?.textContent || '').toLowerCase().includes('appairage annulé')", 3)
          and await p.js(f"!{wa}.querySelector('.wa-code') || {wa}.querySelector('.wa-code').textContent === ''"))

    # Guide : l'aide « ? » ouvre le guide sur le sujet du réglage
    await p.js(f"{wa}.querySelector('.setting-guide').click()")
    opened = await p.until("(document.querySelector('.modal.wide.guide .guide-title')?.textContent || '').includes('WhatsApp')", 4)
    g = await p.js("""(() => { const m = document.querySelector('.modal.wide.guide');
        return { mount: !!m.querySelector('.guide-setting .setting[data-key=whatsapp]'),
                 current: m.querySelector('.guide-topic[aria-current=true]')?.dataset.topic,
                 steps: m.querySelectorAll('.guide-topic.step').length, topics: m.querySelectorAll('.guide-topic').length,
                 script: !!m.querySelector('.guide-body script'), md: m.querySelectorAll('.guide-body p').length }; })()""")
    check(f"« ? » : guide ouvert sur WhatsApp, réglage monté, {g['steps']} étapes / {g['topics']} sujets",
          opened and g["mount"] and g["current"] == "whatsapp" and g["steps"] >= 7 and g["topics"] >= 22
          and not g["script"] and g["md"] >= 2)
    await p.shot("17-guide-whatsapp.png")
    # 19/10 : icône seule (barre du haut allégée), nom dans aria-label et title
    chip = await p.js("""(() => { const c = document.querySelector('#guide-chip');
        return { text: c?.textContent.trim(), label: c?.getAttribute('aria-label'), title: c?.title,
                 icon: c?.querySelector('svg.ico')?.dataset.icon, next: c?.nextElementSibling?.id }; })()""")
    check(f"bouton Guide « {chip['label']} » en icône seule devant Réglages",
          chip["text"] == "" and chip["label"] == "Guide" and chip["title"].startswith("Guide de démarrage")
          and chip["icon"] == "help" and chip["next"] == "settings-chip")
    # Clavier : flèches dans la liste des sujets, Entrée affiche
    await p.js("document.querySelector('.guide-topic[data-topic=micro]').focus()")
    await p.key("ArrowDown", vk=40)
    moved = await p.js("document.activeElement?.dataset.topic")
    await p.key("Enter", vk=13, text="\r")
    shown = await p.until("document.querySelector('.guide-topic[aria-current=true]')?.dataset.topic === "
                          f"{json.dumps(moved)}", 3)
    check(f"flèche bas : focus sur « {moved} », Entrée l'affiche", moved not in (None, "micro") and shown)
    await p.js("document.querySelector('.guide-topic[data-topic=micro]').click()")
    await p.until("!!document.querySelector('.guide-test-btn')", 3)
    n = len(await sent("settings_action"))
    await p.js("document.querySelector('.guide-test-btn').click()")
    tested = await p.until("(document.querySelector('.guide-test-result')?.textContent || '').includes('micro entend')", 4)
    last = (await sent("settings_action"))[-1]
    check("« Tester » le micro : settings_action audio.test/mic, résultat annoncé (role=status)",
          tested and len(await sent("settings_action")) > n and last.get("key") == "audio.test"
          and last.get("action") == "mic"
          and await p.js("document.querySelector('.guide-test-result').getAttribute('role') === 'status'"))
    await p.shot("18-guide-micro.png")
    before = await p.js("document.querySelector('.guide-count').textContent")
    await p.js("document.querySelector('.guide-done input').click()")
    marked = await p.until("document.querySelector('.guide-topic[data-topic=micro]').classList.contains('done')", 3)
    mk = (await sent("guide_mark"))[-1]
    after = await p.js("document.querySelector('.guide-count').textContent")
    check(f"étape cochée : guide_mark envoyé, liste et compteur à jour ({before} → {after})",
          marked and mk.get("topic") == "micro" and mk.get("done") is True and before != after)
    await p.js("document.querySelector('.guide-next').click()")
    check("« Suivant » : sujet suivant, focus sur son titre",
          await p.until("document.activeElement?.classList.contains('guide-title')", 2))
    await p.js("document.querySelector('.guide-finish').click()")
    check("« Terminé » : guide_mark {finish: true}",
          await p.until("window.__sent.some(m => m.cmd === 'guide_mark' && m.finish === true)", 3))
    await p.key("Escape", vk=27)
    check("Échap ferme le guide", await p.until("!document.querySelector('.modal-back')", 2))


async def check_capacites(p: Page) -> None:
    """23/10 (plan P22, lot 2) : page « Tout ce qu'Astrel sait faire », ouverte par le bouton du
    Guide (pas de pastille), vrai catalogue (astrel/capacites.py), skills et connecteurs claude.ai de
    démo ; recherche sans accents, tri, liens « Ouvrir » vers le Guide et les Réglages, Échap."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")
    caps = "document.querySelector('.modal.wide.capacites')"
    await p.js("document.querySelector('#guide-chip').click()")
    await p.until("!!document.querySelector('.modal.wide.guide .guide-capacites')", 4)
    b = await p.js("""(() => { const b = document.querySelector('.guide-capacites');
        return { text: b.textContent.trim(), icon: b.querySelector('svg.ico')?.dataset.icon,
                 chip: !!document.querySelector('#capacites-chip, .chip.capacites') }; })()""")
    check(f"Guide : bouton « {b['text']} » (icône {b['icon']}), aucune pastille de plus",
          b["text"] == "Tout ce qu'Astrel sait faire" and b["icon"] == "sparkles" and not b["chip"])
    await p.js("document.querySelector('.guide-capacites').click()")
    loaded = await p.until(f"{caps}?.querySelectorAll('.cap-group').length >= 15", 5)
    o = await p.js(f"""(() => {{ const m = {caps};
        return {{ title: m.querySelector('.modal-title').textContent, guide: !!document.querySelector('.modal.wide.guide'),
                  groups: [...m.querySelectorAll('.cap-group')].map(g => g.dataset.group),
                  open: [...m.querySelectorAll('.cap-group[open]')].map(g => g.dataset.group),
                  status: m.querySelector('.cap-status').textContent, focus: document.activeElement?.className,
                  bad: m.querySelectorAll('img, script, iframe').length,
                  usage: m.querySelectorAll('.cap-usage').length }}; }})()""")
    req = (await sent("capacites_get"))[-1] if await sent("capacites_get") else {}
    check(f"Capacités : « {o['title']} », {len(o['groups'])} groupes, {o['status']}, guide fermé, focus recherche",
          loaded and o["title"] == "Tout ce qu'Astrel sait faire" and not o["guide"] and o["open"] == ["voix"]
          and {"skills", "claudeai", "bureau", "agents"} <= set(o["groups"]) and o["bad"] == 0
          and o["focus"] == "cap-search" and req.get("lang") == "fr" and "groupe" not in req)
    await p.shot("18b-capacites.png")
    # Recherche sans accents ni casse : « GMAIL » → le connecteur claude.ai seul (bloqué/actif écrits en mots)
    await p.js(f"(() => {{ const s = {caps}.querySelector('.cap-search'); s.value = 'GMAIL'; "
               "s.dispatchEvent(new Event('input')); })()")
    r = await p.js(f"""(() => {{ const m = {caps};
        return {{ groups: [...m.querySelectorAll('.cap-group')].map(g => [g.dataset.group, g.open]),
                  noms: [...m.querySelectorAll('.cap-nom')].map(n => n.textContent),
                  etats: [...m.querySelectorAll('.cap-etat')].map(n => n.textContent) }}; }})()""")
    check(f"recherche « GMAIL » : {r['noms']} ({r['etats']})",
          ["claudeai", True] in r["groups"] and "Gmail" in r["noms"] and "actif pour Astrel" in r["etats"])
    await p.js(f"(() => {{ const s = {caps}.querySelector('.cap-search'); s.value = 'reglages'; "
               "s.dispatchEvent(new Event('input')); })()")
    acc = await p.js(f"[...{caps}.querySelectorAll('.cap-group')].map(g => g.dataset.group)")
    check(f"recherche « reglages » sans accent : {acc}", "reglages" in acc)
    await p.js(f"(() => {{ const s = {caps}.querySelector('.cap-search'); s.value = 'zzzqqq'; "
               "s.dispatchEvent(new Event('input')); })()")
    none = await p.js(f"{caps}.querySelector('.cap-status').textContent")
    check(f"rien trouvé : « {none} »", none == "Rien ne correspond à « zzzqqq »." and await p.js(
        f"{caps}.querySelectorAll('.cap-group').length === 0"))
    await p.js(f"(() => {{ const s = {caps}.querySelector('.cap-search'); s.value = 'founder'; "
               "s.dispatchEvent(new Event('input')); })()")
    sk = await p.js(f"[...{caps}.querySelectorAll('.cap-entry')].map(e => [e.querySelector('.cap-nom').textContent, "
                    "e.querySelector('.cap-etat')?.textContent])")
    check(f"skills : noms et état pour Astrel seulement {sk}", sk == [["founder-cfo", "nom seul pour Astrel"]])
    # Gravité mise en mots, tri « A → Z »
    await p.js(f"(() => {{ const s = {caps}.querySelector('.cap-search'); s.value = 'power_action'; "
               "s.dispatchEvent(new Event('input')); })()")
    grav = await p.js(f"{caps}.querySelector('.cap-grav')?.textContent")
    check(f"power_action : « {grav} »", grav == "Toujours confirmé"
          and await p.js(f"{caps}.querySelector('.cap-grav').classList.contains('grav-critique')"))
    await p.js(f"(() => {{ const m = {caps}; const s = m.querySelector('.cap-search'); s.value = ''; "
               "s.dispatchEvent(new Event('input')); const t = m.querySelector('.cap-sort'); t.value = 'az'; "
               "t.dispatchEvent(new Event('change')); m.querySelector('.cap-group[data-group=claudeai]').open = true; })()")
    az = await p.js(f"[...{caps}.querySelectorAll('.cap-group[data-group=claudeai] .cap-nom')].map(n => n.textContent)")
    check(f"tri A → Z : {az}", az == sorted(az, key=str.casefold) and len(az) == 3)
    color = await p.js(f"""(() => {{ const u = {caps}.querySelector('.cap-etat');
        return [getComputedStyle(u).color, getComputedStyle({caps}.querySelector('.cap-nom')).color]; }})()""")
    check(f"état en gris, plus discret que le nom {color}", color[0] != color[1])
    # Lien « Ouvrir dans le Guide » du groupe claudeai → sujet « connecteurs »
    await p.js(f"{caps}.querySelector('.cap-group[data-group=claudeai] > .cap-link').click()")
    to_guide = await p.until("document.querySelector('.modal.wide.guide .guide-topic[aria-current=true]')?.dataset.topic === 'connecteurs'", 4)
    check("« Ouvrir dans le Guide » : guide ouvert sur « connecteurs », page des capacités fermée",
          to_guide and await p.js(f"!{caps}"))
    await p.js("document.querySelector('.guide-capacites').click()")
    await p.until(f"{caps}?.querySelectorAll('.cap-group').length >= 15", 5)
    await p.js(f"{caps}.querySelector('.cap-group[data-group=reglages]').open = true")
    await p.js(f"[...{caps}.querySelectorAll('.cap-group[data-group=reglages] .cap-entry')]"
               ".find(e => e.querySelector('.cap-nom').textContent === 'Skills').querySelector('.cap-link').click()")
    to_set = await p.until("!!document.querySelector('.modal.wide.settings .settings-group[open] .setting[data-key=skills]')", 4)
    check("« Ouvrir les Réglages » (groupe Skills) : page Réglages, groupe Skills déplié", to_set)
    await p.key("Escape", vk=27)
    check("Échap ferme", await p.until("!document.querySelector('.modal-back')", 2))


async def check_heure_messages(p: Page) -> None:
    """23/10 (demande de l'utilisateur) : heure HH:MM sur chaque message du fil, envoyé et reçu, discrète
    (petite, gris des jetons), balise <time datetime>, hors de la sélection copiée."""
    r = await p.js("""(() => {
      // carte d'image IA (redessinée à chaque étape) exceptée
      const msgs = [...document.querySelectorAll('#transcript .msg:not(.image-job)')];
      const times = msgs.map(m => m.querySelector(':scope > time.msg-time'));
      const ok = times.filter(t => t && /^(\\d{2}\\/\\d{2} )?\\d{2}:\\d{2}$/.test(t.textContent) && !isNaN(Date.parse(t.dateTime)));
      const t0 = ok[0], cs = t0 && getComputedStyle(t0);
      const src = document.createElement('span'); src.style.color = 'var(--muted)'; document.body.append(src);
      const muted = getComputedStyle(src).color; src.remove();
      return { n: msgs.length, ok: ok.length, user: ok.some(t => t.closest('.msg.user')), astrel: ok.some(t => t.closest('.msg.astrel')),
               cards: ok.filter(t => t.closest('.msg.choice, .msg.media-msg')).length,
               size: cs && cs.fontSize, color: cs && cs.color, muted, select: cs && cs.userSelect, sample: t0 && t0.textContent };
    })()""")
    check(f"heure des messages : {r['ok']}/{r['n']} avec <time datetime> (ex. « {r['sample']} »), envoyés, reçus, "
          f"{r['cards']} cartes (choix, médias), "
          f"{r['size']}, gris des jetons ({r['color']}), hors sélection",
          r["n"] >= 2 and r["ok"] == r["n"] and r["user"] and r["astrel"] and r["size"] == "10px"
          and r["color"] == r["muted"] and r["select"] == "none")


async def check_audit(p: Page) -> None:
    """Page « Journal » (plan P3, lot F) sur la démo (`demo_audit`) : pastille, liste en
    textContent (une balise citée reste du texte), filtres par type, page suivante."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")
    await p.js("""window.__au = (s) => document.querySelector('.modal.audit ' + s);
        window.__aus = (s) => [...document.querySelectorAll('.modal.audit ' + s)];""")
    chip = await p.js("""(() => { const c = document.querySelector('#audit-chip');
        return { text: c?.textContent.trim(), icon: c?.querySelector('svg.ico')?.dataset.icon }; })()""")
    check(f"Journal : pastille « {chip['text']} » avec son icône", chip["text"] == "Journal" and chip["icon"] == "activity")
    await p.js("document.querySelector('#audit-chip').click()")
    opened = await p.until("__aus('.audit-item').length === 60", 4)
    first = (await sent("audit_get"))[-1] if await sent("audit_get") else {}
    o = await p.js("""(() => ({ title: document.querySelector('.modal.audit .modal-title').textContent,
        filters: __aus('.audit-filter').map(f => [f.textContent, f.getAttribute('aria-pressed')]),
        first: __au('.audit-item .audit-what').textContent, kind: __au('.audit-item .audit-kind').textContent,
        ok: __au('.audit-item .audit-ok')?.textContent, auth: __au('.audit-item .audit-auth')?.textContent,
        bad: document.querySelector('.modal.audit').querySelectorAll('img, script, iframe').length,
        more: !__au('.audit-more').hidden, status: __au('.audit-status').textContent }))()""")
    check(f"Journal : fenêtre « {o['title']} », 60 entrées, 8 filtres actifs (Levée 18/10, Mémoire 04/11), audit_get {{kinds, limit}}",
          opened and o["title"] == "Journal" and len(o["filters"]) == 8 and all(a == "true" for _, a in o["filters"])
          and first.get("limit") == 60 and len(first.get("kinds") or []) == 8)
    check("Journal : balise citée affichée en texte, jamais en élément",
          o["bad"] == 0 and "<img src=x onerror=alert(1)>" in o["first"] and o["kind"] == "Confirmations"
          and o["ok"] == "permis" and o["auth"] == "voix (oui pour 30 min)")
    await p.shot("18-journal.png")
    await p.js("__aus('.audit-more')[0].click()")
    more = await p.until("__aus('.audit-item').length === 75 && __au('.audit-more').hidden"
                         " && getComputedStyle(__au('.audit-more')).display === 'none'", 4)
    last = (await sent("audit_get"))[-1]
    check("Journal : « Plus anciennes » repart de la dernière entrée (before), puis disparaît",
          more and isinstance(last.get("before"), (int, float)))
    await p.js("__aus('.audit-filter').find(f => f.textContent === 'Refus').click()")
    await p.until("__aus('.audit-item').length === 60", 3)
    q = (await sent("audit_get"))[-1]
    check("Journal : un filtre retiré relance audit_get sans ce type",
          "refus" not in (q.get("kinds") or []) and len(q.get("kinds") or []) == 7 and not await p.js(
              "__aus('.audit-kind').some(k => k.textContent === 'Refus')"))
    await p.js("document.querySelector('.modal.audit .modal-x').click()")
    check("Journal : fermeture", await p.until("!document.querySelector('.modal.audit')", 2))


async def check_propositions(p: Page) -> None:
    """04/11 (T49) : page « Mémoire » sur la démo (`DemoPropositions`) : pastille visible seulement avec des
    propositions en attente, texte exact en textContent (YAML tel quel, balise inerte), Appliquer avec l'empreinte
    reçue, Refuser par la boîte du HUD (jamais prompt()), pastille cachée quand il n'en reste plus."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")
    chip = await p.js("""(() => { const c = document.querySelector('#propositions-chip');
        return { shown: !c.classList.contains('hidden') && c.getClientRects().length > 0,
                 badge: c.querySelector('#propositions-badge').textContent, icon: c.querySelector('svg.ico')?.dataset.icon }; })()""")
    check(f"Mémoire : pastille visible avec {chip['badge']} propositions en attente, icône livre",
          chip["shown"] and chip["badge"] == "2" and chip["icon"] == "book")
    await p.js("document.querySelector('#propositions-chip').click()")
    opened = await p.until("document.querySelectorAll('.modal.propositions .prop-item').length === 2", 4)
    o = await p.js("""(() => { const m = document.querySelector('.modal.propositions');
        const pre = m.querySelector('.prop-item .prop-texte');
        return { title: m.querySelector('.modal-title').textContent, text: pre.textContent,
                 bad: m.querySelectorAll('img, script, iframe').length, yaml: pre.textContent.startsWith('---\\nname:'),
                 add: m.querySelector('.prop-apply').textContent.trim(), apply: m.querySelectorAll('.prop-apply')[1].textContent.trim(),
                 type: m.querySelector('.prop-type').textContent, status: m.querySelector('.prop-status').textContent }; })()""")
    check(f"Mémoire : fenêtre « {o['title']} », 2 propositions, YAML et balise en texte inerte, « {o['add']} » / « {o['apply']} »",
          opened and o["title"] == "Propositions de mémoire" and o["yaml"] and "<img src=x>" in o["text"] and o["bad"] == 0
          and o["add"] == "Ajouter à la mémoire" and o["apply"] == "Appliquer" and o["type"] == "Nouvelle note")
    await p.shot("18b-memoire.png")
    await p.js("document.querySelector('.modal.propositions .prop-apply').click()")
    applied = await p.until("document.querySelectorAll('.modal.propositions .prop-item').length === 1", 4)
    a = (await sent("proposition_appliquer"))[-1] if await sent("proposition_appliquer") else {}
    check("Mémoire : Appliquer envoie proposition_appliquer avec proposition_id et l'empreinte affichée, la liste suit",
          applied and a.get("proposition_id") == "2026-11-03-utilisateur-contraintes-nouvelle"
          and a.get("empreinte") == "a1b2c3d4e5f60718" and a.get("id") != a.get("proposition_id"))
    await p.js("document.querySelector('.modal.propositions .prop-refuse').click()")
    modal = await p.until("!!document.querySelector('.modal:not(.wide) textarea[name=raison]')", 3)
    await p.js("""(() => { const ta = document.querySelector('.modal:not(.wide) textarea[name=raison]');
        ta.value = 'déjà dans la note'; ta.dispatchEvent(new Event('input')); return true; })()""")
    await p.js("document.querySelector('.modal:not(.wide) .modal-ok').click()")
    gone = await p.until("document.querySelectorAll('.modal.propositions .prop-item').length === 0", 4)
    r = (await sent("proposition_refuser"))[-1] if await sent("proposition_refuser") else {}
    hidden = await p.until("document.querySelector('#propositions-chip').classList.contains('hidden')", 3)
    check("Mémoire : Refuser par la boîte du HUD (motif), liste vide, pastille cachée",
          modal and gone and hidden and r.get("raison") == "déjà dans la note")
    await p.js("document.querySelector('.prop-filter:not(.on)').click()")
    both = await p.until("document.querySelectorAll('.modal.propositions .prop-item.done').length === 2", 4)
    check("Mémoire : « Toutes » montre les deux propositions traitées, sans bouton", both and not await p.js(
        "!!document.querySelector('.modal.propositions .prop-apply')"))
    await p.js("document.querySelector('.modal.propositions .modal-x').click()")
    check("Mémoire : fermeture", await p.until("!document.querySelector('.modal.propositions')", 2))


async def check_plans(p: Page) -> None:
    """Page « Plans » (15/10, docs/CONTRAT-PLANS-2026-10-15.md), sur la démo (`DemoPlans`, vrai
    module astrel.plans) : P1 à relire (remarque sur p2, réponse d'Astrel), P2 validé (auto),
    P3 rejeté. Commentaire par point, révision, validation, rejet, mode, état d'erreur."""
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)})")

    async def demo(cmd, **args):  # commande de la démo, sans passer par l'interface
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    await p.js("""window.__pl = (s) => document.querySelector('.modal.plans ' + s);
        window.__pls = (s) => [...document.querySelectorAll('.modal.plans ' + s)];
        window.__pt = (id) => __pl(`.plan-point[data-point="${id}"]`);
        window.__ctrlEnter = (e) => e.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true }));""")

    # ---------------------------------------------------------- pastille, ouverture
    chip = await p.js("""(() => ({ n: document.querySelector('#plans-badge').textContent,
        shown: !document.querySelector('#plans-badge').classList.contains('hidden'),
        attn: document.querySelector('#plans-chip').classList.contains('attn'),
        icon: document.querySelector('#plans-chip svg.ico')?.dataset.icon }))()""")
    check(f"bouton Plans : icône « {chip['icon']} », pastille « {chip['n']} » (1 plan à relire)",
          chip["icon"] == "plans" and chip["n"] == "1" and chip["shown"] and chip["attn"])
    n_get = len(await sent("plans_get"))
    await p.js("document.querySelector('#plans-chip').click()")
    opened = await p.until("!!__pl('.plan-doc[data-plan=\"P1\"]')", 4)
    o = await p.js("""(() => ({ title: document.querySelector('.modal.plans .modal-title').textContent,
        icon: document.querySelector('.modal.plans .modal-title svg.ico')?.dataset.icon,
        filters: __pls('.plans-filter').map(f => [f.dataset.filter, f.querySelector('.plans-filter-n').textContent, f.classList.contains('on')]),
        items: __pls('.plan-item').map(i => i.dataset.plan),
        mode: __pls('.plans-mode-opt').map(b => [b.textContent, b.getAttribute('aria-checked')]),
        status: __pl('.plans-status').classList.contains('hidden') }))()""")
    check(f"Plans : fenêtre large « {o['title']} », plans_get envoyé, liste « à relire » {o['items']}",
          opened and o["title"] == "Plans" and o["icon"] == "plans" and len(await sent("plans_get")) > n_get
          and o["items"] == ["P1"] and o["status"])
    check(f"filtres et compteurs {o['filters']}",
          o["filters"] == [["open", "1", True], ["validated", "1", False], ["rejected", "1", False]])
    check(f"sélecteur de mode en en-tête, Manuel par défaut {o['mode']}",
          o["mode"] == [["Manuel", "true"], ["Auto", "false"]])

    # ---------------------------------------------------------- le plan, point par point
    d = await p.js("""(() => { const doc = __pl('.plan-doc');
        return { sections: [...doc.querySelectorAll('.plan-section .plan-section-title')].map(h => h.textContent),
          points: [...doc.querySelectorAll('.plan-point')].map(li => li.dataset.point),
          buttons: doc.querySelectorAll('.plan-point .plan-comment-btn').length,
          p2: __pt('p2').querySelector('.plan-entry.utilisateur')?.textContent,
          code: __pt('p2').querySelectorAll('code').length,
          trap: __pt('p7').textContent, bad: document.querySelector('.modal.plans').querySelectorAll('img, script, iframe, svg:not(.ico)').length,
          astrel: doc.querySelector('.plan-thread .plan-entry.astrel')?.textContent,
          strong: doc.querySelectorAll('.plan-thread .plan-entry.astrel strong').length,
          request: doc.querySelector('.plan-request-text')?.textContent,
          validate: __pl('.plan-validate').textContent, vdis: __pl('.plan-validate').disabled,
          revise: __pl('.plan-revise').textContent, rdis: __pl('.plan-revise').disabled }; })()""")
    check(f"5 rubriques {d['sections']}, 9 points, un bouton « Commenter » sous chacun",
          d["sections"] == ["Étapes", "Fichiers", "Risques", "Lots", "Vérification"]
          and d["points"] == [f"p{i}" for i in range(1, 10)] and d["buttons"] == 9)
    check(f"remarque gardée sous son point p2, « non envoyée » ({(d['p2'] or '')[:60]}…)",
          d["p2"] and "prévenir la mutuelle" in d["p2"] and "non envoyée" in d["p2"])
    check("points en markdown sûr : code en ligne rendu, piège <img onerror> affiché en texte, aucune balise active",
          d["code"] == 1 and "<img src=x onerror=alert(1)>" in d["trap"] and d["bad"] == 0)
    check(f"fil du plan : réponse d'Astrel en markdown, émoji retiré ({(d['astrel'] or '')[-50:]!r})",
          d["astrel"] and "un seul lot" in d["astrel"] and d["strong"] == 1 and "✅" not in d["astrel"])
    check(f"demande rappelée, boutons « {d['validate']} » et « {d['revise']} » actifs",
          d["request"] and d["request"].startswith("Aide-moi à organiser le déménagement") and d["validate"] == "Valider la version 1"
          and not d["vdis"] and d["revise"] == "Demander une révision (1)" and not d["rdis"])
    found = await p.js(EMOJI_DOM_JS)
    check(f"page Plans : aucun émoji dans le DOM ({found[:5]})", found == [])
    await p.shot("14-plans.png")

    # ---------------------------------------------------------- commentaire sur un point
    await p.js("__pt('p1').querySelector('.plan-comment-btn').click()")
    ok = await p.until("document.activeElement === __pt('p1').querySelector('textarea')", 2)
    check("« Commenter » ouvre l'éditeur sous le point, focus dedans", ok)
    await p.send("Input.insertText", text="Ajoute aussi le raccourci clavier")
    await demo("plans_get")  # le cœur republie la liste pendant la frappe : rien ne doit se perdre
    await asyncio.sleep(0.4)
    kept = await p.js("""(() => { const t = __pt('p1').querySelector('textarea');
        return { v: t?.value, focus: document.activeElement === t }; })()""")
    check(f"nouveau rendu pendant la frappe : texte et focus gardés ({kept})",
          kept["v"] == "Ajoute aussi le raccourci clavier" and kept["focus"])
    await p.shot("15-plans-commentaire.png")
    n = len(await sent("plan_comment"))
    await p.js("__ctrlEnter(__pt('p1').querySelector('textarea'))")
    await p.until(f"window.__sent.filter(m => m.cmd === 'plan_comment').length > {n}", 3)
    last = (await sent("plan_comment"))[-1]
    check(f"Ctrl+Entrée : plan_comment avec plan_id (pas id) et le point ({ {k: last.get(k) for k in ('plan_id', 'point', 'ask')} })",
          last.get("plan_id") == "P1" and last.get("point") == "p1" and last.get("text") == "Ajoute aussi le raccourci clavier"
          and last.get("ask") is False and last.get("id") != "P1")
    check("remarque affichée sous p1, éditeur refermé, « Demander une révision (2) »", await p.until(
        "!!__pt('p1').querySelector('.plan-entry.utilisateur') && !__pt('p1').querySelector('textarea')"
        " && __pl('.plan-revise').textContent === 'Demander une révision (2)'", 3))
    await p.js("__pt('p3').querySelector('.plan-comment-btn').click()")
    await p.js("__pt('p3').querySelector('.plan-add').click()")
    check("remarque vide : message sous le champ, rien d'envoyé", await p.until(
        "!__pt('p3').querySelector('.plan-error').classList.contains('hidden')", 2) and len(await sent("plan_comment")) == n + 1)
    await p.js("__pt('p3').querySelector('.plan-cancel').click()")
    check("Annuler referme l'éditeur", await p.until("!__pt('p3').querySelector('textarea')", 2))

    # ---------------------------------------------------------- question à Astrel (fil général)
    await p.js("__pl('.plan-thread .plan-comment-btn').click()")
    await p.until("document.activeElement === __pl('.plan-thread textarea')", 2)
    await p.send("Input.insertText", text="Le badge compte-t-il les révisions ?")
    await p.js("__pl('.plan-thread .plan-ask').click()")
    await p.until(f"window.__sent.filter(m => m.cmd === 'plan_comment').length > {n + 1}", 3)
    last = (await sent("plan_comment"))[-1]
    check(f"« Poser la question » : plan_comment ask=true sans point ({last.get('ask')}, {last.get('point')!r})",
          last.get("plan_id") == "P1" and last.get("ask") is True and last.get("point") == "")
    check("réponse d'Astrel arrivée dans le fil du plan, question « envoyée »", await p.until(
        "__pls('.plan-thread .plan-entry.astrel').some(e => e.textContent.includes('(démo) Oui'))"
        " && __pls('.plan-thread .plan-entry.utilisateur').some(e => e.textContent.includes('badge') && e.textContent.includes('envoyée')"
        " && !e.textContent.includes('non envoyée'))", 4))

    # ---------------------------------------------------------- valider : confirmation, Échap annule
    n = len(await sent("plan_validate"))
    await p.js("__pl('.plan-validate').click()")
    conf = await p.until("document.querySelectorAll('.modal').length === 2", 2)
    c = await p.js("""(() => { const m = [...document.querySelectorAll('.modal')].find(x => !x.classList.contains('plans'));
        return { title: m.querySelector('.modal-title').textContent, items: [...m.querySelectorAll('.modal-items li')].map(l => l.textContent),
                 ok: m.querySelector('.modal-ok').textContent, focus: document.activeElement?.textContent }; })()""")
    # modal.js : sans `danger`, le focus va au bouton principal (Annuler seulement pour une action dangereuse)
    check(f"Valider : confirmation par-dessus « {c['title']} », {c['items']}, focus « {c['focus']} »",
          conf and c["title"] == "Valider le plan P1 ?" and c["ok"] == "Valider et lancer"
          and c["items"] == ["2 remarques non envoyées seront jointes à la consigne du codeur"]
          and c["focus"] == "Valider et lancer")
    await p.shot("16-plans-valider.png")
    await p.key("Escape", vk=27)
    check("Échap ferme la confirmation seule, rien de validé", await p.until(
        "document.querySelectorAll('.modal').length === 1 && !!__pl('.plan-doc')", 2) and len(await sent("plan_validate")) == n)

    # ---------------------------------------------------------- révision : version 2
    await p.js("__pl('.plan-revise').click()")
    await p.until("window.__sent.some(m => m.cmd === 'plan_revise')", 3)
    last = (await sent("plan_revise"))[-1]
    rev = await p.until("__pl('.plan-head .plan-pill')?.textContent === 'en révision' && __pl('.plan-validate').disabled", 3)
    check(f"révision demandée ({last.get('plan_id')}) : « en révision », validation désactivée", last.get("plan_id") == "P1" and rev)
    v2 = await p.until("__pl('.plan-validate')?.textContent === 'Valider la version 2' && !__pl('.plan-validate').disabled", 6)
    t = await p.js("""(() => ({ toast: [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Plan P1 : version 2 à relire')),
        points: __pls('.plan-point').length, old: __pls('.plan-thread .plan-entry.utilisateur .plan-entry-meta').filter(m => m.textContent.includes('version 1')).length,
        meta: __pl('.plan-meta').textContent }))()""")
    check(f"version 2 republiée : toast, {t['points']} points, remarques de la v1 au fil général ({t['old']}), « {t['meta'][:40]}… »",
          v2 and t["toast"] and t["points"] == 10 and t["old"] == 3 and "version 2" in t["meta"])
    await p.shot("17-plans-revision.png")

    # ---------------------------------------------------------- valider la version 2
    await p.js("__pl('.plan-validate').click()")
    await p.until("document.querySelectorAll('.modal').length === 2", 2)
    await p.js("[...document.querySelectorAll('.modal')].find(x => !x.classList.contains('plans')).querySelector('.modal-ok').click()")
    await p.until("window.__sent.some(m => m.cmd === 'plan_validate')", 3)
    last = (await sent("plan_validate"))[-1]
    check(f"plan_validate envoyé avec la version AFFICHÉE ({last.get('plan_id')}, v{last.get('version')})",
          last.get("plan_id") == "P1" and last.get("version") == 2)
    check("validé : filtre « Validés », pastille « validé », note, plus de boutons, pastille du HUD éteinte", await p.until(
        "__pl('.plans-filter.on')?.dataset.filter === 'validated' && __pl('.plan-head .plan-pill')?.textContent === 'validé'"
        " && __pl('.plan-note')?.textContent.startsWith('Validé par toi') && __pl('.plans-actions').classList.contains('hidden')"
        " && document.querySelector('#plans-badge').classList.contains('hidden')", 3))

    # ---------------------------------------------------------- nouveau plan en direct, rejet avec motif
    await demo("demo_plan")
    ok = await p.until("[...document.querySelectorAll('.toast.clickable')].some(t => t.textContent.includes('Nouveau plan P4 à relire'))"
                       " && document.querySelector('#plans-badge').textContent === '1'", 3)
    check("nouveau plan déposé : toast cliquable et pastille 1", ok)
    await p.js("[...document.querySelectorAll('.toast.clickable')].find(t => t.textContent.includes('Nouveau plan P4')).click()")
    check("clic sur le toast : P4 affiché dans « À relire »", await p.until(
        "__pl('.plans-filter.on')?.dataset.filter === 'open' && !!__pl('.plan-doc[data-plan=\"P4\"]')", 3))
    await p.js("__pl('.plan-reject').click()")
    await p.until("!!document.querySelector('.modal.danger textarea')", 2)
    await p.js("document.querySelector('.modal.danger textarea').focus()")
    await p.send("Input.insertText", text="Pas de fête pour l'instant")
    await p.js("document.querySelector('.modal.danger .modal-ok').click()")
    await p.until("window.__sent.some(m => m.cmd === 'plan_reject')", 3)
    last = (await sent("plan_reject"))[-1]
    check(f"Rejeter : boîte rouge avec motif, plan_reject envoyé ({last.get('plan_id')}, {last.get('reason')!r})",
          last.get("plan_id") == "P4" and last.get("reason") == "Pas de fête pour l'instant")
    check("rejeté : filtre « Rejetés », motif affiché", await p.until(
        "__pl('.plans-filter.on')?.dataset.filter === 'rejected'"
        " && __pl('.plan-note')?.textContent === \"Rejeté : Pas de fête pour l'instant\"", 3))

    # ---------------------------------------------------------- mode Auto : validé dès le dépôt
    await p.js("__pl('.plans-mode-opt.m-auto').click()")
    await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'plan_mode')", 3)
    last = [m for m in await sent("settings_set") if m.get("key") == "plan_mode"][-1]
    check(f"Auto : settings_set plan_mode={last.get('value')}, sélecteur basculé",
          last.get("value") == "auto" and await p.until("__pl('.plans-mode-opt.m-auto').getAttribute('aria-checked') === 'true'", 3))
    await demo("demo_plan")
    ok = await p.until("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Nouveau plan P5 (mode automatique)'))"
                       " && document.querySelector('#plans-badge').classList.contains('hidden')", 3)
    await p.js("__pl('.plans-filter[data-filter=validated]').click()")
    auto = await p.js("__pls('.plan-item').map(i => [i.dataset.plan, i.querySelector('.plan-item-meta').textContent.includes('validé automatiquement')])")
    check(f"plan déposé en Auto : validé seul, sans pastille ({auto})", ok and ["P5", True] in auto and ["P1", False] in auto)
    await p.js("__pl('.plans-mode-opt.m-manual').click()")
    await p.until("__pl('.plans-mode-opt.m-manual').getAttribute('aria-checked') === 'true'", 3)

    # ---------------------------------------------------------- cœur injoignable
    await p.js("document.querySelector('.modal.plans .modal-x').click()")
    await p.until("!document.querySelector('.modal.plans')", 2)
    await demo("demo_plans_offline", on=True)
    await p.js("document.querySelector('#plans-chip').click()")
    err = await p.until("__pl('.plans-status.err')?.textContent === 'Plans indisponibles : (démo) cœur injoignable'", 4)
    check("plans_get refusé : message d'erreur, la liste connue reste", err and await p.js("__pls('.plan-item').length >= 1"))
    await p.js("__pl('.plans-filter[data-filter=rejected]').click()")
    await p.js("__pl('.plan-item[data-plan=\"P3\"]').click()")
    box = await p.until("(__pl('.plans-load-error')?.textContent || '').includes('Plan P3 indisponible : (démo) cœur injoignable')"
                        " && !!__pl('.plans-retry')", 4)
    check("plan non chargé : erreur dans le panneau et bouton « Réessayer »", box)
    await p.shot("18-plans-hors-ligne.png")
    await demo("demo_plans_offline", on=False)
    await p.js("__pl('.plans-retry').click()")
    check("Réessayer : P3 chargé, motif du rejet, titre sans émoji", await p.until(
        "__pl('.plan-note')?.textContent === 'Rejeté : Trop gros pour ce mois-ci : on garde la cuisine actuelle.'"
        " && __pl('.plan-title')?.textContent === 'Refaire toute la cuisine'", 4))

    await p.key("Escape", vk=27)
    check("Échap ferme la page Plans", await p.until("!document.querySelector('.modal.plans')", 2))
    dialogs = [e for e in p.events if e["method"] == "Page.javascriptDialogOpening"]
    check("aucune boîte alert() déclenchée par un plan", not dialogs and not await p.js("window.__pwned"))


# 15/10 (demande : les émojis faisaient « généré par IA ») : icônes vectorielles, hud/src/icons.js.
# Émojis et pictogrammes (dont ▶ ⏸ ⚠ ⚙ ■ ◉ ☰) cherchés dans le texte ET les infobulles du DOM.
EMOJI_DOM_JS = r"""(() => {
    const re = /[\p{Extended_Pictographic}\u2300-\u23FF\u25A0-\u25FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]/u;
    const found = [];
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode())
      if (re.test(n.nodeValue)) found.push((n.parentElement?.id || n.parentElement?.className || '?') + ' : ' + n.nodeValue.trim().slice(0, 40));
    for (const e of document.querySelectorAll('[title], [aria-label], [placeholder]'))
      for (const a of ['title', 'aria-label', 'placeholder'])
        if (re.test(e.getAttribute(a) || '')) found.push(`${e.id || e.className} @${a}`);
    return found;
})()"""
EMOJI_SRC = re.compile("[\u2300-\u23FF\u25A0-\u25FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\U0001F000-\U0001FAFF]")


async def check_icones(p: Page) -> None:
    """Plus d'émojis : icônes SVG en trait fin (currentColor, taille homogène), micro barré
    rouge quand il est coupé, émojis du texte venu du serveur retirés à l'affichage seulement."""
    hud = ROOT / "hud"
    bad = []
    for f in [hud / "index.html", hud / "styles.css", *sorted((hud / "src").glob("*.js"))]:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if EMOJI_SRC.search(line):
                bad.append(f"{f.name}:{i}")
    check(f"sources du HUD (index.html, styles.css, src/*.js) sans émoji ({bad[:5]})", not bad)

    found = await p.js(EMOJI_DOM_JS)
    check(f"fin du parcours : aucun émoji dans le DOM du HUD ({found[:5]})", found == [])

    ic = await p.js("""(() => {
        const all = [...document.querySelectorAll('svg.ico')];
        const size = (s) => [...document.querySelectorAll(s)].map(e => Math.round(e.getBoundingClientRect().width));
        return { n: all.length, left: document.querySelectorAll('i[data-icon]').length,
                 stroke: all.filter(s => s.getAttribute('stroke') !== 'currentColor' || s.getAttribute('fill') !== 'none'
                                         || s.getAttribute('viewBox') !== '0 0 24 24').length,
                 composer: [...new Set(size('#composer .round svg.ico'))], chips: [...new Set(size('#topbar .chip:not([hidden], .hidden) svg.ico'))],  // 04/11 : « Mémoire » caché sans proposition
                 html: [...document.querySelectorAll('svg.ico')].some(s => s.querySelector(':not(path, rect, circle)')) };
    })()""")
    check(f"{ic['n']} icônes SVG (trait currentColor, grille 24), plus aucune marque i[data-icon] ({ic['left']})",
          ic["n"] >= 25 and ic["left"] == 0 and ic["stroke"] == 0 and not ic["html"])
    check(f"taille homogène : barre du bas {ic['composer']} px, boutons du haut {ic['chips']} px",
          ic["composer"] == [17] and ic["chips"] == [14])

    # ---------------------------------------------------------- micro : comme le « muet » d'un appel
    async def mic():
        await p.js(SETTLE_JS)  # P35 : couleur Premium en transition
        return await p.js("""(() => { const b = document.querySelector('#mic-btn');
            return { muted: b.classList.contains('muted'), icon: b.querySelector('svg.ico')?.dataset.icon,
                     color: getComputedStyle(b).color, pressed: b.getAttribute('aria-pressed'), title: b.title }; })()""")
    before = await mic()
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'toggle' }))")  # la démo publie `muted` comme le cœur
    await p.until("document.querySelector('#mic-btn').classList.contains('muted')", 3)
    off = await mic()
    await p.shot("13-micro-coupe.png")
    check(f"micro actif : icône « {before['icon']} », infobulle « {before['title'][:40]}… »",
          not before["muted"] and before["icon"] == "mic" and before["pressed"] == "false" and before["title"].startswith("Micro actif"))
    check(f"micro coupé : icône barrée « {off['icon']} » en rouge ({off['color']}), aria-pressed {off['pressed']}",
          off["muted"] and off["icon"] == "mic-off" and off["color"] == await p.js(f"{TOKEN_JS}('--red')") and off["pressed"] == "true"
          and off["title"].startswith("Micro coupé"))
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'toggle' }))")
    check("micro réactivé : icône normale", await p.until(
        "!document.querySelector('#mic-btn').classList.contains('muted')"
        " && document.querySelector('#mic-btn svg.ico').dataset.icon === 'mic'", 3))

    # ---------------------------------------------------------- texte du serveur : émojis retirés à l'affichage
    srv = await p.js("""(async () => {
        const m = [...document.querySelectorAll('.msg.astrel')].find(x => x.textContent.includes('Trois mails ce matin'));
        if (!m) return null;
        let got = null;
        navigator.clipboard.writeText = async (t) => { got = t; };
        m.querySelector('.copy-btn').click();
        await new Promise(r => setTimeout(r, 100));
        const act = [...document.querySelectorAll('#activity-list .act')].map(a => a.textContent).find(t => t.includes('assurances habitation'));
        return { shown: m.querySelector('.md').textContent, raw: got, act };
    })()""")
    check(f"réponse affichée sans émoji, copie brute intacte ({srv and srv['shown']!r})",
          srv and srv["shown"] == ("Trois mails ce matin, dont la facture d'électricité à régler avant vendredi. "
                                   "À Nantes, dix-sept degrés et un ciel dégagé ce soir.")
          and "✅" in (srv["raw"] or ""))
    check(f"notice du serveur sans émoji dans l'activité ({srv and srv['act']!r})",
          srv and srv["act"] and "Terminé : Comparatif des assurances habitation" in srv["act"] and not EMOJI_SRC.search(srv["act"]))


FIL_JS = """(() => { const t = document.querySelector('#transcript'), tb = t.getBoundingClientRect();
    const ms = [...t.querySelectorAll('.msg:not(.hidden)')];
    const first = ms[0] && ms[0].getBoundingClientRect(), last = ms.at(-1) && ms.at(-1).getBoundingClientRect();
    return { n: ms.length, ch: t.clientHeight, sh: t.scrollHeight, st: Math.round(t.scrollTop), fade: t.classList.contains('fade'),
             mask: getComputedStyle(t).maskImage, max: parseFloat(getComputedStyle(t).maxHeight),
             firstTop: first ? Math.round(first.top - tb.top) : null, lastBottom: last ? Math.round(tb.bottom - last.bottom) : null }; })()"""

CUTS_JS = """(() => { const q = (s) => document.querySelector(s);
    """ + SETTLE_STMT + """;  // P35 : couleurs Premium en transition
    const b = (s) => ({ on: q(s).classList.contains('muted'), pressed: q(s).getAttribute('aria-pressed'),
                        icon: q(s).querySelector('svg.ico')?.dataset.icon, color: getComputedStyle(q(s)).color, title: q(s).title });
    const note = q('#usage-note');
    return { mic: b('#mic-btn'), voice: b('#voice-btn'), all: b('#mute-all-btn'), usage: document.documentElement.dataset.usage,
             note: note.classList.contains('hidden') ? '' : note.textContent,
             seg: q('#usage button.on')?.dataset.mode }; })()"""


async def check_fil_coupures_pieces(p: Page) -> None:
    """16/10, trois demandes de l'utilisateur : (1) le premier message du fil n'est plus rogné
    (le fondu du haut ne vient qu'avec des messages cachés au-dessus) ; (2) pièce collée
    puis phrase DITE : tout part ensemble ; (3) trois coupures (micro, voix, les deux)."""
    await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
    # ---------------------------------------------------------- (1) premier message d'un fil vide
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'channel_create', label: 'Essai seize' }))")
    ok = await p.until("[...document.querySelectorAll('.channel-tab')].some(t => t.textContent.includes('Essai seize'))", 4)
    await p.js("[...document.querySelectorAll('.channel-tab')].find(t => t.textContent.includes('Essai seize')).click()")
    await asyncio.sleep(0.4)
    empty = await p.js(FIL_JS)
    check(f"(1) canal neuf ouvert, fil vide ({empty['n']} message)", ok and empty["n"] == 0)
    long = "Tu peux reprendre là où tu en étais hier, et regarder le premier message en entier. " * 4
    await p.js(f"""(() => {{ const a = document.querySelector('#ask'); a.value = {json.dumps(long)}; a.dispatchEvent(new Event('input'));
        a.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }})); }})()""")
    await p.until("document.querySelectorAll('#transcript .msg:not(.hidden)').length >= 2", 4)
    await asyncio.sleep(0.4)
    f = await p.js(FIL_JS)
    await p.shot("16-premier-message.png")
    check(f"(1) premier message en entier : fil à sa taille ({f['ch']} px pour {f['sh']}), pas défilé, sans fondu "
          f"(classe {f['fade']}, masque {f['mask']}), haut du message à {f['firstTop']} px",
          f["sh"] <= f["ch"] + 1 and f["st"] == 0 and not f["fade"] and f["mask"] == "none" and f["firstTop"] >= 0)
    many = "\\n".join(f"ligne {i} d'un long message" for i in range(40))
    await p.js(f"""(() => {{ const a = document.querySelector('#ask'); a.value = "{many}";
        a.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }})); }})()""")
    await p.until("document.querySelectorAll('#transcript .msg:not(.hidden)').length >= 4", 4)
    await asyncio.sleep(0.5)
    g = await p.js(FIL_JS)
    await p.shot("16-fil-qui-deborde.png")
    check(f"(1) long fil : hauteur plafonnée ({g['ch']} px ≤ {g['max']:.0f}), défile, fondu du haut revenu, "
          f"dernier message visible (marge {g['lastBottom']} px)",
          g["ch"] <= g["max"] + 1 and g["sh"] > g["ch"] and g["fade"] and g["mask"] != "none" and g["lastBottom"] >= 0)

    # ---------------------------------------------------------- (2) pièce collée + phrase dite
    await p.js("""(() => { const dt = new DataTransfer(); dt.items.add(new File(['bonjour'], 'note.txt', { type: 'text/plain' }));
        document.querySelector('#ask').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); })()""")
    staged = await p.until("""window.__sent.some(m => m.cmd === 'attach_stage' && m.attachments.length === 1
        && m.attachments[0].name === 'note.txt' && m.attachments[0].id && m.attachments[0].data)""", 4)
    check("(2) pièce collée : affichée dans la barre ET déposée au cœur (attach_stage, avec son id)",
          staged and await p.js("document.querySelectorAll('#attachments .att').length === 1"))
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_voice', text: 'Lis ce fichier.' }))")
    gone = await p.until("document.querySelector('#attachments').classList.contains('hidden')", 4)
    last = await p.js("""(() => { const m = [...document.querySelectorAll('#transcript .msg.user:not(.hidden)')].at(-1);
        return { text: m.querySelector('span:not(.src):not(.att-chip)')?.textContent, chips: [...m.querySelectorAll('.att-chip')].map(c => c.textContent),
                 ph: document.querySelector('#ask').placeholder }; })()""")
    await p.shot("16-phrase-dite-avec-piece.png")
    check(f"(2) phrase dite : UN message avec la pièce ({last}), barre vidée",
          gone and last["text"] == "Lis ce fichier." and last["chips"] == ["note.txt"] and last["ph"].startswith("Demande à Astrel"))
    check("(2) barre vide : le dépôt du cœur est vidé aussi (attach_stage [])", await p.until(
        "window.__sent.filter(m => m.cmd === 'attach_stage').at(-1).attachments.length === 0", 3))

    # ---------------------------------------------------------- (3) trois coupures
    async def click_until(sel, cond):
        await p.js(f"document.querySelector('{sel}').click()")
        return await p.until(cond, 3)

    c0 = await p.js(CUTS_JS)
    check(f"(3) au départ rien de coupé, chaque bouton le montre ({c0['mic']['icon']}, {c0['voice']['icon']}, {c0['all']['icon']})",
          not any(c0[k]["on"] for k in ("mic", "voice", "all")) and c0["voice"]["icon"] == "volume"
          and c0["all"]["icon"] == "mute-all" and all(c0[k]["pressed"] == "false" for k in ("mic", "voice", "all")))
    ok = await click_until("#voice-btn", "document.querySelector('#voice-btn').classList.contains('muted')")
    c1 = await p.js(CUTS_JS)
    await p.shot("16-voix-coupee.png")
    check(f"(3) voix seule coupée = Silencieux : icône {c1['voice']['icon']} ambre ({c1['voice']['color']}), "
          f"pastille « {c1['seg']} », note « {c1['note'][:40]}… », micro toujours actif",
          ok and c1["usage"] == "silencieux" and c1["seg"] == "silencieux" and c1["voice"]["icon"] == "volume-off"
          and c1["voice"]["pressed"] == "true" and c1["voice"]["color"] == await p.js(f"{TOKEN_JS}('--amber')")
          and c1["note"].startswith("Silencieux : je t'écoute") and not c1["mic"]["on"] and not c1["all"]["on"])
    ok = await click_until("#mute-all-btn", "document.querySelector('#mute-all-btn').classList.contains('muted')"
                           " && document.querySelector('#mic-btn').classList.contains('muted')")
    c2 = await p.js(CUTS_JS)
    await p.shot("16-tout-coupe.png")
    check(f"(3) micro ET voix coupés : trois boutons barrés, note « {c2['note']} »",
          ok and c2["mic"]["icon"] == "mic-off" and c2["voice"]["on"] and c2["all"]["pressed"] == "true"
          and c2["all"]["color"] == await p.js(f"{TOKEN_JS}('--red')") and c2["note"].startswith("Micro et voix coupés")
          and c2["all"]["title"].startswith("Micro et voix coupés"))
    ok = await click_until("#mute-all-btn", "!document.querySelector('#mute-all-btn').classList.contains('muted')"
                           " && !document.querySelector('#mic-btn').classList.contains('muted')"
                           " && document.documentElement.dataset.usage === 'conversation'")
    c3 = await p.js(CUTS_JS)
    check("(3) tout réactivé d'un clic : micro, voix, note retirée, pastille Conversation",
          ok and not c3["voice"]["on"] and c3["note"] == "" and c3["seg"] == "conversation")
    cuts = await p.js("window.__sent.filter(m => ['voice_mute', 'mute_all'].includes(m.cmd)).map(m => [m.cmd, m.on])")
    check(f"(3) commandes envoyées au cœur avec l'état voulu ({cuts})",
          cuts == [["voice_mute", True], ["mute_all", True], ["mute_all", False]])
    # Micro coupé seul puis voix coupée seule : « les deux » s'allume de lui-même (état déduit).
    await click_until("#mic-btn", "document.querySelector('#mic-btn').classList.contains('muted')")
    ok = await click_until("#voice-btn", "document.querySelector('#mute-all-btn').classList.contains('muted')")
    check("(3) micro puis voix coupés un par un : « les deux » montre l'état réel", ok)
    await click_until("#mute-all-btn", "!document.querySelector('#mic-btn').classList.contains('muted')"
                      " && document.documentElement.dataset.usage === 'conversation'")


async def check_messages_non_envoyes(p: Page) -> None:
    """16/10 (bug des messages perdus) : une dictée et un message non transmis ne sont plus
    affichés comme envoyés ; « Envoyer »/« Renvoyer », « Dans la barre » ; fichier vide refusé."""
    UNSENT_JS = """((text) => { const m = [...document.querySelectorAll('#transcript .msg.user:not(.hidden)')]
        .find(x => x.textContent.includes(text));
        return m && { unsent: m.classList.contains('unsent'), resent: m.classList.contains('resent'),
          note: m.querySelector('.unsent-note')?.textContent || '',
          buttons: [...m.querySelectorAll('.unsent-box button')].map(b => b.textContent.trim()),
          border: getComputedStyle(m).borderTopStyle }; })"""
    # 20/10 (demande : « ça me l'écrit à la fois dans l'autre chat et dans un truc à part ») : une
    # dictée PARTIE disparaît du fil (main.js markResent), y compris au rechargement ; il ne reste
    # que le message envoyé. Toutes les bulles visibles qui contiennent le texte, avec leur état.
    ALL_JS = """((text) => [...document.querySelectorAll('#transcript .msg.user:not(.hidden)')]
        .filter(x => x.textContent.includes(text)).map(m => ({ unsent: m.classList.contains('unsent'),
          resent: m.classList.contains('resent'), buttons: m.querySelectorAll('.unsent-box button').length,
          pen: !!m.querySelector('.unsent-note') })))"""

    def only_sent(found) -> bool:  # une seule bulle : le message envoyé, sans marque ni bouton
        return len(found) == 1 and not any(found[0][k] for k in ("unsent", "resent", "buttons", "pen"))
    # 17/10 (bug de l'utilisateur : lien collé puis dictée, Entrée a envoyé le lien seul) : la
    # dictée s'écrit à la SUITE de la barre, sans partir ; Entrée envoie le tout en UN message.
    ASK = "document.querySelector('#ask')"
    await p.js(f"(() => {{ {ASK}.value = 'https://exemple.fr/video'; {ASK}.dispatchEvent(new Event('input')); }})()")
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_dictee', text: 'Résume cette vidéo.' }))")
    await p.until(f"{ASK}.value.includes('Résume cette vidéo.')", 4)
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_dictee', text: 'Et dis-moi ce que tu en penses.' }))")
    await p.until(f"{ASK}.value.includes('Et dis-moi')", 4)
    v = await p.js(f"{ASK}.value")
    b = await p.js(f"{UNSENT_JS}('Résume cette vidéo.')")
    check(f"dictée avec un lien dans la barre : ajoutée à la suite, sans partir ({v!r}, {b})",
          v == "https://exemple.fr/video Résume cette vidéo. Et dis-moi ce que tu en penses."
          and b and b["note"] == "Dictée : dans la barre, Entrée l'envoie avec le reste" and b["buttons"] == [])
    n = await p.js("window.__sent.filter(m => m.cmd === 'say').length")
    await p.js(f"{ASK}.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true, cancelable: true }}))")
    ok = await p.until(f"window.__sent.filter(m => m.cmd === 'say').length > {n}", 3)
    says = await p.js("window.__sent.filter(m => m.cmd === 'say')")
    await p.until(f"{ASK}.value === ''", 3)
    await p.until(f"{ALL_JS}('Résume cette vidéo.').length === 1 && {ALL_JS}('Et dis-moi ce que').length === 1", 3)
    b1 = await p.js(f"{ALL_JS}('Résume cette vidéo.')")
    b2 = await p.js(f"{ALL_JS}('Et dis-moi ce que tu en penses.')")
    whole = await p.js(f"{ALL_JS}({json.dumps(v)})")
    check(f"Entrée : lien + dictées partent en UN seul message, les bulles des dictées parties quittent le fil ({says[n:]}, {b1}, {b2})",
          ok and len(says) == n + 1 and says[-1]["text"] == v and len(says[-1].get("resend_of") or []) == 2
          and only_sent(b1) and only_sent(b2) and only_sent(whole) and await p.js(f"{ASK}.value") == "")
    # 29/10 : message venu de WhatsApp = icône du téléphone (pas le micro), jamais « pas envoyé »
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_whatsapp', text: 'Envoyé depuis mon téléphone.' }))")
    ok = await p.until("[...document.querySelectorAll('#transcript .msg.user')].some(x => x.textContent.includes('Envoyé depuis mon téléphone.'))", 4)
    wa = await p.js("""(() => { const m = [...document.querySelectorAll('#transcript .msg.user')].find(x => x.textContent.includes('Envoyé depuis mon téléphone.'));
        return { icon: m?.querySelector('.src svg')?.getAttribute('data-icon'), unsent: !!m?.querySelector('.unsent-box') }; })()""")
    check(f"message venu de WhatsApp : icône « {wa['icon']} », rien de « pas envoyé »", ok and wa["icon"] == "smartphone" and not wa["unsent"])
    # Barre vidée à la main : la dictée qui y était redevient « pas envoyée » (rien de perdu).
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_dictee', text: 'Phrase dictée du seize.' }))")
    await p.until(f"{ASK}.value === 'Phrase dictée du seize.'", 4)
    await p.js(f"(() => {{ {ASK}.value = ''; {ASK}.dispatchEvent(new Event('input')); }})()")
    d = await p.js(f"{UNSENT_JS}('Phrase dictée du seize.')")
    await p.shot("16-dictee-non-envoyee.png")
    check(f"dictée : marquée non envoyée ({d})",
          d and d["unsent"] and d["note"] == "Dictée : pas envoyée à Astrel" and d["buttons"] == ["Envoyer", "Dans la barre"]
          and d["border"] == "dashed")
    await p.js("""(() => { const a = document.querySelector('#ask'); a.value = 'Lien : https://exemple.fr';
        const m = [...document.querySelectorAll('#transcript .msg.user')].find(x => x.textContent.includes('Phrase dictée du seize.'));
        [...m.querySelectorAll('.unsent-box button')].find(b => b.textContent.includes('Dans la barre')).click(); })()""")
    v = await p.js("document.querySelector('#ask').value")
    check(f"« Dans la barre » : la dictée rejoint ce qui est tapé ({v!r})", v == "Lien : https://exemple.fr Phrase dictée du seize.")
    await p.js("(() => { const a = document.querySelector('#ask'); a.value = ''; a.dispatchEvent(new Event('input')); })()")
    n = await p.js("window.__sent.filter(m => m.cmd === 'say').length")
    await p.js("""(() => { const m = [...document.querySelectorAll('#transcript .msg.user')].find(x => x.textContent.includes('Phrase dictée du seize.'));
        [...m.querySelectorAll('.unsent-box button')].find(b => b.textContent.includes('Envoyer')).click(); })()""")
    ok = await p.until(f"window.__sent.filter(m => m.cmd === 'say').length > {n}", 3)
    last = await p.js("window.__sent.filter(m => m.cmd === 'say').pop()")
    await p.until(f"(() => {{ const f = {ALL_JS}('Phrase dictée du seize.'); return f.length === 1 && !f[0].unsent; }})()", 3)
    d2 = await p.js(f"{ALL_JS}('Phrase dictée du seize.')")
    check(f"« Envoyer » : commande say avec le texte dicté, la bulle de la dictée quitte le fil ({d2})",
          ok and last["text"] == "Phrase dictée du seize." and only_sent(d2))
    check(f"« Envoyer » : la commande say nomme le message renvoyé ({last.get('resend_of')!r})",
          bool(last.get("resend_of")))

    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_unsent', text: 'Message jamais reçu.' }))")
    await p.until("document.querySelector('#transcript .msg.user.unsent') !== null", 4)
    u = await p.js(f"{UNSENT_JS}('Message jamais reçu.')")
    check(f"message non transmis : « Non envoyé » + Renvoyer ({u})",
          u and u["unsent"] and u["note"] == "Non envoyé à Astrel" and u["buttons"] == ["Renvoyer"])

    before = await p.js("document.querySelectorAll('#attachments .att').length")
    await p.js("""(() => { const dt = new DataTransfer(); dt.items.add(new File([], 'vide.txt', { type: 'text/plain' }));
        dt.items.add(new File(['ok'], 'plein.txt', { type: 'text/plain' }));
        document.querySelector('#ask').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); })()""")
    await p.until(f"document.querySelectorAll('#attachments .att').length > {before}", 3)
    await asyncio.sleep(0.3)
    names = await p.js("[...document.querySelectorAll('#attachments .att-name')].map(x => x.textContent)")
    toast = await p.js("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('vide.txt : fichier vide'))")
    check(f"fichier vide refusé dans la barre, l'autre gardé ({names}, avertissement {toast})",
          len(names) == before + 1 and any("plein.txt" in x for x in names) and not any("vide.txt" in x for x in names) and toast)
    await p.js("document.querySelectorAll('#attachments .att .close').forEach(b => b.click())")

    # Relecture du 16/10 : un message de mission (ou réflexe, plan) non transmis n'offre pas « Renvoyer ».
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_unsent', text: 'Tour de mission perdu.', source: 'mission' }))")
    await p.until("[...document.querySelectorAll('#transcript .msg.user.unsent')].some(x => x.textContent.includes('Tour de mission perdu.'))", 4)
    u2 = await p.js(f"{UNSENT_JS}('Tour de mission perdu.')")
    check(f"message de mission non transmis : « Non envoyé », sans « Renvoyer » ({u2})",
          u2 and u2["unsent"] and u2["note"] == "Non envoyé à Astrel" and u2["buttons"] == [])

    # Pièces parties avec une phrase dite dont l'envoi a échoué : elles reviennent dans la barre.
    await p.until("document.querySelectorAll('#attachments .att').length === 0", 3)
    await p.js("""(() => { const dt = new DataTransfer(); dt.items.add(new File(['ok'], 'rendue.txt', { type: 'text/plain' }));
        document.querySelector('#ask').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); })()""")
    await p.until("document.querySelectorAll('#attachments .att').length === 1", 3)
    await p.until("window.__sent.some(m => m.cmd === 'attach_stage' && m.attachments?.some(a => a.name === 'rendue.txt'))", 3)
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_voice', text: 'Regarde la pièce.' }))")
    gone = await p.until("document.querySelectorAll('#attachments .att').length === 0", 3)
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_unsent', text: 'Phrase avec pièce perdue.', with_attachments: true }))")
    back = await p.until("[...document.querySelectorAll('#attachments .att-name')].some(x => x.textContent.includes('rendue.txt'))", 3)
    toast = await p.js("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('remise(s) dans la barre'))")
    check(f"envoi raté : la pièce partie revient dans la barre, avec un avertissement (partie {gone}, revenue {back}, avertissement {toast})",
          gone and back and toast)
    await p.js("document.querySelectorAll('#attachments .att .close').forEach(b => b.click())")

    # HUD rechargé : la dictée envoyée ne réaffiche pas « Envoyer » (pas de doublon), l'autre reste à renvoyer.
    await p.send("Page.reload")
    await p.until("[...document.querySelectorAll('#transcript .msg.user')].some(x => x.textContent.includes('Phrase dictée du seize.'))", 15)
    r1 = await p.js(f"{ALL_JS}('Phrase dictée du seize.')")
    r2 = await p.js(f"{UNSENT_JS}('Message jamais reçu.')")
    r3 = await p.js(f"{ALL_JS}('Et dis-moi ce que tu en penses.')")
    check(f"HUD rechargé : la dictée déjà envoyée ne revient pas dans le fil ({r1}), message perdu toujours « Renvoyer » ({r2})",
          only_sent(r1) and r2 and r2["unsent"] and r2["buttons"] == ["Renvoyer"])
    check(f"HUD rechargé : dictée partie avec le lien absente du fil, ni remise dans la barre ({r3})",
          only_sent(r3) and await p.js(f"{ASK}.value") == "")


async def check_collage_instantane(p: Page) -> None:
    """22/10 (demande : « le copié-collé de screen ou d'image copiée n'est pas instantané dans le
    chat ») : la vignette apparaît DANS LE MÊME tour que le collage (avant réduction et base64),
    marquée « préparation… », puis la pièce est déposée au cœur avec ses données ; Entrée pressée
    pendant la préparation attend la pièce au lieu de partir sans elle."""
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await p.js("document.querySelectorAll('#attachments .att .close').forEach(b => b.click())")
    # capture d'écran HiDPI (2880×1800) bruitée : la réduction prend un vrai temps
    PASTE = """(async () => { const c = document.createElement('canvas'); c.width = 2880; c.height = 1800;
        const g = c.getContext('2d'), img = g.createImageData(2880, 1800);
        for (let i = 0; i < img.data.length; i += 4) { img.data[i] = (i * 7) % 251; img.data[i + 1] = (i * 13) % 241; img.data[i + 2] = (i >> 5) % 255; img.data[i + 3] = 255; }
        g.putImageData(img, 0, 0);
        const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        const a = document.querySelector('#ask'); a.focus();
        const dt = new DataTransfer(); dt.items.add(new File([blob], 'Capture.png', { type: 'image/png' }));
        window.__stagedBefore = window.__sent.filter(m => m.cmd === 'attach_stage').length;
        window.__pasteT0 = performance.now();
        a.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
        const att = document.querySelector('#attachments .att');
        return { size: blob.size, n: document.querySelectorAll('#attachments .att').length, pending: !!att?.classList.contains('att-pending'),
                 busy: att?.getAttribute('aria-busy'), src: att?.querySelector('img')?.src.slice(0, 5) || '',
                 name: att?.querySelector('.att-name')?.textContent || '', shown: !document.querySelector('#attachments').classList.contains('hidden') }; })()"""
    r = await p.js(PASTE)
    check(f"collage : vignette affichée dans le même tour que le collage (PNG {r['size'] // 1024} Ko, source {r['src']}, "
          f"« {r['name']} », aria-busy={r['busy']})",
          r["n"] == 1 and r["shown"] and r["pending"] and r["busy"] == "true" and r["src"] == "blob:"
          and r["name"] == "Capture.png · préparation…")
    await p.shot("22-collage-en-preparation.png")
    ready = await p.until("""window.__sent.filter(m => m.cmd === 'attach_stage').slice(window.__stagedBefore)
        .some(m => m.attachments.length === 1 && m.attachments[0].data && m.attachments[0].name === 'Capture.jpg')""", 10)
    ms = await p.js("Math.round(performance.now() - window.__pasteT0)")
    d = await p.js("""(() => { const att = document.querySelector('#attachments .att'); const st = window.__sent.filter(m => m.cmd === 'attach_stage');
        return { pending: att.classList.contains('att-pending'), name: att.querySelector('.att-name').textContent,
                 kb: Math.round(st.at(-1).attachments[0].data.length * 3 / 4 / 1024),
                 emptyStaged: st.slice(window.__stagedBefore).some(m => m.attachments.some(a => !a.data)) }; })()""")
    check(f"collage : préparée en arrière-plan ({ms} ms, l'ancienne vignette attendait tout ça), déposée réduite "
          f"({d['kb']} Ko, « {d['name']} »), jamais déposée sans données",
          ready and not d["pending"] and d["name"].startswith("Capture.jpg · ") and d["kb"] <= 256 and not d["emptyStaged"])
    await p.shot("22-collage-pret.png")
    await p.js("document.querySelectorAll('#attachments .att .close').forEach(b => b.click())")
    await p.until("document.querySelectorAll('#attachments .att').length === 0", 3)

    # Entrée pressée pendant la préparation : la phrase attend la pièce et part AVEC ses données
    n_say = await p.js("window.__sent.filter(m => m.cmd === 'say').length")
    await p.js(PASTE)
    await p.js("""(() => { const a = document.querySelector('#ask'); a.value = 'Regarde cette capture';
        a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
    sent_ok = await p.until(f"window.__sent.filter(m => m.cmd === 'say').length > {n_say}", 10)
    say = await p.js("window.__sent.filter(m => m.cmd === 'say').at(-1)")
    atts = (say or {}).get("attachments") or []
    check(f"collage puis Entrée aussitôt : « {say and say.get('text')} » part avec la pièce prête "
          f"({[a.get('name') for a in atts]}, données {'présentes' if atts and atts[0].get('data') else 'ABSENTES'})",
          sent_ok and say.get("text") == "Regarde cette capture" and len(atts) == 1 and atts[0].get("data")
          and atts[0].get("name") == "Capture.jpg")
    await p.until("document.querySelectorAll('#attachments .att').length === 0", 4)


async def check_collage_fragilites(p: Page) -> None:
    """22/10 (testeur et relecteur du collage, journal collage-quota-2210 fiches 2 et 3) : (1) Entrée
    n'attend une pièce en préparation que 15 s, puis propose de la retirer et d'envoyer ou d'attendre
    encore, jamais de blocage ; (2) préparation ratée : le message ne part PAS sans l'image, il reste
    dans la barre et la pièce ratée est nommée ; (3) phrase DITE pendant la préparation : prévenu
    qu'elle est partie sans l'image ; (4) visionneuse ouverte sur l'original pendant la réduction :
    l'URL reste valide tant qu'elle est ouverte, libérée à la fermeture. Le décodage est simulé
    (createImageBitmap bloqué ou en échec), puis rétabli."""
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await p.js("document.querySelectorAll('#attachments .att .close').forEach(b => b.click()); "
               "document.querySelectorAll('.toast').forEach(t => t.remove()); "
               "window.__cib = window.__cib || window.createImageBitmap; true")
    paste = """(async (name) => { const c = document.createElement('canvas'); c.width = 64; c.height = 48;
        c.getContext('2d').fillRect(0, 0, 64, 48); const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        const a = document.querySelector('#ask'); a.focus();
        const dt = new DataTransfer(); dt.items.add(new File([blob], name, { type: 'image/png' }));
        a.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
        return document.querySelectorAll('#attachments .att-pending').length; })"""
    enter = "(() => { const a = document.querySelector('#ask'); a.value = %s; a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()"
    says = "window.__sent.filter(m => m.cmd === 'say').length"
    toasts = "[...document.querySelectorAll('.toast')].map(t => t.textContent).join(' | ')"

    # (2) préparation ratée + texte : rien ne part, texte gardé, pièce nommée
    # échec APRÈS l'appui sur Entrée (pièce encore en préparation au moment de l'envoi)
    await p.js("window.createImageBitmap = () => new Promise((_, no) => setTimeout(() => no(new Error('décodage impossible')), 800)), true")
    n0 = await p.js(says)
    await p.js(f"{paste}('Ratee.png')")
    await p.js(enter % json.dumps("Texte avec image ratée"))
    await asyncio.sleep(1.0)
    r = await p.js(f"({{ says: {says}, text: document.querySelector('#ask').value, toasts: {toasts},"
                   " sending: !!document.querySelector('#ask').dataset.sending, atts: document.querySelectorAll('#attachments .att').length })")
    check(f"collage raté + Entrée : rien d'envoyé ({r['says'] - n0}), texte gardé « {r['text']} », pièce nommée ({r['toasts'][:140]})",
          r["says"] == n0 and r["text"] == "Texte avec image ratée" and "« Ratee.png »" in r["toasts"]
          and "pas envoyé" in r["toasts"] and not r["sending"] and r["atts"] == 0)
    await p.js("document.querySelector('#ask').value = ''; document.querySelectorAll('.toast').forEach(t => t.remove()); true")

    # (1) préparation qui ne finit jamais : 15 s, puis la proposition ; « Attendre encore » puis « Retirer et envoyer »
    await p.js("window.createImageBitmap = () => new Promise(() => {}), true")
    n0 = await p.js(says)
    await p.js(f"{paste}('Bloquee.png')")
    t0 = time.monotonic()
    await p.js(enter % json.dumps("Texte avec image bloquée"))
    shown = await p.until("!!document.querySelector('.modal .modal-ok')", 25)
    waited = time.monotonic() - t0
    m = await p.js("({ title: document.querySelector('.modal .modal-title')?.textContent, ok: document.querySelector('.modal .modal-ok')?.textContent,"
                   f" cancel: document.querySelector('.modal .modal-cancel')?.textContent, says: {says} }})")
    check(f"collage bloqué + Entrée : proposition au bout de {waited:.1f} s « {m['title']} » [{m['ok']} | {m['cancel']}], rien d'envoyé",
          shown and 13 <= waited <= 25 and m["ok"] == "Retirer l'image et envoyer" and m["cancel"] == "Attendre encore"
          and m["says"] == n0)
    await p.shot("22-collage-bloque.png")
    await p.js("document.querySelector('.modal .modal-cancel').click()")
    await asyncio.sleep(0.3)
    r = await p.js(f"({{ says: {says}, text: document.querySelector('#ask').value, sending: !!document.querySelector('#ask').dataset.sending,"
                   " pending: document.querySelectorAll('#attachments .att-pending').length })")
    check(f"« Attendre encore » : rien d'envoyé, texte et pièce gardés, Entrée de nouveau libre ({r})",
          r["says"] == n0 and r["text"] == "Texte avec image bloquée" and not r["sending"] and r["pending"] == 1)
    await p.js(enter % json.dumps("Texte avec image bloquée"))
    await p.until("!!document.querySelector('.modal .modal-ok')", 25)
    await p.js("document.querySelector('.modal .modal-ok').click()")
    sent = await p.until(f"{says} > {n0}", 10)
    say = await p.js("window.__sent.filter(m => m.cmd === 'say').at(-1)")
    left = await p.js("document.querySelectorAll('#attachments .att').length")
    check(f"« Retirer l'image et envoyer » : « {say and say.get('text')} » part sans pièce, barre vidée ({left})",
          sent and say.get("text") == "Texte avec image bloquée" and not say.get("attachments") and left == 0)

    # (3) phrase dite pendant la préparation : prévenu, la pièce reste
    await p.js("document.querySelectorAll('.toast').forEach(t => t.remove()); true")
    await p.js(f"{paste}('EnCours.png')")
    await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'demo_voice', 'text': 'Regarde ça.'}))})")
    warned = await p.until("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('partie sans « EnCours.png »'))", 5)
    kept = await p.js("document.querySelectorAll('#attachments .att-pending').length")
    check(f"phrase dite pendant la préparation : prévenu ({warned}), pièce gardée dans la barre ({kept})", warned and kept == 1)
    await p.js("document.querySelectorAll('#attachments .att .close').forEach(b => b.click()); true")

    # (4) visionneuse ouverte sur l'original pendant la réduction
    await p.js("window.createImageBitmap = window.__cib; document.querySelectorAll('.toast').forEach(t => t.remove()); true")
    await p.js("""(async () => { const c = document.createElement('canvas'); c.width = 2880; c.height = 1800;
        const g = c.getContext('2d'), img = g.createImageData(2880, 1800);
        for (let i = 0; i < img.data.length; i += 4) { img.data[i] = (i * 7) % 251; img.data[i + 1] = (i * 13) % 241; img.data[i + 2] = (i >> 5) % 255; img.data[i + 3] = 255; }
        g.putImageData(img, 0, 0);
        const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        const a = document.querySelector('#ask'); a.focus();
        const dt = new DataTransfer(); dt.items.add(new File([blob], 'Grande.png', { type: 'image/png' }));
        a.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
        document.querySelector('#attachments .att-view').click(); return true; })()""")
    opened = await p.until("!!document.querySelector('.modal.viewer img.viewer-img')", 3)
    ready = await p.until("!document.querySelector('#attachments .att-pending') && !!document.querySelector('#attachments .att')", 20)
    v = await p.js("""(async () => { const img = document.querySelector('.modal.viewer img.viewer-img'), src = img?.getAttribute('src') || '';
        window.__viewedSrc = src;
        // fetch() d'une URL blob: est refusé par la CSP du HUD : la validité se lit en rechargeant l'image
        let alive = false; try { const im = new Image(); im.src = src; await im.decode(); alive = true; } catch { alive = false; }
        return { src: src.slice(0, 5), alive, shown: !!img && img.complete && img.naturalWidth > 0,
                 bar: document.querySelector('#attachments .att img')?.getAttribute('src') !== src }; })()""")
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal.viewer')", 3)
    await asyncio.sleep(0.2)
    freed = await p.js("(async () => { try { const im = new Image(); im.src = window.__viewedSrc; await im.decode(); return false; }"
                       " catch { return true; } })()")
    check(f"visionneuse ouverte pendant la réduction : image d'origine toujours valide ({v}), vignette de la barre "
          f"remplacée, original libéré à la fermeture ({freed})",
          opened and ready and v["src"] == "blob:" and v["alive"] and v["shown"] and v["bar"] and freed)
    await p.js("document.querySelectorAll('#attachments .att .close').forEach(b => b.click()); true")


async def check_visionneuse(p: Page) -> None:
    """19/10 (demande : « une fois l'image collée je ne peux pas l'ouvrir en grand ») : clic
    sur la miniature de la barre = image en grand ; Échap, clic à côté, croix ferment, et la
    barre de saisie garde son focus. 19/10 : après l'envoi, le fil montre une vignette de l'image
    (servie par le cœur, liste blanche runtime/attachments) qui ouvre aussi la visionneuse."""
    await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)

    async def mouse(x, y):  # vrai clic de souris (le focus suit, comme pour l'utilisateur)
        for kind in ("mousePressed", "mouseReleased"):
            await p.send("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    async def click(sel):
        r = await p.js(f"(() => {{ const b = document.querySelector('{sel}').getBoundingClientRect();"
                       " return [b.left + b.width / 2, b.top + b.height / 2]; })()")
        await mouse(*r)

    # image 1400×900 (plus grande que la place du fil) + un fichier texte, collés dans la barre en cours de frappe
    await p.js("""(async () => { const c = document.createElement('canvas'); c.width = 1400; c.height = 900;
        const g = c.getContext('2d'); g.fillStyle = '#2a7'; g.fillRect(0, 0, 1400, 900); g.fillStyle = '#fff'; g.fillRect(100, 100, 400, 300);
        const blob = await new Promise(r => c.toBlob(r, 'image/png'));
        const a = document.querySelector('#ask'); a.focus(); a.value = 'Regarde ça'; a.dispatchEvent(new Event('input'));
        const dt = new DataTransfer(); dt.items.add(new File([blob], 'capture.png', { type: 'image/png' }));
        dt.items.add(new File(['texte'], 'note-vue.txt', { type: 'text/plain' }));
        a.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); })()""")
    ok = await p.until("document.querySelectorAll('#attachments .att').length === 2 && !!document.querySelector('#attachments .att-view img')", 6)
    t = await p.js("""(() => { const b = [...document.querySelectorAll('#attachments .att-view')];
        return { n: b.length, tag: b[0]?.tagName, label: b[0]?.getAttribute('aria-label'), cursor: b[0] && getComputedStyle(b[0]).cursor,
                 src: b[0]?.querySelector('img').src.slice(0, 5) }; })()""")
    check(f"visionneuse : miniature cliquable (bouton « {t['label']} », curseur {t['cursor']}, source {t['src']}), "
          "aucune pour le fichier texte", ok and t["n"] == 1 and t["tag"] == "BUTTON" and t["label"] == "Voir capture.png en grand"
          and t["cursor"] == "zoom-in" and t["src"] == "blob:")
    await p.shot("19-visionneuse-avant.png")

    VIEW_JS = """(() => { const box = document.querySelector('.modal.viewer'); if (!box) return null;
        const img = box.querySelector('img.viewer-img'), r = img.getBoundingClientRect(), back = box.parentElement;
        return { role: box.getAttribute('role'), modal: box.getAttribute('aria-modal'), label: box.getAttribute('aria-label'),
                 alt: img.alt, src: img.src.slice(0, 5), done: img.complete && img.naturalWidth > 0, nw: img.naturalWidth,
                 l: r.left, t: r.top, r: r.right, b: r.bottom, w: innerWidth, h: innerHeight, bg: getComputedStyle(back).backgroundColor,
                 x: !!box.querySelector('.modal-x[aria-label="Fermer"]'), focusIn: box.contains(document.activeElement),
                 scripts: box.querySelectorAll('script, iframe, [onerror], [onload]').length }; })()"""
    OPEN = "!!document.querySelector('.modal.viewer img.viewer-img')?.complete"
    CLOSED = "!document.querySelector('.modal.viewer') && !document.querySelector('.modal-back')"
    FOCUS_ASK = "document.activeElement === document.querySelector('#ask')"

    await click("#attachments .att-view")
    opened = await p.until(OPEN, 3)
    v = await p.js(VIEW_JS)
    await p.shot("19-visionneuse-ouverte.png")
    check(f"visionneuse : clic = fenêtre role={v and v['role']} aria-modal={v and v['modal']} « {v and v['label']} », "
          f"alt « {v and v['alt']} », source {v and v['src']}, bouton Fermer, focus dedans",
          opened and v and v["role"] == "dialog" and v["modal"] == "true" and v["label"] == "Image capture.png en grand"
          and v["alt"] == "capture.png" and v["src"] == "blob:" and v["done"] and v["x"] and v["focusIn"] and v["scripts"] == 0)
    if v:
        cx, cy = (v["l"] + v["r"]) / 2, (v["t"] + v["b"]) / 2
        check(f"visionneuse : image réduite à la fenêtre ({v['r'] - v['l']:.0f}×{v['b'] - v['t']:.0f} sur {v['w']}×{v['h']}, "
              f"source {v['nw']} px de large), centrée ({cx:.0f}, {cy:.0f}), fond sombre {v['bg']}",
              v["l"] >= 0 and v["t"] >= 0 and v["r"] <= v["w"] and v["b"] <= v["h"] and v["r"] - v["l"] < v["nw"]
              and abs(cx - v["w"] / 2) <= 4 and abs(cy - v["h"] / 2) <= 40 and v["bg"].startswith("rgba(0, 0, 0, 0.8"))
    await p.key("Escape", vk=27)
    check("visionneuse : Échap ferme, la barre de saisie retrouve le focus, texte et pièces gardés",
          await p.until(CLOSED, 2) and await p.js(FOCUS_ASK) and await p.js(
              "document.querySelector('#ask').value === 'Regarde ça' && document.querySelectorAll('#attachments .att').length === 2"))

    await click("#attachments .att-view")
    await p.until(OPEN, 3)
    await mouse(8, 8)  # sur le fond sombre, loin de l'image
    check("visionneuse : clic à côté ferme, focus rendu à la barre", await p.until(CLOSED, 2) and await p.js(FOCUS_ASK))

    await click("#attachments .att-view")
    await p.until(OPEN, 3)
    await click(".modal.viewer .modal-x")
    check("visionneuse : la croix ferme, focus rendu à la barre", await p.until(CLOSED, 2) and await p.js(FOCUS_ASK))

    # clavier : miniature focalisée, Espace ouvre (Entrée répondrait à une carte de confirmation affichée), Échap ferme
    await p.js("document.querySelector('#attachments .att-view').focus()")
    await p.key(" ", code="Space", vk=32, text=" ")
    kb = await p.until(OPEN, 3)
    await p.key("Escape", vk=27)
    check("visionneuse : au clavier, Espace sur la miniature ouvre, Échap ferme, focus rendu à la miniature",
          kb and await p.until(CLOSED, 2) and await p.js("document.activeElement?.classList.contains('att-view')"))

    # 19/10 (demande : voir l'image dans le fil après l'envoi) : l'image partie devient une
    # vignette du fil (servie par /api/attachment/<file>), le fichier texte reste un nom.
    await p.js("""(() => { const a = document.querySelector('#ask');
        a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
    sent = await p.until("document.querySelector('#attachments').classList.contains('hidden')", 4)
    LAST_USER = "[...document.querySelectorAll('#transcript .msg.user:not(.hidden)')].at(-1)"
    loaded = await p.until(f"(() => {{ const i = {LAST_USER}.querySelector('.att-thumb img');"
                           " return !!i && i.complete && i.naturalWidth > 0; })()", 5)
    await p.js(SETTLE_JS)  # P35 : entrée Premium du message (scale 0,98) finie avant la mesure
    f = await p.js(f"""(() => {{ const m = {LAST_USER}, b = m.querySelector('.att-thumb'), i = b?.querySelector('img');
        const r = i?.getBoundingClientRect();
        return {{ chips: [...m.querySelectorAll('.att-chip')].map(c => c.textContent), thumbs: m.querySelectorAll('.att-thumb').length,
                 tag: b?.tagName, label: b?.getAttribute('aria-label'), alt: i?.alt, path: i && new URL(i.src).pathname,
                 h: r && Math.round(r.height), w: r && Math.round(r.width), cursor: b && getComputedStyle(b).cursor,
                 md: m.querySelectorAll('.md img').length }}; }})()""")
    await p.js("document.querySelector('#transcript').scrollTop = 1e9")
    await p.shot("20-fil-vignette.png")
    check(f"fil : vignette de l'image partie ({f['tag']} « {f['label']} », {f['w']}×{f['h']} px, {f['path']}, curseur {f['cursor']}), "
          f"le fichier texte reste un nom {f['chips']}",
          sent and loaded and f["thumbs"] == 1 and f["tag"] == "BUTTON" and f["label"] == "Voir capture.png en grand"
          and f["alt"] == "capture.png" and (f["path"] or "").startswith("/api/attachment/") and f["h"] == 72
          and 40 <= f["w"] <= 160 and f["cursor"] == "zoom-in" and f["chips"] == ["note-vue.txt"] and f["md"] == 0)
    await p.js(f"{LAST_USER}.querySelector('.att-thumb').click()")
    opened = await p.until(OPEN, 3)
    v = await p.js(VIEW_JS)
    await p.shot("20-fil-vignette-ouverte.png")
    check(f"fil : clic sur la vignette = visionneuse « {v and v['label']} », image servie par le cœur ({v and v['nw']} px)",
          opened and v and v["label"] == "Image capture.png en grand" and v["done"] and v["nw"] == 1400 and v["scripts"] == 0)
    await p.key("Escape", vk=27)
    check("fil : Échap ferme la visionneuse ouverte depuis le fil", await p.until(CLOSED, 2))
    # liste blanche côté serveur (le refus d'un fichier non-image : tests/test_lot_hud.py) :
    # remontée de dossier, chemin absolu, nom inconnu → 404
    codes = await p.js("""(async () => { const st = async (u) => (await fetch(u, { credentials: 'same-origin' })).status;
        return { up: await st('/api/attachment/..%2Fhud-token'), etc: await st('/api/attachment/%2Fetc%2Fpasswd'),
                 none: await st('/api/attachment/rien.png') }; })()""")
    check(f"/api/attachment : seules les images de runtime/attachments sortent ({codes})",
          all(c == 404 for c in codes.values()))
    # image effacée depuis (gardée 7 jours) : repli sur la pastille du nom
    await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_att_gone' }))")
    gone = await p.until(f"""(() => {{ const m = {LAST_USER};
        return m.textContent.includes("Image d'il y a longtemps") && !m.querySelector('.att-thumb')
               && [...m.querySelectorAll('.att-chip')].map(c => c.textContent).join() === 'ancienne.png'; }})()""", 5)
    check("fil : image disparue du disque, la vignette laisse place à son nom", gone)


MESURE_JS ="""(() => {
    const box = (s) => { const e = document.querySelector(s);
      if (!e || e.hidden || e.classList.contains('hidden') || getComputedStyle(e).display === 'none') return null;
      const b = e.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom }; };
    // 18/10 (plan P7) : un bouton caché (« Garde-fou levé » hors levée) n'est pas à l'écran.
    // 22/10 (P23) : une pastille cachée par le CSS (affichage Simple) n'a pas de boîte, elle ne compte pas
    const chips = [...document.querySelectorAll('#topbar .chip:not([hidden]), #conn')].filter(e => e.getClientRects().length)
      .map(e => e.getBoundingClientRect());
    const bar = document.querySelector('#topbar').getBoundingClientRect();
    let overlap = 0;
    for (let i = 0; i < chips.length; i++) for (let j = i + 1; j < chips.length; j++) {
      const a = chips[i], b = chips[j];
      if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) overlap++; }
    const outside = chips.filter(c => c.left < bar.left || c.right > bar.right || c.top < bar.top || c.bottom > bar.bottom).length;
    const wide = ['#right'].filter(s => { const e = document.querySelector(s);
      return getComputedStyle(e).display !== 'none' && e.scrollWidth > e.clientWidth + 1; });
    const t = document.querySelector('#transcript');
    return { w: innerWidth, h: innerHeight, bar: box('#topbar'), total: box('#total-banner'), mission: box('#mission-banner'),
             left: box('#left'), right: box('#right'), dialog: box('#dialog'), composer: box('#composer'),
             overlap, outside, wide, chatH: t.clientHeight,
             rows: new Set([...document.querySelectorAll('#topbar .tgroup')].map(g => { const r = g.getBoundingClientRect(); return Math.round((r.top + r.bottom) / 20); })).size };
})()"""


async def check_barre_du_haut(p: Page) -> None:
    """19/10 (demande : « la barre du haut du HUD est chargée ») : Recadrer et Orbite hors de la barre
    (retirés avec le graphe le 23/10, plan P28), Guide / Réglages en icône seule, connexion
    fondue dans la pastille du logo, « Sans confirmation », section Forces repliée.
    28/10 (T10, décision de l'utilisateur) : « Lire aussitôt », « Mission », « Simple » et « Éteindre » retirés ;
    Autonome, Sans confirmation, Journal, Activité gardés ; Stop tout au bout de la barre."""
    await p.js(SETTLE_JS)
    green, red = await p.js(f"{TOKEN_JS}('--green')"), await p.js(f"{TOKEN_JS}('--red')")  # P35 : jetons du thème posé
    b = await p.js("""(() => {
        const q = (s) => document.querySelector(s), bar = q('#topbar');
        const items = [...q('.top-actions').children];
        const conn = q('#conn');
        return { tools: document.querySelectorAll('[data-action], #graph-tools').length,
                 iconOnly: ['#settings-chip', '#guide-chip'].map(s => [q(s).textContent.trim(), q(s).getAttribute('aria-label'), !!q(s).title]),
                 last: !!items[items.length - 1]?.querySelector('#estop-chip'),
                 gone: ['#reports-chip', '#mission-chip', '#affichage-chip', '#power-chip'].filter(q),
                 kept: ['#autonomy-chip', '#total-chip', '#audit-chip', '#log-btn', '#estop-chip']
                   .filter(s => !q(s) || !q(s).getClientRects().length),
                 conn: { inBrand: !!conn.closest('.brand'), text: conn.textContent, title: conn.title, on: conn.classList.contains('on'),
                         color: getComputedStyle(conn).color, opacity: getComputedStyle(conn).opacity,
                         star: conn.querySelector('svg path')?.getAttribute('fill') || '', glow: getComputedStyle(conn).boxShadow,
                         filter: getComputedStyle(conn).filter, size: Math.round(conn.getBoundingClientRect().width) },
                 total: q('#total-chip').textContent.trim(), totalTitle: q('#total-chip').title,
                 gone: ['#left', '#search', '#inspector', '#top-hubs', '#forces', '#filters'].filter(s => q(s)) };
    })()""")
    check(f"Recadrer et Orbite retirés (plan P28) : {b['tools']} bouton du graphe", b["tools"] == 0)
    check(f"Réglages, Guide en icône seule, nom en aria-label et title ({b['iconOnly']})",
          all(t == "" and lbl and has for t, lbl, has in b["iconOnly"]))
    check(f"T10 : Lire aussitôt, Mission, Simple, Éteindre retirés (restent {b['gone']}) ; Autonome, Sans confirmation, "
          f"Journal, Activité, Stop tout visibles (manquent {b['kept']}) ; Stop tout au bout de la barre ({b['last']})",
          b["gone"] == [] and b["kept"] == [] and b["last"])
    c = b["conn"]
    # 23/10 (plan P31, choix de l'utilisateur) : l'étoile d'Astrel (SVG currentColor) est l'état de la connexion
    check(f"connexion dans l'étoile du logo : « {c['title']} », vert ({c['color']}, opacité {c['opacity']}), "
          f"{c['size']} px, sans lueur ni flou ({c['glow']}, {c['filter']}), aucun texte",
          c["inBrand"] and c["text"] == "" and c["on"] and c["title"] == "Connexion au cœur d'Astrel : en ligne"
          and c["color"] == green and c["opacity"] == "1" and c["star"] == "currentColor"
          and c["glow"] == "none" and c["filter"] == "none" and c["size"] == 20)
    off = await p.js("""(() => { const c = document.querySelector('#conn');
        c.classList.replace('on', 'off'); c.style.transition = 'none';
        const st = getComputedStyle(c), ps = getComputedStyle(c.querySelector('svg path'));
        const r = { color: st.color, opacity: st.opacity, fill: ps.fill, stroke: ps.stroke, width: ps.strokeWidth };
        c.classList.replace('off', 'on'); c.style.transition = ''; return r; })()""")
    # relecture P31 : creuse (contour épais) plutôt qu'atténuée : forme lisible sans couleur, contraste ≥ 3:1
    check(f"étoile hors ligne : rouge ({off['color']}), CREUSE (fill {off['fill']}, contour {off['width']}), "
          f"opacité {off['opacity']}",
          off["color"] == red and off["fill"] == "none" and off["stroke"] == red
          and float(off["width"].rstrip("px")) >= 2 and off["opacity"] == "1")
    check(f"interrupteur « {b['total']} » (id #total-chip inchangé), title explicite",
          b["total"].startswith("Sans confirmation") and b["total"].endswith(("OUI", "NON"))
          and "Sans confirmation" in b["totalTitle"] and "TOTAL" not in b["totalTitle"])  # T27
    # 23/10 (demande de l'utilisateur) : panneau gauche (recherche, Inspecteur, Plus sollicités, Forces) et Filtres retirés
    check(f"panneau gauche et Filtres retirés du DOM (restent : {b['gone']})", b["gone"] == [])


async def check_mise_en_page(p: Page) -> None:
    """15/10 (demande : « les parties sont trop collées, le bandeau chevauche ») : à 1280,
    1440 et 1920 de large, Total ET une mission actifs (deux bandeaux), carte de confirmation
    affichée : rien ne se chevauche, marges et gouttières tenues, aucun débordement."""
    # Total est actif depuis le début (la démo renvoie l'état) ; on relance la mission arrêtée plus haut.
    await p.js(f"window.__ws.send({json.dumps(json.dumps(MISSION_START))})")
    both = await p.until("!document.querySelector('#total-banner').hidden && !document.querySelector('#mission-banner').hidden", 4)
    check("Total et mission actifs : les deux bandeaux affichés", both)
    MIN = 12  # marge minimale tenue entre deux blocs et au bord
    n = lambda v: "—" if v is None else f"{v:.0f}"
    for w, h in ((1280, 800), (1360, 850), (1440, 900), (1580, 950), (1920, 1080)):  # 1360, 1580 : 19/10
        await p.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(0.8)
        m = await p.js(MESURE_JS)
        bar, tot, mis, left, right, dlg, comp = (m[k] for k in ("bar", "total", "mission", "left", "right", "dialog", "composer"))
        # 19/10 : la ligne Total / levée flotte sous la tête (barre + mission), entre les panneaux
        head = max(x["b"] for x in (bar, mis) if x)
        check(f"{w}×{h} : barre en {m['rows']} ligne(s), boutons ni chevauchés ni hors de la barre "
              f"({m['overlap']} / {m['outside']})", m["overlap"] == 0 and m["outside"] == 0)
        check(f"{w}×{h} : mission sous la barre, ligne Total sous la tête, fine, entre les panneaux "
              f"(barre→{n(bar['b'])}, mission {n(mis and mis['t'])}→{n(mis and mis['b'])}, "
              f"Total {n(tot and tot['t'])}→{n(tot and tot['b'])}, {n(tot and tot['l'])}→{n(tot and tot['r'])})",
              tot and mis and mis["t"] >= bar["b"] + 6 and tot["t"] >= head + 6 and tot["b"] - tot["t"] <= 28
              and (not left or tot["l"] >= left["r"] + 4) and tot["r"] <= right["l"] - 4)
        tops = {"gauche": left and round(left["t"]), "droite": round(right["t"]), "fil": round(dlg["t"])}
        check(f"{w}×{h} : panneaux et fil sous la tête ({head:.0f}) avec une marge ({tops})",
              all(v is None or v >= head + MIN for v in tops.values()))
        gut = [round(dlg["l"] - left["r"]) if left else None, round(right["l"] - dlg["r"])]
        check(f"{w}×{h} : gouttières entre panneaux et fil {gut}, bords tenus "
              f"(gauche {n(left and left['l'])}, droite {n(w - right['r'])}, bas {n(h - comp['b'])})",
              all(g is None or g >= MIN for g in gut) and (not left or left["l"] >= MIN)
              and w - right["r"] >= MIN and h - comp["b"] >= MIN and bar["l"] >= MIN and w - bar["r"] >= MIN)
        check(f"{w}×{h} : aucun panneau ne déborde en largeur ({m['wide']})", not m["wide"])
        # 23/10 : plus de panneau gauche ; le fil prend la place (1200 px au plus, centré entre le bord et le panneau droit)
        room = right["l"] - 16 - 16  # bord (--edge) et gouttière (--gap), au plus juste (20 à 1800 px et plus)
        check(f"{w}×{h} : le fil occupe la place libérée ({n(dlg['l'])}→{n(dlg['r'])}, {n(dlg['r'] - dlg['l'])} px "
              f"pour {n(room)} disponibles)", not left and dlg["r"] - dlg["l"] >= min(1200, room - 8) - 1)
        await p.shot(f"11-mise-en-page-{w}.png")
    # Fil tiré au maximum, carte de confirmation affichée : c'est le fil qui rétrécit.
    await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
    await p.js("document.querySelector('#dialog').style.setProperty('--chat-h', '3000px')")
    await asyncio.sleep(0.5)
    m = await p.js(MESURE_JS)
    head = max(x["b"] for x in (m["bar"], m["total"], m["mission"]) if x)  # le fil reste sous la ligne Total
    card = await p.js("!document.querySelector('#confirm').classList.contains('hidden')")
    check(f"fil au maximum (carte {'affichée' if card else 'absente'}) : il reste sous la tête "
          f"({m['dialog']['t']:.0f} ≥ {head:.0f} + {MIN}), hauteur {m['chatH']} px",
          m["dialog"]["t"] >= head + MIN and m["chatH"] >= 60)
    await p.shot("12-fil-maximum-1280.png")
    await p.js("document.querySelector('#dialog').style.removeProperty('--chat-h')")


COMPOSER_OUT = Path("/tmp/captures-composer")
COMPOSER_JS = """(() => {
    const box = (e) => { const r = e.getBoundingClientRect(); return { t: r.top, b: r.bottom, l: r.left, r: r.right, h: r.height }; };
    const q = (s) => document.querySelector(s), comp = q('#composer'), row = q('#composer .composer-row');
    const c = box(comp), parts = [...row.querySelectorAll('.round, #usage')].map(box);
    const tools = box(q('#composer .composer-tools')), acts = box(q('#composer .composer-actions'));
    // boutons flottants (↓, recherche du 04/11) : posés sur le fil exprès, hors de l'empilement mesuré
    const shown = [...q('#dialog').children].filter(e => !['jump-btn', 'search-btn'].includes(e.id) && getComputedStyle(e).display !== 'none'
                                                    && e.getBoundingClientRect().height > 0).map(e => [e.id, box(e)]);
    let covered = 0;
    for (let i = 1; i < shown.length; i++) if (shown[i - 1][1].b > shown[i][1].t + 0.5) covered++;
    const label = q('#usage button.on span');
    return { comp: c, row: box(row), ask: box(q('#ask')), chat: box(q('#transcript')), dialog: box(q('#dialog')),
             head: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--head-h')),
             askScroll: getComputedStyle(q('#ask')).overflowY, toolsR: tools.r, actsL: acts.l,
             outside: parts.filter(p => p.l < c.l || p.r > c.r || p.t < c.t || p.b > c.b).length,
             wide: comp.scrollWidth > comp.clientWidth + 1 || row.scrollWidth > row.clientWidth + 1,
             order: shown.map(s => s[0]).join('>'), covered,
             label: label ? getComputedStyle(label).display !== 'none' : null };
})()"""


async def check_bouton_envoyer(p: Page) -> None:
    """04/11 (l'utilisateur, « comme ChatGPT ») : bouton « Envoyer » (↑) au bout de la rangée de la barre : grisé et non
    cliquable si la barre est vide (ou d'espaces), actif avec du texte ; un clic envoie EXACTEMENT comme Entrée
    (même commande `say`, mêmes champs, barre vidée une fois reçu) ; rien pendant une composition IME ; cible de
    32 px au moins, dernier bouton de la rangée, dans la carte (rangée mesurée aussi par check_composer)."""
    await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
    await p.js("document.querySelectorAll('.modal-back .modal-x, .modal-back .modal-cancel').forEach(b => b.click()), true")
    set_ask = lambda txt: p.js(f"""(() => {{ const a = document.querySelector('#ask'); a.value = {json.dumps(txt)};
        a.dispatchEvent(new Event('input')); return true; }})()""")
    state = """(() => { const b = document.querySelector('#send-btn'), r = b.getBoundingClientRect(),
        c = document.querySelector('#composer').getBoundingClientRect(), row = [...document.querySelectorAll('#composer .composer-row .round')];
        return { disabled: b.disabled, w: r.width, h: r.height, last: row.at(-1) === b, inside: r.left >= c.left && r.right <= c.right
          && r.top >= c.top && r.bottom <= c.bottom, aria: b.getAttribute('aria-label'), title: b.title, icon: b.querySelector('svg')?.dataset.icon,
          cursor: getComputedStyle(b).cursor, keys: b.getAttribute('aria-keyshortcuts') }; })()"""
    await set_ask("")
    await asyncio.sleep(0.1)
    vide = await p.js(state)
    await set_ask("   \n  ")
    await asyncio.sleep(0.1)
    blancs = await p.js(state)
    says = await p.js("window.__sent.filter(m => m.cmd === 'say').length")
    await p.js("document.querySelector('#send-btn').click(), true")
    await asyncio.sleep(0.3)
    check(f"Envoyer : grisé barre vide ({vide['disabled']}) ou d'espaces ({blancs['disabled']}), curseur « {vide['cursor']} », "
          "un clic n'envoie rien",
          vide["disabled"] and blancs["disabled"] and vide["cursor"] == "not-allowed"
          and await p.js("window.__sent.filter(m => m.cmd === 'say').length") == says)
    await set_ask("message par le bouton")
    await asyncio.sleep(0.1)
    actif = await p.js(state)
    check(f"Envoyer : actif avec du texte, « {actif['aria']} », icône {actif['icon']}, {actif['w']:.0f}×{actif['h']:.0f} px, "
          f"dernier de la rangée ({actif['last']}), dans la carte ({actif['inside']})",
          not actif["disabled"] and actif["aria"] == "Envoyer (Entrée)" and actif["title"] == "Envoyer (Entrée)"
          and actif["icon"] == "arrow-up" and actif["w"] >= 32 and actif["h"] >= 32 and actif["last"] and actif["inside"]
          and actif["keys"] == "Enter")
    await p.shot("envoyer-actif-1280.png")
    # composition IME en cours : le clic n'envoie rien
    await p.js("document.querySelector('#ask').dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })), true")
    await p.js("document.querySelector('#send-btn').click(), true")
    await asyncio.sleep(0.3)
    ime = await p.js("window.__sent.filter(m => m.cmd === 'say').length") == says
    await p.js("document.querySelector('#ask').dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })), true")
    await asyncio.sleep(0.1)
    check("Envoyer pendant une composition (IME) : rien n'est envoyé, le texte reste", ime
          and await p.js("document.querySelector('#ask').value") == "message par le bouton")
    r = await p.js("(() => { const b = document.querySelector('#send-btn').getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; })()")
    for kind in ("mousePressed", "mouseReleased"):
        await p.send("Input.dispatchMouseEvent", type=kind, x=r[0], y=r[1], button="left", clickCount=1)
    await p.until(f"window.__sent.filter(m => m.cmd === 'say').length > {says}", 3)
    par_bouton = await p.js("window.__sent.filter(m => m.cmd === 'say').pop()") or {}
    videe = await p.until("document.querySelector('#ask').value === '' && document.querySelector('#send-btn').disabled", 4)
    focus = await p.js("document.activeElement?.id || ''")
    await set_ask("message par Entrée")
    await p.js("document.querySelector('#ask').focus(), true")
    await p.key("Enter", vk=13, text="\r")
    await p.until(f"window.__sent.filter(m => m.cmd === 'say').length > {says + 1}", 3)
    par_entree = await p.js("window.__sent.filter(m => m.cmd === 'say').pop()") or {}
    diag = await p.js("({ sending: document.querySelector('#ask').dataset.sending || '', value: document.querySelector('#ask').value, "
                      "confirm: !document.querySelector('#confirm').classList.contains('hidden'), focus: document.activeElement?.id, "
                      "ws: window.__ws?.readyState })")
    cles = lambda m: sorted(k for k in m if k != "id")
    check(f"clic sur Envoyer = Entrée : même commande, mêmes champs ({cles(par_bouton)} / {cles(par_entree)} ; "
          f"« {par_bouton.get('text')} » / « {par_entree.get('text')} », {diag}), barre vidée "
          f"une fois reçu et bouton regrisé ({videe}), curseur rendu à la barre ({focus})",
          par_bouton.get("text") == "message par le bouton" and par_entree.get("text") == "message par Entrée"
          and cles(par_bouton) == cles(par_entree) and par_bouton.get("channel") == par_entree.get("channel")
          and videe and focus == "ask")
    await p.until("document.querySelector('#ask').value === ''", 3)
    await p.js("document.querySelector('#ask').blur(), true")


async def check_composer(p: Page) -> None:
    """19/10 (l'utilisateur, « comme ChatGPT ») : carte sur deux étages. Le texte grandit vers le
    haut, la rangée de boutons garde sa hauteur et sa position ; rien ne déborde ; le fil
    n'est pas recouvert. Texte de 1, 3 et 12 lignes à 1280, 1440 et 1580 de large."""
    COMPOSER_OUT.mkdir(exist_ok=True)
    set_ask = lambda txt: p.js(f"""(() => {{ const a = document.querySelector('#ask'); a.value = {json.dumps(txt)};
        a.dispatchEvent(new Event('input')); }})()""")
    for w, h in ((1280, 800), (1440, 900), (1580, 950)):
        await p.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
        seen = {}
        for n in (1, 3, 12):
            await set_ask("\n".join(f"ligne {i + 1} du message" for i in range(n)))
            await asyncio.sleep(0.4)
            seen[n] = m = await p.js(COMPOSER_JS)
            await p.shot(f"composer-{w}-{n}l.png")
            (COMPOSER_OUT / f"composer-{w}-{n}l.png").write_bytes((OUT / f"composer-{w}-{n}l.png").read_bytes())
            c, row, ask, chat = m["comp"], m["row"], m["ask"], m["chat"]
            check(f"{w}px, {n} ligne(s) : texte au-dessus de la rangée (texte {ask['t']:.0f}→{ask['b']:.0f}, "
                  f"rangée {row['t']:.0f}→{row['b']:.0f}), pleine largeur ({ask['r'] - ask['l']:.0f}/{c['r'] - c['l']:.0f})",
                  ask["b"] <= row["t"] + 0.5 and ask["t"] >= c["t"] and (ask["r"] - ask["l"]) >= (c["r"] - c["l"]) - 20)
            check(f"{w}px, {n} ligne(s) : boutons dans la carte, rien ne déborde, outils à gauche des actions "
                  f"({m['outside']} hors, débord {m['wide']}, {m['toolsR']:.0f} < {m['actsL']:.0f})",
                  m["outside"] == 0 and not m["wide"] and m["toolsR"] + 8 <= m["actsL"])
            check(f"{w}px, {n} ligne(s) : fil non recouvert ({m['order']}, {m['covered']} chevauchement), "
                  f"fil {chat['h']:.0f} px, sous la tête ({m['dialog']['t']:.0f} ≥ {m['head']:.0f})",
                  m["covered"] == 0 and chat["b"] <= c["t"] + 0.5 and chat["h"] >= 59.5 and m["dialog"]["t"] >= m["head"])
        one, three, twelve = seen[1], seen[3], seen[12]
        bottoms = {k: round(v["row"]["b"], 1) for k, v in seen.items()}
        heights = {k: round(v["row"]["h"], 1) for k, v in seen.items()}
        check(f"{w}px : la rangée de boutons ne bouge pas quand le texte grandit (bas {bottoms}, hauteur {heights})",
              len(set(bottoms.values())) == 1 and set(heights.values()) == {34})
        asks = [round(v["ask"]["h"]) for v in (one, three, twelve)]
        check(f"{w}px : seul l'étage du haut grandit, vers le haut ({asks} px ; carte "
              f"{one['comp']['t']:.0f}→{twelve['comp']['t']:.0f}), 12 lignes plafonnées et défilantes ({twelve['askScroll']})",
              asks[0] <= 32 and asks[0] < asks[1] < asks[2] <= 164 and twelve["comp"]["t"] < one["comp"]["t"]
              and twelve["askScroll"] == "auto" and one["askScroll"] == "hidden")
        check(f"{w}px : sélecteur de mode, nom du mode actif {'affiché' if one['label'] else 'caché'} "
              f"(icônes seules sous 1360 px)", one["label"] == (w >= 1360))
        # bouton « aller en bas » : au-dessus du bas du fil, pas sur la barre qui a grandi
        await p.js("(() => { const t = document.querySelector('#transcript'); t.scrollTop = 0; t.dispatchEvent(new Event('scroll')); })()")
        await asyncio.sleep(0.3)
        j = await p.js("""(() => { const j = document.querySelector('#jump-btn'), r = j.getBoundingClientRect(),
            t = document.querySelector('#transcript').getBoundingClientRect(), c = document.querySelector('#composer').getBoundingClientRect();
            return { shown: !j.classList.contains('hidden'), b: r.bottom, t: r.top, chatT: t.top, chatB: t.bottom, comp: c.top }; })()""")
        check(f"{w}px, 12 lignes : bouton ↓ au-dessus du bas du fil ({j['t']:.0f}→{j['b']:.0f}, fil jusqu'à "
              f"{j['chatB']:.0f}, carte dès {j['comp']:.0f})",
              not j["shown"] or (j["b"] <= j["chatB"] and j["t"] >= j["chatT"] - 1 and j["b"] <= j["comp"]))
        await p.js("document.querySelector('#jump-btn').click()")
    await set_ask("")
    await asyncio.sleep(0.3)
    check("barre vidée : carte compacte de nouveau (texte sur une ligne)",
          await p.js("document.querySelector('#ask').offsetHeight <= 32"))
    await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)


TABS_JS = "[...document.querySelectorAll('#channels .channel-tab:not(.add)')].map(t => t.dataset.chan)"
SHOTS_ORDRE = Path("/tmp/captures-ordre-canaux")


def js_list(ids: list) -> str:
    """Chaîne JS littérale égale à JSON.stringify(ids) (sans espaces)."""
    return json.dumps(json.dumps(ids, separators=(",", ":")))


async def shot_ordre(p: Page, name: str) -> None:
    r = await p.send("Page.captureScreenshot", format="png")
    (SHOTS_ORDRE / name).write_bytes(base64.b64decode(r["data"]))


async def check_nom_affiche(p: Page) -> None:
    """23/10 (plan P31, demande de l'utilisateur) : Réglages › Apparence › « Nom de l'assistant » (texte d'une
    ligne). « <b>x</b> » refusé sur place (rien d'envoyé) ; « Nova » + Entrée → settings_set, la page se
    recharge et rouvre les Réglages : marque, onglet, textes du HUD (t) et du cœur (tx) au nouveau nom,
    élision suivant le nom ; vide → « Astrel »."""
    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    row = "document.querySelector('.setting[data-key=\"assistant_name\"]')"
    await p.js("document.querySelector('#settings-chip').click()")
    found = await p.until(f"!!{row}?.querySelector('input')", 6)
    info = await p.js(f"""(() => {{ const r = {row}, i = r.querySelector('input');
        return {{ value: i.value, max: i.maxLength, ph: i.placeholder, label: r.textContent.includes("Nom de l'assistant") }}; }})()""")
    check(f"Réglages › Apparence : ligne « Nom de l'assistant » ({info})",
          found and info["value"] == "Astrel" and info["max"] == 32 and info["label"])
    n_set = await p.js("window.__sent.filter(m => m.cmd === 'settings_set').length")
    bad = await p.js(f"""(() => {{ const r = {row}, i = r.querySelector('input'); i.value = '<b>x</b>';
        i.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }}));
        const e = r.querySelector('.setting-error'); return e && !e.classList.contains('hidden') ? e.textContent : ''; }})()""")
    none = await p.js("window.__sent.filter(m => m.cmd === 'settings_set').length") == n_set
    check(f"nom avec chevrons refusé sur place : « {bad} », rien d'envoyé", bad == "Une seule ligne, sans chevrons < >." and none)
    await p.js(f"""(() => {{ const i = {row}.querySelector('input'); i.value = 'Nova';
        i.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }})); return true; }})()""")
    await asyncio.sleep(1.0)
    loaded = await p.until("!!document.querySelector('#transcript') && document.title === 'Nova'", 15)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await p.until("!!document.querySelector('.modal.settings')", 5)
    await asyncio.sleep(0.6)
    r = await p.js("""(() => ({ brand: document.querySelector('.brand').innerText.trim(), title: document.title,
        settings: document.querySelector('.modal.settings .modal-title')?.textContent || '',
        ph: document.querySelector('#ask').placeholder, conn: document.querySelector('#conn').title,
        stored: localStorage.getItem('astrel-assistant') }))()""")
    check(f"« Nova » : page rechargée, marque « {r['brand']} », onglet « {r['title']} », Réglages rouverts « {r['settings']} », "
          f"barre « {r['ph'][:20]} », connexion « {r['conn']} »",
          loaded and r["brand"] == "NOVA" and r["title"] == "Nova" and r["settings"] == "Réglages de Nova"
          and r["ph"].startswith("Demande à Nova") and r["conn"] == "Connexion au cœur de Nova : en ligne" and r["stored"] == "Nova")
    # retour au défaut : champ vidé (le cœur rend le défaut) → Astrel
    await demo("settings_set", key="assistant_name", value="")
    await asyncio.sleep(1.0)
    back = await p.until("!!document.querySelector('#transcript') && document.title === 'Astrel'", 15)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await asyncio.sleep(0.5)
    b = await p.js("({ brand: document.querySelector('.brand').innerText.trim(), conn: document.querySelector('#conn').title })")
    check(f"nom vidé : retour à « {b['brand']} » ({b['conn']})",
          back and b["brand"] == "ASTREL" and b["conn"] == "Connexion au cœur d'Astrel : en ligne")
    # Réglages refermés (Échap) : l'essai suivant (ordre des onglets) se joue au clavier
    await p.js("(document.querySelector('.modal.settings') || document).dispatchEvent(new KeyboardEvent('keydown', "
               "{ key: 'Escape', bubbles: true })), true")
    closed = await p.until("!document.querySelector('.modal.settings')", 3)
    check("Réglages refermés après l'essai du nom", closed)


async def check_ordre_canaux(p: Page) -> None:
    """19/10 (demande de l'utilisateur) : classer les onglets en les faisant glisser (ou
    Alt+← →). L'ordre part au cœur (`channel_reorder`), un autre HUD le reçoit, et il
    revient au rechargement. Glisser simulé par des DragEvent (DataTransfer réel)."""
    SHOTS_ORDRE.mkdir(exist_ok=True)
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await p.js("document.querySelectorAll('.modal-cancel').forEach(b => b.click())")
    start = await p.js(TABS_JS)
    check(f"ordre : au moins 3 onglets au départ {start}", len(start) >= 3)
    drag = await p.js("[...document.querySelectorAll('#channels .channel-tab')].every(t => t.classList.contains('add') || t.draggable)")
    check("ordre : chaque onglet de canal se glisse (draggable), pas le « + »",
          drag and not await p.js("document.querySelector('.channel-tab.add').draggable"))
    # Un deuxième HUD (même cookie) : il doit recevoir le nouvel ordre.
    await p.js("""(() => { const main = window.__ws; window.__other = null;
        const w2 = new WebSocket(main.url); window.__w2 = w2; window.__acks = {};
        w2.onmessage = (e) => { const m = JSON.parse(e.data);
            if (m.type === 'channels') window.__other = m.items.map(c => c.id);
            if (m.type === 'ack') window.__acks[m.id] = m.result; }; })()""")
    await p.until("window.__w2.readyState === 1", 4)
    await shot_ordre(p, "avant.png")

    # Glisser le dernier onglet et le lâcher sur la moitié GAUCHE du premier.
    last = start[-1]
    hint = await p.js(f"""(() => {{ const tabs = [...document.querySelectorAll('#channels .channel-tab:not(.add)')];
        const src = tabs[tabs.length - 1], dst = tabs[0], r = dst.getBoundingClientRect(), dt = new DataTransfer();
        const at = {{ bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + 3, clientY: r.top + r.height / 2 }};
        src.dispatchEvent(new DragEvent('dragstart', at));
        dst.dispatchEvent(new DragEvent('dragover', at));
        const mark = dst.classList.contains('drop-before');
        const dim = src.classList.contains('dragging');
        dst.dispatchEvent(new DragEvent('drop', at));
        src.dispatchEvent(new DragEvent('dragend', at));
        return {{ mark, dim }}; }})()""")
    check(f"ordre : pendant le glisser, onglet estompé et trait avant la cible {hint}", hint["mark"] and hint["dim"])
    want = [last] + start[:-1]
    shown = await p.js(TABS_JS)
    sentcmd = await p.js("window.__sent.filter(m => m.cmd === 'channel_reorder').pop()")
    check(f"ordre : lâché devant le premier, il passe en tête {shown}", shown == want)
    check(f"ordre : commande channel_reorder envoyée avec cet ordre ({sentcmd})", sentcmd and sentcmd.get("order") == want)
    check("ordre : l'autre HUD reçoit le nouvel ordre",
          await p.until(f"JSON.stringify(window.__other) === {js_list(want)}", 4))
    left = await p.js("document.querySelectorAll('.channel-tab.dragging, .channel-tab.drop-before, .channel-tab.drop-after').length")
    check(f"ordre : plus de marque de glisser après le lâcher ({left})", left == 0)

    # Clavier : Alt+→ sur l'onglet qui a le focus le décale d'un cran, le focus reste dessus.
    await p.js(f"[...document.querySelectorAll('#channels .channel-tab')].find(t => t.dataset.chan === {json.dumps(last)}).focus()")
    base = {"key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39, "modifiers": 1}
    await p.send("Input.dispatchKeyEvent", type="rawKeyDown", **base)
    await p.send("Input.dispatchKeyEvent", type="keyUp", **base)
    want2 = [want[1], last] + want[2:]
    ok = await p.until(f"JSON.stringify({TABS_JS}) === {js_list(want2)}", 3)
    focus = await p.js("document.activeElement?.dataset?.chan")
    check(f"ordre : Alt+→ décale l'onglet ({await p.js(TABS_JS)}), focus gardé ({focus})", ok and focus == last)
    await p.until(f"JSON.stringify(window.__other) === {js_list(want2)}", 4)

    # Un id inconnu est refusé par le cœur (démo = vrai module des canaux), l'ordre ne bouge pas.
    await p.js("""(() => { const main = window.__ws;
        window.__w2.send(JSON.stringify({ cmd: 'channel_reorder', id: 'ordre-x', order: ['inconnu', 'general'] }));
        window.__ws = main; })()""")
    await p.until("!!window.__acks['ordre-x']", 4)
    bad = await p.js("window.__acks['ordre-x']")
    check(f"ordre : id inconnu refusé ({bad})", bad and bad.get("ok") is False)

    # Rechargement : l'ordre revient du fichier.
    await p.js("window.__w2.close(); location.reload()")
    await asyncio.sleep(0.5)
    await p.until("document.querySelectorAll('#channels .channel-tab:not(.add)').length >= 3", 15)
    await asyncio.sleep(0.6)
    after = await p.js(TABS_JS)
    await shot_ordre(p, "apres.png")
    check(f"ordre : retenu après rechargement du HUD {after}", after == want2)
    # Remettre l'ordre de départ (la démo écrit dans un fichier jetable, mais par propreté).
    await p.js(f"window.__ws && window.__ws.send(JSON.stringify({{ cmd: 'channel_reorder', order: {json.dumps(start)} }}))")
    check("ordre : remis comme au départ", await p.until(f"JSON.stringify({TABS_JS}) === {js_list(start)}", 4))

    # Relecture du 19/10 : Alt+→ maintenu (repeat) ignoré ; trois appuis rapides = un envoi
    # en vol puis le DERNIER ordre seulement, et le résultat final est le bon.
    n0 = await sent_count(p, "channel_reorder")
    first = start[0]
    await p.js(f"""(() => {{ const t = [...document.querySelectorAll('#channels .channel-tab')].find(t => t.dataset.chan === {json.dumps(first)});
        t.focus();
        const k = (repeat) => document.activeElement.dispatchEvent(new KeyboardEvent('keydown',
            {{ key: 'ArrowRight', code: 'ArrowRight', altKey: true, repeat, bubbles: true, cancelable: true }}));
        k(true); }})()""")
    await asyncio.sleep(0.3)
    check("ordre : Alt+→ maintenu (repeat) ne déplace rien",
          await p.js(TABS_JS) == start and await sent_count(p, "channel_reorder") == n0)
    await p.js("""(() => { for (let i = 0; i < 3; i++) document.activeElement.dispatchEvent(new KeyboardEvent('keydown',
        { key: 'ArrowRight', code: 'ArrowRight', altKey: true, bubbles: true, cancelable: true })); })()""")
    burst = start[1:4] + [first] + start[4:]
    ok = await p.until(f"JSON.stringify({TABS_JS}) === {js_list(burst)}", 4)
    await asyncio.sleep(0.8)
    n = await sent_count(p, "channel_reorder") - n0
    last_sent = await p.js("window.__sent.filter(m => m.cmd === 'channel_reorder').pop()")
    check(f"ordre : trois Alt+→ rapides = {n} envois (en vol + dernier), ordre final {await p.js(TABS_JS)}",
          ok and n == 2 and last_sent.get("order") == burst and await p.js(TABS_JS) == burst)
    await p.js(f"window.__ws.send(JSON.stringify({{ cmd: 'channel_reorder', order: {json.dumps(start)} }}))")
    await p.until(f"JSON.stringify({TABS_JS}) === {js_list(start)}", 4)


SHOTS_P41 = Path("/tmp/captures-p41")


async def check_non_lus(p: Page) -> None:
    """27/10 (plan P41, demande de l'utilisateur : des messages se perdaient dans d'autres canaux) : la
    pastille des non-lus d'un onglet a un nom ACCESSIBLE (pas seulement un chiffre coloré), SURVIT au
    rechargement du HUD (heure de visite par canal, localStorage) et retombe à zéro à l'ouverture."""
    SHOTS_P41.mkdir(exist_ok=True)

    async def shot(name: str) -> None:
        r = await p.send("Page.captureScreenshot", format="png")
        (SHOTS_P41 / name).write_bytes(base64.b64decode(r["data"]))

    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, **args}))})")

    tab = "(id) => [...document.querySelectorAll('#channels .channel-tab')].find(t => t.dataset.chan === id)"
    pill = f"(id) => ({tab})(id)?.querySelector('.unread')?.textContent || ''"
    label = f"(id) => ({tab})(id)?.getAttribute('aria-label') || ''"
    await p.js("document.querySelectorAll('.modal-cancel').forEach(b => b.click())")
    start = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || ''")
    proj = await p.js("[...document.querySelectorAll('#channels .channel-tab:not(.add)')].map(t => t.dataset.chan)"
                      ".find(c => c && c !== 'general') || ''")
    check(f"P41 : un canal de projet pour l'essai ({proj})", bool(proj))
    if not proj:
        return
    await p.js(f"({tab})('general').click()")
    await p.until(f"({pill})({json.dumps(proj)}) === ''", 3)
    await shot("avant.png")
    for i in (1, 2):
        await demo("demo_report", channel=proj, text=f"Compte rendu P41 numéro {i}.")
    ok = await p.until(f"({pill})({json.dumps(proj)}) === '2'", 4)
    said = await p.js(f"({label})({json.dumps(proj)})")
    hidden = await p.js(f"({tab})({json.dumps(proj)})?.querySelector('.unread')?.getAttribute('aria-hidden')")
    check(f"P41 : pastille « 2 » sur l'onglet du projet, nom accessible « {said} »",
          ok and "2 messages non lus" in said and hidden == "true")
    await shot("pastille.png")
    await p.js("location.reload(), true")
    await asyncio.sleep(0.5)
    await p.until("document.querySelectorAll('#channels .channel-tab:not(.add)').length >= 2", 15)
    kept = await p.until(f"({pill})({json.dumps(proj)}) === '2'", 6)
    check(f"P41 : pastille gardée après rechargement du HUD ({await p.js(f'({pill})({json.dumps(proj)})')!r})", kept)
    await shot("apres-rechargement.png")
    await p.js(f"({tab})({json.dumps(proj)}).click()")
    cleared = await p.until(f"({pill})({json.dumps(proj)}) === ''", 3)
    check("P41 : pastille remise à zéro à l'ouverture du canal",
          cleared and "non lu" not in await p.js(f"({label})({json.dumps(proj)})"))
    await p.js(f"({tab})('general').click()")
    await p.js("location.reload(), true")
    await asyncio.sleep(0.5)
    await p.until("document.querySelectorAll('#channels .channel-tab:not(.add)').length >= 2", 15)
    await asyncio.sleep(0.6)
    check("P41 : canal déjà lu, pas de pastille au rechargement suivant",
          await p.js(f"({pill})({json.dumps(proj)})") == "")
    await shot("apres-lecture.png")
    if start:
        await p.js(f"({tab})({json.dumps(start)})?.click()")


async def sent_count(p: Page, cmd: str) -> int:
    return await p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)}).length")


async def check_videos_ia(p: Page) -> None:
    """28/10 (plan P41 lot B3) : carte d'une vidéo IA dans le fil. Modèle, durée et crédits estimés ;
    en cours : étape et temps écoulé qui avance, « Annuler » ; finie : « Vidéo prête » et la vidéo
    juste dessous (carte de médias, lecteur au clic) ; « Annuler » envoie video_cancel (job_id) et la
    carte finit « Vidéo annulée. ». Vrai module `videos_ia` de la démo, faux worker (aucun navigateur)."""
    await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await p.js("document.querySelectorAll('.modal-back').forEach(b => b.remove())")
    chan = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || ''")

    async def demo(mode):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'demo_video', 'channel': chan, 'mode': mode}))})")

    last = "[...document.querySelectorAll('.msg.video-job')].at(-1)"
    step = f"{last}.querySelector('.image-step')?.textContent || ''"
    await demo("ok")
    ok = await p.until(f"{last}?.classList.contains('image-en_cours') && ({step}).startsWith('Google Flow génère la vidéo… · ')", 8)
    first = await p.js(step)
    info = await p.js(f"""(() => {{ const c = {last};
        return {{ head: c.querySelector('.image-head').textContent, credit: c.querySelector('.image-credit')?.textContent,
                 cancel: c.querySelector('.image-retry')?.textContent, role: c.querySelector('.image-loading')?.getAttribute('role'),
                 aria: c.getAttribute('aria-label') }}; }})()""")
    advanced = await p.until(f"({step}) !== {json.dumps(first)}", 3)  # rafraîchi chaque seconde
    later = await p.js(step)
    check(f"vidéo IA : carte en cours « {info['credit']} », « {first} » puis « {later} », bouton « {info['cancel']} »",
          ok and info["head"] == "Vidéo IA" and info["credit"] == "Veo 3.1 Lite · 8 s · 10 crédits · compte principal"
          and info["cancel"] == "Annuler" and info["role"] == "status" and info["aria"] == "Vidéo générée par une IA"
          and advanced and later.startswith("Google Flow génère la vidéo… · 0:0"))
    ok = await p.until(f"{last}.classList.contains('image-fini') && !!{last}.nextElementSibling?.querySelector('.media-video')", 10)
    info = await p.js(f"""(() => {{ const c = {last};
        return {{ ready: [...c.querySelectorAll('.image-credit')].at(-1)?.textContent, cancel: !!c.querySelector('.image-retry'),
                 shimmer: !!c.querySelector('.image-shimmer'), auto: !!document.querySelector('video[autoplay]') }}; }})()""")
    check(f"vidéo IA : finie « {info['ready']} », lecteur juste dessous, sans autolecture ni « Annuler »",
          ok and info["ready"] == "Vidéo prête : elle est juste en dessous." and not info["cancel"]
          and not info["shimmer"] and not info["auto"])

    await demo("lent")
    ok = await p.until(f"{last}?.classList.contains('image-en_cours') && !!{last}.querySelector('.image-retry')", 8)
    await p.js(f"{last}.querySelector('.image-retry').click()")
    await p.until("window.__sent.some(m => m.cmd === 'video_cancel')", 3)
    sent = await p.js("window.__sent.filter(m => m.cmd === 'video_cancel').at(-1)")
    ok = ok and await p.until(f"{last}.classList.contains('image-echec')", 8)
    err = await p.js(f"{last}.querySelector('.image-error')?.textContent")
    check(f"vidéo IA : « Annuler » envoie video_cancel avec job_id ({sent.get('job_id')}), carte « {err} »",
          ok and isinstance(sent.get("job_id"), str) and len(sent["job_id"]) == 12 and err == "Vidéo annulée."
          and not await p.js(f"!!{last}.querySelector('.image-retry')"))
    found = await p.js(EMOJI_DOM_JS)
    check(f"vidéo IA : aucun émoji dans le DOM ({found[:3]})", found == [])


async def check_videos_panneau(p: Page) -> None:
    """28/10 (plan P41 lot B4) : onglet « Vidéo » du panneau Images (modèle et durée = réglage `video_choix`
    avec le coût en crédits, crédits des comptes, « Générer » = `video_generate` sans moteur ni modèle) et
    crédits vidéo par compte dans Réglages › Images IA. 29/10 : deux menus, « Modèle » (groupés par site)
    puis « Durée » (coût compris), comme les images. Vrai module `videos_ia` de la démo, faux worker."""
    await p.js("document.querySelectorAll('.modal-back').forEach(b => b.remove())")
    await p.js("document.querySelector('#images-btn').click()")
    await p.until("!document.querySelector('#image-panel').classList.contains('hidden')", 3)
    await p.js("[...document.querySelectorAll('.ip-kind button')].find(b => b.textContent === 'Vidéo').click()")
    ok = await p.until("document.querySelector('.ip-video-choice').options.length > 0 "
                       "&& /Crédits : principal \\d+\\/50/.test(document.querySelector('.ip-note').textContent)", 4)
    info = await p.js("""(() => { const pn = document.querySelector('#image-panel'), s = pn.querySelector('.ip-video-choice'),
            d = pn.querySelector('.ip-video-length');
        return { title: pn.querySelector('.ip-title').textContent, value: s.value, length: d.value,
                 groups: [...s.querySelectorAll('optgroup')].map(g => g.label + ' : ' + [...g.querySelectorAll('option')].map(o => o.textContent).join(' | ')),
                 lengths: [...d.options].map(o => o.textContent), lengthOff: d.disabled,
                 engine: !!pn.querySelector('.ip-engine').offsetParent, mode: !!pn.querySelector('.ip-mode').offsetParent,
                 choice: !!s.offsetParent && !!d.offsetParent, checked: pn.querySelector('.ip-kind button.on')?.getAttribute('aria-checked'),
                 note: pn.querySelector('.ip-note').textContent }; })()""")
    check(f"P41 B4 onglet Vidéo : « {info['title']} », modèles {info['groups']}, durées {info['lengths']}, note « {info['note']} »",
          ok and info["title"] == "Vidéo" and info["choice"] and not info["engine"] and not info["mode"]
          and info["checked"] == "true" and info["value"] == "flow/lite" and info["length"] == "flow/lite/8"
          and info["lengths"] == ["8 s · 10 crédits"] and info["lengthOff"]
          and info["groups"][0] == "Google Flow : Veo 3.1 Lite | Veo 3.1 Fast | Omni 1.1 Flash (720p)"
          and info["groups"][1].startswith("Dreamina : Seedance 2.0 Mini (720p) | ") and "HappyHorse 1.1 (720p)" in info["groups"][1]
          and not any("Quality" in g for g in info["groups"]))
    # Autre modèle : même durée s'il l'a, sinon sa première (rapide : pas de 8 s, donc 5 ; Omni : pas de 5 s, donc 4)
    await p.js("(() => { const s = document.querySelector('.ip-video-choice'); s.value = 'dreamina/seedance-2-rapide'; s.dispatchEvent(new Event('change')); })()")
    ok = await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'video_choix' && m.value === 'dreamina/seedance-2-rapide/5')", 3)
    ok = ok and await p.until("[...document.querySelector('.ip-video-length').options].map(o => o.textContent).join(',') === '5 s · 60 crédits,10 s · 120 crédits,15 s · 180 crédits'", 3)
    await p.js("(() => { const s = document.querySelector('.ip-video-choice'); s.value = 'flow/omni-flash'; s.dispatchEvent(new Event('change')); })()")
    ok = ok and await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'video_choix' && m.value === 'flow/omni-flash/4')", 3)
    await p.js("(() => { const s = document.querySelector('.ip-video-length'); s.value = 'flow/omni-flash/10'; s.dispatchEvent(new Event('change')); })()")
    ok = ok and await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'video_choix' && m.value === 'flow/omni-flash/10')", 3)
    await p.js("(() => { const s = document.querySelector('.ip-video-length'); s.value = 'flow/omni-flash/4'; s.dispatchEvent(new Event('change')); })()")
    ok = ok and await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'video_choix' && m.value === 'flow/omni-flash/4')"
                              " && document.querySelector('.ip-video-length').value === 'flow/omni-flash/4'", 3)
    check("29/10 vidéo : autre modèle = même durée ou sa première (rapide 5 s, Omni 4 s), autre durée = même modèle (Omni 10 s puis 4 s)", ok)
    last = "[...document.querySelectorAll('.msg.video-job')].at(-1)"
    n = await p.js("document.querySelectorAll('.msg.video-job').length")
    await p.js("""(() => { const a = document.querySelector('.ip-prompt'); a.value = 'une vague qui se brise au ralenti';
        a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
    await p.until("window.__sent.some(m => m.cmd === 'video_generate')", 3)
    g = await p.js("window.__sent.filter(m => m.cmd === 'video_generate').at(-1)")
    ok = ok and await p.until(f"document.querySelectorAll('.msg.video-job').length === {n + 1} && {last}.classList.contains('image-fini')", 12)
    credit = await p.js(f"{last}.querySelector('.image-credit')?.textContent")
    check(f"P41 B4 Générer : video_generate {{prompt, image: {g and g.get('image')!r}, image_fin: {g and g.get('image_fin')!r}, "
          f"format: {g and g.get('format')!r}}}, carte « {credit} », prompt vidé",
          ok and g.get("prompt") == "une vague qui se brise au ralenti" and g.get("image") == "" and "engine" not in g
          and g.get("image_fin") == "" and g.get("format") == "16:9"
          and "model" not in g and credit == "Omni 1.1 Flash (720p) · 4 s · 7 crédits · compte principal"
          and await p.js("document.querySelector('.ip-prompt').value === ''"))
    await check_videos_format_fin(p, last)
    # Réglages › Images IA (roue du panneau) : crédits vidéo par compte
    await p.js("document.querySelector('.ip-settings').click()")
    ok = await p.until("/crédits restants/.test(document.querySelector('.setting[data-key=\"videos.comptes\"]')?.textContent || '')", 4)
    lines = await p.js("[...document.querySelectorAll('.setting[data-key=\"videos.comptes\"] .info-line')].map(l => l.textContent)")
    check(f"P41 B4 Réglages : crédits vidéo par compte ({lines[:2]})",
          ok and any(l.startswith("principal · Google Flow") and "/50 crédits restants" in l and "renouvelés" in l for l in lines))
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal')", 3)
    # remis comme avant : réglage par défaut, onglet Image, panneau fermé
    await p.js("(() => { const s = document.querySelector('.ip-video-choice'); s.value = 'flow/lite'; s.dispatchEvent(new Event('change')); })()")
    await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'video_choix' && m.value === 'flow/lite/8')", 3)
    await p.js("[...document.querySelectorAll('.ip-kind button')].find(b => b.textContent === 'Image').click()")
    await p.js("document.querySelector('.ip-close').click()")
    check("P41 B4 retour à l'onglet Image, panneau refermé", await p.until(
        "document.querySelector('#image-panel').classList.contains('hidden') && !!document.querySelector('.ip-engine').offsetParent === false", 3))


async def check_videos_format_fin(p: Page, last: str) -> None:
    """30/10 (demande de l'utilisateur) : onglet Vidéo, FORMAT 16:9 (défaut) ou 9:16 et image de FIN facultative :
    deux pièces au plus, étiquetées « Début » et « Fin », inversables ; Dreamina (sans images) grise le
    trombone et Générer ; « Générer » = `video_generate {image, image_fin, format}`. Appelé par
    `check_videos_panneau` (onglet Vidéo ouvert, réglage sur Omni 1.1 Flash 4 s) ; laisse le format en 16:9
    et aucune pièce."""
    # --- format : menu « Format », 16:9 par défaut, 9:16 retenu (rien envoyé au cœur avant Générer)
    f = await p.js("""(() => { const s = document.querySelector('.ip-video-format');
        return { value: s.value, options: [...s.options].map(o => o.value + '=' + o.textContent), aria: s.getAttribute('aria-label'),
                 shown: !!s.offsetParent, label: s.closest('.ip-field')?.querySelector('.ip-label')?.textContent }; })()""")
    n_set = await p.js("window.__sent.filter(m => m.cmd === 'settings_set').length")
    await p.js("(() => { const s = document.querySelector('.ip-video-format'); s.value = '9:16'; s.dispatchEvent(new Event('change')); })()")
    kept = await p.until("document.querySelector('.ip-video-format').value === '9:16'", 2)
    check(f"30/10 vidéo, format : menu « {f['label']} » ({f['options']}), 16:9 par défaut, 9:16 choisi sans réglage du cœur",
          f["shown"] and f["value"] == "16:9" and f["aria"] == "Format" and f["label"] == "Format"
          and f["options"] == ["16:9=16:9 · horizontal", "9:16=9:16 · vertical (Reels, TikTok)"] and kept
          and await p.js(f"window.__sent.filter(m => m.cmd === 'settings_set').length === {n_set}"))

    # --- deux images : 1re = début, 2e = fin ; une 3e refusée ; « Inverser début et fin »
    add = """(async (names) => { const dt = new DataTransfer();
        for (const [i, name] of names.entries()) { const c = document.createElement('canvas'); c.width = 64 + i; c.height = 36;
            const x = c.getContext('2d'); x.fillStyle = i ? '#a52' : '#2a8'; x.fillRect(0, 0, c.width, c.height);
            dt.items.add(new File([await new Promise(r => c.toBlob(r, 'image/png'))], name, { type: 'image/png' })); }
        const i = document.querySelector('#image-panel input[type=file]'); i.files = dt.files;
        i.dispatchEvent(new Event('change')); return true; })"""
    await p.js(f"{add}(['debut.png', 'fin.png'])")
    ok = await p.until("document.querySelectorAll('.ip-pieces .att').length === 2 && !document.querySelector('.ip-pieces .att-pending')", 8)
    pieces = """(() => { const pn = document.querySelector('#image-panel');
        return { roles: [...pn.querySelectorAll('.ip-pieces .att')].map(a => (a.dataset.role || '') + ':' + (a.querySelector('.att-role')?.textContent || '')),
                 names: [...pn.querySelectorAll('.ip-pieces .att-name')].map(n => n.textContent),
                 aria: [...pn.querySelectorAll('.ip-pieces .att-view')].map(v => v.getAttribute('aria-label')),
                 attach: pn.querySelector('.ip-attach').disabled, send: pn.querySelector('.ip-send').disabled,
                 swap: pn.querySelector('.ip-swap')?.getAttribute('aria-label') || '', note: pn.querySelector('.ip-note').textContent }; })()"""
    a = await p.js(pieces)
    check(f"30/10 vidéo, images : {a['roles']} {a['names']}, trombone grisé, « {a['swap']} »",
          ok and a["roles"] == ["debut:Début", "fin:Fin"] and a["names"] == ["debut.png", "fin.png"]
          and a["aria"] == ["Début : voir debut.png en grand", "Fin : voir fin.png en grand"]
          and a["attach"] and not a["send"] and a["swap"] == "Inverser début et fin")
    n_piece = await p.js("window.__sent.filter(m => m.cmd === 'image_piece').length")
    await p.js(f"{add}(['troisieme.png'])")
    toast = await p.until("[...document.querySelectorAll('#toasts .toast')].some(t => t.textContent.includes('Deux images au plus pour une vidéo'))", 3)
    await asyncio.sleep(0.3)
    check("30/10 vidéo : une 3e image refusée (toast), rien déposé",
          toast and await p.js(f"document.querySelectorAll('.ip-pieces .att').length === 2 "
                               f"&& window.__sent.filter(m => m.cmd === 'image_piece').length === {n_piece}"))
    await p.js("document.querySelector('.ip-swap').click()")
    swapped = await p.js(pieces)
    focus = await p.js("document.activeElement?.classList.contains('ip-swap')")
    await p.js("document.querySelector('.ip-swap').click()")
    back = await p.js(pieces)
    check(f"30/10 vidéo : « Inverser début et fin » ({swapped['names']} puis {back['names']}), le focus reste sur le bouton",
          swapped["names"] == ["fin.png", "debut.png"] and swapped["roles"] == ["debut:Début", "fin:Fin"]
          and back["names"] == ["debut.png", "fin.png"] and focus)

    # --- Dreamina ne prend pas d'image : trombone et Générer grisés, Entrée n'envoie rien, message clair
    await p.js("(() => { const s = document.querySelector('.ip-video-choice'); s.value = 'dreamina/seedance-2-rapide'; s.dispatchEvent(new Event('change')); })()")
    ok = await p.until("window.__sent.filter(m => m.cmd === 'settings_set' && m.key === 'video_choix').at(-1)?.value === 'dreamina/seedance-2-rapide/5'"
                       " && document.querySelector('.ip-send').disabled", 3)
    d = await p.js(pieces)
    title = await p.js("document.querySelector('.ip-attach').getAttribute('aria-label')")
    n_gen = await p.js("window.__sent.filter(m => m.cmd === 'video_generate').length")
    await p.js("""(() => { const a = document.querySelector('.ip-prompt'); a.value = 'un phare sous la pluie';
        a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
    await asyncio.sleep(0.4)
    want = "Dreamina ne prend pas d'image de début ni de fin : retire-les ou choisis un modèle Google Flow"
    check(f"30/10 vidéo, Dreamina avec images : trombone et Générer grisés, Entrée n'envoie rien, « {d['note'][-95:]} »",
          ok and d["attach"] and d["send"] and want in d["note"] and title == want
          and await p.js(f"window.__sent.filter(m => m.cmd === 'video_generate').length === {n_gen}"))

    # --- retour sur Flow : Générer = video_generate {image (début), image_fin, format 9:16}
    await p.js("(() => { const s = document.querySelector('.ip-video-choice'); s.value = 'flow/omni-flash'; s.dispatchEvent(new Event('change')); })()")
    ok = await p.until("window.__sent.filter(m => m.cmd === 'settings_set' && m.key === 'video_choix').at(-1)?.value === 'flow/omni-flash/4'"
                       " && !document.querySelector('.ip-send').disabled", 3)
    n = await p.js("document.querySelectorAll('.msg.video-job').length")
    await p.js("""(() => { const a = document.querySelector('.ip-prompt'); a.value = 'un lever de soleil sur la mer';
        document.querySelector('.ip-send').click(); })()""")
    ok = ok and await p.until(f"window.__sent.filter(m => m.cmd === 'video_generate').length === {n_gen + 1}", 4)
    g = await p.js("window.__sent.filter(m => m.cmd === 'video_generate').at(-1)")
    ok = ok and await p.until(f"document.querySelectorAll('.msg.video-job').length === {n + 1} && {last}.classList.contains('image-fini')", 12)
    image, fin = str(g.get("image") or ""), str(g.get("image_fin") or "")
    check(f"30/10 vidéo, Générer avec début et fin : format {g.get('format')!r}, image …{image[-24:]}, "
          f"image_fin …{fin[-24:]}, carte finie, pièces vidées",
          ok and g.get("prompt") == "un lever de soleil sur la mer" and g.get("format") == "9:16"
          and image.startswith("/") and "/attachments/" in image and image.endswith("debut.png")
          and fin.startswith("/") and "/attachments/" in fin and fin.endswith("fin.png")
          and await p.js("document.querySelectorAll('.ip-pieces .att').length === 0 && !document.querySelector('.ip-swap')"))

    # remis comme avant : 16:9
    await p.js("(() => { const s = document.querySelector('.ip-video-format'); s.value = '16:9'; s.dispatchEvent(new Event('change')); })()")
    await p.until("document.querySelector('.ip-video-format').value === '16:9'", 2)


async def check_images_ia(p: Page) -> None:
    """21/10 (plan P14, demande de l'utilisateur) : carte d'une image IA dans le fil. Animation de
    chargement et étape tant que l'image n'est pas finie, puis l'image (visionneuse au clic) et
    « Généré avec Gemini · compte principal » ; échec : raison, texte de l'IA, « Réessayer » ; grille
    des variantes ; animation coupée par prefers-reduced-motion. Vrai module `images_ia` de la
    démo, faux worker (aucun navigateur, aucun site)."""
    await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await p.js("document.querySelectorAll('.modal-back').forEach(b => b.remove())")

    # La carte va dans le canal ouvert du HUD (comme l'outil MCP : canal de l'échange en cours).
    chan = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || ''")

    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, 'channel': chan, **args}))})")

    async def card_shot(name, sel):
        await p.js(f"document.querySelector({json.dumps(sel)})?.scrollIntoView({{block: 'center'}})")
        await asyncio.sleep(0.25)
        b = await p.js(f"(() => {{ const r = document.querySelector({json.dumps(sel)})?.getBoundingClientRect();"
                       " return r && { x: r.left - 8, y: r.top - 8, width: r.width + 16, height: r.height + 16 }; })()")
        if not b:
            return
        r = await p.send("Page.captureScreenshot", format="png",
                         clip={"x": max(0, b["x"]), "y": max(0, b["y"]), "width": b["width"], "height": b["height"], "scale": 1})
        (OUT / name).write_bytes(base64.b64decode(r["data"]))

    last = "[...document.querySelectorAll('.msg.image-job')].at(-1)"
    n0 = await p.js("document.querySelectorAll('.msg.image-job').length")
    await demo("demo_image", mode="ok")
    # la NOUVELLE carte : une image du contrôle précédent peut encore être en cours (file commune)
    ok = await p.until(f"document.querySelectorAll('.msg.image-job').length > {n0} "
                       f"&& {last}.classList.contains('image-en_cours') && !!{last}.querySelector('.image-shimmer')", 10)
    info = await p.js(f"""(() => {{ const c = {last}, s = c.querySelector('.image-shimmer');
        const st = getComputedStyle(s, '::after');
        return {{ step: c.querySelector('.image-step')?.textContent, anim: st.animationName, role: c.querySelector('.image-loading').getAttribute('role'),
                 prompt: c.querySelector('.image-prompt').textContent, head: c.querySelector('.image-head').textContent,
                 hidden: c.classList.contains('hidden') }}; }})()""")
    check(f"image IA : carte de chargement animée ({info['anim']}), étape « {info['step']} », rôle {info['role']}",
          ok and info["anim"] == "image-sweep" and info["role"] == "status" and not info["hidden"]
          and info["prompt"].startswith("un phare") and info["head"] == "Image IA")
    await p.until(f"{last}.querySelector('.image-step')?.textContent === 'Gemini génère l\\'image…'", 5)
    await card_shot("21-image-chargement.png", ".msg.image-job.image-en_cours")
    ok = await p.until(f"{last}.classList.contains('image-fini') && {last}.querySelector('.image-view img')?.naturalWidth > 0", 12)
    info = await p.js(f"""(() => {{ const c = {last}, img = c.querySelector('.image-view img');
        return {{ credit: c.querySelector('.image-credit')?.textContent, shimmer: !!c.querySelector('.image-shimmer'),
                 src: img.getAttribute('src').startsWith('/api/attachment/'), w: img.getBoundingClientRect().width,
                 title: c.querySelector('.image-credit').title }}; }})()""")
    check(f"image IA : image affichée ({info['w']:.0f} px), animation retirée, « {info['credit']} »",
          ok and not info["shimmer"] and info["src"] and info["w"] > 200
          and info["credit"] == "Généré avec Gemini · compte principal" and info["title"].startswith("Enregistrée dans Images-IA : "))
    await card_shot("22-image-finie.png", ".msg.image-job.image-fini")
    await p.js(f"{last}.querySelector('.image-view').click()")
    opened = await p.until("!!document.querySelector('.modal.viewer img.viewer-img')", 3)
    await p.key("Escape", vk=27)
    check("image IA : clic sur l'image = visionneuse, Échap la ferme",
          opened and await p.until("!document.querySelector('.modal.viewer')", 3))

    await demo("demo_image", mode="refus")
    ok = await p.until(f"{last}?.classList.contains('image-echec')", 12)
    info = await p.js(f"""(() => {{ const c = {last};
        return {{ err: c.querySelector('.image-error')?.textContent, ai: c.querySelector('.image-ai-text')?.textContent,
                 label: c.querySelector('.image-ai-label')?.textContent, retry: c.querySelector('.image-retry')?.textContent,
                 shimmer: !!c.querySelector('.image-shimmer') }}; }})()""")
    check(f"image IA : échec « {info['err']} », texte de l'IA tel quel, bouton « {info['retry']} »",
          ok and info["err"] == "L'IA a refusé de créer cette image." and info["ai"].startswith("I can't create realistic")
          and info["label"] == "Réponse de Gemini :" and info["retry"] == "Réessayer" and not info["shimmer"])
    await card_shot("23-image-echec.png", ".msg.image-job.image-echec")
    n = await p.js("document.querySelectorAll('.msg.image-job').length")
    await p.js(f"{last}.querySelector('.image-retry').click()")
    await p.until("window.__sent.some(m => m.cmd === 'image_retry')", 3)
    sent = await p.js("window.__sent.filter(m => m.cmd === 'image_retry').at(-1)")
    check(f"image IA : « Réessayer » envoie image_retry avec job_id ({sent.get('job_id')}), nouvelle carte qui aboutit",
          isinstance(sent.get("job_id"), str) and len(sent["job_id"]) == 12
          and await p.until(f"document.querySelectorAll('.msg.image-job').length === {n + 1} && {last}.classList.contains('image-fini')", 14))

    await demo("demo_image", mode="variantes")
    ok = await p.until(f"{last}?.classList.contains('image-fini') && {last}.querySelectorAll('.image-grid.n4 .image-view img').length === 4"
                       f" && [...{last}.querySelectorAll('.image-view img')].every(i => i.naturalWidth > 0)", 14)
    cols = await p.js(f"getComputedStyle({last}.querySelector('.image-grid')).gridTemplateColumns.split(' ').length")
    check(f"image IA : 4 variantes en grille ({cols} colonnes)", ok and cols == 2)
    await card_shot("24-image-variantes.png", ".msg.image-job.image-fini:last-of-type")

    await p.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
    await demo("demo_image", mode="lent")
    ok = await p.until(f"{last}?.classList.contains('image-en_cours') && !!{last}.querySelector('.image-shimmer')", 6)
    anim = await p.js(f"getComputedStyle({last}.querySelector('.image-shimmer'), '::after').animationName")
    check(f"image IA : mouvement réduit, animation coupée ({anim}), carte toujours visible", ok and anim == "none")
    await p.send("Emulation.setEmulatedMedia", features=[])
    found = await p.js(EMOJI_DOM_JS)
    check(f"image IA : aucun émoji dans le DOM ({found[:3]})", found == [])
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)


CAPTURES_A5 = Path("/tmp/captures-p40-a5")


async def check_images_panneau(p: Page) -> None:
    """28/10 (plan P40 lot A5, demande de l'utilisateur : « finir tout le multicompte / images IA ») : panneau
    « Images » sous la barre (prompt, Auto/Manuel = réglage `images_mode`, moteur et modèle = `images_manuel`,
    pièces jointes par `image_piece`, « Générer » = `image_generate`, zéro jeton) ; carte « Essayer avec X »
    (manuel en échec, `image_retry` + `engine`) ; Réglages › Images (comptes dans l'ordre du repli, épuisé,
    pas connecté, plafonds ; Connecter, monter, ajouter, retirer). Vrai module `images_ia` de la démo,
    faux worker, connexion simulée (aucune fenêtre Chrome)."""
    CAPTURES_A5.mkdir(exist_ok=True)
    while await p.js("!!document.querySelector('.modal-back')"):
        await p.key("Escape", vk=27)
        await asyncio.sleep(0.2)
    await p.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    last_sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)}).at(-1) || null")
    count = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)}).length")
    last = "[...document.querySelectorAll('.msg.image-job')].at(-1)"

    async def shot(name, sel="#dialog"):
        if sel.startswith(".modal"):  # page Réglages : la fenêtre entière, la section défilée en haut
            await p.js(f"document.querySelector({json.dumps(sel)})?.scrollIntoView({{block: 'start'}})")
            await asyncio.sleep(0.2)
            r = await p.send("Page.captureScreenshot", format="png")
            (CAPTURES_A5 / name).write_bytes(base64.b64decode(r["data"]))
            return
        b = await p.js(f"(() => {{ const r = document.querySelector({json.dumps(sel)})?.getBoundingClientRect();"
                       " return r && { x: r.left - 6, y: r.top - 6, width: r.width + 12, height: r.height + 12 }; })()")
        if b:
            r = await p.send("Page.captureScreenshot", format="png", clip={"x": max(0, b["x"]), "y": max(0, b["y"]),
                             "width": b["width"], "height": b["height"], "scale": 1})
            (CAPTURES_A5 / name).write_bytes(base64.b64decode(r["data"]))

    await shot("0-avant-barre.png")
    # --- ouverture : sous la barre, Auto par défaut, moteur et modèle grisés, état des comptes
    await p.js("document.querySelector('#images-btn').click()")
    ok = await p.until("!document.querySelector('#image-panel').classList.contains('hidden') "
                       "&& document.activeElement === document.querySelector('.ip-prompt')", 3)
    await p.until("/disponible/.test(document.querySelector('.ip-note').textContent)", 4)
    info = await p.js("""(() => { const pn = document.querySelector('#image-panel'), c = document.querySelector('#composer');
        const on = pn.querySelector('.ip-mode button.on');
        return { below: pn.getBoundingClientRect().top >= c.getBoundingClientRect().bottom - 1,
                 mode: on?.textContent, checked: on?.getAttribute('aria-checked'),
                 engine: pn.querySelector('.ip-engine').selectedOptions[0]?.textContent, eoff: pn.querySelector('.ip-engine').disabled,
                 moff: pn.querySelector('.ip-model').disabled, note: pn.querySelector('.ip-note').textContent,
                 expanded: document.querySelector('#images-btn').getAttribute('aria-expanded'),
                 pe: getComputedStyle(pn).pointerEvents, html: pn.innerHTML.includes('<script') }; })()""")
    check(f"P40 A5 panneau : ouvert SOUS la barre, prompt focalisé, « {info['mode']} » choisi, moteur « {info['engine']} », "
          f"note « {info['note']} »",
          ok and info["below"] and info["mode"] == "Auto" and info["checked"] == "true" and info["eoff"] and info["moff"]
          and info["engine"] == "Auto : Gemini → ChatGPT → Bing Image Creator" and info["note"] == "2 comptes disponibles sur 4"
          and info["expanded"] == "true" and info["pe"] == "auto" and not info["html"])
    await shot("1-panneau-auto.png")
    # --- Générer en auto (Entrée) : image_generate engine auto, carte qui aboutit, prompt vidé
    n = await p.js("document.querySelectorAll('.msg.image-job').length")
    await p.js("""(() => { const a = document.querySelector('.ip-prompt'); a.value = 'un phare breton sous la pluie';
        a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
    await p.until("window.__sent.some(m => m.cmd === 'image_generate')", 3)
    g = await last_sent("image_generate")
    done = await p.until(f"document.querySelectorAll('.msg.image-job').length === {n + 1} && {last}.classList.contains('image-fini')", 14)
    credit = await p.js(f"{last}.querySelector('.image-credit')?.textContent")
    shown = await p.js(f"!{last}.classList.contains('hidden')")  # canal ouvert du HUD, comme une phrase tapée
    check(f"P40 A5 auto : image_generate {{engine: {g and g.get('engine')}, files: {g and g.get('files')}}}, carte « {credit} », prompt vidé",
          g and g.get("engine") == "auto" and g.get("prompt") == "un phare breton sous la pluie" and g.get("files") == []
          and "model" not in g and isinstance(g.get("channel"), str) and g["channel"] and shown and done and credit == "Généré avec Gemini · compte principal"
          and await p.js("document.querySelector('.ip-prompt').value === ''"))
    # prompt vide : rien ne part
    n_gen = await count("image_generate")
    await p.js("document.querySelector('.ip-send').click()")
    await asyncio.sleep(0.3)
    check("P40 A5 prompt vide : rien n'est envoyé", await count("image_generate") == n_gen)
    # --- Manuel : réglage images_mode, puis ChatGPT (compte jamais connecté) → échec + « Essayer avec Gemini »
    await p.js("[...document.querySelectorAll('.ip-mode button')].find(b => b.textContent === 'Manuel').click()")
    await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'images_mode' && m.value === 'manuel')", 3)
    ok = await p.until("!document.querySelector('.ip-engine').disabled && document.querySelector('.ip-engine').value === 'gemini'", 3)
    await p.js("(() => { const s = document.querySelector('.ip-engine'); s.value = 'chatgpt'; s.dispatchEvent(new Event('change')); })()")
    ok = ok and await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'images_manuel' && m.value === 'chatgpt/defaut')", 3)
    ok = ok and await p.until("document.querySelector('.ip-note').textContent === '0 compte disponible sur 1'"
                              " && document.querySelector('.ip-engine').value === 'chatgpt'", 3)
    note = await p.js("document.querySelector('.ip-note').textContent")
    check(f"P40 A5 manuel : images_mode et images_manuel envoyés, moteur ChatGPT, note « {note} »", ok)
    # aller-retour 1 (fiche 3) : Alt, Ctrl ou Méta + flèche ne change pas le mode (flèche seule : oui, pas testé ici)
    n_mode = await p.js("window.__sent.filter(m => m.cmd === 'settings_set' && m.key === 'images_mode').length")
    await p.js("""(() => { const b = document.querySelector('.ip-mode button.on'); b.focus();
        for (const mod of ['altKey', 'ctrlKey', 'metaKey'])
          b.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true, [mod]: true })); })()""")
    await asyncio.sleep(0.3)
    check("P40 A5 Alt/Ctrl/Méta + flèche sur Auto/Manuel : mode inchangé, aucun settings_set",
          await p.js("document.querySelector('.ip-mode button.on').textContent") == "Manuel"
          and await p.js("window.__sent.filter(m => m.cmd === 'settings_set' && m.key === 'images_mode').length") == n_mode)
    await shot("2-panneau-manuel.png")
    await p.js("""(() => { const a = document.querySelector('.ip-prompt'); a.value = 'une carte de vœux';
        document.querySelector('.ip-send').click(); })()""")
    ok = await p.until(f"{last}.classList.contains('image-echec') && !!{last}.querySelector('.image-other')", 8)
    g = await last_sent("image_generate")
    card = await p.js(f"""(() => {{ const c = {last}; return {{ err: c.querySelector('.image-error')?.textContent,
        other: c.querySelector('.image-other')?.textContent }}; }})()""")
    check(f"P40 A5 manuel ChatGPT sans compte : {{engine: {g.get('engine')}, model: {g.get('model')}}}, échec « {card['err']} », "
          f"bouton « {card['other']} »",
          ok and g.get("engine") == "chatgpt" and g.get("model") == "defaut"
          and card["err"] == "Aucun compte connecté pour ce moteur : connexion à faire une fois, à la main."
          and card["other"] == "Essayer avec Gemini")
    await shot("3-carte-essayer-avec.png", ".msg.image-job.image-echec:last-of-type")
    n = await p.js("document.querySelectorAll('.msg.image-job').length")
    await p.js(f"{last}.querySelector('.image-other').click()")
    await p.until("window.__sent.some(m => m.cmd === 'image_retry' && m.engine === 'gemini')", 3)
    r = await last_sent("image_retry")
    check(f"P40 A5 « Essayer avec Gemini » : image_retry {{engine: {r.get('engine')}}}, nouvelle carte Gemini qui aboutit",
          r.get("engine") == "gemini" and isinstance(r.get("job_id"), str)
          and await p.until(f"document.querySelectorAll('.msg.image-job').length === {n + 1} && {last}.classList.contains('image-fini')", 14))
    # --- pièces jointes : une image PNG (image_piece), un texte refusé ; Bing grise pièces et envoi
    await p.js("(() => { const s = document.querySelector('.ip-engine'); s.value = 'gemini'; s.dispatchEvent(new Event('change')); })()")
    await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'images_manuel' && m.value === 'gemini/defaut')", 3)
    await p.js("""(async () => { const c = document.createElement('canvas'); c.width = 80; c.height = 60;
        const x = c.getContext('2d'); x.fillStyle = '#2a8'; x.fillRect(0, 0, 80, 60);
        const png = await new Promise(r => c.toBlob(r, 'image/png'));
        const dt = new DataTransfer(); dt.items.add(new File([png], 'croquis.png', { type: 'image/png' }));
        dt.items.add(new File(['pas une image'], 'notes.txt', { type: 'text/plain' }));
        const i = document.querySelector('#image-panel input[type=file]'); i.files = dt.files;
        i.dispatchEvent(new Event('change')); return true; })()""")
    ok = await p.until("document.querySelectorAll('.ip-pieces .att').length === 1 && !document.querySelector('.ip-pieces .att-pending')", 8)
    toasts = await p.js("[...document.querySelectorAll('#toasts .toast')].map(t => t.textContent).join(' | ')")
    pc = await last_sent("image_piece")
    check(f"P40 A5 pièces : PNG déposée par image_piece ({pc and pc.get('name')}), texte refusé ({toasts[-90:]})",
          ok and pc and pc.get("name") == "croquis.png" and "notes.txt" in toasts and "refusée : image illisible" in toasts
          and "decoded" not in toasts)
    await p.js("(() => { const s = document.querySelector('.ip-engine'); s.value = 'bing'; s.dispatchEvent(new Event('change')); })()")
    await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'images_manuel' && m.value === 'bing/defaut')", 3)
    b = await p.js("""(() => ({ attach: document.querySelector('.ip-attach').disabled, send: document.querySelector('.ip-send').disabled,
        note: document.querySelector('.ip-note').textContent }))()""")
    n_gen = await count("image_generate")
    await p.js("""(() => { const a = document.querySelector('.ip-prompt'); a.value = 'un phare';
        a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
    await asyncio.sleep(0.4)
    check(f"P40 A5 Bing (sans pièce jointe) : trombone et Générer grisés, Entrée n'envoie rien, « {b['note']} »",
          b["attach"] and b["send"] and "Bing Image Creator ne prend pas de pièce jointe" in b["note"]
          and await count("image_generate") == n_gen)
    await p.js("document.querySelector('.ip-prompt').value = ''")
    await shot("4-panneau-pieces-bing.png", "#image-panel")
    await p.js("(() => { const s = document.querySelector('.ip-engine'); s.value = 'gemini'; s.dispatchEvent(new Event('change')); })()")
    await p.until("!document.querySelector('.ip-send').disabled", 3)
    n = await p.js("document.querySelectorAll('.msg.image-job').length")
    await p.js("""(() => { const a = document.querySelector('.ip-prompt'); a.value = 'le même croquis en aquarelle';
        document.querySelector('.ip-send').click(); })()""")
    await p.until(f"document.querySelectorAll('.msg.image-job').length === {n + 1}", 4)
    g = await last_sent("image_generate")
    files = g.get("files") or []
    ok = await p.until(f"{last}.classList.contains('image-fini')", 14)
    pieces_txt = await p.js(f"{last}.querySelector('.image-pieces')?.textContent")
    check(f"P40 A5 Générer avec pièce : files = chemin absolu des pièces jointes ({files[:1]}), « {pieces_txt} », pièces vidées",
          len(files) == 1 and files[0].startswith("/") and "/attachments/" in files[0] and files[0].endswith("croquis.png")
          and ok and pieces_txt == "Avec 1 pièce jointe" and await p.js("document.querySelectorAll('.ip-pieces .att').length === 0"))
    # aller-retour 1 (fiche 3) : pendant l'attente de l'accusé, l'utilisateur retape et ajoute une pièce : gardées
    n_gen = await count("image_generate")
    await p.js("""(async () => { const a = document.querySelector('.ip-prompt'); a.value = 'un second croquis';
        document.querySelector('.ip-send').click();
        a.value = 'idée suivante';
        const c = document.createElement('canvas'); c.width = 40; c.height = 30;
        const png = await new Promise(r => c.toBlob(r, 'image/png'));
        const dt = new DataTransfer(); dt.items.add(new File([png], 'apres.png', { type: 'image/png' }));
        const i = document.querySelector('#image-panel input[type=file]'); i.files = dt.files;
        i.dispatchEvent(new Event('change')); return true; })()""")
    ok = await p.until(f"window.__sent.filter(m => m.cmd === 'image_generate').length === {n_gen + 1}"
                       " && !document.querySelector('.ip-send').disabled && !document.querySelector('.ip-pieces .att-pending')", 6)
    g = await last_sent("image_generate")
    kept = await p.js("""(() => ({ prompt: document.querySelector('.ip-prompt').value,
        names: [...document.querySelectorAll('.ip-pieces .att-name')].map(n => n.textContent) }))()""")
    check(f"P40 A5 accusé : texte retapé gardé (« {kept['prompt']} »), pièce ajoutée pendant l'envoi gardée ({kept['names']})",
          ok and g.get("prompt") == "un second croquis" and g.get("files") == [] and kept["prompt"] == "idée suivante"
          and kept["names"] == ["apres.png"])
    await p.js("document.querySelector('.ip-pieces .att .close').click(); document.querySelector('.ip-prompt').value = ''")
    # retour en Auto (réglage), Échap ferme le panneau
    await p.js("[...document.querySelectorAll('.ip-mode button')].find(b => b.textContent === 'Auto').click()")
    await p.until("window.__sent.some(m => m.cmd === 'settings_set' && m.key === 'images_mode' && m.value === 'auto')"
                  " && document.querySelector('.ip-engine').disabled", 3)
    await p.js("document.querySelector('.ip-prompt').focus()")
    await p.key("Escape", vk=27)
    check("P40 A5 Échap : panneau refermé, bouton éteint",
          await p.until("document.querySelector('#image-panel').classList.contains('hidden') "
                        "&& document.querySelector('#images-btn').getAttribute('aria-expanded') === 'false'", 2))

    # --- Réglages › Images
    await p.js("document.querySelector('#settings-chip').click()")
    await p.until("document.querySelectorAll('.modal.wide .settings-group').length === 15", 3)
    await p.js("(() => { const d = document.querySelector('.modal.wide details[data-group=images]'); d.open = true; "
               "d.scrollIntoView({block: 'start'}); })()")
    ok = await p.until("document.querySelectorAll('.img-account').length === 4", 3)
    acc = await p.js("""(() => { const g = document.querySelector('.modal.wide details[data-group=images]');
        const rows = [...g.querySelectorAll('.img-account')].map(li => ({ alias: li.querySelector('.img-alias').textContent,
          lines: [...li.querySelectorAll('.info-line')].map(l => l.textContent), buttons: [...li.querySelectorAll('button')].map(b => b.textContent || b.getAttribute('aria-label')) }));
        return { rows, icons: [...g.querySelectorAll('.setting')].map(s => [s.dataset.key, s.querySelector('.setting-head svg.ico')?.dataset.icon]),
                 caps: [...g.querySelectorAll('[data-key="images.plafonds"] .info-line')].map(l => l.textContent),
                 group: g.querySelector('.settings-group-ico svg')?.dataset.icon }; })()""")
    rows = {r["alias"]: r for r in acc["rows"]}
    check(f"P40 A5 Réglages › Images : icônes {acc['group']} / {acc['icons']}",
          acc["group"] == "image" and dict(acc["icons"]) == {"images_mode": "zap", "images_ordre": "layers", "images_manuel": "target",
                                                               "images.comptes": "user", "images.plafonds": "gauge",
                                                               "video_choix": "play", "videos.comptes": "play"})  # P41 B4
    check(f"P40 A5 comptes dans l'ordre du repli ({list(rows)}) : principal compté ({rows.get('principal', {}).get('lines')}), famille épuisé, perso pas connecté, Bing sans compte",
          ok and list(rows) == ["principal", "famille", "perso", "Bing Image Creator"]
          and len(rows["principal"]["lines"]) == 1 and re.fullmatch(r"GeminiDisponible · ([4-9]|1\d)/20 aujourd'hui", rows["principal"]["lines"][0])
          and rows["famille"]["lines"][0].startswith("GeminiÉpuisé, de nouveau essayé vers ")
          and rows["perso"]["lines"] == ["ChatGPTPas connecté · 0/3 sur 24 h"]
          and rows["Bing Image Creator"]["buttons"] == []
          and rows["perso"]["buttons"][0] == "Connecter" and rows["principal"]["buttons"][0] == "Reconnecter")
    check(f"P40 A5 plafonds ({acc['caps']})",
          acc["caps"] == ["Gemini20 par compte et par jour (remise à zéro à minuit) · pièces jointes acceptées",
                          "ChatGPT3 par compte sur 24 h (depuis la première image) · pièces jointes acceptées",
                          "Bing Image Creator15 par compte et par jour (remise à zéro à minuit) · sans compte · sans pièce jointe"])
    await shot("5-reglages-images.png", ".modal.wide details[data-group=images]")
    # Connecter perso : commande, message, ligne « fenêtre ouverte » (connexion simulée)
    await p.js("document.querySelector('[data-fkey=\"images|perso|connecter\"]').click()")
    await p.until("window.__sent.some(m => m.cmd === 'image_compte_connecter' && m.alias === 'perso')", 3)
    ok = await p.until("/Fenêtre Chrome ouverte sur le compte perso/.test(document.querySelector('[data-key=\"images.comptes\"] .setting-result')?.textContent || '')"
                       " && /Fenêtre de connexion ouverte/.test(document.querySelector('.img-account[data-alias=perso]').textContent)"
                       " && document.querySelector('[data-fkey=\"images|perso|connecter\"]').disabled", 4)
    check("P40 A5 « Connecter » : image_compte_connecter, message de la fenêtre, bouton grisé tant qu'elle est ouverte", ok)
    # monter famille : ordre [famille, principal, perso], puis le redescendre
    await p.js("document.querySelector('[data-fkey=\"images|famille|monter\"]').click()")
    await p.until("window.__sent.some(m => m.cmd === 'image_compte_ordre')", 3)
    o = await last_sent("image_compte_ordre")
    ok = await p.until("[...document.querySelectorAll('.img-account[data-alias]')].map(l => l.dataset.alias).join() === 'famille,principal,perso'", 3)
    check(f"P40 A5 monter famille : image_compte_ordre {o.get('ordre')}, liste redessinée", ok and o.get("ordre") == ["famille", "principal", "perso"])
    await p.js("document.querySelector('[data-fkey=\"images|famille|descendre\"]').click()")
    await p.until("[...document.querySelectorAll('.img-account[data-alias]')].map(l => l.dataset.alias).join() === 'principal,famille,perso'", 3)
    # ajouter (nom refusé, puis accepté), retirer (boîte rouge)
    await p.js("document.querySelector('[data-fkey=\"images|ajouter|google\"]').click()")
    await p.until("!!document.querySelector('.modal:not(.wide) input')", 2)
    await p.js("(() => { const i = document.querySelector('.modal:not(.wide) input'); i.value = 'Mauvais Nom'; "
               "document.querySelector('.modal:not(.wide) .modal-ok').click(); })()")
    bad = await p.until("/Nom de compte invalide/.test(document.querySelector('.modal:not(.wide)')?.textContent || '')", 2)
    await p.js("(() => { const i = document.querySelector('.modal:not(.wide) input'); i.value = 'portfolio'; "
               "document.querySelector('.modal:not(.wide) .modal-ok').click(); })()")
    ok = await p.until("!!document.querySelector('.img-account[data-alias=portfolio]') && !document.querySelector('.modal:not(.wide)')", 4)
    a = await last_sent("image_compte_ajouter")
    check(f"P40 A5 ajouter : nom invalide refusé dans la boîte, puis image_compte_ajouter {a and {k: a.get(k) for k in ('alias', 'fournisseur')}}",
          bad and ok and a.get("alias") == "portfolio" and a.get("fournisseur") == "google")
    await p.js("document.querySelector('[data-fkey=\"images|portfolio|retirer\"]').click()")
    red = await p.until("!!document.querySelector('.modal.danger')", 2)
    await p.js("document.querySelector('.modal.danger .modal-ok').click()")
    ok = await p.until("!document.querySelector('.img-account[data-alias=portfolio]')", 4)
    check("P40 A5 retirer : boîte rouge, image_compte_retirer, compte retiré de la liste",
          red and ok and (await last_sent("image_compte_retirer") or {}).get("alias") == "portfolio")
    found = await p.js(EMOJI_DOM_JS)
    check(f"P40 A5 : aucun émoji dans le DOM ({found[:3]})", found == [])
    await shot("6-reglages-images-apres.png", ".modal.wide details[data-group=images]")
    await p.js("document.querySelector('.modal.wide .modal-x').click()")
    await p.until("!document.querySelector('.modal-back')", 2)
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)


async def check_medias(p: Page) -> None:
    """22/10 (plan P26, demande de l'utilisateur : « afficher tout, pdf, image, vidéo ») : médias du fil
    par le VRAI module astrel.medias de la démo. Carte show_media (galerie d'images, PDF page par
    page, son, vidéo créée au clic), compte rendu avec médias, question à choix illustrée, « Ouvrir »
    seul pour un fichier trop gros, secret refusé ; micro retenu pendant la lecture (`media_playing`),
    un seul lecteur vivant ; CSP et 404 de la route ; Simple (taille et chemin masqués) ; 3 thèmes."""
    # 03/11 (CI) : sans poppler, la carte PDF n'a pas de pages et la suite plantait sur `.media-step` absent
    poppler = bool(shutil.which("pdfinfo") and shutil.which("pdftoppm"))
    check("médias : poppler-utils présent (pdfinfo, pdftoppm) pour les aperçus PDF", poppler)
    if not poppler:
        return
    await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await p.js("document.querySelectorAll('.modal-back').forEach(b => b.remove())")
    chan = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || ''")

    async def demo(cmd, **args):
        await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': cmd, 'channel': chan, **args}))})")

    async def mouse_click(expr):  # vrai clic (activation utilisateur, comme l'utilisateur)
        r = await p.js(f"(() => {{ const e = {expr}; e.scrollIntoView({{block: 'center'}}); const b = e.getBoundingClientRect();"
                       " return [b.left + b.width / 2, b.top + b.height / 2]; })()")
        for kind in ("mousePressed", "mouseReleased"):
            await p.send("Input.dispatchMouseEvent", type=kind, x=r[0], y=r[1], button="left", clickCount=1)

    async def card_shot(name, expr):  # fenêtre ET fil hauts le temps de la capture : la carte entière, rien par-dessus
        await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=1700, deviceScaleFactor=1, mobile=False)
        await p.js("(() => { const d = document.querySelector('#dialog'); d.dataset.shotH = d.style.getPropertyValue('--chat-h');"
                   " d.style.setProperty('--chat-h', '1350px'); return true; })()")
        await p.js(f"{expr}?.scrollIntoView({{block: 'center'}})")
        await asyncio.sleep(0.4)
        b = await p.js(f"(() => {{ const r = {expr}?.getBoundingClientRect();"
                       " return r && { x: r.left - 8, y: r.top - 8, width: r.width + 16, height: r.height + 16 }; })()")
        if b:
            r = await p.send("Page.captureScreenshot", format="png",
                             clip={"x": max(0, b["x"]), "y": max(0, b["y"]), "width": b["width"], "height": b["height"], "scale": 1})
            (OUT / name).write_bytes(base64.b64decode(r["data"]))
        await p.js("(() => { const d = document.querySelector('#dialog'), h = d.dataset.shotH || '';"
                   " if (h) d.style.setProperty('--chat-h', h); else d.style.removeProperty('--chat-h');"
                   " delete d.dataset.shotH; return true; })()")
        await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)

    async def theme(name, mode="dark"):  # 25/10 (plan P35) : Premium seul, `name` ignoré
        return await premium_mode(p, mode)

    card = "[...document.querySelectorAll('.msg.media-msg')].at(-1)"
    plays = "window.__sent.filter(m => m.cmd === 'media_playing').map(m => m.playing)"
    n0 = await p.js("document.querySelectorAll('.msg.media-msg').length")
    await demo("demo_media", kind="show")
    # Miniatures `loading=lazy` : chargées seulement près de l'écran. Après le parcours complet, le
    # fil peut ne plus être collé en bas (bouton ↓) : la carte est amenée à l'écran, comme l'utilisateur
    # le ferait en descendant (sinon échec « non chargées » du 22/10, 15 h 46 et 15 h 54).
    await p.until(f"document.querySelectorAll('.msg.media-msg').length > {n0}", 8)
    await p.js(f"{card}?.scrollIntoView({{block: 'center'}}), true")
    # 22/10 soir : échec au passage COMPLET seulement (ciblé : ok). Sous charge, une miniature ou
    # la page 1 peut mettre plus de 15 s (calcul dans l'executor de la démo, partagé). On attend
    # la fin RÉELLE du chargement (évènement load ou error, `complete`) jusqu'à 40 s, puis on
    # distingue « encore en cours » de « en erreur » dans le libellé (`imgs`).
    # Mesuré au passage complet (19:20) : serveur 200 en ~0,2 s pour les 3, mais les 2 miniatures
    # « en cours » 40 s : le fil, moins haut à ce stade, coupait le haut de la carte centrée et une
    # image paresseuse hors de la zone visible du fil n'est jamais demandée. Comme l'utilisateur qui fait
    # défiler : la première image pas encore finie est amenée à l'écran à chaque relevé.
    imgs_ok = (f"(() => {{ const c = {card}; if (!c) return false; const all = [...c.querySelectorAll('.media-thumb img, .media-pdf img')];"
               " all.find(i => !i.complete)?.scrollIntoView({block: 'nearest'});"
               " return c.querySelectorAll('.media-thumb img').length === 2 && !!c.querySelector('.media-pdf img')"
               " && all.every(i => i.complete && i.naturalWidth > 0); })()")
    t_load = time.time()
    ok = await p.until(imgs_ok, 40)
    t_load = time.time() - t_load
    imgs = await p.js(f"""(() => {{ const c = {card}; return c ? [...c.querySelectorAll('.media-thumb img, .media-pdf img')]
        .map(i => (i.complete ? (i.naturalWidth > 0 ? 'chargée' : 'ERREUR') : 'en cours') + (i.getClientRects().length ? '' : ' (invisible)'))
        : 'carte absente'; }})()""")
    if not ok:  # dit au serveur ce qu'il rend pour chaque image (statut HTTP), sans rien changer à la carte
        imgs = [imgs, await p.js(f"""(async () => {{ const out = [];
            for (const i of {card}.querySelectorAll('.media-thumb img, .media-pdf img')) {{
              const t = performance.now();
              try {{ const r = await fetch(i.getAttribute('src'), {{ cache: 'no-store', signal: AbortSignal.timeout(4000) }}); out.push(r.status + ' en ' + Math.round(performance.now() - t) + ' ms'); }}
              catch (e) {{ out.push('fetch refusé : ' + e.name); }} }}
            return out; }})()""")]
    info = await p.js(f"""(() => {{ const c = {card}, ids = /^\\/api\\/media\\/[0-9a-f]{{24}}(\\?v=(thumb|page&n=\\d+))?$/;
        const srcs = [...c.querySelectorAll('img, audio')].map(e => e.getAttribute('src'));
        const a = c.querySelector('audio');
        return {{ title: c.querySelector('.media-title')?.textContent, srcs: srcs.every(s => ids.test(s)), n: srcs.length,
                 lazy: [...c.querySelectorAll('img')].every(i => i.loading === 'lazy'), at: c.querySelector('.media-at')?.textContent,
                 prev: c.querySelector('.media-step').disabled, audio: a && [a.preload, a.controls, a.paused],
                 video: !!c.querySelector('video'), play: c.querySelector('.media-play')?.textContent,
                 size: c.querySelector('.media-size')?.textContent || '', path: c.querySelector('.media-path')?.textContent || '',
                 sizeShown: !!c.querySelector('.media-size')?.getClientRects().length,
                 hidden: c.classList.contains('hidden'), wide: c.scrollWidth > c.clientWidth + 1 }}; }})()""")
    check(f"P26 show_media : carte « {info['title']} », 2 miniatures et page 1 du PDF chargées ({ok}, {t_load:.1f} s, {imgs}), {info['n']} sources "
          f"/api/media/<id> seulement ({info['srcs']}), paresseuses ({info['lazy']}), « {info['at']} » (◀ grisé), "
          f"audio {info['audio']}, aucune <video> avant le clic (bouton « {info['play']} »), cachée {info['hidden']}, "
          f"déborde {info['wide']}",
          ok and info["title"] == "Affiches et devis pour la fête des voisins" and info["srcs"] and info["lazy"] and info["at"] == "1/3"
          and info["prev"] and info["audio"] == ["none", True, True] and not info["video"] and info["play"] == "Lire la vidéo"
          and not info["hidden"] and not info["wide"])
    check(f"P26 Avancé : taille « {info['size']} » et chemin d'origine visibles ({info['path'][:30]}…)",
          info["sizeShown"] and re.fullmatch(r"\d+(,\d)? (o|Ko|Mo)", info["size"]) is not None
          and info["path"].startswith("/tmp/"))
    await card_shot("p26-media-carte.png", card)
    # PDF : page suivante
    await p.js(f"{card}.querySelectorAll('.media-step')[1].click()")
    ok = await p.until(f"{card}.querySelector('.media-at').textContent === '2/3' && "
                       f"{card}.querySelector('.media-pdf img').getAttribute('src').endsWith('n=2') && "
                       f"{card}.querySelector('.media-pdf img').complete && {card}.querySelector('.media-pdf img').naturalWidth > 0", 8)
    check("P26 PDF : ▶ passe à « 2/3 », page 2 rendue par le cœur (pdftoppm)", ok)
    await p.js(f"{card}.querySelector('.media-page').click()")
    page_src = await p.until("!!document.querySelector('.modal.viewer img.viewer-img')", 3) and \
        await p.js("document.querySelector('.modal.viewer img.viewer-img').getAttribute('src')")
    await p.key("Escape", vk=27)
    check(f"P26 PDF : clic sur la page = cette page en grand ({page_src}), Échap ferme",
          bool(page_src) and re.fullmatch(r"http://127\.0\.0\.1:\d+/api/media/[0-9a-f]{24}\?v=page&n=2", page_src or "") is not None
          and await p.until("!document.querySelector('.modal.viewer')", 3))
    # visionneuse : l'ORIGINAL, pas la miniature
    await p.js(f"{card}.querySelector('.media-thumb').click()")
    src = await p.until("!!document.querySelector('.modal.viewer img.viewer-img')", 3) and \
        await p.js("document.querySelector('.modal.viewer img.viewer-img').getAttribute('src')")
    await p.key("Escape", vk=27)
    check(f"P26 image : clic = visionneuse sur l'original ({src}), Échap la ferme",
          bool(src) and re.fullmatch(r"http://127\.0\.0\.1:\d+/api/media/[0-9a-f]{24}", src or "") is not None
          and await p.until("!document.querySelector('.modal.viewer')", 3))
    # Ouvrir : commande réservée au HUD, avec l'id du PDF
    await p.js(f"{card}.querySelector('.media-pdf .media-open').click()")
    sent_open = await p.until("window.__sent.some(m => m.cmd === 'media_open')", 3) and \
        await p.js("window.__sent.filter(m => m.cmd === 'media_open').at(-1)")
    check(f"P26 « Ouvrir » : media_open avec media_id ({(sent_open or {}).get('media_id')})",
          bool(sent_open) and re.fullmatch(r"[0-9a-f]{24}", str(sent_open.get("media_id"))) is not None)
    # vidéo au clic, micro retenu, un seul lecteur
    await mouse_click(f"{card}.querySelector('.media-play')")
    made = await p.until(f"!!{card}.querySelector('video')", 3)
    played = await p.until(f"{plays}.at(-1) === true || !!{card}.querySelector('.media-video-box .media-note')", 8)
    vstate = await p.js(f"""(() => {{ const v = {card}.querySelector('video'), n = {card}.querySelector('.media-video-box .media-note');
        return {{ playing: !!v && !v.paused, note: n?.textContent || '', autoplay: !!v?.autoplay, focus: document.activeElement === v,
                 src: v?.getAttribute('src') || '' }}; }})()""")
    await card_shot("p26-media-video.png", f"{card}.querySelector('.media-video')")
    check(f"P26 vidéo : <video> créée au clic (sans autoplay), lecture {vstate['playing']} ou raison « {vstate['note']} », "
          f"micro retenu ({await p.js(plays)})",
          made and played and not vstate["autoplay"]
          and ((vstate["playing"] and (await p.js(plays))[-1] is True) or vstate["note"].startswith("Ce format")))
    if vstate["playing"]:
        r = await p.send("Runtime.evaluate", expression=f"{card}.querySelector('audio').play().then(() => true, e => String(e))",
                         awaitPromise=True, returnByValue=True, userGesture=True)
        audio_ok = r["result"].get("value") is True
        back = await p.until(f"!{card}.querySelector('video') && !!{card}.querySelector('.media-play')", 4)
        check(f"P26 un seul lecteur : lancer le son ({audio_ok}) arrête la vidéo, redevenue carte ►, micro toujours retenu",
              audio_ok and back and (await p.js(plays))[-1] is True)
        await p.js(f"{card}.querySelector('audio').pause()")
        check(f"P26 son en pause : micro relâché (media_playing {await p.js(plays)})",
              await p.until(f"{plays}.at(-1) === false", 3))
    # compte rendu avec médias
    await demo("demo_media", kind="report")
    rep = "[...document.querySelectorAll('.msg.astrel')].at(-1)"
    ok = await p.until(f"{rep}?.querySelector('.md')?.textContent.startsWith('Le devis est prêt')"
                       f" && {rep}.querySelectorAll('.media-list .media-thumb img').length === 1"
                       f" && !!{rep}.querySelector('.media-list .media-pdf') && !!{rep}.querySelector('.report-files')", 8)
    order = await p.js(f"[...{rep}.children].map(c => c.className.split(' ')[0])")
    check(f"P26 post_report(files) : médias sous la liste des chemins ({order})",
          ok and order.index("report-files") < order.index("media-list"))
    await card_shot("p26-media-compte-rendu.png", rep)
    # question à choix illustrée
    await demo("demo_media", kind="choice")
    q = "[...document.querySelectorAll('.msg.choice')].at(-1)"
    ok = await p.until(f"{q}?.querySelector('.choice-question').textContent === 'Quelle affiche pour la fête des voisins ?'"
                       f" && {q}.querySelectorAll('.choice-gallery .media-thumb img').length === 3", 6)
    letters = await p.js(f"[...{q}.querySelectorAll('.choice-gallery .media-letter')].map(e => e.textContent)")
    pos = await p.js(f"[...{q}.children].map(c => c.classList.contains('choice-gallery') ? 'choice-gallery' : c.className.split(' ')[0])")
    check(f"P26 ask_choice(images) : galerie de 3 miniatures {letters}, au-dessus des réponses ({pos})",
          ok and letters == ["a)", "b)", "c)"] and pos.index("choice-gallery") < pos.index("choice-options"))
    await card_shot("p26-media-question.png", q)
    # trop gros : Ouvrir seul ; secret : refusé, aucune carte
    n = await p.js("document.querySelectorAll('.msg.media-msg').length")
    await demo("demo_media", kind="big")
    ok = await p.until(f"document.querySelectorAll('.msg.media-msg').length === {n + 1}", 4)
    big = await p.js(f"(() => {{ const c = {card}; return {{ note: c.querySelector('.media-note')?.textContent, "
                     f"open: !!c.querySelector('.media-open'), media: c.querySelectorAll('img, video, audio').length }}; }})()")
    check(f"P26 trop gros : « {big['note']} », bouton Ouvrir seul, rien de chargé",
          ok and big["note"].startswith("Trop gros") and big["open"] and big["media"] == 0)
    await demo("demo_media", kind="secret")
    await asyncio.sleep(0.6)
    check("P26 fichier de config/ d'Astrel : refusé, aucune carte",
          await p.js("document.querySelectorAll('.msg.media-msg').length") == n + 1)
    # route : CSP, nosniff, 404
    route = await p.js(f"""(async () => {{ const u = [...document.querySelectorAll('.media-thumb img')].at(-1).getAttribute('src');
        const r = await fetch(u), bad = await fetch('/api/media/..%2F..%2Fconfig'), unk = await fetch('/api/media/' + '0'.repeat(24));
        return {{ csp: r.headers.get('content-security-policy'), nosniff: r.headers.get('x-content-type-options'),
                 type: r.headers.get('content-type'), bad: bad.status, unk: unk.status }}; }})()""")
    check(f"P26 route /api/media : CSP « {route['csp']} », {route['nosniff']}, {route['type']}, 404 ({route['bad']}, {route['unk']})",
          # 04/11 (lot B, M4) : le serveur ajoute `frame-ancestors 'none'` à toute CSP.
          route["csp"] == "default-src 'none'; sandbox; frame-ancestors 'none'" and route["nosniff"] == "nosniff"
          and route["type"] == "image/webp"
          and route["bad"] == 404 and route["unk"] == 404)
    # Simple : cartes visibles, ni taille ni chemin
    await p.js("document.documentElement.dataset.affichage = 'simple', true")
    await asyncio.sleep(0.3)
    s = await p.js("""(() => { const c = [...document.querySelectorAll('.msg.media-msg')].at(-2);
        return { shown: !c.classList.contains('hidden') && c.getClientRects().length > 0,
                 tech: [...document.querySelectorAll('.media-size, .media-path')].filter(e => e.getClientRects().length).length,
                 names: [...c.querySelectorAll('.media-name')].filter(e => e.getClientRects().length).length }; })()""")
    await card_shot("p26-media-simple.png", "[...document.querySelectorAll('.msg.media-msg')].at(-2)")
    await p.js("delete document.documentElement.dataset.affichage, true")
    check(f"P26 Simple : carte visible, noms gardés ({s['names']}), taille et chemin masqués ({s['tech']} visibles)",
          s["shown"] and s["tech"] == 0 and s["names"] >= 3)
    # 700 px : rien ne déborde
    await p.send("Emulation.setDeviceMetricsOverride", width=700, height=900, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(0.5)
    w = await p.js("""(() => { const t = document.querySelector('#transcript');
        return { fil: t.scrollWidth - t.clientWidth, cards: [...document.querySelectorAll('.msg.media-msg, .media-card')]
          .filter(c => c.getClientRects().length && c.scrollWidth > c.clientWidth + 1).length }; })()""")
    check(f"P26 700 px : ni fil ({w['fil']} px) ni carte ({w['cards']}) qui débordent", w["fil"] <= 0 and w["cards"] == 0)
    await p.send("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
    # thèmes
    for name, mode in (("premium", "dark"), ("premium", "light"), ("premium", "auto")):  # P35 : Premium seul
        ok = await theme(name, mode)
        await asyncio.sleep(0.4)
        await p.js("document.getAnimations().forEach(a => { if (a instanceof CSSTransition) a.finish(); }), true")
        c = await p.js(f"""(() => {{ const c = {card.replace('.at(-1)', '.at(-2)')}, st = getComputedStyle(c),
            o = getComputedStyle(c.querySelector('.media-open'));
            return {{ bg: st.backgroundColor, border: st.borderTopColor, ink: o.color, obg: o.backgroundColor }}; }})()""")
        await card_shot(f"p26-media-{name}-{mode}.png", card.replace(".at(-1)", ".at(-2)"))
        check(f"P26 thème {name}/{mode} : carte {c['bg']} / bord {c['border']}, Ouvrir {c['ink']}", ok and c["bg"] and c["border"])
    # image abîmée (ou trop de pixels pour le cœur) : la miniature rend 404, la carte se replie
    n2 = await p.js("document.querySelectorAll('.msg.media-msg').length")
    await demo("demo_media", kind="broken")
    ok = await p.until(f"document.querySelectorAll('.msg.media-msg').length === {n2 + 1}", 4)
    await p.js(f"{card}?.scrollIntoView({{block: 'center'}}), true")
    folded = await p.until(f"!!{card}.querySelector('.media-only .media-note')", 8)
    br = await p.js(f"(() => {{ const c = {card}; return {{ note: c.querySelector('.media-note')?.textContent || '', "
                    f"open: !!c.querySelector('.media-open'), img: c.querySelectorAll('img').length, "
                    f"name: c.querySelector('.media-name')?.textContent || '' }}; }})()")
    check(f"P26 miniature absente (image abîmée, 404) : repli « {br['note']} », nom « {br['name']} », Ouvrir, aucune image cassée",
          ok and folded and br["note"] == "Aperçu indisponible." and br["open"] and br["img"] == 0 and br["name"] == "photo-abimee.png")
    found = await p.js(EMOJI_DOM_JS)
    check(f"P26 : aucun émoji dans le DOM ({found[:3]})", found == [])
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)


ASK_DEMO_JS = """((cmd, args) => new Promise((res) => {
  // commande à la démo AVEC sa réponse (accusé `ack` de même id ; conn.js ignore un id qu'il n'a pas émis)
  const ws = window.__ws, id = 800000 + Math.floor(Math.random() * 100000);
  const on = (ev) => { try { const m = JSON.parse(ev.data); if (m.type === 'ack' && m.id === id) {
    ws.removeEventListener('message', on); res(m.result); } } catch {} };
  ws.addEventListener('message', on); ws.send(JSON.stringify({ ...args, cmd, id })); setTimeout(() => res(null), 4000); }))"""

LIENS_JS = """((md) => [...md.querySelectorAll('a')].map(a => { const ico = a.querySelector(':scope > svg.ico');
  const txt = a.querySelector('.md-link-text'); return { text: a.textContent, href: a.href, target: a.target, rel: a.rel,
  cls: a.className, icon: ico?.dataset.icon || '', hidden: ico?.getAttribute('aria-hidden') || '', w: ico ? ico.getBoundingClientRect().width : 0,
  desc: a.getAttribute('aria-description') || '', after: (a.nextSibling?.textContent || '').slice(0, 2),
  clipped: !!txt && txt.scrollWidth > txt.clientWidth + 1, cursor: getComputedStyle(a).cursor }; }))"""


async def check_recherche_chemins_echap(p: Page) -> None:
    """04/11 (chat, lot 2, demandes de l'utilisateur) : chemins de fichiers d'une réponse copiés d'un clic (jamais dans
    un bloc ```, rien d'ouvert) ; Échap arrête la réponse comme ■ (commande `stop`), SEULEMENT sans carte ni
    fenêtre à l'écran ; Ctrl+K ouvre la recherche dans tous les canaux (`history_query`, index jetable de la
    démo), résultats groupés par canal, clic = le message dans son canal ; rien de tout cela en Simple."""
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await p.js("document.querySelectorAll('.modal-back .modal-x, .modal-back .modal-cancel').forEach(b => b.click()), true")
    await p.js("document.querySelectorAll('.menu, .toast').forEach(b => b.remove()), true")
    ask_demo = lambda cmd, **a: p.js(f"{ASK_DEMO_JS}({json.dumps(cmd)}, {json.dumps(a)})")
    sent = lambda cmd: p.js(f"window.__sent.filter(m => m.cmd === {json.dumps(cmd)}).length")
    await p.js("""window.__opened = []; window.open = (...a) => { window.__opened.push(a); return null; };
        window.__clip = null; navigator.clipboard.writeText = async (t) => { window.__clip = t; }; true""")
    start = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || 'general'")
    await p.js("document.querySelector('.channel-tab[data-chan=\"general\"]')?.click(), true")

    async def mouse(expr):  # vrai clic (activation utilisateur, `detail` 1)
        r = await p.js(f"(() => {{ const e = {expr}; e.scrollIntoView({{block: 'center'}}); const b = e.getBoundingClientRect();"
                       " return [b.left + Math.min(12, b.width / 2), b.top + b.height / 2]; })()")
        for kind in ("mousePressed", "mouseReleased"):
            await p.send("Input.dispatchMouseEvent", type=kind, x=r[0], y=r[1], button="left", clickCount=1)

    async def ctrl_k():
        base = {"key": "k", "code": "KeyK", "windowsVirtualKeyCode": 75, "modifiers": 2}
        await p.send("Input.dispatchKeyEvent", type="keyDown", **base)
        await p.send("Input.dispatchKeyEvent", type="keyUp", **base)

    async def esc():
        await p.key("Escape", vk=27)
        await asyncio.sleep(0.35)

    # ---------------------------------------------------------- chemins de fichiers : copiés, jamais ouverts
    await p.js("""(() => { const a = document.querySelector('#ask'); a.value = 'démo chemins'; a.dispatchEvent(new Event('input'));
        a.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); })()""")
    got = await p.until("[...document.querySelectorAll('.msg.astrel:not(.live) .md')].some(m => m.textContent.startsWith('Voici les fichiers'))", 10)
    md = "[...document.querySelectorAll('.msg.astrel .md')].filter(m => m.textContent.startsWith('Voici les fichiers')).at(-1)"
    paths = await p.js(f"""[...{md}.querySelectorAll('.md-path')].map(e => ({{ text: e.textContent, tag: e.tagName,
        role: e.getAttribute('role'), tab: e.tabIndex, title: e.title, desc: e.getAttribute('aria-description') || '',
        cursor: getComputedStyle(e).cursor, inPre: !!e.closest('pre') }}))""") if got else []
    want = ["/home/user/Projets/exemple/run.sh", "~/Documents/Notes/courses.md", "C:\\Users\\Lea\\Bureau\\devis.pdf",
            "/data/Mes documents/Note de frais.pdf", "/tmp/astrel-demo/a.py"]
    check(f"chemins d'une réponse : 5 reconnus, ni date, ni « et/ou », ni adresse, ni bloc ``` ({[x['text'] for x in paths]})",
          [x["text"] for x in paths] == want and not any(x["inPre"] for x in paths))
    check("chemins : rôle bouton, au clavier (tabindex 0), titre et description, curseur « copier », le code entier "
          "quand il n'est qu'un chemin",
          paths and all(x["role"] == "button" and x["tab"] == 0 and x["title"] and x["desc"] and x["cursor"] == "copy"
                        for x in paths) and [x["tag"] for x in paths] == ["SPAN", "SPAN", "SPAN", "CODE", "SPAN"])
    links = await sent("open_link")
    await mouse(f"{md}.querySelectorAll('.md-path')[0]")
    copied = await p.until(f"window.__clip === {json.dumps(want[0])}", 3)
    toast_ok = await p.until(f"[...document.querySelectorAll('.toast')].some(t => t.textContent.includes({json.dumps('Chemin copié : ' + want[0])}))", 3)
    check(f"clic sur un chemin : copié ({copied}), toast « Chemin copié », rien d'ouvert (ni open_link, ni onglet)",
          copied and toast_ok and await sent("open_link") == links and await p.js("window.__opened.length") == 0)
    confirms = await sent("confirm")
    await p.js(f"{md}.querySelectorAll('.md-path')[2].focus(), true")
    await p.key("Enter", vk=13)
    await asyncio.sleep(0.3)
    check("au clavier (Tab puis Entrée) : le chemin Windows est copié tel quel, Entrée ne remonte pas",
          await p.js("window.__clip") == want[2] and await sent("confirm") == confirms)
    await p.js(f"{md}.closest('.msg').querySelector('.copy-btn').click()")
    await asyncio.sleep(0.2)
    clip = await p.js("window.__clip") or ""
    check("« Copier » de la réponse : le texte brut, chemins et bloc compris",
          clip.startswith("Voici les fichiers :") and "`/data/Mes documents/Note de frais.pdf`" in clip
          and "cat /etc/hosts" in clip)
    await p.shot("chemins-reponse.png")
    await p.js("document.activeElement?.blur(), document.querySelectorAll('.toast').forEach(t => t.remove()), true")

    # ---------------------------------------------------------- Échap = ■, seulement sans carte
    # Questions à choix laissées en attente par les contrôles précédents : répondues ICI (page seule), sinon
    # Échap leur revient (c'est voulu, contrôlé plus bas).
    for cid in await p.js("[...document.querySelectorAll('.msg.choice:not(.answered):not(.cancelled) .choice-question')]"
                          ".map(q => q.id.replace(/^choice-q-/, ''))"):
        await fake_event(p, {"type": "choice_answered", "id": cid, "value": "(contrôle)"})
    await p.js("document.querySelector('#activity').classList.remove('hidden'), true")
    await ask_demo("demo_answering")
    await p.until("!!document.querySelector('.msg.astrel.live')", 3)
    stops = await sent("stop")
    await esc()
    check("Échap avec le panneau Activité ouvert : le panneau se ferme d'abord, aucun arrêt",
          await p.js("document.querySelector('#activity').classList.contains('hidden')") and await sent("stop") == stops)
    await p.js("document.querySelector('#stop-btn').click(), true")  # réponse close (même commande)
    await p.until("!document.querySelector('.msg.astrel.live')", 3)
    await asyncio.sleep(0.3)
    stops = await sent("stop")
    await esc()
    check("Échap sans réponse en cours : rien d'envoyé", await sent("stop") == stops)
    await ask_demo("demo_answering")
    live = await p.until("!!document.querySelector('.msg.astrel.live')", 3)
    await esc()
    stopped = await p.until(f"window.__sent.filter(m => m.cmd === 'stop').length === {stops + 1}", 3)
    tst = await p.until("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Réponse arrêtée (Échap).'))", 3)
    log = (await ask_demo("demo_stop_log") or {}).get("items") or []
    check(f"Échap pendant une réponse : `stop` (comme ■), via le HUD ({log[-1:]}), toast, réponse close",
          live and stopped and tst and log and log[-1]["via"] == "hud"
          and await p.until("!document.querySelector('.msg.astrel.live')", 3))
    stop_btn = await p.js("({ keys: document.querySelector('#stop-btn').getAttribute('aria-keyshortcuts'), "
                          "title: document.querySelector('#stop-btn').title })")
    check(f"bouton ■ : raccourci Échap annoncé ({stop_btn})",
          stop_btn["keys"] == "Escape" and stop_btn["title"].startswith("Arrêter (Échap)"))
    # carte de confirmation : Échap = « Non », jamais un arrêt ; une fois la carte partie, Échap arrête
    await ask_demo("demo_answering")
    await p.until("!!document.querySelector('.msg.astrel.live')", 3)
    stops, confirms = await sent("stop"), await sent("confirm")
    await ask_demo("demo_confirm")
    await p.until("!document.querySelector('#confirm').classList.contains('hidden')", 3)
    await esc()
    no = await p.js("window.__sent.filter(m => m.cmd === 'confirm').pop() || {}")
    check(f"Échap avec une carte de confirmation : « Non » à la carte ({no.get('allow')}), aucun arrêt",
          await sent("confirm") == confirms + 1 and no.get("allow") is False and await sent("stop") == stops)
    # question à choix visible : Échap ne l'arrête pas
    await ask_demo("demo_choice", channel="general")
    await p.until("!!document.querySelector('.msg.choice:not(.answered):not(.cancelled):not(.hidden)')", 3)
    await esc()
    check("Échap avec une question à choix à l'écran : aucun arrêt", await sent("stop") == stops)
    for cid in await p.js("[...document.querySelectorAll('.msg.choice:not(.answered):not(.cancelled) .choice-question')]"
                          ".map(q => q.id.replace(/^choice-q-/, ''))"):
        await ask_demo("choice_answer", choice_id=cid, value="Attendre ton retour")
    await p.until("!document.querySelector('.msg.choice:not(.answered):not(.cancelled):not(.hidden)')", 3)
    # boîte ouverte (Réglages) : Échap la ferme, la réponse continue
    await p.js("document.querySelector('#settings-chip').click(), true")
    await p.until("!!document.querySelector('.modal-back')", 3)
    await esc()
    check("Échap avec une fenêtre ouverte (Réglages) : la fenêtre se ferme, aucun arrêt",
          await p.js("!document.querySelector('.modal-back')") and await sent("stop") == stops)
    # menu ouvert : fermé d'abord ; puis dans la barre de saisie (permise) : arrêt
    await p.js("(() => { const m = document.createElement('div'); m.className = 'menu'; document.body.append(m); })()")
    await esc()
    check("Échap avec un menu ouvert : le menu se ferme d'abord, aucun arrêt",
          await p.js("!document.querySelector('.menu')") and await sent("stop") == stops)
    await p.js("document.querySelector('#ask').focus(), true")
    await esc()
    check("Échap dans la barre de saisie pendant la réponse : arrêt",
          await p.until(f"window.__sent.filter(m => m.cmd === 'stop').length === {stops + 1}", 3))
    await p.js("document.querySelector('#ask').blur(), document.querySelectorAll('.toast').forEach(t => t.remove()), true")

    # ---------------------------------------------------------- recherche dans tous les canaux (Ctrl+K)
    seed = await ask_demo("demo_recherche") or {}
    autre = seed.get("autre") or ""
    await asyncio.sleep(0.6)  # index du fil : un fil à part (history.FilIndex)
    stops = await sent("stop")
    await ctrl_k()
    opened = await p.until("!!document.querySelector('.modal.search-panel') && "
                           "document.activeElement?.classList.contains('search-input')", 3)
    check(f"Ctrl+K : fenêtre de recherche, curseur dans le champ ({opened})", opened)
    await p.send("Input.insertText", text="sirius")
    res = await p.until("document.querySelectorAll('.search-panel .search-group:not(.archive)').length >= 2 "
                        "&& !!document.querySelector('.search-panel .search-group.archive')", 5)
    view = await p.js("""(() => { const m = document.querySelector('.search-panel');
        return { groups: [...m.querySelectorAll('.search-group:not(.archive) .search-group-name')].map(e => e.textContent),
          hits: m.querySelectorAll('button.search-hit').length, marks: [...m.querySelectorAll('mark')].map(e => e.textContent.toLowerCase()),
          archive: m.querySelector('.search-group.archive .search-group-head')?.textContent || '',
          archiveHits: m.querySelectorAll('.search-group.archive .search-hit').length,
          status: m.querySelector('.search-status')?.textContent || '', role: m.querySelector('.search-status')?.getAttribute('role'),
          label: m.querySelector('.search-input').getAttribute('aria-label') }; })()""")
    q = await p.js("window.__sent.filter(m => m.cmd === 'history_query').pop() || {}")
    check(f"recherche : `history_query` ({q.get('query')!r}), groupes par canal {view['groups']}, mots marqués, "
          f"archives « {view['archive']} » ({view['status']})",
          res and q.get("query") == "sirius" and "Général" in view["groups"] and len(view["groups"]) == 2
          and view["hits"] >= 5 and view["marks"] and set(view["marks"]) == {"sirius"} and view["archiveHits"] == 1
          and view["archive"].startswith("Avant le") and view["role"] == "status" and view["label"])
    await p.shot("recherche-resultats.png")
    # résultat encore dans le fil : le canal s'ouvre, le message se montre (liseré, focus)
    hit = ("[...document.querySelectorAll('.search-panel button.search-hit')]"
           ".find(b => b.textContent.includes('Gîte réservé'))")
    await p.js(f"{hit}.click(), true")
    went = await p.until(f"!document.querySelector('.modal.search-panel') && "
                         f"document.querySelector('.channel-tab.on')?.dataset.chan === {json.dumps(autre)}", 3)
    shown = await p.js("""(() => { const m = document.activeElement?.closest?.('.msg');
        return { found: !!m && m.classList.contains('found'), text: m?.textContent || '' }; })()""")
    check(f"clic sur un résultat : fenêtre fermée, canal « {autre} » ouvert, message montré et sous le focus ({shown['found']})",
          went and shown["found"] and "Gîte réservé" in shown["text"])
    await p.shot("recherche-message-montre.png")
    # résultat sorti du fil affiché : l'extrait reste, note et « Ouvrir le canal »
    await p.js("document.querySelector('#search-btn').click(), true")
    again = await p.until("document.querySelector('.search-panel .search-input')?.value === 'sirius' "
                          "&& document.querySelectorAll('.search-panel button.search-hit').length >= 5", 5)
    old = ("[...document.querySelectorAll('.search-panel button.search-hit')]"
           ".find(b => b.textContent.includes('Ancienne note'))")
    await p.js(f"{old}.click(), true")
    note = await p.js(f"""(() => {{ const n = {old}.parentElement.querySelector('.search-note');
        return {{ shown: !n.classList.contains('hidden'), text: n.textContent, btn: n.querySelector('button')?.textContent || '',
                 open: !!document.querySelector('.modal.search-panel') }}; }})()""")
    check(f"bouton loupe : recherche rouverte telle quelle ({again}) ; message sorti du fil : la fenêtre reste, note "
          f"et « {note['btn']} »", again and note["shown"] and note["open"] and "n'est plus dans le fil" in note["text"]
          and note["btn"].startswith("Ouvrir le canal"))
    await esc()
    check("Échap ferme la recherche sans rien arrêter",
          await p.js("!document.querySelector('.modal.search-panel')") and await sent("stop") == stops)
    # Simple : contrôlé dans check_affichage_simple (bouton caché, Ctrl+K sans effet)
    btn = await p.js("""(() => { const b = document.querySelector('#search-btn'); return { shown: b.getClientRects().length > 0,
        keys: b.getAttribute('aria-keyshortcuts'), aria: b.getAttribute('aria-label') }; })()""")
    check(f"Avancé : bouton loupe visible au bout des onglets, Ctrl+K annoncé ({btn})",
          btn["shown"] and btn["keys"] == "Control+K" and btn["aria"] == "Rechercher dans les conversations")
    found = await p.js(EMOJI_DOM_JS)
    check(f"chemins, Échap, recherche : aucun émoji dans le DOM ({found[:3]})", found == [])
    await p.js(f"document.querySelector('.channel-tab[data-chan={json.dumps(start)}]')?.click(), true")
    await asyncio.sleep(0.3)


async def check_moteur_local(p: Page) -> None:
    """04/11 (moteur local, lot 1) : demande et réponse traitées par Qwen portent le badge « Local » ; la réponse
    locale s'écrit dans SA bulle, à côté d'une réponse de Claude en cours dans le même canal (jamais mêlées)."""
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    ch = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || ''")
    await fake_event(p, {"type": "transcript", "who": "user", "text": "en local : badge", "source": "clavier",
                         "channel": ch, "mid": "loc-q1", "engine": "local"})
    await fake_event(p, {"type": "reply_delta", "text": "Claude écrit ", "channel": ch, "reply_to": "x"})
    await fake_event(p, {"type": "reply_delta", "text": "Qwen écrit ", "channel": ch, "reply_to": "loc-q1",
                         "engine": "local"})
    await fake_event(p, {"type": "reply_delta", "text": "encore.", "channel": ch, "reply_to": "x"})
    await fake_event(p, {"type": "reply", "text": "Qwen écrit seul.", "channel": ch, "reply_to": "loc-q1",
                         "mid": "loc-r1", "engine": "local"})
    await fake_event(p, {"type": "reply", "text": "Claude écrit encore.", "channel": ch, "reply_to": "x", "mid": "cl-r1"})
    info = await p.js("""(() => { const ms = [...document.querySelectorAll('#transcript .msg')].slice(-3);
        return ms.map(m => ({ who: m.classList.contains('user') ? 'user' : 'astrel', local: m.classList.contains('engine-local'),
          badge: m.querySelector('.engine-badge')?.textContent || '', title: m.querySelector('.engine-badge')?.title || '',
          text: (m.querySelector('.md') || m.querySelector('span:not(.src):not(.engine-badge)'))?.textContent || '' })); })()""")
    check(f"moteur local : badge « Local » sur la demande et la réponse, bulles séparées de Claude ({info})",
          len(info) == 3 and info[0]["who"] == "user" and info[0]["local"] and info[0]["badge"] == "Local"
          and "rien n'est sorti" in info[0]["title"]
          and sorted((m["local"], m["text"]) for m in info[1:]) == [(False, "Claude écrit encore."),
                                                                    (True, "Qwen écrit seul.")])


async def check_liens_et_barre(p: Page) -> None:
    """04/11 (demande : « les liens ne sont pas ouvrables comme c'est le cas pour ChatGPT avec un logo indiquant
    que c'est un lien, on est obligé de copier-coller », puis « d'autres petits détails de ce genre ») :
    adresses nues cliquables dans les réponses ET dans ses messages (icône, nouvel onglet, ponctuation hors du
    lien, rien dans le code), ouvertes par le cœur (`open_link`) avec repli sur un onglet sans opener ;
    Copier sur ses messages (texte brut) ; ↑ dans la barre vide ; « / » pour la barre ; point du bouton ↓."""
    await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
    await p.until("!!window.__ws && window.__ws.readyState === 1", 10)
    await p.js("document.querySelectorAll('.modal-back, .menu, .toast').forEach(b => b.remove()), true")
    ask_demo = lambda cmd, **a: p.js(f"{ASK_DEMO_JS}({json.dumps(cmd)}, {json.dumps(a)})")
    await p.js("""window.__opened = []; window.open = (...a) => { window.__opened.push(a); return null; };
        window.__clip = null; navigator.clipboard.writeText = async (t) => { window.__clip = t; }; true""")

    async def mouse(expr, button="left"):  # vrai clic (activation utilisateur, `detail` 1, comme l'utilisateur)
        r = await p.js(f"(() => {{ const e = {expr}; e.scrollIntoView({{block: 'center'}}); const b = e.getBoundingClientRect();"
                       " return [b.left + Math.min(12, b.width / 2), b.top + b.height / 2]; })()")
        for kind in ("mousePressed", "mouseReleased"):
            await p.send("Input.dispatchMouseEvent", type=kind, x=r[0], y=r[1], button=button, clickCount=1)

    def say(text):
        return p.js(f"""(() => {{ const a = document.querySelector('#ask'); a.value = {json.dumps(text)};
            a.dispatchEvent(new Event('input')); a.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', bubbles: true }})); }})()""")

    # ---------------------------------------------------------- réponse : adresses nues, ponctuation, code
    await say("démo liens")
    got = await p.until("[...document.querySelectorAll('.msg.astrel:not(.live) .md')].some(m => m.textContent.startsWith('Voici les liens'))", 10)
    md = "[...document.querySelectorAll('.msg.astrel .md')].filter(m => m.textContent.startsWith('Voici les liens')).at(-1)"
    links = await p.js(f"{LIENS_JS}({md})") if got else []
    texts = [x["text"] for x in links]
    want = ["https://github.com/exemple/projet", "https://fr.wikipedia.org/wiki/Nantes_(France)", "https://example.org/page",
            "https://www.example.com/un/chemin/vraiment/tres/long/qui/n/en/finit/pas?avec=des&parametres=nombreux&encore=oui",
            "en markdown"]
    check(f"liens d'une réponse : 5 adresses cliquables, ponctuation et parenthèses justes ({texts})", texts == want)
    check("liens d'une réponse : point final hors du lien (« . »), virgule après la parenthèse (« , »)",
          len(links) == 5 and links[0]["after"].startswith(".") and links[1]["after"].startswith(","))
    check(f"liens d'une réponse : icône « ouvrir ailleurs » visible et cachée aux lecteurs d'écran, nouvel onglet sans "
          f"opener ni referrer, main au survol ({[(x['icon'], round(x['w'])) for x in links][:2]})",
          links and all(x["icon"] == "external-link" and x["hidden"] == "true" and x["w"] >= 8 and x["target"] == "_blank"
                        and "noopener" in x["rel"] and "noreferrer" in x["rel"] and x["cursor"] == "pointer" and x["desc"]
                        and x["href"].startswith("https://") for x in links))
    code = await p.js(f"""(() => {{ const m = {md}; return {{ inCode: m.querySelectorAll('code a, pre a').length,
        codeText: [...m.querySelectorAll('code')].map(c => c.textContent).join('|'), http: m.textContent.includes('http://pas-https.example') }}; }})()""")
    check(f"jamais de lien dans le code (en ligne, bloc) ni en http:// ({code['codeText']})",
          code["inCode"] == 0 and "https://code.example/en-ligne" in code["codeText"] and "https://bloc.example/jamais" in code["codeText"]
          and code["http"] and not any("code.example" in t or "bloc.example" in t or "pas-https" in t for t in texts))
    long_ = links[3] if len(links) == 5 else {}
    check(f"adresse très longue : rognée à l'affichage (« … », classe {long_.get('cls')}), texte et href entiers",
          long_.get("clipped") and "long" in long_.get("cls", "") and long_.get("text") == want[3] and long_.get("href") == want[3])
    sel = await p.js(f"""(() => {{ const t = {md}.querySelectorAll('a')[3].querySelector('.md-link-text'), r = document.createRange();
        r.selectNodeContents(t); const s = getSelection(); s.removeAllRanges(); s.addRange(r); const v = s.toString();
        s.removeAllRanges(); return v; }})()""")
    check("adresse rognée : la sélection à la main donne l'adresse ENTIÈRE", sel == want[3])
    await p.js(f"{md}.closest('.msg').querySelector('.copy-btn').click()")
    await asyncio.sleep(0.2)
    clip = await p.js("window.__clip")
    check("« Copier » d'une réponse à liens : le texte brut (markdown compris)",
          bool(clip) and clip.startswith("Voici les liens :") and "[en markdown](https://docs.example.org/guide)" in clip)
    await p.shot("liens-reponse.png")

    # ---------------------------------------------------------- clic : navigateur du système (cœur), repli
    before = len((await ask_demo("demo_link_log"))["items"])
    await mouse(f"{md}.querySelectorAll('a')[0]")
    opened = await p.until("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Lien ouvert dans ton navigateur'))", 4)
    log = (await ask_demo("demo_link_log"))["items"]
    focus = await p.js("document.activeElement?.tagName || ''")
    check(f"clic sur un lien : `open_link` au cœur (navigateur de l'utilisateur), via le HUD, toast, aucun onglet du HUD, "
          f"le lien ne garde pas le focus ({log[before:]}, {focus})",
          opened and log[before:] == [{"url": "https://github.com/exemple/projet", "via": "hud"}]
          and await p.js("window.__opened.length") == 0 and focus != "A")
    await mouse(f"{md}.querySelectorAll('a')[4]", button="middle")
    await asyncio.sleep(0.4)
    log = (await ask_demo("demo_link_log"))["items"]
    check(f"clic du milieu : même chemin ({log[-1]})", log[-1] == {"url": "https://docs.example.org/guide", "via": "hud"})
    await ask_demo("demo_link_log", fail=True)
    await mouse(f"{md}.querySelectorAll('a')[2]")
    await p.until("window.__opened.length === 1", 4)
    fb = await p.js("window.__opened")
    await ask_demo("demo_link_log", fail=False)
    check(f"cœur qui refuse (plus ancien) : repli sur un onglet SANS opener ni referrer ({fb})",
          fb == [["https://example.org/page", "_blank", "noopener,noreferrer"]])
    await p.js(f"{md}.querySelectorAll('a')[1].focus(), true")
    await p.key("Enter", vk=13)
    await asyncio.sleep(0.4)
    log = (await ask_demo("demo_link_log"))["items"]
    check(f"au clavier (Tab puis Entrée) : le lien s'ouvre aussi ({log[-1]['url']})",
          log[-1]["url"] == "https://fr.wikipedia.org/wiki/Nantes_(France)")
    await p.js("document.activeElement?.blur(), true")
    ctx = await p.js(f"""(() => {{ const m = {md}, fire = (e) => {{ const ev = new MouseEvent('contextmenu', {{ bubbles: true,
          cancelable: true, clientX: 500, clientY: 500 }}); e.dispatchEvent(ev); const r = {{ prevented: ev.defaultPrevented,
          menu: !!document.querySelector('.menu') }}; document.querySelectorAll('.menu').forEach(x => x.remove()); return r; }};
        return {{ link: fire(m.querySelector('a .md-link-text')), text: fire(m.querySelector('li')) }}; }})()""")
    check(f"clic droit sur un lien : menu de Chrome (« Copier l'adresse du lien »), pas celui des canaux ; ailleurs dans "
          f"le message : menu « Déplacer » inchangé ({ctx})",
          ctx["link"] == {"prevented": False, "menu": False} and ctx["text"] == {"prevented": True, "menu": True})

    # ---------------------------------------------------------- message de l'utilisateur : liens, Copier
    typed = "Regarde https://github.com/Zaudrax/Astrel. Et le **code** `https://code.example/x` (https://ok.example/y)"
    await say(typed)
    ok = await p.until(f"[...document.querySelectorAll('.msg.user')].some(m => m.querySelector(':scope > span:not([class])')?.textContent === {json.dumps(typed)})", 5)
    um = f"[...document.querySelectorAll('.msg.user')].filter(m => m.querySelector(':scope > span:not([class])')?.textContent === {json.dumps(typed)}).at(-1)"
    ulinks = await p.js(f"{LIENS_JS}({um}.querySelector(':scope > span:not([class])'))") if ok else []
    check(f"message de l'utilisateur : texte tel que tapé (ni gras ni code rendus), 2 liens avec icône "
          f"({[x['text'] for x in ulinks]})",
          ok and [x["text"] for x in ulinks] == ["https://github.com/Zaudrax/Astrel", "https://ok.example/y"]
          and all(x["icon"] == "external-link" and x["target"] == "_blank" and "noopener" in x["rel"] for x in ulinks)
          and await p.js(f"!{um}.querySelector('strong, code, .md')"))
    contrast = await p.js(f"""(() => {{ const m = {um}, a = m.querySelector('a.md-link');
        return [getComputedStyle(a).color, getComputedStyle(m).color]; }})()""") if ulinks else ["", "?"]
    check(f"lien dans la bulle de l'utilisateur : couleur du texte de la bulle ({contrast})", contrast[0] == contrast[1])
    await p.js("window.__clip = null")
    await p.js(f"{um}.querySelector('.copy-btn').click()")
    await asyncio.sleep(0.2)
    cb = await p.js(f"""(() => {{ const b = {um}.querySelector('.copy-btn'); return {{ clip: window.__clip, title: b.title,
        aria: b.getAttribute('aria-label'), outside: b.getBoundingClientRect().right <= {um}.getBoundingClientRect().left + 1 }}; }})()""")
    check(f"Copier sur le message de l'utilisateur : le texte brut, bouton à gauche de la bulle (« {cb['title']} »)",
          cb["clip"] == typed and cb["title"] == "Copier ton message (texte brut)" and cb["aria"] == "Copier" and cb["outside"])
    await p.shot("liens-message-utilisateur.png")

    # ---------------------------------------------------------- ↑ dans la barre vide, « / »
    await p.js("(() => { const a = document.querySelector('#ask'); a.value = ''; a.dispatchEvent(new Event('input')); a.focus(); })()")
    await p.key("ArrowUp", vk=38)
    v1 = await p.js("document.querySelector('#ask').value")
    await p.key("ArrowUp", vk=38)
    v2 = await p.js("document.querySelector('#ask').value")
    await p.key("ArrowDown", vk=40)
    v3 = await p.js("document.querySelector('#ask').value")
    await p.key("ArrowDown", vk=40)
    v4 = await p.js("document.querySelector('#ask').value")
    check(f"↑ dans la barre vide : dernier message, ↑ le précédent, ↓ ↓ revient à la barre vide "
          f"({v1[:24]!r}, {v2[:24]!r}, {v3[:24]!r}, {v4!r})",
          v1 == typed and v2 == "démo liens" and v3 == typed and v4 == "")
    await p.js("(() => { const a = document.querySelector('#ask'); a.value = 'brouillon'; a.dispatchEvent(new Event('input')); })()")
    await p.key("ArrowUp", vk=38)
    kept = await p.js("document.querySelector('#ask').value")
    check(f"↑ dans une barre qui contient du texte : rien n'est remplacé ({kept!r})", kept == "brouillon")
    await p.js("(() => { const a = document.querySelector('#ask'); a.blur(); document.body.focus(); })()")
    await p.key("/", code="Slash", vk=191, text="/")
    sl = await p.js("({ focus: document.activeElement?.id || '', value: document.querySelector('#ask').value, "
                    "keys: document.querySelector('#ask').getAttribute('aria-keyshortcuts') })")
    check(f"« / » hors d'un champ : curseur dans la barre, rien d'inséré ({sl})",
          sl["focus"] == "ask" and sl["value"] == "brouillon" and sl["keys"] == "/")
    await p.key("/", code="Slash", vk=191, text="/")
    typed_slash = await p.js("document.querySelector('#ask').value")
    check(f"« / » dans la barre : un « / » ordinaire ({typed_slash!r})", typed_slash == "brouillon/")
    await p.js("(() => { const a = document.querySelector('#ask'); a.value = ''; a.dispatchEvent(new Event('input')); a.blur(); })()")

    # ---------------------------------------------------------- point « nouveaux messages » sur ↓
    await p.js("document.querySelector('#dialog').style.setProperty('--chat-h', '180px'), true")
    await asyncio.sleep(0.3)
    await p.js("(() => { const t = document.querySelector('#transcript'); t.scrollTop = 0; t.dispatchEvent(new Event('scroll')); })()")
    await asyncio.sleep(0.2)
    j0 = await p.js("({ shown: !document.querySelector('#jump-btn').classList.contains('hidden'), "
                    "dot: document.querySelector('#jump-btn').classList.contains('has-new') })")
    chan = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || 'general'")
    await ask_demo("demo_report", text="Compte rendu arrivé pendant la lecture.", channel=chan)
    dot = await p.until("document.querySelector('#jump-btn').classList.contains('has-new')", 4)
    j1 = await p.js("""(() => { const j = document.querySelector('#jump-btn'), a = getComputedStyle(j, '::after');
        return { aria: j.getAttribute('aria-label'), title: j.title, bg: a.backgroundColor, w: a.width,
                 top: document.querySelector('#transcript').scrollTop }; })()""")
    await p.shot("liens-point-nouveaux.png")
    check(f"message arrivé pendant la lecture plus haut : point sur ↓ (« {j1['aria']} », {j1['bg']}, {j1['w']}), "
          f"le fil ne bouge pas ({j1['top']})", j0["shown"] and not j0["dot"] and dot and j1["aria"] == "Aller en bas : nouveaux messages"
          and j1["w"] == "8px" and j1["bg"] not in ("", "rgba(0, 0, 0, 0)") and j1["top"] == 0)
    await p.js("document.querySelector('#jump-btn').click()")
    await asyncio.sleep(0.3)
    j2 = await p.js("({ dot: document.querySelector('#jump-btn').classList.contains('has-new'), "
                    "aria: document.querySelector('#jump-btn').getAttribute('aria-label') })")
    check(f"revenu en bas : point retiré, nom « {j2['aria']} »", not j2["dot"] and j2["aria"] == "Aller en bas")
    await p.js("document.querySelector('#dialog').style.removeProperty('--chat-h'), true")
    found = await p.js(EMOJI_DOM_JS)
    check(f"liens et barre : aucun émoji dans le DOM ({found[:3]})", found == [])


def main() -> int:
    chrome =shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        print("Chrome introuvable")
        return 2
    OUT.mkdir(exist_ok=True)
    env = dict(os.environ, ASTREL_HUD_PORT=str(PORT), PYTHONUNBUFFERED="1")
    demo = subprocess.Popen([sys.executable, str(ROOT / "tools" / "hud_demo.py"), "--once"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    profile = tempfile.mkdtemp(prefix="astrel-chrome-")
    browser = None
    try:
        url = ""
        t0 = time.time()
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
                                    "--window-size=1600,1000", "about:blank"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        asyncio.run(run(url))
    finally:
        if browser:
            browser.terminate()
        demo.terminate()
        shutil.rmtree(profile, ignore_errors=True)
    failed = [c for c, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} contrôles passés ; captures dans {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
