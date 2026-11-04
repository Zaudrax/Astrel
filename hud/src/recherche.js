// Recherche dans tous les canaux (04/11, demande de l'utilisateur : « une recherche dans tous les canaux »).
//
// Ouverte par le bouton loupe au bout de la rangée des onglets ou par Ctrl+K (main.js). Ctrl+F n'est PAS
// pris : il reste la recherche de Chrome dans la page (le fil visible), que l'utilisateur connaît ; Ctrl+K est le
// raccourci de recherche des messageries (Slack, Discord) et n'a aucun rôle dans la fenêtre du HUD.
// Côté cœur : commande `history_query {query}` (astrel/history.py `commande_hud`, HUD de l'utilisateur seul,
// réponse bornée) sur l'index du FIL (canal, id du message, heure) puis des archives d'avant le fil
// (transcriptions du CLI, sans canal). Résultats groupés par canal, extrait et date ; clic : le canal
// s'ouvre et le message se montre s'il est encore dans le fil du HUD (60 derniers par canal), sinon
// l'extrait reste affiché avec une note. Tout en nœuds DOM (textContent, <mark> pour les mots trouvés),
// jamais d'innerHTML : un extrait peut venir d'un mail ou d'une page lue par Astrel. En Simple : ni bouton
// ni raccourci (les noms de projets y restent cachés).

import { openPanel } from "./modal.js";
import { stripEmoji } from "./icons.js";
import { assistantName, locale, t, tx } from "./i18n.js";
import { msgTime } from "./heure.js";

export const MARK_OPEN = "\u0002";
export const MARK_CLOSE = "\u0003";
const DEBOUNCE_MS = 280;
const MIN_CHARS = 2;

/** Extrait du cœur → morceaux { t: "text" | "mark", v } (marques FTS5 \u0002…\u0003). Une marque sans
 *  fermeture va jusqu'au bout ; une fermeture sans ouverture est ignorée. Caractères de contrôle retirés. */
export function marquer(extrait) {
  const s = String(extrait ?? "").replace(/[\u0000\u0001\u0004-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, " ");
  const out = [];
  let on = false;
  let buf = "";
  const flush = () => { if (buf) { out.push({ t: on ? "mark" : "text", v: buf }); buf = ""; } };
  for (const ch of s) {
    if (ch === MARK_OPEN) { if (!on) { flush(); on = true; } continue; }
    if (ch === MARK_CLOSE) { if (on) { flush(); on = false; } continue; }
    buf += ch;
  }
  flush();
  return out;
}

/** Résultats du fil → groupes par canal AFFICHÉ (`shownIn` : canal supprimé → canal par défaut), dans
 *  l'ordre de pertinence du premier résultat de chaque canal ; l'ordre des résultats est gardé. */
export function grouper(results, shownIn = (id) => id) {
  const groups = new Map();
  for (const r of Array.isArray(results) ? results : []) {
    if (!r || typeof r !== "object") continue;
    const ch = shownIn(String(r.channel || ""));
    if (!groups.has(ch)) groups.set(ch, []);
    groups.get(ch).push(r);
  }
  return [...groups].map(([channel, items]) => ({ channel, items }));
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function extraitNode(extrait) {
  const p = el("span", "search-extrait");
  for (const piece of marquer(extrait)) {
    const v = stripEmoji(piece.v);
    if (piece.t === "mark") p.append(el("mark", "", v));
    else p.append(document.createTextNode(v));
  }
  return p;
}

let ctx = null;        // { command, toast, chan, goTo }
let last = "";         // dernière recherche (rouverte telle quelle)

/** `opts` : command (conn.js), chan() → { list, label(c), shownIn(id), byId(id) }, goTo(résultat) → vrai si
 *  le message est montré dans le fil. */
export function initRecherche(opts) { ctx = opts; }

export function openSearch() {
  if (!ctx) return null;
  const p = openPanel({ title: t("search.title"), icon: "search", cls: "search-panel" });
  const bar = el("div", "search-bar");
  const input = el("input", "search-input");
  input.type = "search";
  input.id = `search-q-${Date.now()}`;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.maxLength = 200; // = history.REQUETE_MAX
  input.placeholder = t("search.placeholder");
  input.setAttribute("aria-label", t("search.label"));
  const status = el("p", "search-status");
  status.id = `${input.id}-status`;
  status.setAttribute("role", "status");
  input.setAttribute("aria-describedby", status.id);
  bar.append(input);
  const list = el("div", "search-results");
  const foot = el("p", "ad-foot search-foot", t("search.foot"));
  p.body.replaceChildren(bar, status, list, foot);
  p.body.setAttribute("aria-live", "off");

  let timer = 0;
  let asked = ""; // requête dont la réponse est attendue (une plus récente l'emporte)
  const say = (text, cls = "") => { status.textContent = text; status.className = `search-status${cls ? ` ${cls}` : ""}`; };

  async function run(q) {
    clearTimeout(timer);
    const query = q.trim().replace(/\s+/g, " ");
    last = q;
    if (query.length < MIN_CHARS) { asked = ""; list.replaceChildren(); say(query ? t("search.short") : ""); return; }
    if (query === asked) return;
    asked = query;
    say(t("search.searching"));
    list.setAttribute("aria-busy", "true");
    let r;
    try { r = await ctx.command("history_query", { query }, 5000, 20000); } catch (err) { r = { ok: false, error: String(err?.message || err) }; }
    if (asked !== query || !input.isConnected) return; // une autre recherche est partie, ou la fenêtre est fermée
    list.removeAttribute("aria-busy");
    if (!r?.ok) { asked = ""; list.replaceChildren(); say(t("search.failed", { error: tx(r?.error || "?") }), "err"); return; }
    render(r, query);
  }

  function hitHead(r) {
    const when = msgTime(Number(r.ts) || 0, Date.now() / 1000, locale());
    const head = el("span", "search-hit-head");
    head.append(el("span", `search-who ${r.who === "user" ? "user" : "astrel"}`, r.who === "user" ? t("search.you") : assistantName()));
    const time = el("time", "search-when", when.text);
    time.dateTime = when.iso;
    time.title = when.title;
    head.append(time);
    return head;
  }

  function render(r, query) {
    const { list: chans, label, shownIn, byId, color } = ctx.chan();
    const results = Array.isArray(r.results) ? r.results : [];
    const archives = Array.isArray(r.archives) ? r.archives : [];
    const n = results.length + archives.length;
    say(n ? (r.more ? t("search.count_more", { n }) : t("search.count", { n })) : t("search.none", { q: query }));
    const parts = [];
    for (const g of grouper(results, shownIn)) {
      const c = byId(g.channel);
      const sec = el("section", "search-group");
      const h = el("h4", "search-group-head");
      const dot = el("span", "dot");
      dot.style.setProperty("--chan", color(c));
      h.append(dot, el("span", "search-group-name", c ? label(c) : g.channel), el("span", "search-group-n", t("search.in_channel", { n: g.items.length })));
      h.id = `search-g-${parts.length}-${Date.now()}`;
      const ul = el("ul", "search-list");
      ul.setAttribute("aria-labelledby", h.id);
      for (const it of g.items) {
        const li = el("li", "search-item");
        const b = el("button", "search-hit");
        b.type = "button";
        b.append(hitHead(it), extraitNode(it.extrait));
        b.title = t("search.go_title");
        const note = el("p", "search-note hidden");
        b.onclick = () => {
          if (ctx.goTo(it, () => p.close())) return;
          note.textContent = t("search.not_loaded");
          note.classList.remove("hidden");
          if (c && chans.length > 1 && !note.querySelector("button")) {
            const open = el("button", "mini search-open", t("search.open_channel", { name: label(c) }));
            open.type = "button";
            open.onclick = () => { p.close(); ctx.openChannel(c.id); };
            note.append(" ", open);
          }
        };
        li.append(b, note);
        ul.append(li);
      }
      sec.append(h, ul);
      parts.push(sec);
    }
    if (archives.length) {
      const sec = el("section", "search-group archive");
      const since = Number(r.fil_debut) > 0
        ? new Date(Number(r.fil_debut) * 1000).toLocaleDateString(locale(), { day: "numeric", month: "long", year: "numeric" }) : "";
      const h = el("h4", "search-group-head", since ? t("search.archive_since", { date: since }) : t("search.archive"));
      h.title = t("search.archive_title");
      h.id = `search-a-${Date.now()}`;
      const ul = el("ul", "search-list");
      ul.setAttribute("aria-labelledby", h.id);
      for (const it of archives) {
        const li = el("li", "search-item");
        const box = el("div", "search-hit archive");
        box.append(hitHead(it), extraitNode(it.extrait));
        li.append(box);
        ul.append(li);
      }
      sec.append(h, ul);
      parts.push(sec);
    }
    list.replaceChildren(...parts);
  }

  // Flèches : du champ au premier résultat, d'un résultat à l'autre ; ↑ sur le premier revient au champ.
  const hits = () => [...list.querySelectorAll("button.search-hit")];
  input.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); input.select(); return; }
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); asked = ""; run(input.value); return; }
    if (e.key === "ArrowDown" && hits().length) { e.preventDefault(); hits()[0].focus(); }
  });
  list.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const all = hits();
    const at = all.indexOf(document.activeElement);
    if (at < 0) return;
    e.preventDefault();
    if (e.key === "ArrowUp" && at === 0) { input.focus(); return; }
    all[Math.max(0, Math.min(all.length - 1, at + (e.key === "ArrowDown" ? 1 : -1)))].focus();
  });
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => run(input.value), DEBOUNCE_MS);
  });
  p.closed.then(() => clearTimeout(timer));
  input.value = last;
  input.focus();
  input.select();
  if (last.trim()) run(last);
  return p;
}
