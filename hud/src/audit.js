// Page « Journal » du HUD (plan P3, lot F ; dorsal : astrel/audit.py). Le journal
// d'audit unifié : confirmations du garde-fou et qui les a tranchées, envois WhatsApp,
// plans validés, commandes de Claude refusées, actions passées en Autonome TOTAL,
// sous-agents lancés. Lecture seule.
//
// Commande : audit_get {kinds, before, limit} (refusée à Claude par le cœur), les
// plus récentes d'abord ; « Plus anciennes » repart de la dernière entrée affichée.
// Textes du serveur en textContent seulement, jamais d'innerHTML : ils peuvent
// citer une commande ou un message.

import { command } from "./conn.js";
import { openPanel } from "./modal.js";
import { setLabel } from "./icons.js";
import { locale, t, tx } from "./i18n.js";

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

const KINDS = [
  ["confirmation", "audit.k_confirmation"], ["refus", "audit.k_refus"], ["whatsapp", "audit.k_whatsapp"],
  ["plan", "audit.k_plan"], ["total", "audit.k_total"], ["agent", "audit.k_agent"], ["levee", "audit.k_levee"],  // 18/10 (plan P7)
  ["memoire", "audit.k_memoire"],  // 04/11 (T49) : propositions de mémoire appliquées ou refusées
];
const LABEL = Object.fromEntries(KINDS);
const PAGE = 60;
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString(locale(), {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");

let view = null; // fenêtre ouverte
let items = [];
const active = new Set(KINDS.map(([k]) => k));
let more = false;
let busy = false;
let error = "";

export function initAudit() {
  const chip = document.querySelector("#audit-chip");
  if (chip) chip.onclick = () => open();
}

async function open() {
  if (view) { view.panel.box.focus(); return; }
  const panel = openPanel({ title: t("audit.chip"), icon: "activity", cls: "audit" });
  const status = el("p", "audit-status", t("audit.loading"));
  status.setAttribute("role", "status");
  const filters = el("div", "audit-filters");
  filters.setAttribute("role", "group");
  filters.setAttribute("aria-label", t("audit.filters_aria"));
  const refreshBtn = el("button", "audit-refresh");
  refreshBtn.type = "button";
  setLabel(refreshBtn, "refresh", t("audit.refresh"), "lead");
  refreshBtn.onclick = () => load(false);
  const bar = el("div", "audit-bar");
  bar.append(filters, refreshBtn);
  const list = el("ol", "audit-list");
  const moreBtn = el("button", "audit-more", t("audit.more"));
  moreBtn.type = "button";
  moreBtn.onclick = () => load(true);
  panel.body.append(bar, status, list, moreBtn);
  view = { panel, status, filters, list, moreBtn };
  panel.closed.then(() => { if (view?.panel === panel) view = null; });
  renderFilters();
  await load(false);
}

async function load(older) {
  if (busy || !view) return;
  busy = true;
  error = "";
  render();
  const args = { kinds: [...active], limit: PAGE };
  if (older && items.length) args.before = items[items.length - 1].ts;
  const r = await command("audit_get", args);
  busy = false;
  if (!view) return;
  if (Array.isArray(r?.items)) {
    items = older ? items.concat(r.items) : r.items;
    more = Boolean(r.more);
  } else {
    error = tx(r?.error) || t("common.unknown_error");
  }
  render();
}

function renderFilters() {
  const box = view.filters;
  box.replaceChildren();
  for (const [kind, label] of KINDS) {
    const b = el("button", `audit-filter${active.has(kind) ? " on" : ""}`, t(label));
    b.type = "button";
    b.setAttribute("aria-pressed", active.has(kind) ? "true" : "false");
    b.onclick = () => {
      if (active.has(kind) && active.size === 1) return; // au moins un type affiché
      if (active.has(kind)) active.delete(kind); else active.add(kind);
      renderFilters();
      load(false);
    };
    box.append(b);
  }
}

function entry(e) {
  const li = el("li", `audit-item k-${String(e.kind || "").replace(/[^a-z]/g, "")}`);
  if (e.ok === false) li.classList.add("refused");
  const head = el("div", "audit-head");
  head.append(el("span", "audit-when", when(e.ts)), el("span", "audit-kind", LABEL[e.kind] ? t(LABEL[e.kind]) : String(e.kind || "?")));
  if (e.ok === true || e.ok === false) head.append(el("span", "audit-ok", t(e.ok ? "audit.allowed" : "audit.refused")));
  head.append(el("span", "audit-who", tx(String(e.who || ""))));
  li.append(head, el("div", "audit-what", tx(String(e.what || ""))));
  if (e.auth) li.append(el("div", "audit-auth", tx(String(e.auth))));
  return li;
}

function render() {
  if (!view) return;
  const { status, list, moreBtn } = view;
  status.classList.toggle("err", Boolean(error));
  status.textContent = error ? t("audit.unreadable", { error })
    : busy ? t("audit.loading")
      : items.length ? t("audit.count", { n: items.length })
        : t("audit.empty");
  list.replaceChildren(...items.map(entry));
  moreBtn.hidden = !more || Boolean(error);
  moreBtn.disabled = busy;
}
