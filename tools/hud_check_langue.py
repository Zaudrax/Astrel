#!/usr/bin/env python3
"""Le HUD en ANGLAIS, contrôlé en Chrome HEADLESS (rien ne s'affiche à l'écran), sur la démo
(tools/hud_demo.py) : 20/10, demande de l'utilisateur (« traduction complète de l'application en
anglais, et le choix de passer de l'anglais au français »).

    .venv/bin/python tools/hud_check_langue.py      # captures dans /tmp/captures-i18n/

Parcours : HUD en français (défaut) → Réglages › Langue de l'interface = English (la page se
recharge et rouvre les Réglages) → chaque section des Réglages, Plans, Journal, chaque sujet du
Guide, boîtes de dialogue, menus, carte de confirmation, question à choix, levée du garde-fou
avec le mot anglais « lift » → retour en français. À chaque étape, tous les textes VISIBLES et les
attributs title / aria-label / placeholder sont relevés ; un texte qui a l'air français fait
échouer le contrôle, sauf :
- le CONTENU (messages de la conversation, plans et commandes écrits par Claude, notes du
  coffre, chemins) : sélecteurs `CONTENT` ;
- les commandes VOCALES citées entre « » (Astrel comprend le français : « oui », « mode
  silencieux ») ;
- quelques textes de la démo qui imitent une conversation (`DEMO_CONTENT`).
Ports à part (4798, CDP 9338) : peut tourner à côté de tools/hud_check_rendu.py.
Code de sortie 0 si tout passe ; la liste de ce qui reste est imprimée.
"""
from __future__ import annotations

import asyncio
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from hud_check_rendu import ASK_DEMO_JS, Page  # noqa: E402  (même pilote DevTools)

PORT = 4798
CDP_PORT = 9338
OUT = Path("/tmp/captures-i18n")

# Zones de CONTENU : écrit par l'utilisateur, Claude, le coffre, ou une commande (reste tel quel).
CONTENT = ", ".join((
    ".msg .md", ".msg > span:not([class])", ".choice-question", ".choice-label", ".choice-status",
    "#confirm-cmd", ".task b", ".task .step", ".plan-title", ".plan-section > h3", ".plan-intro",
    ".plan-point-text", ".plan-request-text", ".plan-entry-text", ".plan-item-title", ".plan-note",
    ".audit-what", ".audit-auth", ".prop-texte", ".prop-note", ".prop-raison", "#mission b", ".mission-last", ".mission-reason", "#activity-list .muted",
    ".att-name", ".report-file", ".wa-code", "#mission-banner-info", ".choice-btn",
    "pre", "code", ".setting-block",
    ".ad-data",  # 21/10 (plan P18) : détail d'agent, consigne, actions, erreurs, passation
    ".search-extrait", ".search-group-name",  # 04/11 : recherche, extraits des messages et noms des canaux
    # 23/10 (plan P22) : page des capacités, NOMS qui sont des identifiants ou du contenu : phrases vocales
    # (françaises exprès), règles de réflexe écrites par l'utilisateur, noms d'agents et de skills.
    ".cap-group:is([data-group=voix], [data-group=reflexes], [data-group=agents], [data-group=skills]) .cap-nom",
))
# Textes de la démo qui jouent le rôle de la conversation ou de Claude (français voulu).
DEMO_CONTENT = (
    "mails de ce matin", "météo Nantes", "écran principal", "ami@exemple.fr <b>pas du html</b>",
    "Comparatif des assurances habitation", "Quel nom pour le nouveau canal ?",
    # tâches de la démo (descriptions écrites par Claude) : titre, infobulle et nom accessible des lignes
    "Trier les mails de la semaine", "Copier les photos sur le disque externe", "Relire tous les contrats d'assurance",
    "Comparer les billets de train", "Liste des cartons du déménagement",
    # tâches injectées par simple_en (hud_check_rendu.lot_b_tasks)
    "Agent du lot B", "Fini récent", "Fini ancien", "Commande finie",
)
NAMES = {"Rémy", "Général"}  # noms propres (Général ne doit PAS rester : contrôlé à part)
FR_WORDS = {
    "le", "la", "les", "des", "du", "une", "et", "pour", "avec", "dans", "sur", "est", "pas", "tu", "ton", "ta",
    "tes", "aucun", "aucune", "rien", "ici", "sans", "qui", "que", "quand", "je", "il", "elle", "mais", "ou",
    "où", "au", "aux", "ce", "cette", "ces", "son", "sa", "ses", "ne", "plus", "tout", "toute", "tous", "vers",
    "chez", "depuis", "après", "avant", "encore", "déjà", "très", "ni", "un", "de", "en", "à", "été", "être",
}
ACCENT = re.compile(r"[àâäçéèêëîïôöùûüœÀÂÇÉÈÊÎÔÙÛŒ]")

COLLECT_JS = """((skip) => {
  const out = [];
  const name = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')
    + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\\s+/).join('.') : '');
  const vis = (e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const n = w.currentNode, p = n.parentElement, t = n.textContent.trim();
    if (!t || !p || p.closest('script, style') || p.closest(skip) || !vis(p)) continue;
    // commande vocale citée « **comme ceci** » (le guide) : française exprès
    const before = (p.previousSibling?.textContent || '').trimEnd(), after = (p.nextSibling?.textContent || '').trimStart();
    if (before.endsWith('«') && after.startsWith('»')) continue;
    out.push(['texte', name(p), t]);
  }
  for (const e of document.querySelectorAll('[title], [aria-label], [placeholder]')) {
    if (e.closest(skip)) continue;
    for (const a of ['title', 'aria-label', 'placeholder']) {
      const v = (e.getAttribute(a) || '').trim();
      if (v) out.push([a, name(e), v]);
    }
  }
  for (const o of document.querySelectorAll('option')) if (!o.closest(skip)) out.push(['option', name(o), o.textContent.trim()]);
  return out;
})"""

FOUND: dict[str, set[tuple[str, str, str]]] = {}
CHECKS: list[tuple[str, bool]] = []


def check(label: str, ok) -> None:
    CHECKS.append((label, bool(ok)))
    print(("  ok  " if ok else "  ÉCHEC ") + label, flush=True)


def french(text: str) -> bool:
    """Le texte a-t-il l'air français ? (citations « … » et noms propres mis à part)"""
    t = re.sub(r"«[^»]*»", " ", text)
    for c in DEMO_CONTENT:
        t = t.replace(c, " ")
    words = re.findall(r"[A-Za-zÀ-ÿ'’-]+", t)
    words = [x for x in words if x not in NAMES]
    if any(ACCENT.search(x) for x in words):
        return True
    return any(x.lower().strip("'’") in FR_WORDS for x in words if not x[:1].isupper() or len(x) > 1)


async def scan(p: Page, where: str) -> list[tuple[str, str, str]]:
    items = await p.js(f"{COLLECT_JS}({json.dumps(CONTENT)})")
    bad = [(k, el, t) for k, el, t in items if french(t)]
    FOUND.setdefault(where, set()).update(bad)
    return bad


async def wait(p: Page, expr: str, timeout: float = 8.0) -> bool:
    """`Page.until` qui supporte un rechargement en cours (« target navigated »)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if await p.js(expr):
                return True
        except RuntimeError:
            pass  # page en train de se recharger
        await asyncio.sleep(0.1)
    return False


async def click(p: Page, selector: str) -> bool:
    return bool(await p.js(f"(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return false; e.click(); return true; }})()"))


async def close_panel(p: Page) -> None:
    await p.js("document.querySelector('.modal-x')?.click()")
    await asyncio.sleep(0.2)


async def set_language(p: Page, lang: str) -> None:
    """Réglages › Langue de l'interface, comme l'utilisateur : sélecteur puis `change`."""
    if not await p.js("!!document.querySelector('.modal.settings')"):
        await click(p, "#settings-chip")
    await p.until("!!document.querySelector('.setting[data-key=\"ui_language\"] select')", 8)
    await p.js(f"""(() => {{
        const d = document.querySelector('.setting[data-key="ui_language"]').closest('details'); d.open = true;
        const s = document.querySelector('.setting[data-key="ui_language"] select');
        s.value = {json.dumps(lang)}; s.dispatchEvent(new Event('change', {{ bubbles: true }})); }})()""")


SIMPLE_EN_JS = """(() => { const q = (s) => document.querySelector(s), txt = (s) => (q(s)?.innerText || '').trim();
  const clip = [...document.querySelectorAll('.alert-seg')].filter(e => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map(e => e.id);
  return { attr: document.documentElement.dataset.affichage || '', brain: !!q('#brain-btn'),
           state: (q('#state-label')?.textContent || '').trim(), /* innerText suit la casse CSS (« READY ») */ total: txt('#total-banner b'), lift: txt('#lift-banner b'), mem: txt('#memory-banner b'),
           history: (q('#tasks .tasks-history > summary')?.textContent || ''), clip,
           sensors: [...document.querySelectorAll('#sensors .sensor b')].map(b => b.textContent) }; })()"""


async def simple_en(p: Page) -> None:
    """22/10 (relecture P23, fiche 2) : affichage Simple en ANGLAIS : interrupteur, pastilles de sécurité
    sobres (plus de « Show the brain » : graphe retiré le 23/10, plan P28), orbe « Ready », « History (n) » ; à 1600 et 1280 px, aucune pastille
    rognée ; aucun texte français visible. Les capteurs en mots sont relevés en Avancé, avant."""
    from hud_check_rendu import fake_event, lot_b_tasks
    sensors = await p.js("[...document.querySelectorAll('#sensors .sensor b')].map(b => b.textContent)")
    # 23/10 (demande de l'utilisateur) : le panneau ne garde que WATCH ; les autres dans Réglages › Capteurs
    check(f"capteur du panneau en mots, en anglais : {sensors}", sensors == ["Screen view"])
    send = lambda cmd: p.js(f"window.__ws.send({json.dumps(json.dumps(cmd))}), true")
    await send({"cmd": "total", "mode": "on"})
    await send({"cmd": "settings_action", "key": "garde_fou_levee", "action": "lever", "confirm": "lift"})
    await send({"cmd": "demo_memory", "state": "blocage", "active": True})
    await p.until("['#total-banner', '#lift-banner', '#memory-banner'].every(s => !document.querySelector(s).hidden)", 5)
    await fake_event(p, lot_b_tasks(time.time()))
    # 23/10 (plan P28) : sans le graphe, la page charge plus vite et la démo peut encore être sur sa carte de
    # confirmation (« asking ») : l'orbe est mise au repos ici, le contrôle porte sur la traduction de « Prêt ».
    await fake_event(p, {"type": "state", "state": "sleeping", "detail": "", "muted": False})
    # 28/10 (T10, décision de l'utilisateur) : plus d'interrupteur « Simple » dans la barre ; même commande
    # que Réglages › Apparence › Affichage
    await send({"cmd": "settings_set", "key": "affichage", "value": "simple"})
    on = await p.until("document.documentElement.dataset.affichage === 'simple'", 5)
    await asyncio.sleep(0.6)
    await p.js("document.querySelectorAll('.toast').forEach(t => t.remove()), true")
    s = await p.js(SIMPLE_EN_JS)
    check(f"Simple en anglais : sans bouton du cerveau ({not s['brain']}), orbe "
          f"« {s['state']} », pastilles « {s['total']} » / « {s['lift']} » / « {s['mem']} », « {s['history']} »",
          on and not s["brain"] and s["state"] == "Ready"
          and s["total"] == "No confirmation" and s["lift"] == "Guardrail lifted" and s["mem"] == "Memory almost full"
          and s["history"].startswith("History ("))
    await scan(p, "affichage Simple")
    await p.shot("apres-en-simple.png")
    clips = {}
    for w, h in ((1280, 800), (1600, 1000)):
        await p.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(0.6)
        clips[w] = (await p.js(SIMPLE_EN_JS))["clip"]
        if w == 1280:
            await p.shot("apres-en-simple-1280.png")
    check(f"Simple en anglais : aucune pastille d'alerte rognée ({clips})", not any(clips.values()))
    # état rendu : Avancé, levée rétablie, mémoire normale (Total comme avant)
    await send({"cmd": "settings_set", "key": "affichage", "value": "avance"})
    await p.until("!document.documentElement.dataset.affichage", 5)
    await send({"cmd": "settings_action", "key": "garde_fou_levee", "action": "retablir"})
    await send({"cmd": "demo_memory", "state": "ok"})
    await asyncio.sleep(0.4)


async def calibrage_en(p: Page) -> None:
    """02/11 : assistant de calibrage du micro en anglais (présentation, mesure, résultat), séance de la démo."""
    await click(p, "#settings-chip")
    await p.until("!!document.querySelector('[data-fkey=\"audio.calibrage|act|ouvrir\"]')", 8)
    await p.js("(() => { const b = document.querySelector('[data-fkey=\"audio.calibrage|act|ouvrir\"]'); "
               "b.closest('details').open = true; b.click(); })()")
    ok = await p.until("!!document.querySelector('.modal.wide.calibrage .calib-actions .ok')", 5)
    title = await p.js("document.querySelector('.modal.wide.calibrage .modal-title')?.textContent")
    await scan(p, "Calibrage › présentation")
    await click(p, ".modal.wide.calibrage .calib-actions .ok")
    measuring = await p.until("document.querySelector('.calib-big')?.textContent.startsWith('Say')", 6)
    await scan(p, "Calibrage › mesure")
    done = await p.until("!!document.querySelector('.calib-table')", 10)
    await scan(p, "Calibrage › résultat")
    labels = await p.js("[...document.querySelectorAll('.calib-table th, .calib-actions .btn')].map(e => e.textContent)")
    check(f"calibrage en anglais (« {title} », {labels})", ok and measuring and done and title == "Calibrate the mic"
          and "Speech threshold (VAD)" in labels and "Save" in labels and "Redo" in labels)
    await p.shot("apres-en-calibrage.png")
    await p.key("Escape", vk=27)
    await p.until("!document.querySelector('.modal.wide.calibrage')", 3)
    await close_panel(p)


async def liens_en(p: Page) -> None:
    """04/11 (liens du fil, petits détails de la barre) en anglais : description du lien, toast après le clic,
    Copier sur le message de l'utilisateur, point « nouveaux messages » du bouton ↓, indication ↑ de la barre."""
    # la séquence de la démo (sa phrase DITE ramène le fil en bas, comme toute phrase de l'utilisateur) d'abord finie
    await p.until("[...document.querySelectorAll('.msg.astrel')].some(m => m.textContent.includes('Trois mails ce matin'))", 30)
    await asyncio.sleep(1.5)
    chan = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || 'general'")
    text = "Look at https://example.org/en please"
    await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'say', 'text': text, 'channel': chan}))}), true")
    um = f"[...document.querySelectorAll('.msg.user')].filter(m => m.textContent.startsWith({json.dumps(text)})).at(-1)"
    ok = await p.until(f"!!{um}?.querySelector('a.md-link')", 5)
    r = await p.js(f"""(() => {{ const m = {um}, a = m.querySelector('a.md-link'), b = m.querySelector('.copy-btn');
        return {{ desc: a.getAttribute('aria-description'), copy: b.title, aria: b.getAttribute('aria-label'),
                 ph: document.querySelector('#ask').placeholder }}; }})()""") if ok else {}
    check(f"liens en anglais : « {r.get('desc')} », Copier « {r.get('copy')} », barre « {r.get('ph')} »",
          ok and r["desc"] == "opens in your browser" and r["copy"] == "Copy your message (raw text)" and r["aria"] == "Copy"
          and r["ph"] == "Ask Astrel…  (Shift+Enter: new line · ↑: your last message)")
    await p.js(f"{um}.querySelector('a.md-link').click(), true")
    toast = await p.until("[...document.querySelectorAll('.toast')].some(t => t.textContent === 'Link opened in your browser.')", 4)
    check("clic sur un lien en anglais : « Link opened in your browser. »", toast)
    await p.js("document.querySelector('#dialog').style.setProperty('--chat-h', '180px'), true")
    await asyncio.sleep(0.3)
    await p.js("(() => { const t = document.querySelector('#transcript'); t.scrollTop = 0; t.dispatchEvent(new Event('scroll')); })()")
    await asyncio.sleep(0.2)
    await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'demo_report', 'text': 'Report that arrived.', 'channel': chan}))}), true")
    dot = await p.until("document.querySelector('#jump-btn').classList.contains('has-new')", 4)
    j = await p.js("""(() => { const t = document.querySelector('#transcript'), last = [...t.querySelectorAll('.msg')].at(-1);
        return { aria: document.querySelector('#jump-btn').getAttribute('aria-label'), title: document.querySelector('#jump-btn').title,
                 sh: t.scrollHeight, ch: t.clientHeight, st: t.scrollTop, last: (last?.textContent || '').slice(0, 30),
                 hidden: !!last?.classList.contains('hidden') }; })()""")
    check(f"bouton ↓ en anglais avec un nouveau message : « {j['aria']} » ({j})",
          dot and j["aria"] == "Go to bottom: new messages" and j["title"] == "Go to bottom: new messages")
    await scan(p, "liens, Copier, bouton ↓ (nouveaux messages)")
    await p.js("document.querySelector('#jump-btn').click(), true")
    await p.js("document.querySelector('#dialog').style.removeProperty('--chat-h'), true")
    await asyncio.sleep(0.2)


async def chat_lot2_en(p: Page) -> None:
    """04/11 (chat, lot 2) en anglais : chemin copié d'un clic (titre, toast), ■ et Échap (titre, toast), recherche
    dans tous les canaux (bouton, fenêtre, état, groupes, archives, note d'un message sorti du fil)."""
    ask_demo = lambda cmd, **a: p.js(f"{ASK_DEMO_JS}({json.dumps(cmd)}, {json.dumps(a)})")
    await p.js("navigator.clipboard.writeText = async () => {}; document.querySelectorAll('.toast').forEach(t => t.remove()), true")
    chan = await p.js("document.querySelector('.channel-tab.on')?.dataset.chan || 'general'")
    await p.js(f"window.__ws.send({json.dumps(json.dumps({'cmd': 'say', 'text': 'démo chemins', 'channel': chan}))}), true")
    md = "[...document.querySelectorAll('.msg.astrel:not(.live) .md')].filter(m => m.textContent.startsWith('Voici les fichiers')).at(-1)"
    ok = await p.until(f"!!{md}?.querySelector('.md-path')", 10)
    r = await p.js(f"""(() => {{ const e = {md}.querySelector('.md-path'); e.click();
        return {{ title: e.title, desc: e.getAttribute('aria-description'), text: e.textContent }}; }})()""") if ok else {}
    toast = await p.until("[...document.querySelectorAll('.toast')].some(t => t.textContent.startsWith('Path copied: '))", 4)
    check(f"chemin en anglais : « {r.get('title')} », « {r.get('desc')} », toast « Path copied » ({toast})",
          ok and r["title"] == "Copy this path (click, or Enter on the keyboard)" and r["desc"] == "copies the path, opens nothing"
          and toast)
    stop = await p.js("({ title: document.querySelector('#stop-btn').title, keys: document.querySelector('#stop-btn').getAttribute('aria-keyshortcuts') })")
    send = await p.js("({ title: document.querySelector('#send-btn').title, aria: document.querySelector('#send-btn').getAttribute('aria-label') })")
    check(f"bouton Envoyer en anglais : « {send['title']} »", send == {"title": "Send (Enter)", "aria": "Send (Enter)"})
    # carte de confirmation de la séquence de la démo (posée juste après sa réponse) : « Non » (Échap y vaut « Non »)
    if await p.until("!document.querySelector('#confirm').classList.contains('hidden')", 4):
        await click(p, "#confirm-no")
        await p.until("document.querySelector('#confirm').classList.contains('hidden')", 3)
    # question à choix laissée en attente par la démo : répondue dans la page seule (sinon Échap lui revient, voulu)
    await p.js("""[...document.querySelectorAll('.msg.choice:not(.answered):not(.cancelled) .choice-question')].forEach(q =>
        window.__ws.onmessage({ data: JSON.stringify({ type: 'choice_answered', id: q.id.replace(/^choice-q-/, ''), value: '-' }) })), true""")
    await ask_demo("demo_answering")
    await p.until("!!document.querySelector('.msg.astrel.live')", 4)
    await p.js("document.activeElement?.blur(), true")
    # panneau Activité ouvert par un contrôle précédent : Échap le fermerait d'abord (voulu, contrôlé dans hud_check_rendu)
    await p.js("document.querySelector('#activity').classList.add('hidden'), true")
    await p.key("Escape", vk=27)
    esc = await p.until("[...document.querySelectorAll('.toast')].some(t => t.textContent === 'Answer stopped (Esc).')", 4)
    check(f"■ et Échap en anglais : « {stop['title'][:30]}… », toast « Answer stopped (Esc). » ({esc})",
          stop["title"].startswith("Stop (Esc): ") and stop["keys"] == "Escape" and esc)
    await ask_demo("demo_recherche")
    await asyncio.sleep(0.6)
    btn = await p.js("({ title: document.querySelector('#search-btn').title, aria: document.querySelector('#search-btn').getAttribute('aria-label') })")
    await click(p, "#search-btn")
    opened = await p.until("!!document.querySelector('.modal.search-panel .search-input')", 4)
    await p.js("document.querySelector('.search-panel .search-input').focus(), true")
    await p.send("Input.insertText", text="sirius")
    got = await p.until("!!document.querySelector('.search-panel .search-group.archive')", 6)
    v = await p.js("""(() => { const m = document.querySelector('.search-panel');
        return { title: m.querySelector('.modal-title').textContent, ph: m.querySelector('.search-input').placeholder,
                 status: m.querySelector('.search-status').textContent,
                 archive: m.querySelector('.search-group.archive .search-group-head').textContent,
                 who: [...new Set([...m.querySelectorAll('.search-who')].map(e => e.textContent))],
                 n: m.querySelector('.search-group-n')?.textContent || '' }; })()""") if got else {}
    check(f"recherche en anglais : bouton « {btn['title']} », fenêtre « {v.get('title')} », « {v.get('status')} », "
          f"« {v.get('archive')} », {v.get('who')}",
          opened and got and btn["title"] == "Search all channels (Ctrl+K)" and btn["aria"] == "Search the conversations"
          and v["title"] == "Search the conversations" and v["ph"].startswith("One or more words")
          and v["status"].endswith("messages found") and v["archive"].startswith("Before ") and "You" in v["who"]
          and v["n"].endswith("messages"))
    await p.js("""[...document.querySelectorAll('.search-panel button.search-hit')]
        .find(b => b.textContent.includes('Ancienne note'))?.click(), true""")
    note = await p.until("[...document.querySelectorAll('.search-panel .search-note')].some(n => !n.classList.contains('hidden') "
                         "&& n.textContent.startsWith('This message is no longer in the displayed thread'))", 3)
    check("recherche en anglais : note d'un message sorti du fil, bouton « Open the … channel »", note and await p.js(
        "[...document.querySelectorAll('.search-panel .search-open')].some(b => /^Open the .+ channel$/.test(b.textContent))"))
    await scan(p, "chemins, Échap, recherche")
    await p.shot("apres-en-recherche.png")
    await close_panel(p)


async def run(url: str) -> None:
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
    ws_url = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
    import websockets
    async with websockets.connect(ws_url, max_size=50_000_000) as ws:
        p = Page(ws)
        pump = asyncio.create_task(p.pump())
        await p.send("Page.enable")
        await p.send("Runtime.enable")
        await p.send("Page.addScriptToEvaluateOnNewDocument", source="""
            const send = WebSocket.prototype.send;
            WebSocket.prototype.send = function (d) { window.__ws = this; return send.call(this, d); };
        """)
        await p.send("Emulation.setDeviceMetricsOverride", width=1600, height=1000, deviceScaleFactor=1, mobile=False)
        await p.send("Page.navigate", url=url)
        check("HUD chargé", await p.until("!!document.querySelector('.channel-tab')", 15))
        await asyncio.sleep(1.5)
        check("français par défaut (lang=fr, « Stop tout »)",
              await p.js("document.documentElement.lang === 'fr' && document.querySelector('#estop-chip').textContent.includes('Stop tout')"))
        await p.shot("avant-fr-hud.png")
        await click(p, "#settings-chip")
        await p.until("!!document.querySelector('.modal.settings .settings-group')", 8)
        await p.js("""(() => { const d = document.querySelector('.setting[data-key="ui_language"]').closest('details');
            d.open = true; document.querySelector('.setting[data-key="ui_language"]').scrollIntoView(); })()""")
        await asyncio.sleep(0.3)
        await p.shot("avant-fr-reglages.png")

        # ---------------------------------------------------------- bascule en anglais
        await set_language(p, "en")
        ok = await wait(p, "document.documentElement.lang === 'en' && !!document.querySelector('.modal.settings')", 15)
        check("Réglages › English : la page se recharge en anglais et rouvre les Réglages", ok)
        await asyncio.sleep(1.0)
        check("barre du haut en anglais (« Stop all », « No confirmation »)", await p.js(
            "document.querySelector('#estop-chip').textContent.includes('Stop all') && document.querySelector('#total-chip').textContent.includes('No confirmation')"))
        check("titre des Réglages en anglais", await p.js("document.querySelector('.modal.settings .modal-title').textContent.includes('Astrel settings')"))
        check("canal par défaut affiché « General »", await p.js(
            "[...document.querySelectorAll('.channel-tab span')].some(s => s.textContent === 'General')"))
        await p.shot("apres-en-reglages.png")
        groups = await p.js("[...document.querySelectorAll('.modal.settings details.settings-group')].map(d => d.dataset.group)")
        for g in groups:
            await p.js(f"""(() => {{ for (const d of document.querySelectorAll('.modal.settings details.settings-group'))
                d.open = d.dataset.group === {json.dumps(g)}; }})()""")
            await asyncio.sleep(0.15)
            await scan(p, f"Réglages › {g}")
        # Mot de confirmation traduit : « lift » lève le garde-fou de la démo.
        await p.js("""(() => { for (const d of document.querySelectorAll('.modal.settings details.settings-group'))
            d.open = d.dataset.group === 'garde_fou'; })()""")
        await click(p, '.setting[data-key="garde_fou_levee"] [data-action="lever"]')
        await p.until("!!document.querySelector('.modal-back:not(.panel-back) input')", 5)
        await scan(p, "Réglages › boîte « Lift the guardrail »")
        await p.shot("apres-en-mot-lift.png")
        await p.js("""(() => { const i = document.querySelector('.modal-back:not(.panel-back) input'); i.value = 'lift';
            i.dispatchEvent(new Event('input', { bubbles: true }));
            document.querySelector('.modal-back:not(.panel-back) .modal-ok').click(); })()""")
        check("le mot anglais « lift » lève le garde-fou (bandeau GUARDRAIL LIFTED)",
              await p.until("!document.querySelector('#lift-banner').hidden && document.querySelector('#lift-banner').textContent.includes('GUARDRAIL LIFTED')", 6))
        await scan(p, "Réglages › garde-fou levé")
        await click(p, "#lift-off")
        await p.until("document.querySelector('#lift-banner').hidden", 5)
        await close_panel(p)

        # ---------------------------------------------------------- page principale
        await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_quota' }))")
        await p.js("document.querySelector('#activity').classList.remove('hidden')")
        await asyncio.sleep(0.6)
        await scan(p, "HUD (barre, panneaux, fil)")
        await p.shot("apres-en-hud.png")
        await liens_en(p)  # 04/11 : liens du fil, Copier, bouton ↓
        await chat_lot2_en(p)  # 04/11 : chemins copiés, Échap = ■, recherche dans tous les canaux
        await p.js("document.querySelector('#activity').classList.add('hidden')")
        # 28/10 (plan P40 lot A5) : panneau « Images » sous la barre, en Auto puis en Manuel
        await click(p, "#images-btn")
        await p.until("/available/.test(document.querySelector('#image-panel .ip-note')?.textContent || '')", 4)
        await scan(p, "panneau Images (auto)")
        check("panneau Images en anglais (« AI images », « Generate », « Auto: Gemini → … »)", await p.js(
            "(() => { const pn = document.querySelector('#image-panel'); return pn.querySelector('.ip-title').textContent === 'AI images'"
            " && pn.querySelector('.ip-send').textContent === 'Generate'"
            " && pn.querySelector('.ip-engine').selectedOptions[0].textContent.startsWith('Auto: Gemini'); })()"))
        await p.shot("apres-en-images.png")
        await click(p, "#images-btn")
        # 21/10 (plan P18) : tâches finies dans « en cours » et détail d'agent au clic
        await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_tasks_done' }))")
        await p.until("document.querySelectorAll('#tasks .task.done').length === 2", 4)
        check("tâches finies en anglais (« Done · 61 actions », « Failed · 24 actions »)", await p.js(
            "[...document.querySelectorAll('#tasks .task.done .step')].map(s => s.textContent).join('|').includes('Done · 61 actions')"
            " && [...document.querySelectorAll('#tasks .task.done .step')].some(s => s.textContent.endsWith('Failed · 24 actions'))"))
        await scan(p, "en cours : tâches finies")
        # descriptions de d1, d2 traduites par le catalogue (« Sort this week's emails ») ; d4, d5 non
        for desc, where in (("Sort this week", "détail d'agent en cours"), ("Copy the photos", "détail d'une commande"),
                            ("Comparer les billets", "détail d'agent en échec"), ("Liste des cartons", "détail avec passation")):
            await p.js(f"[...document.querySelectorAll('#tasks .task')].find(r => r.textContent.includes({json.dumps(desc)})).click()")
            await p.until("!!document.querySelector('.modal.agent-detail .ad-meta')", 4)
            await asyncio.sleep(0.4)
            await scan(p, where)
            if desc == "Sort this week":
                check("détail d'agent en anglais (« Running », « Brief », « Latest actions »)", await p.js(
                    "(() => { const m = document.querySelector('.modal.agent-detail'); const h = [...m.querySelectorAll('.ad-title')].map(x => x.textContent);"
                    " return m.querySelector('.ad-state').textContent === 'Running' && h.includes('Brief') && h.includes('Latest actions')"
                    " && h.includes('Steps') && h.includes('Reasoning effort'); })()"))
                await p.shot("apres-en-detail-agent.png")
            await close_panel(p)
        await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_expire', task_id: 'd5' }))")
        await p.js("[...document.querySelectorAll('#tasks .task')].find(r => r.textContent.includes('Liste des cartons')).click()")
        check("détail expiré en anglais (« no longer available »)", await p.until(
            "(document.querySelector('.modal.agent-detail .ad-expired')?.textContent || '').includes('no longer available')", 4))
        await scan(p, "détail expiré")
        await close_panel(p)
        # carte de confirmation (texte du garde-fou traduit) et question à choix
        await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_confirm' }))")
        await p.until("!document.querySelector('#confirm').classList.contains('hidden')", 5)
        check("carte de confirmation traduite (« Heads up: a scheduled task. Should I continue? »)", await p.js(
            "document.querySelector('#confirm-question').textContent === 'Heads up: a scheduled task. Should I continue?'"))
        await scan(p, "carte de confirmation")
        await p.shot("apres-en-confirmation.png")
        await click(p, "#confirm-no")
        await p.js("window.__ws.send(JSON.stringify({ cmd: 'demo_choice' }))")
        await asyncio.sleep(0.5)
        await scan(p, "question à choix")
        # menus et boîtes. 28/10 (hud-boutons-t10, décision de l'utilisateur) : plus de boutons « Éteindre » ni
        # « Mission » dans la barre ; leurs boîtes s'ouvrent depuis les Réglages (Système, Garde-fou).
        from hud_check_rendu import reglages_bouton
        titles = {}
        for sel, where in (("#estop-chip", "boîte « Emergency stop »"),
                           ("systeme.eteindre|act|eteindre", "boîte « Shut down »"),
                           ("mission|act|lancer", "boîte « Mission »"),
                           ("#channels .channel-tab.add", "boîte « New channel »"),
                           ("#total-chip", "boîte « TOTAL autonomy »")):
            if sel.startswith("#"):
                await click(p, sel)
            else:
                await reglages_bouton(p, sel)
            opened = await p.until("!!document.querySelector('.modal-back') && !document.querySelector('.modal.wide')", 4)
            titles[sel] = opened and await p.js("document.querySelector('.modal-title')?.textContent")
            await asyncio.sleep(0.2)
            await scan(p, where)
            await p.js("document.querySelector('.modal-back .modal-cancel')?.click()")
            await asyncio.sleep(0.2)
        check(f"Réglages en anglais : boîtes « {titles['systeme.eteindre|act|eteindre']} » et "
              f"« {titles['mission|act|lancer']} »",
              titles["systeme.eteindre|act|eteindre"] == "Shut down Astrel?"
              and titles["mission|act|lancer"] == "Start an autonomous mission")
        check("badge d'effort en anglais (« Effort High », « Brain reasoning effort »)", await p.js(
            "document.querySelector('#effort-badge').textContent.trim() === 'Effort High'"
            " && document.querySelector('#effort-badge').title.startsWith('Brain reasoning effort')"))  # 23/10 (plan P28)
        for sel, where in (("#model-badge", "menu des modèles"), ("#effort-badge", "menu de l'effort"),
                           ("#voice-badge", "menu des voix"), ("#mic-badge", "menu des micros")):
            await click(p, sel)
            await asyncio.sleep(0.5)
            await scan(p, where)
            await p.js("document.querySelectorAll('.menu').forEach(m => m.remove())")
        await p.js("""document.querySelector('#usage').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 600, clientY: 900 }))""")
        await asyncio.sleep(0.3)
        await scan(p, "menu « after my answer »")
        await p.js("document.querySelectorAll('.menu').forEach(m => m.remove())")
        await p.js("""document.querySelector('.channel-tab').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 500, clientY: 500 }))""")
        await asyncio.sleep(0.3)
        await scan(p, "menu d'un canal")
        await p.js("document.querySelectorAll('.menu').forEach(m => m.remove())")
        # modes : silencieux, dictée (notes du panneau droit)
        for mode in ("silencieux", "dictee", "conversation"):
            await click(p, f'#usage button[data-mode="{mode}"]')
            await asyncio.sleep(0.4)
            await scan(p, f"mode {mode}")

        # ---------------------------------------------------------- Plans, Journal, Guide
        await click(p, "#plans-chip")
        await p.until("!!document.querySelector('.modal.plans .plan-item')", 8)
        for f in ("open", "validated", "rejected"):
            await click(p, f'.plans-filter[data-filter="{f}"]')
            await asyncio.sleep(0.3)
            await click(p, ".plan-item")
            await asyncio.sleep(0.4)
            await scan(p, f"Plans › {f}")
        await click(p, '.plans-filter[data-filter="open"]')
        await asyncio.sleep(0.3)
        await click(p, ".plan-comment-btn")
        await asyncio.sleep(0.2)
        await scan(p, "Plans › remarque")
        await p.shot("apres-en-plans.png")
        await close_panel(p)
        await click(p, "#audit-chip")
        await p.until("!!document.querySelector('.modal.audit .audit-status')", 6)
        await asyncio.sleep(0.6)
        await scan(p, "Journal")
        await close_panel(p)
        await click(p, "#propositions-chip")  # 04/11 (T49) : page « Memory »
        await p.until("!!document.querySelector('.modal.propositions .prop-item')", 6)
        await asyncio.sleep(0.4)
        await scan(p, "Memory")
        await close_panel(p)
        await click(p, "#guide-chip")
        await p.until("!!document.querySelector('.modal.guide .guide-topic')", 8)
        topics = await p.js("[...document.querySelectorAll('.guide-topic')].map(b => b.dataset.topic)")
        for tid in topics:
            await click(p, f'.guide-topic[data-topic="{tid}"]')
            await asyncio.sleep(0.15)
            await scan(p, f"Guide › {tid}")
        check(f"guide en anglais ({len(topics)} sujets, « Welcome »)",
              await p.js("[...document.querySelectorAll('.guide-topic')].some(b => b.textContent.includes('Welcome'))"))
        await click(p, '.guide-topic[data-topic="langue"]')
        await asyncio.sleep(0.2)
        await p.shot("apres-en-guide.png")
        # 23/10 (plan P22) : page « Everything Astrel can do », ouverte par le bouton du Guide, tout déplié
        await click(p, ".guide-capacites")
        loaded = await p.until("document.querySelectorAll('.modal.capacites .cap-group').length >= 15", 8)
        await p.js("document.querySelectorAll('.modal.capacites .cap-group').forEach(d => { d.open = true; })")
        await asyncio.sleep(0.3)
        cap = await p.js("""(() => { const m = document.querySelector('.modal.capacites');
            return { title: m.querySelector('.modal-title').textContent, status: m.querySelector('.cap-status').textContent,
                     sort: [...m.querySelectorAll('.cap-sort option')].map(o => o.textContent) }; })()""")
        check(f"capacités en anglais : « {cap['title']} », {cap['status']}, tri {cap['sort']}",
              loaded and cap["title"] == "Everything Astrel can do" and cap["status"].endswith("capabilities")
              and cap["sort"] == ["Original order", "A → Z", "Most used"])
        await scan(p, "Capacités (tout déplié)")
        await p.shot("apres-en-capacites.png")
        await close_panel(p)

        # ---------------------------------------------------------- calibrage du micro en anglais (02/11)
        await calibrage_en(p)

        # ---------------------------------------------------------- affichage Simple en anglais (22/10, plan P23)
        await simple_en(p)

        # ---------------------------------------------------------- retour en français
        await set_language(p, "fr")
        ok = await wait(p, "document.documentElement.lang === 'fr' && !!document.querySelector('.modal.settings')", 15)
        await asyncio.sleep(0.8)
        check("retour en français : la page se recharge (« Réglages d'Astrel », « Stop tout »)", ok and await p.js(
            "document.querySelector('.modal.settings .modal-title').textContent.includes(\"Réglages d'Astrel\")"
            " && document.querySelector('#estop-chip').textContent.includes('Stop tout')"))
        await p.shot("retour-fr.png")
        pump.cancel()


def main() -> int:
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        print("Chrome introuvable")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    import hud_check_rendu
    hud_check_rendu.OUT = OUT  # captures de Page.shot
    env = dict(os.environ, ASTREL_HUD_PORT=str(PORT), PYTHONUNBUFFERED="1")
    env.pop("ASTREL_UI_LANGUAGE", None)
    demo = subprocess.Popen([sys.executable, str(ROOT / "tools" / "hud_demo.py"), "--once"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    profile = tempfile.mkdtemp(prefix="astrel-chrome-i18n-")
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
    left = sorted({(k, el, t) for items in FOUND.values() for k, el, t in items}, key=lambda x: x[2])
    for where, items in FOUND.items():
        for k, el, t in sorted(items):
            print(f"  reste en français [{where}] {k} {el} : {t[:160]}")
    check(f"aucun texte français dans le HUD en anglais ({len(left)} restant(s))", not left)
    failed = [c for c, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} contrôles passés ; captures dans {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
