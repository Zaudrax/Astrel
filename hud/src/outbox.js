// File d'envoi (outbox) du HUD (09/11, carte T88 ; dorsal : astrel/outbox.py, astrel/coeur/outbox.py).
// Les messages SORTANTS qu'Astrel prépare (WhatsApp aujourd'hui, courriel plus tard) attendent ici avant
// de partir. Chaque brouillon montre le texte EXACT qui sera envoyé, puis quatre choix : Envoyer (envoi,
// action critique, l'utilisateur seul), Jeter, Éditer en ligne (change le texte) ou Réviser (consigne libre →
// le moteur réécrit le brouillon, aperçu avant envoi). RIEN ne part sans un « Envoyer » explicite.
//
// Commandes (refusées à Claude par le cœur) : outbox_get, outbox_accept {outbox_id}, outbox_refuse
// {outbox_id, raison}, outbox_edit {outbox_id, text}, outbox_revise {outbox_id, instruction}. Clé
// outbox_id (jamais id : le HUD l'écrase). Pastille : évènement collant `outbox {en_attente}`.
// Textes du serveur en textContent seulement, jamais d'innerHTML ni de markdown (le message part tel quel).

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

const STATES = { pending: "outbox.s_pending", revising: "outbox.s_revising", sent: "outbox.s_sent",
  refused: "outbox.s_refused" };

let toast = () => {};
let view = null; // fenêtre ouverte
let items = [];
let busy = false;
let error = "";

export function initOutbox(showToast) {
  toast = showToast || toast;
  const chip = document.querySelector("#outbox-chip");
  if (chip) chip.onclick = () => open();
  on("outbox", (ev) => {
    const n = Number(ev?.en_attente) || 0;
    const badge = document.querySelector("#outbox-badge");
    if (badge) { badge.textContent = String(n); badge.classList.toggle("hidden", !n); }
    if (chip) {
      chip.classList.toggle("hidden", !n);
      chip.title = n ? t("outbox.chip_to_review", { n }) : t("outbox.chip_title");
    }
    if (view && !busy) load();
  });
}

async function open() {
  if (view) { view.panel.box.focus(); return; }
  const panel = openPanel({ title: t("outbox.title"), icon: "message", cls: "outbox" });
  const intro = el("p", "outbox-intro", t("outbox.intro"));
  const status = el("p", "outbox-status", t("outbox.loading"));
  status.setAttribute("role", "status");
  const list = el("ol", "outbox-list");
  panel.body.append(intro, status, list);
  view = { panel, status, list };
  panel.closed.then(() => { if (view?.panel === panel) view = null; });
  await load();
}

async function load() {
  if (busy || !view) return;
  busy = true;
  error = "";
  render();
  const r = await command("outbox_get", {});
  busy = false;
  if (!view) return;
  if (Array.isArray(r?.brouillons)) items = r.brouillons;
  else error = tx(r?.error) || t("common.unknown_error");
  render();
}

function done(r, okMsg) {
  if (r?.ok) toast(okMsg, "info");
  else toast(tx(r?.error) || t("common.unknown_error"), "error");
  return load();
}

async function accept(d, btns) {
  btns.forEach((b) => { b.disabled = true; });
  await done(await command("outbox_accept", { outbox_id: d.id }), t("outbox.sent"));
}

async function refuse(d, btns) {
  const v = await openModal({
    title: t("outbox.refuse_title"), text: d.text, ok: t("outbox.refuse"), danger: true,
    fields: [{ name: "raison", label: t("outbox.refuse_reason"), multiline: true }],
  });
  if (!v) return;
  btns.forEach((b) => { b.disabled = true; });
  await done(await command("outbox_refuse", { outbox_id: d.id, raison: v.raison || "" }), t("outbox.refused"));
}

async function edit(d) {
  const v = await openModal({
    title: t("outbox.edit_title"), ok: t("common.save"),
    fields: [{ name: "text", label: t("outbox.edit_field"), value: d.text, multiline: true, rows: 6, required: true }],
  });
  if (!v) return;
  await done(await command("outbox_edit", { outbox_id: d.id, text: v.text }), t("outbox.edited"));
}

async function revise(d) {
  const v = await openModal({
    title: t("outbox.revise_title"), text: d.text, ok: t("outbox.revise"),
    fields: [{ name: "instruction", label: t("outbox.revise_field"),
      placeholder: t("outbox.revise_placeholder"), multiline: true, required: true }],
  });
  if (!v) return;
  await done(await command("outbox_revise", { outbox_id: d.id, instruction: v.instruction }), t("outbox.revise_asked"));
}

function entry(d) {
  const open = d.state === "pending" || d.state === "revising";
  const li = el("li", `outbox-item st-${String(d.state || "").replace(/[^a-z]/g, "")}${open ? "" : " done"}`);
  const head = el("div", "outbox-head");
  head.append(el("span", "outbox-channel", String(d.label || d.channel || "")));
  if (d.to) head.append(el("span", "outbox-to", t("outbox.to", { to: String(d.to) })));
  head.append(el("span", "outbox-state", t(STATES[d.state] || "outbox.s_pending")));
  if (Number(d.revisions) > 0) head.append(el("span", "outbox-rev", t("outbox.revised", { n: Number(d.revisions) })));
  li.append(head);
  const pre = el("pre", "outbox-text", String(d.text || ""));
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", t("outbox.text_aria"));
  li.append(pre);
  if (d.reason) li.append(el("div", "outbox-reason", String(d.reason)));
  if (d.state === "pending") {
    const actions = el("div", "outbox-actions");
    const send = el("button", "outbox-accept"); send.type = "button";
    setLabel(send, "check", t("outbox.accept"), "lead");
    const drop = el("button", "outbox-refuse"); drop.type = "button";
    setLabel(drop, "trash", t("outbox.refuse"), "lead");
    const pen = el("button", "outbox-edit"); pen.type = "button";
    setLabel(pen, "pen", t("outbox.edit"), "lead");
    const red = el("button", "outbox-revise"); red.type = "button";
    setLabel(red, "refresh", t("outbox.revise"), "lead");
    const btns = [send, drop, pen, red];
    send.onclick = () => accept(d, btns);
    drop.onclick = () => refuse(d, btns);
    pen.onclick = () => edit(d);
    red.onclick = () => revise(d);
    actions.append(send, drop, pen, red);
    li.append(actions);
  }
  return li;
}

function render() {
  if (!view) return;
  const { status, list } = view;
  const waiting = items.filter((d) => d.state === "pending" || d.state === "revising").length;
  status.classList.toggle("err", Boolean(error));
  status.textContent = error ? t("outbox.unreadable", { error })
    : busy ? t("outbox.loading")
      : waiting ? t("outbox.count", { n: waiting })
        : t("outbox.empty");
  list.replaceChildren(...items.map(entry));
}
