// Mise à jour disponible (10/11/2026 ; dorsal : astrel/maj.py, astrel/nouveautes.py).
// Le cœur vérifie le dépôt une minute après le démarrage puis toutes les 24 h, et publie l'évènement
// COLLANT `maj_dispo` {disponible, behind, version_locale, version, majeure, notes:[{version, date, texte}],
// bloque, lancee}. Ici :
// - pastille « Mise à jour » en haut (cachée s'il n'y a rien), plus voyante pour une version MAJEURE ;
// - version majeure : la fenêtre des nouveautés s'ouvre d'elle-même, UNE fois par version et par
//   navigateur (localStorage `astrel-maj-vue`, simple confort : sans lui, elle se rouvre au chargement) ;
// - « Mettre à jour » = le bouton de Réglages › Mises à jour (`settings_action maj update`), même
//   confirmation. Rien ne s'installe sans ce clic.
// Notes = markdown SÛR (markdown.js : nœuds DOM, jamais d'innerHTML) ; le reste en textContent.

import { command, on } from "./conn.js";
import { openModal, openPanel } from "./modal.js";
import { renderMarkdown } from "./markdown.js";
import { t, tx } from "./i18n.js";

const STORE = "astrel-maj-vue";

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

let toast = () => {};
let etat = null;
let view = null;
let busy = false;

const vue = () => { try { return globalThis.localStorage?.getItem(STORE) || ""; } catch { return ""; } };
const marquerVue = (v) => { try { globalThis.localStorage?.setItem(STORE, v); } catch { /* confort seulement */ } };

export function initMaj(showToast) {
  toast = showToast || toast;
  const chip = document.querySelector("#maj-chip");
  if (chip) chip.onclick = () => open();
  on("maj_dispo", (ev) => {
    etat = ev || null;
    paintChip();
    if (view) render();
    if (etat?.disponible && etat.majeure && etat.version && vue() !== etat.version) {
      marquerVue(etat.version);
      open();
    }
  });
}

// Texte et infobulle de la pastille (exportés pour les tests).
export function chipText(ev) {
  return ev?.version ? t("maj.chip_version", { version: ev.version }) : t("maj.chip");
}

export function chipTitle(ev) {
  if (!ev?.disponible) return t("maj.chip_title");
  if (ev.version) return t(ev.majeure ? "maj.chip_major" : "maj.chip_minor", { version: ev.version });
  return t("maj.chip_commits", { n: Number(ev.behind) || 0 });
}

function paintChip() {
  const chip = document.querySelector("#maj-chip");
  if (!chip) return;
  const on_ = !!etat?.disponible;
  chip.classList.toggle("hidden", !on_);
  chip.classList.toggle("majeure", on_ && !!etat.majeure);
  chip.title = chipTitle(etat);
  chip.setAttribute("aria-label", chipTitle(etat));
  const label = chip.querySelector("span");
  if (label) label.textContent = chipText(etat);
}

function open() {
  if (view) { view.panel.box.focus(); return; }
  const panel = openPanel({ title: t("maj.title"), icon: "download", cls: "maj" });
  view = { panel };
  panel.closed.then(() => { if (view?.panel === panel) view = null; });
  render();
}

function render() {
  if (!view) return;
  const ev = etat || {};
  const body = view.panel.body;
  body.replaceChildren();
  const tete = el("div", `maj-tete${ev.majeure ? " majeure" : ""}`);
  if (ev.version) {
    tete.append(el("p", "maj-titre", t(ev.majeure ? "maj.head_major" : "maj.head", { version: ev.version })));
  } else if (ev.disponible) {
    tete.append(el("p", "maj-titre", t("maj.head_commits", { n: Number(ev.behind) || 0 })));
  } else {
    tete.append(el("p", "maj-titre", t("maj.up_to_date")));
  }
  if (ev.version_locale) tete.append(el("p", "maj-installee", t("maj.installed", { version: ev.version_locale })));
  body.append(tete);

  const notes = Array.isArray(ev.notes) ? ev.notes : [];
  if (notes.length) {
    const list = el("div", "maj-notes");
    for (const n of notes) {
      const sec = el("section", "maj-note");
      sec.append(el("h3", "maj-note-titre", n.date ? `${n.version} — ${n.date}` : String(n.version || "")));
      sec.append(renderMarkdown(String(n.texte || ""), document));
      list.append(sec);
    }
    body.append(list);
  } else if (ev.disponible) {
    body.append(el("p", "maj-vide", t(ev.version ? "maj.no_notes" : "maj.fixes_only")));
  }

  if (ev.lancee) body.append(el("p", "maj-info", t("maj.launched")));
  else if (ev.disponible && ev.bloque) body.append(el("p", "maj-bloque", tx(ev.bloque)));

  const actions = el("div", "modal-actions maj-actions");
  const later = el("button", "btn ghost", t("maj.later"));
  later.type = "button";
  later.onclick = () => view?.panel.close();
  actions.append(later);
  if (ev.disponible && !ev.lancee) {
    const go = el("button", "btn ok", t("maj.install"));
    go.type = "button";
    go.disabled = busy || !!ev.bloque;
    if (ev.bloque) go.title = tx(ev.bloque);
    go.onclick = () => installer();
    actions.append(go);
  }
  body.append(actions);
}

async function installer() {
  if (busy) return;
  const ok = await openModal({ title: t("maj.install"), text: t("maj.confirm"), ok: t("maj.install") });
  if (!ok) return;
  busy = true;
  render();
  const r = await command("settings_action", { key: "maj", action: "update" }, 10000, 30000);
  busy = false;
  if (r?.ok === false) toast(tx(r.error) || t("settings.action_refused"), "warning");
  else toast(tx(r?.text || "") || t("maj.launched"));
  render();
}
