// Page « Capacités » du HUD : tout ce qu'Astrel sait faire (21/10, plan P22, demande de
// l'utilisateur : « une page du HUD listant tout ce qu'Astrel sait faire […], générée plutôt que
// figée »).
//
// Données : commande `capacites_get {lang}` du cœur (astrel/capacites.py, refusée à Claude),
// déjà traduites. Réponse { groupes: [{ id, titre, lien?, entrees: [{ nom, texte?, etat?,
// gravite?, usage?, lien? }] }] } ; un groupe SANS `entrees` (réponse découpée pour rester
// sous 4096 octets) est demandé seul par `capacites_get {lang, groupe}`.
// Rendu : une section repliable <details> par groupe, recherche (sans accents ni casse, tous
// les mots), tri (ordre d'origine, A→Z, plus utilisées). TOUT en textContent : noms et
// descriptions viennent de docstrings, de règles et de notes, ce sont des DONNÉES.
// Entrée : bouton du Guide (lot 2, guide.js) ; `initCapacites({ openGuide, openSettings })`
// reçoit de main.js de quoi suivre les liens « Ouvrir » (pas d'import de guide.js ici :
// guide.js importera ce module).

import { command } from "./conn.js";
import { openPanel } from "./modal.js";
import { icon } from "./icons.js";
import { lang, locale, t, tx } from "./i18n.js";

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

// ------------------------------------------------------------------ recherche et tri (testés en Node)

/** Texte comparable : sans accents, sans casse, ligatures dépliées, apostrophes et blancs unifiés. */
export function fold(s) {
  return String(s ?? "")
    .replace(/œ/g, "oe").replace(/Œ/g, "OE").replace(/æ/g, "ae").replace(/Æ/g, "AE")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[’‘`´]/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

/** Mots de la recherche (repliés, guillemets et ponctuation de bord retirés). */
export function words(query) {
  return fold(query).split(" ").map((w) => w.replace(/^[«»"“”()]+|[«»"“”(),.;:!?]+$/g, "")).filter(Boolean);
}

/** Les groupes réduits aux entrées qui contiennent TOUS les mots (titre du groupe compris). */
export function filtrer(groupes, query) {
  const ws = words(query);
  const list = Array.isArray(groupes) ? groupes : [];
  if (!ws.length) return list.map((g) => ({ ...g, entrees: entries(g) }));
  const out = [];
  for (const g of list) {
    const head = fold(g.titre);
    const kept = entries(g).filter((e) => {
      const hay = `${head} ${fold(e.nom)} ${fold(e.texte)} ${fold(e.etat)}`;
      return ws.every((w) => hay.includes(w));
    });
    if (kept.length || (g.erreur && ws.every((w) => head.includes(w)))) out.push({ ...g, entrees: kept });
  }
  return out;
}

const bare = (s) => String(s ?? "").replace(/^[\s«»"“”]+/, "");

/** Entrées triées : "defaut" (ordre du cœur), "az" (alphabétique, accents compris), "usage". */
export function trier(entrees, mode = "defaut", loc = "fr-FR") {
  const list = Array.isArray(entrees) ? [...entrees] : [];
  if (mode === "defaut") return list;
  const coll = new Intl.Collator(loc, { sensitivity: "base", numeric: true });
  const byName = (a, b) => coll.compare(bare(a.nom), bare(b.nom));
  if (mode === "az") return list.sort(byName);
  if (mode === "usage") return list.sort((a, b) => (Number(b.usage) || 0) - (Number(a.usage) || 0) || byName(a, b));
  return list;
}

/** Nombre total d'entrées. */
export const compter = (groupes) => (Array.isArray(groupes) ? groupes : []).reduce((n, g) => n + entries(g).length, 0);

function entries(g) { return Array.isArray(g?.entrees) ? g.entrees.filter((e) => e && e.nom != null) : []; }

// ------------------------------------------------------------------ fenêtre

let links = { openGuide: null, openSettings: null };
let view = null; // { panel, status, search, sort, list }
let data = null; // groupes reçus
const openState = new Map(); // id du groupe → déplié (hors recherche)

export function initCapacites(opts = {}) {
  links = { openGuide: opts.openGuide || null, openSettings: opts.openSettings || null };
}

export async function openCapacites() {
  if (view) { view.search.focus(); return; }
  const panel = openPanel({ title: t("capacites.title"), icon: "sparkles", cls: "capacites" });
  const bar = el("div", "cap-bar");
  const search = el("input", "cap-search");
  search.type = "search";
  search.placeholder = t("capacites.search");
  search.setAttribute("aria-label", t("capacites.search_aria"));
  search.autocomplete = "off";
  search.spellcheck = false;
  const sort = el("select", "cap-sort");
  sort.setAttribute("aria-label", t("capacites.sort_aria"));
  for (const [value, label] of [["defaut", t("capacites.sort_default")], ["az", t("capacites.sort_az")],
    ["usage", t("capacites.sort_usage")]]) {
    const o = el("option", "", label);
    o.value = value;
    sort.append(o);
  }
  bar.append(search, sort);
  const status = el("p", "cap-status", t("common.loading"));
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const list = el("div", "cap-list");
  panel.body.append(bar, status, list);
  view = { panel, status, search, sort, list };
  search.addEventListener("input", render);
  sort.addEventListener("change", render);
  panel.closed.then(() => { if (view?.panel === panel) view = null; });
  search.focus();
  await load();
}

async function load() {
  const r = await command("capacites_get", { lang: lang() });
  if (!view) return;
  if (r?.ok === false || !Array.isArray(r?.groupes)) {
    view.status.textContent = t("capacites.unavailable", { error: r?.error ? tx(String(r.error)) : t("common.error") });
    view.status.classList.add("err");
    return;
  }
  const groupes = r.groupes.filter((g) => g && g.id != null);
  // Réponse découpée : chaque groupe sans entrées est demandé seul.
  await Promise.all(groupes.map(async (g) => {
    if (Array.isArray(g.entrees)) return;
    const one = await command("capacites_get", { lang: lang(), groupe: g.id });
    const got = Array.isArray(one?.groupes) ? one.groupes.find((x) => x?.id === g.id) : null;
    // Sous-appel en échec (refus, coupure, réponse illisible) : le groupe le DIT, pas de liste vide muette.
    g.entrees = Array.isArray(got?.entrees) ? got.entrees : [];
    if (one?.ok === false || !Array.isArray(got?.entrees)) g.erreur = one?.error ? tx(String(one.error)) : t("common.error");
  }));
  if (!view) return;
  data = groupes;
  if (!openState.size && data[0]) openState.set(String(data[0].id), true);
  render();
}

function render() {
  if (!view || !data) return;
  const q = view.search.value;
  const searching = words(q).length > 0;
  const shown = filtrer(data, q);
  const n = compter(shown);
  view.status.classList.remove("err");
  view.status.textContent = searching && !n ? t("capacites.none", { q: q.trim() }) : t("capacites.count", { n });
  const focusKey = view.list.contains(document.activeElement) ? document.activeElement.dataset.fkey : null;
  view.list.replaceChildren(...shown.map((g) => groupeDom(g, searching, view.sort.value)));
  if (focusKey) [...view.list.querySelectorAll("[data-fkey]")].find((x) => x.dataset.fkey === focusKey)?.focus({ preventScroll: true });
}

/** Section <details> d'un groupe (exportée pour les tests : rendu en faux DOM). */
export function groupeDom(g, searching = false, sort = "defaut") {
  const id = String(g.id);
  const d = el("details", "cap-group");
  d.dataset.group = id;
  d.open = searching || !!openState.get(id);
  d.addEventListener("toggle", () => { if (!words(view?.search.value ?? "").length) openState.set(id, d.open); });
  const sum = el("summary", "cap-summary");
  sum.dataset.fkey = `cap|group|${id}`;
  sum.append(el("span", "cap-title", String(g.titre ?? id)), el("span", "cap-n", String(entries(g).length)));
  d.append(sum);
  if (g.lien?.type === "guide" && links.openGuide) {
    d.append(linkButton(t("capacites.open_guide"), "book", `cap|glink|${id}`, () => follow(g.lien)));
  }
  if (g.erreur) {
    // Libellé (langues.js) et détail séparés : le détail est une DONNÉE du cœur, en textContent.
    const err = el("p", "cap-status err");
    err.append(el("span", "", t("capacites.group_failed")), el("span", "cap-err-detail", ` ${String(g.erreur)}`));
    err.setAttribute("role", "status");
    d.append(err);
  }
  const ul = el("ul", "cap-entries");
  for (const e of trier(entries(g), sort, locale())) ul.append(entry(e, id));
  d.append(ul);
  return d;
}

function entry(e, gid) {
  const li = el("li", "cap-entry");
  const head = el("div", "cap-head");
  head.append(el("span", "cap-nom", String(e.nom)));
  if (e.etat) head.append(el("span", "cap-etat", String(e.etat)));
  if (e.gravite) {
    const g = String(e.gravite);
    const label = g === "critique" ? t("capacites.grav_critique")
      : g === "sensible" ? t("capacites.grav_sensible") : t("capacites.grav_libre");
    head.append(el("span", `cap-grav grav-${g === "critique" || g === "sensible" ? g : "libre"}`, label));
  }
  if (Number(e.usage) > 0) head.append(el("span", "cap-usage", t("capacites.uses", { n: Number(e.usage) })));
  li.append(head);
  if (e.texte) li.append(el("p", "cap-texte", String(e.texte)));
  const lien = e.lien;
  if (lien && ((lien.type === "guide" && links.openGuide) || (lien.type === "reglages" && links.openSettings))) {
    const label = lien.type === "guide" ? t("capacites.open_guide") : t("capacites.open_settings");
    li.append(linkButton(label, lien.type === "guide" ? "book" : "settings",
      `cap|link|${gid}|${String(lien.id)}`, () => follow(lien)));
  }
  return li;
}

function linkButton(label, iconName, fkey, onclick) {
  const b = el("button", "btn cap-link");
  b.type = "button";
  b.dataset.fkey = fkey;
  b.append(icon(iconName, "lead"), el("span", "", label));
  b.onclick = onclick;
  return b;
}

function follow(lien) {
  const fn = lien.type === "guide" ? links.openGuide : links.openSettings;
  if (!fn) return;
  view?.panel.close(); // une seule fenêtre large à la fois (openPanel)
  fn(String(lien.id));
}
