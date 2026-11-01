// Langue de l'interface du HUD (20/10, demande de l'utilisateur : « avoir une traduction complète
// de l'application en anglais et avoir le choix de passer de anglais à français »).
//
// - `t(clé, params)` : textes PROPRES au HUD, dictionnaires FR/EN de langues.js. Clé absente
//   en anglais : le français ; absente partout : la clé elle-même. `{nom}` est remplacé par
//   params.nom ; une entrée { one, other } choisit le pluriel d'après params.n (règles du
//   navigateur : 0 et 1 au singulier en français, 1 seul en anglais).
// - `tx(texte)` : textes VENUS DU CŒUR (notices, Réglages, erreurs), écrits en français par le
//   Python. Traduits par le catalogue astrel/locales/en.json, même règle que astrel/i18n.py
//   (clé exacte, sinon modèle à `{parties variables}`, sinon morceaux « a · b », sinon tel quel).
// - Langue : réglage `ui_language` de la page Réglages (runtime/settings.json, évènement
//   `settings`), gardé aussi en localStorage pour peindre la page dès le chargement. Un
//   changement recharge la page (le cœur rejoue l'historique, le brouillon est gardé) : tout
//   le HUD repart dans la nouvelle langue, sans relancer Astrel. Défaut : français.
// - HTML statique : attributs data-i18n (texte), data-i18n-title, data-i18n-aria,
//   data-i18n-placeholder ; le français reste écrit dans index.html (repli, tests).

import SERVEUR from "../../astrel/locales/en.json" with { type: "json" };
import { EN, FR } from "./langues.js";

export const LANGS = ["fr", "en"];
const STORE = "astrel-lang";

function stored() {
  try {
    const v = globalThis.localStorage?.getItem(STORE);
    return LANGS.includes(v) ? v : "fr";
  } catch { return "fr"; }
}

let current = stored();
const beforeReload = new Set();

export const lang = () => current;
/** Locale des dates et des nombres (heure sur 24 h dans les deux langues). */
export const locale = () => (current === "en" ? "en-GB" : "fr-FR");

/** Pour les tests (node) : change la langue sans stockage ni rechargement. */
export function useLang(l) { if (LANGS.includes(l)) current = l; }

/** `fn` est appelé juste avant un rechargement dû à la langue (rouvrir une page ensuite). */
export function onBeforeLangReload(fn) { beforeReload.add(fn); }

/** Langue choisie dans les Réglages : retenue, et la page se recharge si elle change. */
export function setLang(l) {
  if (!LANGS.includes(l) || l === current) return false;
  // Sans stockage, la page rechargée repartirait dans l'ancienne langue et se rechargerait sans fin.
  let kept = false;
  try { globalThis.localStorage?.setItem(STORE, l); kept = globalThis.localStorage?.getItem(STORE) === l; } catch { kept = false; }
  if (!kept) return false;
  current = l;
  for (const fn of beforeReload) { try { fn(l); } catch { /* rien */ } }
  if (typeof location !== "undefined") location.reload();
  return true;
}

/** Évènement `settings` (ou réponse settings_get) : suit la valeur de `ui_language`. */
export function syncLang(ev) {
  for (const g of ev?.groups || []) {
    for (const it of g.items || []) if (it.key === "ui_language") return setLang(String(it.value || "fr"));
  }
  return false;
}

// ------------------------------------------------------------------ nom affiché (Astrel)
// 23/10 (plan P31, demande de l'utilisateur) : le produit s'appelle « Astrel », nom CONFIGURABLE (réglage
// `assistant_name` de la page Réglages, évènement `settings` ; défaut « Astrel » ; même source que la
// bulle, astrel/i18n.py assistant_name). Remplacé à l'AFFICHAGE seulement : textes PROPRES au HUD
// (`t()`, avant les paramètres), HTML statique, et textes du cœur CONNUS du catalogue (`tx`, dans les
// parties fixes du modèle seulement : une partie variable peut porter une commande, un chemin ou un
// texte de Claude). Jamais dans un message de Claude ou de l'utilisateur. Restent tels quels : « Hey Jarvis »
// et « OK Astrel » (mot d'éveil, modèle audio fixe), les identifiants (astrel, ASTREL_*, Astrel.service,
// chemins …/Astrel/…, astrel-hud).
// Gardé en localStorage pour peindre la page dès le chargement ; un changement recharge la page.
export const DEFAULT_NAME = "Astrel";
const NAME_STORE = "astrel-assistant";
// ni « Hey Jarvis » / « OK Astrel » (mot d'éveil), ni un identifiant (astrel-hud, Astrel.service, …/Astrel/…)
const NAME_RX = /(?<![\w/.-])(?<![Hh]ey\s)(?<![Oo][Kk]\s)Astrel(?![\w/-]|\.\w)/g;

/** Nom propre, une ligne, 32 caractères au plus ; vide ou illisible = défaut. */
export function cleanName(v) {
  // « <b>Nova</b> » → « Nova » (balise retirée entière), comme astrel/i18n.py clean_name
  const s = String(v ?? "").replace(/<[^<>]*>/g, "").replace(/[\u0000-\u001f\u007f<>]/g, " ")
    .replace(/\s+/g, " ").trim().slice(0, 32).trim();
  return s || DEFAULT_NAME;
}

function storedName() {
  try { return cleanName(globalThis.localStorage?.getItem(NAME_STORE)); } catch { return DEFAULT_NAME; }
}

let assistant = storedName();
export const assistantName = () => assistant;

/** Pour les tests (node) : change le nom sans stockage ni rechargement. */
export function useName(n) { assistant = cleanName(n); }

/** « Astrel » → nom affiché ; en français, élision devant une voyelle (« de Astrel » → « d'Astrel »). */
export function withName(text, name = assistant) {
  if (typeof text !== "string" || !text.includes("Astrel") || name === "Astrel") return text;
  let out = text.replace(NAME_RX, () => name);
  if (current === "fr" && /^[AEIOUYÀÂÉÈÊËÎÏÔÛÙÜaeiouyàâéèêëîïôûùü]/.test(name)) {
    const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // début de mot au sens Unicode : « bibliothèque Astrel » ne devient pas « bibliothèqu'Astrel »
    out = out.replace(new RegExp(`(?<![\\p{L}\\w])([DdJj]|[Qq]u|[Ll]orsqu|[Pp]uisqu|[Jj]usqu)e\\s(?=${esc}(?![\\p{L}\\w-]))`, "gu"), "$1'");
  } else if (current === "fr") {
    // 29/10 (renommage, plan P42) : les textes écrivent « d'Astrel » ; nom choisi qui commence par une
    // consonne : « de Nova » (comme astrel/i18n.py `nom`).
    const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    out = out.replace(new RegExp(`(?<![\\p{L}\\w])([DdJj]|[Qq]u|[Ll]orsqu|[Pp]uisqu|[Jj]usqu)['’](?=${esc}(?![\\p{L}\\w-]))`, "gu"), "$1e ");
  }
  return out;
}

/** Évènement `settings` (ou réponse settings_get) : suit `assistant_name` ; la page se recharge
 *  s'il change (tous les textes déjà posés le reprennent). Absent (cœur sans ce réglage) : rien. */
export function syncName(ev) {
  for (const g of ev?.groups || []) {
    for (const it of g.items || []) {
      if (it.key !== "assistant_name") continue;
      const n = cleanName(it.value);
      if (n === assistant) return false;
      let kept = false;
      try { globalThis.localStorage?.setItem(NAME_STORE, n); kept = globalThis.localStorage?.getItem(NAME_STORE) === n; } catch { kept = false; }
      assistant = n;
      applyName();
      if (!kept) return false; // sans stockage : pas de rechargement en boucle, le nom vaut pour la page
      for (const fn of beforeReload) { try { fn("name"); } catch { /* rien */ } }
      if (typeof location !== "undefined") location.reload();
      return true;
    }
  }
  return false;
}

/** Nom dans la marque du HUD et le titre de l'onglet. */
export function applyName() {
  const doc = globalThis.document;
  if (!doc) return;
  doc.title = assistant;
  const b = doc.querySelector?.(".brand-name");
  if (b) b.textContent = assistant;
}

// ------------------------------------------------------------------ textes du HUD

const rules = new Map();
function pluralForm(n) {
  const loc = locale();
  if (!rules.has(loc)) rules.set(loc, new Intl.PluralRules(loc));
  return rules.get(loc).select(Number(n) || 0);
}

function fill(text, params) {
  return text.replace(/\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g, (m, k) => (params && params[k] != null ? String(params[k]) : m));
}

export function t(key, params) {
  let v = (current === "en" ? EN[key] : undefined) ?? FR[key];
  if (v == null) return key;
  if (typeof v === "object") v = v[pluralForm(params?.n)] ?? v.other ?? v.one ?? "";
  return fill(withName(String(v)), params); // le nom avant les paramètres : jamais dans un texte reçu
}

/** Les deux dictionnaires (tests : mêmes clés de part et d'autre). */
export const dictionaries = () => ({ FR, EN });

// ------------------------------------------------------------------ textes du cœur

const PLACEHOLDER = /\{([a-z_][a-z0-9_]*)\}/g;
// [séparateur français, anglais, strict] : « a, b et c » seulement si tous les morceaux sont connus.
const SEPARATORS = [["\n", "\n", false], [" · ", " · ", false], [" ; ", "; ", false], [" et ", " and ", true],
  [", ", ", ", true]];
const exact = new Map();
const patterns = [];
for (const [fr, en] of Object.entries(SERVEUR)) {
  if (fr.startsWith("_") || typeof en !== "string" || !en) continue;
  exact.set(fr, en);
  const names = [...fr.matchAll(PLACEHOLDER)].map((m) => m[1]);
  if (!names.length) continue;
  const literal = fr.replace(PLACEHOLDER, "");
  const parts = fr.split(PLACEHOLDER).filter((_, i) => i % 2 === 0);
  // « {a}, {b} de {c} » prendrait n'importe quoi : sans début fixe, au moins 4 lettres fixes.
  if (!/[A-Za-zÀ-ÿ]/.test(parts[0]) && (literal.match(/[A-Za-zÀ-ÿ]/g) || []).length < 4) continue;
  const rx = new RegExp(`^${parts.map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("([\\s\\S]*?)")}$`);
  patterns.push({ prefix: parts[0], literal, rx, names, en, fr });
}
patterns.sort((a, b) => b.literal.length - a.literal.length); // le plus précis d'abord

// Texte CONNU du catalogue rendu en anglais (`en` vrai) ou en français (le texte lui-même), le nom
// affiché (P31) posé dans ses parties FIXES seulement ; null si inconnu.
function english(text, depth, en = true) {
  const hit = exact.get(text);
  if (hit != null) return withName(en ? hit : text);
  const trimmed = text.trim();
  if (trimmed !== text && trimmed) {
    const inner = english(trimmed, depth, en);
    if (inner == null) return null;
    return text.slice(0, text.length - text.trimStart().length) + inner + text.slice(text.trimEnd().length);
  }
  for (const p of patterns) {
    if (p.prefix && !text.startsWith(p.prefix)) continue;
    const m = p.rx.exec(text);
    if (!m) continue;
    const values = {};
    p.names.forEach((name, i) => {
      const v = m[i + 1];
      values[name] = depth < 2 && v ? (english(v, depth + 1, en) ?? v) : v;
    });
    // le nom dans le modèle, jamais dans les valeurs (commande, chemin, texte de Claude)
    return withName(en ? p.en : p.fr).replace(PLACEHOLDER, (all, name) => (name in values ? values[name] : all));
  }
  if (depth < 3) {
    for (const [sep, enSep, strict] of SEPARATORS) {
      if (!text.includes(sep)) continue;
      const parts = text.split(sep);
      const done = parts.map((x) => english(x, depth + 1, en));
      if (strict ? done.every((d) => d != null) : done.some((d) => d != null)) {
        return parts.map((x, i) => done[i] ?? x).join(en ? enSep : sep);
      }
    }
  }
  return null;
}

// Après traduction : nombres et unités à l'anglaise (« 4,3 Go » → « 4.3 GB »), comme astrel/i18n.py.
const UNITS = [[/(\d),(\d)/g, "$1.$2"], [/(\d) ?Go\b/g, "$1 GB"], [/(\d) ?Mo\b/g, "$1 MB"], [/(\d) ?Ko\b/g, "$1 KB"]];

const txCache = new Map();
/** Texte écrit en français par le cœur, dans la langue de l'interface (repli : tel quel). */
export function tx(text) {
  if (typeof text !== "string" || !text) return text;
  const en = current === "en";
  if (!en && !text.includes("Astrel")) return text; // français : seul le nom affiché (P31) peut changer
  const key = `${current}|${assistant}|${text}`;
  let out = txCache.get(key);
  if (out === undefined) {
    out = english(text, 0, en);
    if (out == null) out = text;
    else if (en) for (const [rx, rep] of UNITS) out = out.replace(rx, rep);
    if (txCache.size > 2000) txCache.clear();
    txCache.set(key, out);
  }
  return out;
}

// ------------------------------------------------------------------ HTML statique

const ATTRS = [["i18nTitle", "title"], ["i18nAria", "aria-label"], ["i18nPlaceholder", "placeholder"]];

/** Traduit les marques data-i18n* sous `root` (le français d'index.html sert de repli). */
export function applyI18n(root = document) {
  document.documentElement.lang = current;
  if (root === document) applyName();
  if (current === "fr") { // index.html est écrit en français : seul le nom (P31) y change
    for (const e of root.querySelectorAll("[data-i18n], [data-i18n-title], [data-i18n-aria], [data-i18n-placeholder]")) {
      if (e.dataset.i18n && e.textContent.includes("Astrel")) e.textContent = t(e.dataset.i18n);
      for (const [k, attr] of ATTRS) if (e.dataset[k] && e.getAttribute(attr)?.includes("Astrel")) e.setAttribute(attr, t(e.dataset[k]));
    }
    return;
  }
  for (const e of root.querySelectorAll("[data-i18n], [data-i18n-title], [data-i18n-aria], [data-i18n-placeholder]")) {
    if (e.dataset.i18n) e.textContent = t(e.dataset.i18n);
    for (const [k, attr] of ATTRS) if (e.dataset[k]) e.setAttribute(attr, t(e.dataset[k]));
  }
}
