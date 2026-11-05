// 05/11 (lot 6 du moteur local, docs/chantiers/HYBRIDE-QWEN.md) : deux commandes de l'utilisateur pour le moteur local.
// 1. Sélecteur « Qui répond » à côté du badge du modèle : le réglage `moteur_mode` (auto, local, claude, hybride),
//    lu dans l'évènement `settings`, changé par `settings_set` (refusé à Claude par le cœur). Réglage de
//    confidentialité : visible en Simple comme en Avancé. Hors ligne (« rien ne sort ») : forcé au local, grisé,
//    la raison au survol et au clic.
// 2. « Envoyer à Claude » sous une réponse locale : `local_vers_claude {mid}` (HUD seul) renvoie la DEMANDE
//    d'origine, que le cœur garde en mémoire (`local_demandes` : la liste de leurs mid, relue à chaque connexion).
//    Une demande sensible passe d'abord par la carte « routage » ; oubliée ou hors ligne : grisé, la raison écrite.
//    05/11 : cœur plus ancien que le HUD (« commande inconnue ») : grisé, « relance Astrel », pas « plus en mémoire ».
// Rien en innerHTML : nœuds et textContent seulement.

import { command, on } from "./conn.js";
import { icon, setLabel } from "./icons.js";
import { t, tx } from "./i18n.js";

const MODES = ["auto", "local", "claude", "hybride"];
const ICONS = { auto: "orbit", local: "shield", claude: "cloud", hybride: "layers" };
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

let toast = () => {};
const st = { mode: "", locked: false, lockReason: "", offSetting: false, offModel: false };
const offline = () => st.offSetting || st.offModel;

// ------------------------------------------------------------------ sélecteur « Qui répond »

function badgeEl() { return document.querySelector("#engine-badge"); }

function paintBadge() {
  const b = badgeEl();
  if (!b) return;
  const mode = MODES.includes(st.mode) ? st.mode : "";
  const off = offline();
  if (off) setLabel(b, "shield-check", t("engine.offline"), "lead");
  else setLabel(b, mode ? ICONS[mode] : "orbit", mode ? t(`engine.short.${mode}`) : "—", "lead");
  b.classList.toggle("off", off || st.locked);
  b.classList.toggle("is-local", off || mode === "local");
  b.setAttribute("aria-disabled", off || st.locked ? "true" : "false");
  const label = mode ? t(`engine.label.${mode}`) : "—";
  b.title = off ? t("engine.offline_title")
    : st.locked ? t("engine.locked_title", { mode: label, reason: tx(st.lockReason) || "ASTREL_MOTEUR_MODE" })
      : t("engine.title", { mode: label });
  b.setAttribute("aria-label", b.title);
}

function closeMenu(focusBack = false) {
  const m = document.querySelector(".menu.engine-menu");
  if (m) m.remove();
  const b = badgeEl();
  if (b) b.setAttribute("aria-expanded", "false");
  if (focusBack && b) b.focus();
}

async function choose(mode) {
  closeMenu(true);
  if (mode === st.mode) return;
  const r = await command("settings_set", { key: "moteur_mode", value: mode });
  if (r?.ok === false) { toast(t("engine.unchanged", { error: tx(r.error) || t("common.error") }), "error"); return; }
  readSettings(r); // la réponse porte les réglages à jour ; l'évènement `settings` suit aussi
  toast(t("engine.set", { mode: t(`engine.label.${mode}`) }));
}

function openMenu() {
  const b = badgeEl();
  if (offline()) { toast(t("engine.offline_title"), "warning", "shield-check"); return; }
  if (st.locked) { toast(b.title, "warning"); return; }
  if (document.querySelector(".menu.engine-menu")) { closeMenu(true); return; }
  document.querySelectorAll(".menu").forEach((m) => m.remove());
  const rect = b.getBoundingClientRect();
  const menu = el("div", "menu engine-menu");
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", t("engine.menu_title"));
  menu.append(el("div", "small muted menu-head", t("engine.menu_title")));
  const items = [];
  for (const mode of MODES) {
    const item = el("button", `item${st.mode === mode ? " active" : ""}`);
    item.type = "button";
    item.dataset.mode = mode;
    item.setAttribute("role", "menuitemradio");
    item.setAttribute("aria-checked", st.mode === mode ? "true" : "false");
    const head = el("span", "engine-item-head");
    head.append(icon(ICONS[mode], "lead"), document.createTextNode(t(`engine.label.${mode}`)));
    item.append(head, el("span", "small muted engine-item-desc", t(`engine.desc.${mode}`)));
    item.onclick = (e) => { e.stopPropagation(); choose(mode); };
    items.push(item);
    menu.append(item);
  }
  menu.append(el("div", "small muted engine-note", t("engine.menu_note")));
  // Clavier : flèches, Début/Fin, Échap (rend le focus au badge), Tab ferme.
  menu.addEventListener("keydown", (e) => {
    const i = items.indexOf(document.activeElement);
    let next = -1;
    if (e.key === "ArrowDown") next = (i + 1) % items.length;
    else if (e.key === "ArrowUp") next = (i - 1 + items.length) % items.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(true); return; }
    else if (e.key === "Tab") { closeMenu(false); return; }
    if (next >= 0) { e.preventDefault(); items[next].focus(); }
  });
  menu.style.top = `${rect.bottom + 6}px`;
  menu.style.right = `${innerWidth - rect.right}px`;
  document.body.append(menu);
  b.setAttribute("aria-expanded", "true");
  (items.find((x) => x.dataset.mode === st.mode) || items[0]).focus({ preventScroll: true });
  setTimeout(() => addEventListener("click", (e) => {
    if (!menu.contains(e.target)) closeMenu(false);
  }, { once: true }), 0);
}

function readSettings(ev) {
  if (!Array.isArray(ev?.groups)) return;
  for (const g of ev.groups) for (const it of g.items || []) {
    if (it.key === "moteur_mode") {
      st.mode = String(it.value || "auto");
      st.locked = !!it.locked;
      st.lockReason = String(it.lock_reason || "");
    } else if (it.key === "hors_ligne") st.offSetting = it.value === true;
  }
  paintBadge();
  paintAll();
}

// ------------------------------------------------------------------ « Envoyer à Claude » sous une réponse locale

const memo = new Set();     // mid des demandes locales que le cœur garde encore
const boxes = new Set();    // réponses locales qui portent le bouton : { m, req, madeOffline, state }
// 05/11 (essais réels de l'utilisateur) : un HUD rouvert prend le code du disque, le cœur garde celui de son démarrage.
// Cœur d'avant le lot 6 (« commande inconnue : local_demandes ») : tous les boutons disaient « plus en mémoire ».
let coreOld = false;
const unknownCmd = (r) => /^commande inconnue : local_/.test(String(r?.error || ""));
let refreshing = null;      // mid ajoutés pendant une relecture de `local_demandes` (pas effacés par sa réponse)

function paint(x) {
  const { btn, why } = x;
  let reason = "";
  if (x.state === "sent") reason = t("local.to_claude_sent");
  else if (x.state === "card") reason = t("local.to_claude_card");
  else if (x.madeOffline) reason = t("local.why_made_offline");
  else if (coreOld) reason = t("local.why_core_old");
  else if (offline()) reason = t("local.why_offline");
  else if (!memo.has(x.req)) reason = t("local.why_forgotten");
  const can = !reason && !x.busy;
  btn.disabled = !can;
  btn.classList.toggle("off", !!reason);
  btn.title = reason || t("local.to_claude_title");
  why.textContent = reason;
  why.hidden = !reason;
}

function paintAll() {
  for (const x of boxes) {
    if (!x.m.node.isConnected) { boxes.delete(x); continue; }
    paint(x);
  }
}

/** Réponse locale finie (ou rejouée) : bouton « Envoyer à Claude » sous la bulle. */
export function localReplyActions(m, ev) {
  if (!m || ev?.engine !== "local" || m.localBox || !ev.reply_to) return;
  const req = String(ev.reply_to);
  if (ev.vers_claude) {
    memo.add(req);
    refreshing?.add(req);
  }
  const box = el("div", "local-actions");
  const btn = el("button", "read-btn to-claude");
  btn.type = "button";
  setLabel(btn, "cloud", t("local.to_claude"), "lead");
  const why = el("span", "local-why");
  why.id = `local-why-${req}`;
  btn.setAttribute("aria-describedby", why.id);
  box.append(btn, why);
  m.node.append(box);
  const x = { m, req, btn, why, madeOffline: !!ev.hors_ligne, state: "", busy: false };
  m.localBox = x;
  boxes.add(x);
  btn.onclick = async (e) => {
    e.stopPropagation();
    if (btn.disabled) return;
    x.busy = true;
    paint(x);
    const r = await command("local_vers_claude", { mid: req });
    x.busy = false;
    if (unknownCmd(r)) { coreOld = true; paintAll(); return; }
    if (r?.ok === false) {
      if (r.raison === "oubliee") memo.delete(req);
      toast(t("local.to_claude_failed", { error: tx(r.error) || t("common.send_failed") }), "error");
    } else {
      memo.delete(req);
      x.state = r?.carte ? "card" : "sent";
    }
    paint(x);
  };
  paint(x);
}

async function refreshMemo() {
  const pendant = new Set();
  refreshing = pendant;
  const r = await command("local_demandes");
  if (refreshing === pendant) refreshing = null;
  coreOld = unknownCmd(r);
  memo.clear();
  if (r?.ok !== false && Array.isArray(r?.mids)) for (const mid of r.mids) memo.add(String(mid));
  for (const mid of pendant) memo.add(mid); // réponse arrivée pendant la relecture : sa liste ne l'avait pas encore
  if (typeof r?.hors_ligne === "boolean") st.offSetting = r.hors_ligne;
  paintBadge();
  paintAll();
}

export function initMoteur(toastFn) {
  toast = toastFn || toast;
  const b = badgeEl();
  if (b) b.onclick = (e) => { e.stopPropagation(); openMenu(); };
  on("settings", readSettings);
  on("model", (ev) => { st.offModel = !!ev.offline; paintBadge(); paintAll(); });
  on("hello", () => { refreshMemo(); });
  paintBadge();
}
