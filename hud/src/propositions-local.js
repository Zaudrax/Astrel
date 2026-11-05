// Panneau « Propositions de Qwen » du HUD (05/11, lot 5 du moteur local ; dorsal : astrel/propositions_local.py,
// astrel/coeur/propositions_local.py). Le modèle local ne peut pas écrire un fichier : il PROPOSE. Chaque
// proposition montre le diff calculé par le cœur contre le fichier ACTUEL, puis « Appliquer » (l'utilisateur seul,
// depuis ce HUD : le cœur rejuge l'empreinte du fichier, le périmètre, le garde-fou et prend un instantané)
// et « Refuser » (motif facultatif). Même mise en page que la page « Mémoire » (propositions.js, T49).
//
// Commandes (refusées à Claude par le cœur) : propositions_local_get {toutes}, proposition_local_appliquer
// {proposition_id, empreinte} (HUD seulement), proposition_local_refuser {proposition_id, raison}. Pastille :
// évènement collant `propositions_local {en_attente}`. Tout en textContent, jamais d'innerHTML.

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

const ETATS = { identique: "qprop.f_same", modifié: "qprop.f_changed", absent: "qprop.f_missing",
  illisible: "qprop.f_unreadable" };

let toast = () => {};
let view = null;
let items = [];
let toutes = false;
let busy = false;
let error = "";

export function initPropositionsLocal(showToast) {
  toast = showToast || toast;
  const chip = document.querySelector("#qprop-chip");
  if (chip) chip.onclick = () => open();
  on("propositions_local", (ev) => {
    const n = Number(ev?.en_attente) || 0;
    const badge = document.querySelector("#qprop-badge");
    if (badge) { badge.textContent = String(n); badge.classList.toggle("hidden", !n); }
    if (chip) {
      chip.classList.toggle("hidden", !n);
      chip.title = n ? t("qprop.chip_to_review", { n }) : t("qprop.chip_title");
    }
    if (view && !busy) load();
  });
}

async function open() {
  if (view) { view.panel.box.focus(); return; }
  const panel = openPanel({ title: t("qprop.title"), icon: "shield", cls: "propositions" });
  const intro = el("p", "prop-intro", t("qprop.intro"));
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
  const r = await command("propositions_local_get", { toutes });
  busy = false;
  if (!view) return;
  if (Array.isArray(r?.propositions)) items = r.propositions;
  else error = tx(r?.error) || t("common.unknown_error");
  render();
}

const nom = (p) => String(p.fichier || "").split(/[\\/]/).pop() || "?";

async function apply(p, btns) {
  btns.forEach((b) => { b.disabled = true; });
  const r = await command("proposition_local_appliquer", { proposition_id: p.proposition_id, empreinte: p.empreinte });
  if (r?.ok) toast(t("qprop.applied", { file: nom(p) }), "info");
  else toast(tx(r?.error) || t("common.unknown_error"), "error");
  await load();
}

async function refuse(p, btns) {
  const v = await openModal({
    title: t("prop.refuse_title"), text: String(p.fichier || ""), ok: t("prop.refuse"),
    fields: [{ name: "raison", label: t("prop.refuse_reason"), multiline: true }],
  });
  if (!v) return;
  btns.forEach((b) => { b.disabled = true; });
  const r = await command("proposition_local_refuser", { proposition_id: p.proposition_id, raison: v.raison || "" });
  if (r?.ok) toast(t("qprop.refused", { file: nom(p) }), "info");
  else toast(tx(r?.error) || t("common.unknown_error"), "error");
  await load();
}

// Diff unifié : une ligne par <span>, classée par son premier caractère (ajout, retrait, en-tête de bloc).
function diffBlock(lines) {
  const pre = el("pre", "prop-texte qprop-diff");
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", t("qprop.diff_aria"));
  for (const raw of Array.isArray(lines) ? lines : []) {
    const line = String(raw);
    const cls = line.startsWith("+++") || line.startsWith("---") ? "qd-file"
      : line.startsWith("@@") ? "qd-hunk" : line.startsWith("+") ? "qd-add" : line.startsWith("-") ? "qd-del" : "qd-ctx";
    pre.append(el("span", cls, `${line}\n`));
  }
  return pre;
}

function entry(p) {
  const waiting = p.statut === "en attente";
  const changed = waiting && p.etat_fichier !== "identique";
  const li = el("li", `prop-item k-qwen${waiting ? "" : " done"}`);
  const head = el("div", "prop-head");
  head.append(el("span", "prop-type", t(p.nouveau ? "qprop.t_new" : "qprop.t_change")),
    el("span", "prop-note", String(p.fichier || "")), el("span", "prop-date", String(p.date || "")));
  li.append(head);
  li.append(el("div", "prop-renvois", t("qprop.lines", { plus: Number(p.plus) || 0, minus: Number(p.moins) || 0 })));
  if (p.raison) li.append(el("div", "prop-raison", String(p.raison)));
  if (changed) {
    li.append(el("div", "prop-raison qprop-warn", t(ETATS[p.etat_fichier] || "qprop.f_changed")));
  }
  li.append(diffBlock(p.diff));
  if (waiting) {
    const actions = el("div", "prop-actions");
    const ok = el("button", "prop-apply");
    ok.type = "button";
    setLabel(ok, "check", t("prop.apply"), "lead");
    ok.disabled = changed;
    if (changed) ok.title = t("qprop.apply_blocked");
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
    li.append(el("div", "prop-done", t(p.statut === "appliquée" ? "qprop.done_applied" : "prop.done_refused",
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
