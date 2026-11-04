// Page « Mémoire » du HUD (04/11, carte T49 ; dorsal : astrel/coeur/propositions.py, tools/vault_review.py).
// Les propositions de mémoire (notes que Claude propose d'ajouter ou de réécrire dans le coffre) attendaient
// sans écran depuis le 21/10. Chaque proposition montre le texte EXACT qui sera écrit, puis « Ajouter à la
// mémoire » (ou « Appliquer ») et « Refuser » (motif facultatif). L'empreinte reçue est renvoyée telle
// quelle : le cœur refuse une proposition réécrite entre l'affichage et le clic.
//
// Commandes (refusées à Claude par le cœur) : propositions_get {toutes}, proposition_appliquer
// {proposition_id, empreinte}, proposition_refuser {proposition_id, raison}. Pastille : évènement
// collant `propositions {en_attente}` ; le bouton n'apparaît que s'il y a des propositions en attente.
// Textes du serveur en textContent seulement (le YAML se lit tel quel, pas de markdown), jamais d'innerHTML.

import { command, on } from "./conn.js";
import { openModal, openPanel } from "./modal.js";
import { setLabel } from "./icons.js";
import { t, tx } from "./i18n.js";

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

const TYPES = { nouvelle: "prop.t_nouvelle", consolidation: "prop.t_consolidation", perimee: "prop.t_perimee",
  fusion: "prop.t_fusion" };

let toast = () => {};
let view = null; // fenêtre ouverte
let items = [];
let toutes = false;
let busy = false;
let error = "";

export function initPropositions(showToast) {
  toast = showToast || toast;
  const chip = document.querySelector("#propositions-chip");
  if (chip) chip.onclick = () => open();
  on("propositions", (ev) => {
    const n = Number(ev?.en_attente) || 0;
    const badge = document.querySelector("#propositions-badge");
    if (badge) { badge.textContent = String(n); badge.classList.toggle("hidden", !n); }
    if (chip) {
      chip.classList.toggle("hidden", !n);
      chip.title = n ? t("prop.chip_to_review", { n }) : t("prop.chip_title");
    }
    if (view && !busy) load();
  });
}

async function open() {
  if (view) { view.panel.box.focus(); return; }
  const panel = openPanel({ title: t("prop.title"), icon: "book", cls: "propositions" });
  const intro = el("p", "prop-intro", t("prop.intro"));
  const status = el("p", "prop-status", t("prop.loading"));
  status.setAttribute("role", "status");
  const filters = el("div", "prop-filters");
  filters.setAttribute("role", "group");
  filters.setAttribute("aria-label", t("prop.filters_aria"));
  const list = el("ol", "prop-list");
  panel.body.append(intro, filters, status, list);
  view = { panel, status, filters, list };
  panel.closed.then(() => { if (view?.panel === panel) view = null; });
  renderFilters();
  await load();
}

function renderFilters() {
  const box = view.filters;
  box.replaceChildren();
  for (const [all, label] of [[false, "prop.f_waiting"], [true, "prop.f_all"]]) {
    const b = el("button", `prop-filter${toutes === all ? " on" : ""}`, t(label));
    b.type = "button";
    b.setAttribute("aria-pressed", toutes === all ? "true" : "false");
    b.onclick = () => { if (toutes !== all) { toutes = all; renderFilters(); load(); } };
    box.append(b);
  }
}

async function load() {
  if (busy || !view) return;
  busy = true;
  error = "";
  render();
  const r = await command("propositions_get", { toutes });
  busy = false;
  if (!view) return;
  if (Array.isArray(r?.propositions)) items = r.propositions;
  else error = tx(r?.error) || t("common.unknown_error");
  render();
}

async function apply(p, btns) {
  btns.forEach((b) => { b.disabled = true; });
  const r = await command("proposition_appliquer", { proposition_id: p.proposition_id, empreinte: p.empreinte });
  if (r?.ok) toast(t("prop.applied", { note: p.note }), "info");
  else toast(tx(r?.error) || t("common.unknown_error"), "error");
  await load();
}

async function refuse(p, btns) {
  const v = await openModal({
    title: t("prop.refuse_title"), text: p.note, ok: t("prop.refuse"),
    fields: [{ name: "raison", label: t("prop.refuse_reason"), multiline: true }],
  });
  if (!v) return;
  btns.forEach((b) => { b.disabled = true; });
  const r = await command("proposition_refuser", { proposition_id: p.proposition_id, raison: v.raison || "" });
  if (r?.ok) toast(t("prop.refused", { note: p.note }), "info");
  else toast(tx(r?.error) || t("common.unknown_error"), "error");
  await load();
}

function entry(p) {
  const waiting = p.statut === "en attente";
  const li = el("li", `prop-item k-${String(p.type || "").replace(/[^a-z]/g, "")}${waiting ? "" : " done"}`);
  const head = el("div", "prop-head");
  head.append(el("span", "prop-type", TYPES[p.type] ? t(TYPES[p.type]) : String(p.type || "?")),
    el("span", "prop-note", String(p.note || "")), el("span", "prop-date", String(p.date || "")));
  li.append(head);
  if (p.renvois?.length) li.append(el("div", "prop-renvois", t("prop.renvois", { notes: p.renvois.join(", ") })));
  if (p.raison) li.append(el("div", "prop-raison", String(p.raison)));
  const pre = el("pre", "prop-texte", String(p.texte || ""));
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", t("prop.text_aria"));
  li.append(pre);
  if (waiting) {
    const actions = el("div", "prop-actions");
    const ok = el("button", "prop-apply");
    ok.type = "button";
    setLabel(ok, "check", t(p.type === "nouvelle" ? "prop.add" : "prop.apply"), "lead");
    const no = el("button", "prop-refuse");
    no.type = "button";
    setLabel(no, "x", t("prop.refuse"), "lead");
    const btns = [ok, no];
    ok.onclick = () => apply(p, btns);
    no.onclick = () => refuse(p, btns);
    actions.append(ok, no);
    li.append(actions);
  } else {
    const fait = p.traitee || {};
    li.append(el("div", "prop-done", t(p.statut === "appliquée" ? "prop.done_applied" : "prop.done_refused",
      { when: String(fait.le || ""), reason: String(fait.raison || "") })));
  }
  return li;
}

function render() {
  if (!view) return;
  const { status, list } = view;
  status.classList.toggle("err", Boolean(error));
  status.textContent = error ? t("prop.unreadable", { error })
    : busy ? t("prop.loading")
      : items.length ? t("prop.count", { n: items.length })
        : t(toutes ? "prop.empty_all" : "prop.empty");
  list.replaceChildren(...items.map(entry));
}
