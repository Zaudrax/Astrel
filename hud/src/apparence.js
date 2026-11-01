// Thème du HUD (21/10, plan P17 ; demande de l'utilisateur : « changer le thème de l'app Astrel
// ainsi que sa bulle entièrement via les réglages »).
// 25/10 (plan P35, demande de l'utilisateur) : le thème Classique est abandonné, Premium est le SEUL
// thème du HUD. Le réglage `theme` (encore envoyé par un cœur d'avant P35) est IGNORÉ ; restent
// `theme_mode` (auto/light/dark) et `theme_accent`.
//
// - Réglages `theme_mode` et `theme_accent` de la page Réglages (évènement `settings`) :
//   appliqués SANS rechargement, par les attributs data-mode et data-accent de <html>
//   (styles.css, bloc « P17 thèmes ») ; data-theme="premium" est posé en dur dans index.html.
// - « auto » suit le système (matchMedia prefers-color-scheme), y compris pendant l'affichage.
// - Gardé en localStorage (`astrel-theme`) : hud/theme-boot.js le pose avant la première
//   peinture. Un ancien cache `{theme: "classique", …}` ne fait plus rien : seuls son mode et son
//   accent sont lus.
// - Changement de mode ou d'accent : transitions coupées le temps du changement (recette better-ui
//   « suppress transitions on theme switch »), sinon chaque couleur glisserait en même temps.

const STORE = "astrel-theme";
const KEYS = { theme_mode: "mode", theme_accent: "accent" };
const MODES = ["auto", "light", "dark"];
const ACCENTS = ["bleu", "graphite", "vert", "orange"];
const DEFAULT = { mode: "auto", accent: "bleu" };

const listeners = new Set();
let choice = load();
let applied = null; // { theme, mode, accent } réellement posés (mode résolu)
const dark = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;

// Minuscules, comme la bulle (astrel/theme.py) et la page Réglages : « DARK » = sombre ;
// valeur inconnue = défaut (Automatique, bleu).
const low = (v) => String(v ?? "").trim().toLowerCase();

function clean(raw) {
  const c = { ...DEFAULT };
  const mode = low(raw?.mode), accent = low(raw?.accent);
  if (MODES.includes(mode)) c.mode = mode;
  if (ACCENTS.includes(accent)) c.accent = accent;
  return c;
}

function load() {
  try { return clean(JSON.parse(globalThis.localStorage?.getItem(STORE) || "{}")); } catch { return { ...DEFAULT }; }
}

/** « dark » ou « light » : « auto » suit le système (sombre si le système ne dit rien). */
export function resolveMode(c = choice) {
  const mode = low(c?.mode);
  if (mode === "light" || mode === "dark") return mode;
  return dark && !dark.matches && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

/** Thème posé : { theme: "premium", mode (résolu), accent, choice }. */
export function themeNow() {
  return { theme: "premium", mode: resolveMode(), accent: choice.accent, choice: { ...choice } };
}

/** `fn(themeNow())` à chaque changement de thème posé (orbe). */
export function onTheme(fn) { listeners.add(fn); }

function suppressTransitions(run) {
  const doc = globalThis.document;
  if (!doc?.head) { run(); return; }
  const style = doc.createElement("style");
  style.textContent = "*,*::before,*::after{transition:none !important}";
  doc.head.append(style);
  run();
  void doc.body?.offsetHeight; // vide le style en attente : les nouvelles couleurs sont posées
  requestAnimationFrame(() => requestAnimationFrame(() => style.remove()));
}

/** Pose le mode et l'accent choisis sur <html> (rien ne change si ce sont déjà eux). */
export function applyTheme(c = choice) {
  choice = clean(c);
  const now = themeNow();
  const key = `${now.mode}|${now.accent}`;
  const before = applied ? `${applied.mode}|${applied.accent}` : null;
  if (key === before) return false;
  const root = globalThis.document?.documentElement;
  const set = () => {
    if (!root) return;
    root.dataset.theme = "premium"; // déjà dans index.html ; reposé si un script l'avait retiré
    root.dataset.mode = now.mode;
    root.dataset.accent = now.accent;
  };
  if (before === null) set(); else suppressTransitions(set);
  applied = now;
  for (const fn of listeners) { try { fn(now); } catch { /* un écouteur fautif n'empêche pas les autres */ } }
  return true;
}

// ------------------------------------------------------------------ affichage Simple / Avancé
// 22/10 (plan P23, demande de l'utilisateur : montrer Astrel à quelqu'un qui le découvre, l'écran
// « trop geek ») : réglage `affichage` (simple/avance) de Réglages › Apparence (l'interrupteur « Simple »
// de la barre est retiré le 28/10, T10, décision de l'utilisateur). Simple = attribut data-affichage="simple" sur <html> (styles.css, bloc
// « P23 affichage ») ; Avancé = AUCUN attribut, la page reste celle d'avant.
// À part du thème : changer d'affichage ne repeint ni l'orbe ni le graphe (pas d'écouteur onTheme).
// Gardé en localStorage (`astrel-affichage`) : hud/theme-boot.js le pose avant la première peinture.
const STORE_AFF = "astrel-affichage";
const AFFICHAGES = ["simple", "avance"];
const affListeners = new Set();
let affichage = loadAffichage();
let affApplied = null;

function cleanAffichage(v) {
  const a = low(v);
  return AFFICHAGES.includes(a) ? a : "avance";
}

function loadAffichage() {
  try { return cleanAffichage(globalThis.localStorage?.getItem(STORE_AFF)); } catch { return "avance"; }
}

/** Affichage posé : « simple » ou « avance ». */
export function affichageNow() { return affichage; }

/** `fn(affichage)` à chaque changement d'affichage posé (interrupteur, canaux). */
export function onAffichage(fn) { affListeners.add(fn); }

/** Pose l'affichage sur <html> (rien ne change si c'est déjà lui). */
export function applyAffichage(v = affichage) {
  affichage = cleanAffichage(v);
  if (affichage === affApplied) return false;
  const root = globalThis.document?.documentElement;
  const set = () => {
    if (!root) return;
    if (affichage === "simple") root.dataset.affichage = "simple";
    else delete root.dataset.affichage;
  };
  if (affApplied === null) set(); else suppressTransitions(set);
  affApplied = affichage;
  for (const fn of affListeners) { try { fn(affichage); } catch { /* un écouteur fautif n'empêche pas les autres */ } }
  return true;
}

function syncAffichage(ev) {
  for (const g of ev?.groups || []) {
    for (const it of g.items || []) {
      if (it.key !== "affichage") continue;
      const a = cleanAffichage(it.value);
      try { globalThis.localStorage?.setItem(STORE_AFF, a); } catch { /* sans stockage */ }
      return applyAffichage(a);
    }
  }
  return false;
}

/** Évènement `settings` (ou réponse settings_get) : suit `theme_mode`, `theme_accent` et
 *  l'affichage (`affichage`, P23) ; `theme` est ignoré (P35). Rend vrai si le mode ou l'accent
 *  posé a changé. */
export function syncTheme(ev) {
  syncAffichage(ev);
  const raw = {};
  let seen = false;
  for (const g of ev?.groups || []) {
    for (const it of g.items || []) {
      if (Object.prototype.hasOwnProperty.call(KEYS, it.key)) { raw[KEYS[it.key]] = String(it.value ?? ""); seen = true; }
    }
  }
  if (!seen) return false;
  const c = clean(raw);
  // `theme: "premium"` gardé dans le cache : un HUD d'avant P35 rouvert sur ce profil reste en Premium
  try { globalThis.localStorage?.setItem(STORE, JSON.stringify({ theme: "premium", ...c })); } catch { /* sans stockage */ }
  return applyTheme(c);
}

/** À chaque connexion (`hello`) : demande le mode et l'accent au cœur (`settings_get`, qui rend et
 *  republie les réglages). Relecture P17 : le cache localStorage ne suffit pas (profil Chrome neuf,
 *  settings.json édité à la main) ; la bulle, elle, lit le fichier. Le changement éventuel passe
 *  par applyTheme (transitions coupées : pas de glissé de couleurs). */
export async function askTheme(command) {
  let r = null;
  try { r = await command("settings_get"); } catch { return false; }
  return Array.isArray(r?.groups) ? syncTheme(r) : false;
}

// Le système passe en clair ou en sombre : suivi en direct quand le mode est « auto ».
dark?.addEventListener?.("change", () => { if (choice.mode === "auto") applyTheme(choice); });
