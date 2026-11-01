// Page « Réglages » du HUD (15/10, demande de l'utilisateur ; contrat :
// docs/CONTRAT-REGLAGES-2026-10-15.md). GÉNÉRIQUE : elle dessine ce que décrit
// l'évènement `settings` du cœur (groupes, réglages typés) sans connaître chaque
// réglage. Textes du serveur TOUJOURS en textContent, jamais d'innerHTML.
// Commandes : settings_get, settings_set {key, value}, settings_apply (après
// confirmation : le cerveau redémarre, les tâches de fond sont coupées). Rien
// n'est dit à voix haute.
// 17/10 (plan P4, lot 1) : genres `info` (lignes {label, text, level}, bloc
// copiable, lien https) et `action` (boutons {id, label, danger, confirm, word}) ;
// commande `settings_action {key, action, confirm}` (jamais `id`) ; un groupe peut
// être ouvert d'office (`open`) ; la fenêtre s'ouvre seule si `first_run`.
// 18/10 (plan P5) : au premier lancement c'est le GUIDE qui s'ouvre (guide.js) ; un
// réglage décrit avec `guide` porte un bouton « ? » (aide par sujet) ; un réglage peut
// être monté dans le guide (`mountSetting`) ; un bouton `input` demande une valeur
// (envoyée sous `value`) ; ligne `whatsapp` : QR ou code d'appairage, reçus par
// l'évènement `whatsapp_link` SEULEMENT (en mémoire, jamais dans `settings`).

import qrcode from "qrcode-generator";
import { command, on } from "./conn.js";
import { openModal, openPanel } from "./modal.js";
import { icon, setLabel } from "./icons.js";
import { safeHref } from "./markdown.js";
import { onBeforeLangReload, syncLang, syncName, t, tx } from "./i18n.js";
import { askTheme, resolveMode, syncTheme } from "./apparence.js";
import { palette } from "./theme.js";

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

const TRI = [["on", "settings.tri_on"], ["name-only", "settings.tri_name"], ["off", "settings.tri_off"]];

// 19/10 (demande : « des icônes dans les Réglages permettraient d'avoir une interface plus
// lisible ») : une icône (icons.js) devant chaque section et chaque réglage important, choisie
// par l'id du groupe et la clé du réglage (astrel/settings.py, tools/hud_demo.py). Section
// inconnue : roue dentée ; réglage inconnu : pas d'icône (rien d'inventé).
const GROUP_ICONS = {
  compte: "user", demarrage: "rocket", diagnostic: "activity", voix: "volume", confidentialite: "shield",
  maj: "download", systeme: "monitor", whatsapp: "smartphone", navigateurs: "globe", connecteurs: "plug",
  skills: "sparkles", plugins: "puzzle", seuils: "bell", cerveau: "brain", garde_fou: "shield-check",
  limite: "gauge", modele_local: "database", fournisseur: "cloud", apparence: "palette",
  capteurs: "scan", // 23/10 : tous les capteurs (le panneau du HUD ne garde que WATCH)
  initiatives: "sparkles", // 24/10 (plan P32) : initiatives d'Astrel (brief du matin)
  images: "image", // 28/10 (plan P40 lot A5) : images IA (comptes, moteurs, plafonds : images.js)
};
const ITEM_ICONS = {
  ui_language: "languages", // 20/10 : langue de l'interface
  // 21/10 (plan P17) : apparence. Depuis P35, le cœur ne déclare plus `theme` ; un cœur d'avant P35
  // peut encore l'envoyer, masqué par HIDDEN_ITEMS : son icône ne sert plus, gardée sans effet.
  theme: "palette", theme_mode: "sun-moon", theme_accent: "droplet",
  affichage: "eye", // 22/10 (plan P23) : affichage Simple / Avancé (même icône que l'interrupteur de la barre)
  assistant_name: "sparkles", // 23/10 (plan P31) : nom affiché (Astrel)
  compte: "user", demarrage: "rocket", diagnostic: "activity", effacer: "trash",
  "audio.mic": "mic", "audio.output": "volume", "audio.voice": "waves", "audio.wake": "listen", "audio.test": "play",
  wake_legacy: "history", wake_names: "pen", // 25/10 (plan P36) : ancien mot d'éveil (alias), variantes du nom
  "audio.followup": "message", "audio.followup_s": "timer", // 20/10 : écoute prolongée après une réponse
  stt_language: "languages", confidentialite: "eye", history_keep_days: "history", donnees: "database", maj: "download",
  "systeme.autostart": "power", "systeme.bubble": "message", "systeme.hotkeys": "keyboard", whatsapp: "smartphone",
  "browser.main_visible": "monitor", "browser.extra_visible": "eye", "browser.extra_count": "layers",
  connectors: "plug", skills: "sparkles", plugins: "puzzle",
  agent_alert_tokens: "bot", agent_alert_s: "timer", quota_alert_pct: "gauge",
  brain_effort: "zap", session_idle_reset_s: "clock", context_relief_tokens: "refresh", plan_mode: "plans",
  rappel: "book", garde_fou_levee: "unlock",
  // 24/10 (plan P29 lot B) : cases de confirmation (résumé et boutons, puis une grille par groupe)
  confirmations: "shield-check", "confirmations.garde_fou": "shield", "confirmations.secrets": "lock",
  "confirmations.fichiers": "layers", "confirmations.systeme": "monitor", "confirmations.git": "history",
  "confirmations.exterieur": "globe", "confirmations.autres": "help",
  quota_offer: "gauge", quota_offer_pct: "gauge", reprise_auto: "refresh", quota_refresh: "refresh",
  quota_arret: "bot", quota_note_pct: "gauge", quota_stop_pct: "gauge", // 24/10 (plan P34) : agents près de la limite
  quota_reprise: "refresh", // 27/10 : relance des agents arrêtés pour le quota (auto, carte, rien)
  "local.mode": "brain", "local.materiel": "monitor", "local.modeles": "database",
  fournisseur: "cloud", "fournisseur.cles": "lock", depense_alerte_usd: "bell",
  capteurs: "scan",
  // 24/10 (plan P32) : initiatives d'Astrel
  initiatives_routines: "activity", initiatives: "sparkles", initiatives_brief: "sun-moon", initiatives_quota: "gauge",
  initiatives_echeances: "clock", initiatives_courrier: "message", initiatives_max_jour: "bell", initiatives_calme: "timer",
  // 28/10 (hud-boutons-t10) : accès retirés de la barre du HUD (comptes rendus, mission, extinction)
  reports: "volume", mission: "target", "systeme.eteindre": "power",
  // 28/10 (plan P40 lot A5) : images IA, mode auto ou manuel, ordre des moteurs, moteur et modèle en manuel
  images_mode: "zap", images_ordre: "layers", images_manuel: "target",
  // 28/10 (plan P41 lot B3) : vidéo IA, modèle et durée (coût en crédits)
  video_choix: "play",
};
// 25/10 (plan P35, demande de l'utilisateur) : thème Classique abandonné, Premium seul. Le réglage
// `theme` (encore envoyé par un cœur d'avant P35) n'est pas affiché : plus de cartes de thème.
const HIDDEN_ITEMS = new Set(["theme"]);
const shownItems = (g) => (g?.items || []).filter((it) => !HIDDEN_ITEMS.has(it.key));
const own = (map, k) => (Object.prototype.hasOwnProperty.call(map, k) ? map[k] : "");
export const groupIcon = (id) => own(GROUP_ICONS, String(id)) || "settings";
export const itemIcon = (key) => own(ITEM_ICONS, String(key));

let last = null; // dernier évènement `settings` reçu
let view = null; // fenêtre ouverte : { panel, status, banner, groups }
let toast = () => {};
const openGroups = new Map(); // id du groupe → déplié ? (gardé d'une ouverture à l'autre)
const errors = new Map(); // clé du réglage → message (gardé entre deux rendus)
const results = new Map(); // clé du réglage → dernier résultat d'action {text, level, block, link}
const running = new Set(); // « clé|action » en cours : bouton grisé jusqu'à la réponse
const mounts = new Set(); // réglages montés ailleurs (guide) : { box, key }
let guideHelp = null; // ouvre le guide sur un sujet (branché par guide.js)

// 20/10 : la langue de l'interface change → la page se recharge (i18n.js) ; la page
// « Réglages » ouverte se rouvre ensuite, sur le même réglage.
const REOPEN = "astrel-reopen-settings";
// 23/10 (plan P31) : de même quand le nom affiché (`assistant_name`) change.
onBeforeLangReload((what) => {
  if (view) try { sessionStorage.setItem(REOPEN, what === "name" ? "assistant_name" : "ui_language"); } catch { /* rien */ }
});
const reloads = (ev) => syncLang(ev) || syncName(ev); // vrai : rechargement en cours

export function initSettings(toastFn) {
  toast = toastFn || toast;
  document.querySelector("#settings-chip").onclick = () => open();
  on("settings", (ev) => {
    last = ev;
    syncTheme(ev); // 21/10 (plan P17) : thème appliqué sans rechargement (apparence.js)
    if (reloads(ev)) return; // rechargement en cours
    render();
    let again = "";
    try { again = sessionStorage.getItem(REOPEN) || ""; sessionStorage.removeItem(REOPEN); } catch { /* rien */ }
    if (again) openSettings(again);
  });
  // relecture P17 : thème redemandé au cœur à chaque connexion (cache périmé, profil neuf)
  on("hello", () => { askTheme(command); });
  on("whatsapp_link", onLink);
  // Refus ou erreur de validation : `notice` portant `setting_error: {key, message}`.
  // Le `settings` à jour peut arriver avant ou après : le message est gardé.
  on("notice", (ev) => { if (!ev.replay && ev.setting_error) noteError(ev.setting_error); });
  on("setting_error", (ev) => { if (!ev.replay) noteError(ev.setting_error || ev); });
}

function noteError(err) {
  if (!err || typeof err !== "object" || !err.key) return;
  errors.set(String(err.key), tx(String(err.message || "")) || t("settings.refused"));
  render();
}

// Réglage ACTUEL (le dernier `settings` reçu, mis à jour de façon optimiste).
function item(key) {
  for (const g of last?.groups || []) for (const it of g.items || []) if (it.key === key) return it;
  return null;
}

// ------------------------------------------------------------------ fenêtre

/** Aide par sujet : `fn(topic)` ouvre le guide sur ce sujet (guide.js). */
export function onGuideHelp(fn) { guideHelp = fn; }

/** Ouvre la page « Réglages », dépliée et défilée jusqu'au réglage `key` s'il est donné. */
export async function openSettings(key) {
  await open();
  if (!key || !view) return;
  const g = (last?.groups || []).find((x) => (x.items || []).some((it) => it.key === key));
  if (g) { for (const x of last.groups) openGroups.set(x.id, x.id === g.id); render(); }
  const row = [...view.groups.querySelectorAll(".setting")].find((r) => r.dataset.key === key);
  if (row) {
    row.scrollIntoView({ block: "center" });
    (row.querySelector("button, select, input") || row).focus?.({ preventScroll: true });
  }
}

/** Dernier `settings` connu, sinon demandé au cœur (le guide en a besoin avant la page). */
export async function loadSettings() {
  if (last) return last;
  const r = await command("settings_get");
  if (Array.isArray(r?.groups) && !last) { last = { ...r, type: "settings" }; render(); }
  return last;
}

/** Monte le réglage `key` dans un autre panneau (guide) : { el, dispose } ; il suit
 *  chaque `settings` comme la page elle-même (mêmes contrôles, mêmes commandes). */
export function mountSetting(key) {
  const box = el("div", "setting-mount");
  const m = { box, key };
  mounts.add(m);
  paintMount(m);
  return { el: box, dispose: () => mounts.delete(m) };
}

function paintMount(m) {
  const it = item(m.key);
  const active = document.activeElement;
  const fkey = m.box.contains(active) ? active.dataset.fkey : null;
  m.box.replaceChildren(it ? renderItem(it, true) : el("p", "setting-help", t("settings.when_running")));
  if (fkey) [...m.box.querySelectorAll("[data-fkey]")].find((x) => x.dataset.fkey === fkey)?.focus({ preventScroll: true });
}

async function open() {
  if (view) { view.panel.box.focus(); return; }
  const panel = openPanel({ title: t("settings.title"), icon: "settings", cls: "settings" });
  const banner = el("div", "settings-banner hidden");
  banner.setAttribute("role", "status");
  const apply = el("button", "btn settings-apply", t("settings.apply"));
  apply.type = "button";
  apply.onclick = applyNow;
  banner.append(el("span", "", t("settings.pending_banner")), apply);
  const status = el("p", "settings-status", t("common.loading"));
  const groups = el("div", "settings-groups");
  panel.body.append(banner, status, groups);
  view = { panel, status, banner, groups };
  panel.closed.then(() => { if (view?.panel === panel) view = null; });
  if (last) render(); // l'état connu tout de suite, le cœur le rafraîchit
  await refresh();
}

async function refresh() {
  const r = await command("settings_get");
  if (!view) return;
  if (Array.isArray(r?.groups)) { last = { ...r, type: "settings" }; syncTheme(last); if (!reloads(last)) render(); return; }
  if (r?.ok === false) {
    view.status.textContent = t("settings.unavailable", { error: tx(r.error) || t("common.unknown_error") });
    view.status.classList.remove("hidden");
    view.status.classList.add("err");
  } else if (!last) {
    // l'accusé peut précéder l'évènement `settings` (deux files côté serveur) : on patiente
    const v = view;
    setTimeout(() => {
      if (view !== v || last) return;
      v.status.textContent = t("settings.unavailable", { error: t("settings.nothing") });
      v.status.classList.add("err");
    }, 3000);
  }
}

async function applyNow() {
  const waiting = [];
  for (const g of last?.groups || []) for (const it of shownItems(g)) if (it.pending) waiting.push(tx(it.label) || it.key);
  const yes = await openModal({
    title: t("settings.apply_title"),
    text: t("settings.apply_text"),
    items: waiting, ok: t("settings.apply"), danger: true,
  });
  if (!yes) return;
  const r = await command("settings_apply");
  if (r?.ok === false) toast(t("settings.not_applied", { error: tx(r.error) || t("common.error") }), "error");
}

// ------------------------------------------------------------------ envoi

async function send(key, value) {
  errors.delete(key);
  render();
  const r = await command("settings_set", { key, value });
  if (r?.setting_error) noteError(r.setting_error);
  if (r?.ok === false) {
    if (!r.setting_error) { errors.set(key, tx(r.error) || t("settings.refused")); render(); }
    refresh(); // resynchroniser : la valeur optimiste n'a pas été prise
  } else if (Array.isArray(r?.groups)) {
    last = { ...r, type: "settings" };
    syncTheme(last);
    if (!reloads(last)) render();
  }
}

// Valeur changée tout de suite à l'écran (deux clics rapides sur des cases d'un même
// réglage ne s'annulent pas : le second part de l'état déjà modifié), puis envoyée.
function change(key, value) {
  const it = item(key);
  if (!it || it.locked) return;
  it.value = value;
  send(key, value);
}

// 28/10 (hud-boutons-t10) : bouton exécuté par le HUD lui-même (`hud` dans l'action décrite par le cœur :
// « Lancer une mission », « Éteindre »). main.js branche la fonction, qui envoie SA commande habituelle
// (`mission start`, `quit`) avec sa propre confirmation : mêmes droits que les anciens boutons de la barre.
// La page Réglages se referme d'abord.
const hudActions = new Map();
export function onHudAction(name, fn) { hudActions.set(String(name), fn); }

// 28/10 (plan P40 lot A5) : lignes PROPRES au HUD ajoutées au bas d'une section (Réglages › Images :
// comptes et plafonds, dessinés par images.js d'après l'évènement `ia_etat`, pas d'après `settings`).
// `fn()` rend une liste d'éléments ; la page est redessinée par `rerenderSettings()` quand ils changent.
const groupExtras = new Map();
export function onGroupExtra(id, fn) { groupExtras.set(String(id), fn); }
export function rerenderSettings() { render(); }

// Bouton d'une ligne `info`/`action` : confirmation d'abord si demandée (mot à
// saisir pour une action destructrice), puis `settings_action`. Le résultat
// (texte, bloc copiable, lien) s'affiche sous la ligne et y reste.
async function act(key, a) {
  const tag = `${key}|${a.id}`;
  if (running.has(tag)) return;
  if (a.hud) {
    const fn = hudActions.get(String(a.hud));
    if (fn) {
      view?.panel.close(); // une seule boîte à l'écran ; le bandeau de mission se voit ensuite
      await fn();
    } else {
      results.set(key, { text: t("settings.action_refused"), level: "error" });
      render();
    }
    return;
  }
  let word = "";
  let input = "";
  const label = tx(a.label);
  if (a.word) {
    // 20/10 : le mot à taper est montré dans la langue de l'interface (« lever » / « lift ») ;
    // le cœur accepte les deux (settings.Reglages.action).
    const shown = tx(String(a.word));
    const r = await openModal({
      title: label, text: tx(a.confirm) || t("settings.irreversible"), danger: true, ok: label,
      fields: [{ name: "word", label: t("settings.type_word", { word: shown }), required: true }],
    });
    if (!r) return;
    const typed = r.word.trim().toLowerCase();
    if (typed !== String(a.word).toLowerCase() && typed !== shown.toLowerCase()) {
      results.set(key, { text: t("settings.word_missing", { word: shown }), level: "error" });
      render();
      return;
    }
    word = r.word.trim();
  } else if (a.input) {
    // Valeur demandée (numéro pour « Relier avec un code ») : envoyée sous `value`.
    // `secret` (clé API, plan P11) : saisie masquée, jamais affichée ni gardée ici.
    const r = await openModal({
      title: label, text: tx(a.confirm) || "", ok: label,
      fields: [{ name: "value", label: tx(String(a.input.label || "")) || t("settings.value"), required: true,
        ...(a.input.secret ? { type: "password" } : {}) }],
    });
    if (!r) return;
    input = r.value.trim();
  } else if (a.confirm || a.danger) {
    const yes = await openModal({ title: label, text: tx(a.confirm) || t("settings.confirm"), ok: label, danger: !!a.danger });
    if (!yes) return;
  }
  running.add(tag);
  results.delete(key);
  render();
  // 45 s : « Délier » attend la déconnexion côté téléphone (jusqu'à ~30 s).
  const r = await command("settings_action", { key, action: a.id, ...(word ? { confirm: word } : {}),
    ...(input ? { value: input } : {}) }, 10000, 45000);
  running.delete(tag);
  if (Array.isArray(r?.groups)) last = { ...r, type: "settings" };
  const text = r?.ok === false ? (tx(r.error) || t("settings.action_refused")) : tx(r?.text || "");
  // Fin d'appairage arrivée PENDANT l'action (`whatsapp_link` done/failed) : son message,
  // plus complet, n'est pas remplacé par l'accusé du bouton.
  const keep = results.get(key)?.fromLink && r?.ok !== false;
  if ((text || r?.block || r?.link) && !keep) {
    results.set(key, { text, level: r?.ok === false ? "error" : (r?.level || "ok"), block: r?.block || "", link: r?.link });
  }
  render();
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast(t("common.copied"));
  } catch {
    toast(t("common.copy_failed"), "warning");
  }
}

// ------------------------------------------------------------------ rendu

function render() {
  for (const m of [...mounts]) paintMount(m);
  if (!view) return;
  const { panel, status, banner, groups } = view;
  const list = Array.isArray(last?.groups) ? last.groups : null;
  banner.classList.toggle("hidden", !last?.restart_needed);
  if (!list) return;
  status.classList.add("hidden");
  // garder le défilement, le champ focalisé et la saisie en cours d'un nombre
  const scroll = panel.body.scrollTop;
  const active = document.activeElement;
  const fkey = groups.contains(active) ? active.dataset.fkey : null;
  const draft = fkey && active.tagName === "INPUT" && active.type === "number" ? active.value : null;
  groups.replaceChildren(...list.map(renderGroup));
  if (fkey) {
    const again = [...groups.querySelectorAll("[data-fkey]")].find((x) => x.dataset.fkey === fkey);
    if (again) {
      if (draft != null) again.value = draft;
      again.focus({ preventScroll: true });
    }
  }
  panel.body.scrollTop = scroll;
}

function renderGroup(g) {
  const d = el("details", "settings-group");
  d.dataset.group = g.id;
  // `open` du cœur (compte, premier lancement), sinon tout replié ; le choix de l'utilisateur prime.
  d.open = openGroups.has(g.id) ? openGroups.get(g.id) : g.open === true;
  // 19/10 : pastille d'icône, titre, puis chevron à droite (tourne quand la section est ouverte)
  const s = el("summary", "settings-group-title");
  const tile = el("span", "settings-group-ico");
  tile.append(icon(groupIcon(g.id)));
  s.append(tile, el("span", "settings-group-name", tx(g.title) || g.id));
  const waiting = shownItems(g).filter((it) => it.pending).length;
  if (waiting) s.append(el("span", "setting-pending", t("settings.n_pending", { n: waiting })));
  s.append(icon("chevron", "settings-group-chevron"));
  d.append(s);
  d.addEventListener("toggle", () => openGroups.set(g.id, d.open));
  // 19/10 : accordéon — ouvrir une section replie les autres (clic de l'utilisateur seulement)
  s.addEventListener("click", () => {
    if (d.open) return;
    for (const o of d.parentElement?.querySelectorAll("details.settings-group[open]") || []) if (o !== d) o.open = false;
  });
  for (const it of shownItems(g)) d.append(renderItem(it));
  const extra = groupExtras.get(String(g.id));
  if (extra) d.append(...extra());
  return d;
}

function renderItem(it, mounted = false) {
  const row = el("div", "setting");
  row.dataset.key = it.key;
  row.classList.toggle("locked", !!it.locked);
  row.classList.toggle("pending", !!it.pending);
  const head = el("div", "setting-head");
  const ico = itemIcon(it.key);
  row.classList.toggle("with-ico", !!ico);
  if (ico) head.append(icon(ico, "setting-ico"));
  const label = tx(it.label) || it.key;
  head.append(el("span", "setting-label", label));
  if (it.guide && guideHelp && !mounted) {
    // Aide par sujet (plan P5) : ouvre le guide sur le sujet du réglage.
    const q = el("button", "setting-guide");
    q.type = "button";
    q.append(icon("help"));
    q.setAttribute("aria-label", t("settings.help_aria", { label }));
    q.title = t("settings.help_title");
    q.dataset.fkey = `${it.key}|guide`;
    q.onclick = () => guideHelp(String(it.guide));
    head.append(q);
  }
  if (it.pending) head.append(el("span", "setting-pending", t("settings.pending")));
  const ctl = control(it);
  const panel = it.kind === "info" || it.kind === "action"; // l'aide AVANT les lignes et les boutons
  row.classList.toggle("wide", panel || it.kind === "flags" || it.kind === "tri");
  row.append(head);
  if (panel && it.help) row.append(el("div", "setting-help", tx(it.help)));
  row.append(ctl);
  if (!panel && it.help) row.append(el("div", "setting-help", tx(it.help)));
  if (results.has(it.key)) row.append(resultBox(results.get(it.key), it.key));
  if (it.locked) {
    const lock = el("div", "setting-lock");
    setLabel(lock, "lock", tx(it.lock_reason) || t("settings.locked"), "lead");
    row.append(lock);
  }
  const err = el("div", "setting-error");
  err.setAttribute("role", "alert");
  err.textContent = tx(errors.get(it.key) || "");
  err.classList.toggle("hidden", !errors.has(it.key));
  row.append(err);
  return row;
}

function control(it) {
  const off = !!it.locked;
  switch (it.kind) {
    case "bool": {
      const b = el("button", `chip toggle setting-switch${it.value ? " on" : ""}`);
      b.type = "button";
      b.setAttribute("role", "switch");
      b.setAttribute("aria-checked", it.value ? "true" : "false");
      b.setAttribute("aria-label", tx(it.label) || it.key);
      b.dataset.fkey = it.key;
      b.disabled = off;
      b.append(el("span", "sw"), el("b", "val", t(it.value ? "common.yes_caps" : "common.no_caps")));
      b.onclick = () => change(it.key, !item(it.key)?.value);
      return b;
    }
    case "int":
    case "float":
      return numberControl(it, off);
    case "text":
      return textControl(it, off); // 23/10 (plan P31) : nom affiché (`assistant_name`)
    case "choice": {
      // 21/10 (plan P17) : thème en cartes d'aperçu, accent en pastilles (`display` du cœur)
      if (it.display === "swatches") return accentSwatches(it, off);
      const sel = el("select", "setting-select");
      sel.dataset.fkey = it.key;
      sel.disabled = off;
      sel.setAttribute("aria-label", tx(it.label) || it.key);
      for (const c of it.choices || []) {
        const o = el("option", "", c.label != null ? tx(String(c.label)) : String(c.value));
        o.value = String(c.value);
        sel.append(o);
      }
      sel.value = String(it.value ?? "");
      sel.onchange = () => change(it.key, sel.value);
      return sel;
    }
    case "flags": {
      const box = el("div", "setting-flags");
      const value = it.value && typeof it.value === "object" ? it.value : {};
      for (const name of Object.keys(value)) {
        const lab = el("label", `setting-flag${value[name] ? " on" : ""}`);
        const cb = el("input");
        cb.type = "checkbox";
        cb.checked = !!value[name];
        cb.disabled = off;
        cb.dataset.fkey = `${it.key}|${name}`;
        cb.dataset.name = name;
        cb.onchange = () => change(it.key, { ...(item(it.key)?.value || {}), [name]: cb.checked });
        // 19/10 (plan P10) : icône locale du service (masque teint), sinon pastille à lettre.
        const ic = it.icons?.[name];
        const ico = el("span", `setting-flag-ico${ic?.src ? "" : " letter"}`, ic?.src ? null : (ic?.letter || ""));
        ico.setAttribute("aria-hidden", "true");
        if (ic?.color) ico.style.backgroundColor = ic.color;
        if (ic?.src) ico.style.setProperty("--ico", `url("${ic.src}")`);
        // 24/10 (P29 lot B) : libellé traduit (cases de confirmation) ; un nom propre reste tel quel.
        lab.append(cb, ...(ic ? [ico] : []), el("span", "", tx(it.labels?.[name]) || name));
        box.append(lab);
      }
      return box;
    }
    case "tri": {
      const box = el("div", "setting-tri");
      const value = it.value && typeof it.value === "object" ? it.value : {};
      let n = 0;
      for (const name of Object.keys(value)) {
        const line = el("div", "tri-line");
        line.dataset.name = name;
        line.append(el("span", "tri-name", it.labels?.[name] || name));
        const seg = el("div", "tri-seg");
        seg.setAttribute("role", "radiogroup");
        seg.setAttribute("aria-label", it.labels?.[name] || name);
        const group = `tri-${it.key}-${n++}`;
        for (const [state, label] of TRI) {
          const lab = el("label", `tri-opt s-${state}${value[name] === state ? " on" : ""}`);
          const r = el("input");
          r.type = "radio";
          r.name = group;
          r.value = state;
          r.checked = value[name] === state;
          r.disabled = off;
          r.dataset.fkey = `${it.key}|${name}|${state}`;
          r.onchange = () => { if (r.checked) change(it.key, { ...(item(it.key)?.value || {}), [name]: state }); };
          lab.append(r, el("span", "", t(label)));
          seg.append(lab);
        }
        line.append(seg);
        box.append(line);
      }
      return box;
    }
    case "info":
    case "action":
      return infoControl(it);
    default: // genre inconnu : la valeur en texte, sans contrôle
      return el("span", "setting-raw", typeof it.value === "object" ? JSON.stringify(it.value) : String(it.value ?? ""));
  }
}

// 21/10 (plan P17) : choix en rôle radio (pastilles d'accent ; les cartes du thème sont retirées
// le 25/10, plan P35). Flèches gauche et droite pour passer d'un choix à l'autre.
function radioGroup(it, off, make) {
  const box = el("div", "accent-swatches");
  box.setAttribute("role", "radiogroup");
  box.setAttribute("aria-label", tx(it.label) || it.key);
  const choices = it.choices || [];
  choices.forEach((c, i) => {
    const b = make(c);
    const on = String(c.value) === String(it.value ?? "");
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.tabIndex = on || (i === 0 && !choices.some((x) => String(x.value) === String(it.value))) ? 0 : -1;
    b.dataset.fkey = `${it.key}|${c.value}`;
    b.disabled = off;
    b.onclick = () => { if (String(item(it.key)?.value) !== String(c.value)) change(it.key, String(c.value)); };
    b.onkeydown = (e) => {
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!step) return;
      e.preventDefault();
      const next = choices[(i + step + choices.length) % choices.length];
      change(it.key, String(next.value));
      requestAnimationFrame(() => box.querySelector(`[data-fkey="${CSS.escape(`${it.key}|${next.value}`)}"]`)?.focus());
    };
    box.append(b);
  });
  return box;
}

function accentSwatches(it, off) {
  // couleur de chaque pastille dans le mode tel qu'il serait posé (même règle qu'apparence.js)
  const mode = resolveMode({ mode: String(item("theme_mode")?.value || "auto") });
  return radioGroup(it, off, (c) => {
    const b = el("button", "accent-swatch");
    const label = c.label != null ? tx(String(c.label)) : String(c.value);
    b.setAttribute("aria-label", label);
    b.title = label;
    b.style.setProperty("--sw", palette("premium", mode, String(c.value)).accent);
    return b;
  });
}

const LEVELS = ["ok", "info", "warn", "error"];

// Lien https seulement (markdown.js) ; sinon l'adresse reste du texte, copiable.
function linkTo(link) {
  const url = typeof link === "string" ? link : link?.url;
  const label = (typeof link === "object" && link?.label) || url || "";
  const href = safeHref(url);
  if (!href) return el("span", "setting-link", url ? `${tx(label)} : ${url}` : tx(label));
  const a = el("a", "setting-link", tx(label));
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.title = href;
  return a;
}

function copyBlock(text, fkey) {
  const wrap = el("div", "setting-block");
  const pre = el("pre", "", text);
  const b = el("button", "btn setting-copy", t("common.copy"));
  b.type = "button";
  b.dataset.fkey = fkey;
  b.onclick = () => copy(text);
  wrap.append(pre, b);
  return wrap;
}

// Lignes d'état {label, text, level}, bloc copiable, lien, puis les boutons.
function infoControl(it) {
  const box = el("div", "setting-info");
  const v = it.value && typeof it.value === "object" ? it.value : {};
  const lines = Array.isArray(v.lines) ? v.lines : [];
  if (lines.length) {
    const dl = el("dl", "info-lines");
    for (const l of lines) {
      const level = LEVELS.includes(l?.level) ? l.level : "info";
      const row = el("div", `info-line lv-${level}`);
      row.append(el("dt", "", tx(String(l?.label ?? ""))), el("dd", "", tx(String(l?.text ?? ""))));
      dl.append(row);
    }
    box.append(dl);
  }
  if (v.link) box.append(linkTo(v.link));
  if (v.block) box.append(copyBlock(String(v.block), `${it.key}|copier`));
  if (it.key === "whatsapp" && v.link_box) { box.append(linkBox.root); paintLink(); }
  const actions = Array.isArray(it.actions) ? it.actions : [];
  if (actions.length) {
    const bar = el("div", "setting-actions");
    for (const a of actions) {
      if (!a?.id) continue;
      const busy = running.has(`${it.key}|${a.id}`);
      const lab = tx(a.label) || a.id;
      const b = el("button", `btn setting-action${a.danger ? " danger" : ""}`, busy ? `${lab}…` : lab);
      b.type = "button";
      b.dataset.action = a.id;
      b.dataset.fkey = `${it.key}|act|${a.id}`;
      b.disabled = !!a.disabled || !!it.locked || busy;
      if (a.reason) b.title = tx(a.reason);
      b.onclick = () => act(it.key, a);
      bar.append(b);
      if (a.disabled && a.reason) bar.append(el("span", "setting-action-why", tx(a.reason)));
    }
    box.append(bar);
  }
  return box;
}

function resultBox(res, key) {
  const level = LEVELS.includes(res.level) ? res.level : "info";
  const box = el("div", `setting-result lv-${level}`);
  box.setAttribute("role", "status");
  if (res.text) box.append(el("div", "result-text", tx(res.text)));
  if (res.link) box.append(linkTo(res.link));
  if (res.block) box.append(copyBlock(String(res.block), `${key}|résultat`));
  return box;
}

function numberControl(it, off) {
  const wrap = el("div", "setting-number");
  const input = el("input");
  input.type = "number";
  for (const k of ["min", "max", "step"]) if (it[k] != null) input[k] = String(it[k]);
  input.inputMode = it.kind === "int" ? "numeric" : "decimal";
  input.value = it.value ?? "";
  input.disabled = off;
  input.dataset.fkey = it.key;
  input.setAttribute("aria-label", tx(it.label) || it.key);
  wrap.append(input);
  if (it.unit) wrap.append(el("span", "setting-unit", tx(it.unit)));
  const showError = (msg) => {
    errors.set(it.key, msg);
    const err = wrap.parentElement?.querySelector(".setting-error");
    if (err) { err.textContent = msg; err.classList.remove("hidden"); }
  };
  const commit = () => {
    const cur = item(it.key);
    if (!cur || cur.locked) return;
    const raw = input.value.trim();
    const v = Number(raw);
    const bounds = { min: cur.min ?? "…", max: cur.max ?? "…" };
    if (!raw || !Number.isFinite(v)) { showError(t("settings.number", bounds)); return; }
    if (cur.kind === "int" && !Number.isInteger(v)) { showError(t("settings.integer", bounds)); return; }
    if ((cur.min != null && v < cur.min) || (cur.max != null && v > cur.max)) { showError(t("settings.number", bounds)); return; }
    if (v === cur.value) return; // Entrée puis sortie du champ : un seul envoi
    change(it.key, v);
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); commit(); }
  });
  input.addEventListener("change", commit);
  input.addEventListener("input", () => {
    if (!errors.has(it.key)) return;
    errors.delete(it.key);
    const err = wrap.parentElement?.querySelector(".setting-error");
    if (err) err.classList.add("hidden");
  });
  return wrap;
}

// 23/10 (plan P31) : texte d'une ligne (nom affiché). Même vérification que le cœur (settings._text) :
// une ligne, sans chevrons, `max` caractères au plus ; vide = le nom par défaut. Le cœur revalide.
function textControl(it, off) {
  const wrap = el("div", "setting-number setting-text");
  const input = el("input");
  input.type = "text";
  if (it.max != null) input.maxLength = Number(it.max);
  input.value = String(it.value ?? "");
  input.placeholder = String(it.default ?? "");
  input.disabled = off;
  input.spellcheck = false;
  input.autocomplete = "off";
  input.dataset.fkey = it.key;
  input.setAttribute("aria-label", tx(it.label) || it.key);
  wrap.append(input);
  const showError = (msg) => {
    errors.set(it.key, msg);
    const err = wrap.parentElement?.querySelector(".setting-error");
    if (err) { err.textContent = msg; err.classList.remove("hidden"); }
  };
  const commit = () => {
    const cur = item(it.key);
    if (!cur || cur.locked) return;
    const v = input.value.trim().replace(/\s+/g, " ");
    if (/[<>\u0000-\u001f\u007f]/.test(v)) { showError(t("settings.text_chars")); return; }
    if (v === String(cur.value ?? "") || (!v && cur.value === cur.default)) return; // un seul envoi
    change(it.key, v);
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); commit(); }
  });
  input.addEventListener("change", commit);
  input.addEventListener("input", () => {
    if (!errors.has(it.key)) return;
    errors.delete(it.key);
    const err = wrap.parentElement?.querySelector(".setting-error");
    if (err) err.classList.add("hidden");
  });
  return wrap;
}

// ------------------------------------------------------------------ appairage WhatsApp (18/10, plan P5)
// Le QR et le code donnent accès au compte : reçus par `whatsapp_link` seulement (le
// cœur ne les journalise pas et ne les met pas dans `settings`), gardés en mémoire
// ici, effacés à la fin de l'appairage ; un QR de plus de 60 s n'est plus dessiné.

const QR_TTL_S = 60;
let link = null; // dernier `whatsapp_link` (starting, qr, code, linked, error)
let linkTimer = 0;
const linkBox = buildLinkBox();

function buildLinkBox() {
  const root = el("div", "wa-link");
  root.setAttribute("role", "group");
  root.setAttribute("aria-label", t("wa.aria"));
  const warn = el("p", "wa-link-warn");
  setLabel(warn, "lock", t("wa.warn"), "lead");
  const canvas = el("canvas", "wa-qr");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", t("wa.qr_aria"));
  const code = el("output", "wa-code");
  const text = el("p", "wa-link-text");
  text.setAttribute("role", "status");
  root.append(warn, canvas, code, text);
  return { root, warn, canvas, code, text };
}

function onLink(ev) {
  if (ev.state === "done" || ev.state === "failed") {
    link = null;
    results.set("whatsapp", ev.state === "done"
      ? { text: t("wa.linked", { number: ev.number ? ` (${ev.number})` : "" }), level: "ok", fromLink: true }
      : { text: t("wa.not_linked", { reason: tx(ev.reason) || t("wa.interrupted") }), level: "error", fromLink: true });
  } else {
    link = ev;
  }
  paintLink();
  render();
}

function drawQr(canvas, data) {
  const q = qrcode(0, "L");
  q.addData(data);
  q.make();
  const n = q.getModuleCount();
  const quiet = 4; // marge blanche exigée par les lecteurs
  const scale = Math.max(4, Math.floor(264 / (n + 2 * quiet)));
  const size = (n + 2 * quiet) * scale;
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff"; // toujours sombre sur clair, quel que soit le thème
  ctx.fillRect(0, 0, size, size);
  ctx.fillStyle = "#000";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
    if (q.isDark(r, c)) ctx.fillRect((c + quiet) * scale, (r + quiet) * scale, scale, scale);
  }
}

function clearQr(canvas) {
  canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
  canvas.width = 0;
  canvas.height = 0;
}

function paintLink() {
  const { root, warn, canvas, code, text } = linkBox;
  clearTimeout(linkTimer);
  const ev = link;
  const showQr = ev?.state === "qr" && typeof ev.qr === "string" && Date.now() / 1000 - (ev.ts || 0) <= QR_TTL_S;
  if (showQr) {
    try { drawQr(canvas, ev.qr); } catch { clearQr(canvas); }
    linkTimer = setTimeout(paintLink, Math.max(500, ((ev.ts || 0) + QR_TTL_S) * 1000 - Date.now()));
  } else {
    clearQr(canvas);
  }
  canvas.classList.toggle("hidden", !showQr);
  const theCode = ev?.state === "code" && typeof ev.code === "string" ? ev.code : "";
  code.textContent = theCode ? theCode.replace(/^(.{4})(.{4})$/, "$1-$2") : "";
  code.setAttribute("aria-label", theCode ? t("wa.code_aria", { code: theCode.split("").join(" ") }) : "");
  code.classList.toggle("hidden", !theCode);
  warn.classList.toggle("hidden", !showQr && !theCode);
  let msg = "";
  let level = "info";
  if (!ev) msg = "";
  else if (ev.state === "starting") msg = t(ev.method === "code" ? "wa.starting_code" : "wa.starting_qr");
  else if (ev.state === "qr") msg = showQr ? t("wa.qr", { n: ev.n || 1 }) : t("wa.qr_expired");
  else if (ev.state === "code") msg = t("wa.code");
  else if (ev.state === "linked") {
    msg = t("wa.detected", { number: ev.number || "?", name: ev.name ? ` (${ev.name})` : "" });
    level = "ok";
  } else if (ev.state === "error") { msg = t("wa.error", { reason: tx(ev.reason) || t("wa.unknown") }); level = "error"; }
  text.textContent = msg;
  text.className = `wa-link-text lv-${level}`;
  root.classList.toggle("hidden", !ev);
}
