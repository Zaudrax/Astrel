// Page « Plans » du HUD (15/10, demande de l'utilisateur ; contrat :
// docs/CONTRAT-PLANS-2026-10-15.md, dorsal : astrel/plans.py). Pour un gros chantier,
// Astrel dépose un plan ; l'utilisateur le relit ici point par point, le commente, en
// demande une révision, puis le valide : SEULEMENT alors le codeur part.
//
// Évènements : `plans` (sticky : {mode, pending, items}) et `plan` ({new?, plan}).
// Commandes : plans_get, plan_get, plan_comment, plan_revise, plan_validate,
// plan_reject, toujours avec `plan_id` (« id » est écrasé par conn.js), et
// settings_set {key: "plan_mode"} pour le sélecteur Manuel/Auto.
// Textes du serveur en textContent ou par markdown.js, jamais d'innerHTML. Rien
// n'est dit à voix haute : un nouveau plan n'est signalé que par un toast.

import { command, on } from "./conn.js";
import { openModal, openPanel } from "./modal.js";
import { icon, setLabel, stripEmoji } from "./icons.js";
import { renderMarkdown } from "./markdown.js";
import { keepOnReplace } from "./remplacement.js";
import { assistantName, locale, t, tx } from "./i18n.js";

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

function button(cls, ico, text, fkey) {
  const b = el("button", cls);
  b.type = "button";
  if (ico) setLabel(b, ico, text, "lead"); else b.textContent = text;
  if (fkey) b.dataset.fkey = fkey;
  return b;
}

const FILTERS = [["open", "plans.f_open"], ["validated", "plans.f_validated"], ["rejected", "plans.f_rejected"]];
const STATUS = ["pending", "revising", "validated", "rejected"];
const statusLabel = (s) => (STATUS.includes(s) ? t(`plans.s_${s}`) : s);
const MODES = [
  ["manual", "plans.mode_manual", "plans.mode_manual_title"],
  ["auto", "plans.mode_auto", "plans.mode_auto_title"],
];
const groupOf = (status) => (status === "validated" || status === "rejected" ? status : "open");
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString(locale(), {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "");

let last = null; // dernier évènement `plans`
let listError = ""; // plans_get refusé (la liste connue reste affichée)
const full = new Map(); // id → plan complet (évènement `plan` ou réponse de plan_get)
const loading = new Set(); // plan_get en cours
const planErrors = new Map(); // id → message (plan_get refusé)
let view = null; // fenêtre ouverte
let toast = () => {};
let filter = "open";
let selected = "";
const drafts = new Map(); // `${id}|${point}` → saisie en cours (gardée d'un rendu et d'une ouverture à l'autre)
// 19/10 : passées au nouveau HUD quand `./hud.sh` ferme celui-ci (remplacement.js) ;
// une saisie déjà commencée dans le nouveau n'est pas écrasée.
keepOnReplace("plans", {
  save: () => Object.fromEntries([...drafts].filter(([, v]) => typeof v === "string" && v.trim())),
  restore: (saved) => {
    for (const [key, text] of Object.entries(saved || {})) {
      if (typeof text === "string" && text.trim() && !(drafts.get(key) || "").trim()) drafts.set(key, text);
    }
    render();
  },
});
const editing = new Set(); // éditeurs de remarque ouverts
const errors = new Map(); // `${id}|${point}` ou `${id}|action` → message
const busy = new Set(); // commandes en vol (pas de double clic)
let seen = null; // id → version vue (nouvelle version signalée) ; null avant le premier `plans`
let composing = false; // composition IME en cours : le rendu attend
let deferred = false;
let focusNext = null; // data-fkey à focaliser après le prochain rendu

export function initPlans(toastFn) {
  toast = toastFn || toast;
  document.querySelector("#plans-chip").onclick = () => open();
  on("plans", (ev) => {
    last = ev;
    listError = "";
    noteVersions(ev);
    updateChip();
    staleCheck();
    render();
  });
  on("plan", (ev) => {
    const p = ev.plan;
    if (!p || typeof p !== "object" || !p.id) return;
    full.set(p.id, p);
    planErrors.delete(p.id);
    if (ev.new && !ev.replay) notifyNew(p);
    render();
  });
}

// ------------------------------------------------------------------ pastille, notifications

function updateChip() {
  const chip = document.querySelector("#plans-chip");
  const badge = document.querySelector("#plans-badge");
  const n = Number(last?.pending) || 0;
  badge.textContent = String(n);
  badge.classList.toggle("hidden", !n);
  chip.classList.toggle("attn", n > 0);
  chip.title = n ? t("plans.chip_to_review", { n }) : t("plans.chip_title");
}

function notifyNew(p) {
  const auto = last?.mode === "auto";
  const n = toast(t(auto ? "plans.new_auto" : "plans.new", { id: p.id, title: stripEmoji(p.title || "") }), "info", "plans");
  if (n) { n.classList.add("clickable"); n.onclick = () => open(p.id); }
}

// Une nouvelle version d'un plan déjà connu (après une révision) : signalée comme un nouveau plan.
function noteVersions(ev) {
  const items = Array.isArray(ev.items) ? ev.items : [];
  const first = seen === null;
  const before = seen || new Map();
  seen = new Map(items.map((i) => [i.id, i.version]));
  if (first || ev.replay) return;
  for (const i of items) {
    if (before.has(i.id) && i.version > before.get(i.id) && i.status === "pending") {
      const n = toast(t("plans.new_version", { id: i.id, version: i.version }), "info", "plans");
      if (n) { n.classList.add("clickable"); n.onclick = () => open(i.id); }
    }
  }
}

// ------------------------------------------------------------------ fenêtre

const summary = (id) => (last?.items || []).find((i) => i.id === id) || null;

async function open(pid) {
  if (pid) {
    selected = pid;
    filter = groupOf(summary(pid)?.status || full.get(pid)?.status);
  }
  if (view) { view.panel.box.focus(); render(); return; }
  const panel = openPanel({ title: t("plans.chip"), icon: "plans", cls: "plans" });
  const head = panel.box.querySelector(".modal-head");
  const mode = el("div", "plans-mode");
  mode.setAttribute("role", "radiogroup");
  mode.setAttribute("aria-label", t("plans.mode_aria"));
  head.insertBefore(mode, head.querySelector(".modal-x"));
  const status = el("p", "plans-status", t("plans.loading"));
  status.setAttribute("role", "status");
  const side = el("div", "plans-side");
  const filters = el("div", "plans-filters");
  filters.setAttribute("role", "tablist");
  filters.setAttribute("aria-label", t("plans.filters_aria"));
  const list = el("div", "plans-list");
  side.append(filters, list);
  const main = el("div", "plans-main");
  const scroll = el("div", "plans-detail");
  const actions = el("div", "plans-actions hidden");
  main.append(scroll, actions);
  panel.body.append(status, side, main);
  view = { panel, mode, status, filters, list, scroll, actions, shown: "" };
  panel.closed.then(() => { if (view?.panel === panel) view = null; });
  render(); // l'état connu tout de suite (évènement sticky), le cœur le rafraîchit
  await refresh();
}

async function refresh() {
  const r = await command("plans_get");
  if (!view) return;
  if (Array.isArray(r?.items)) {
    last = { ...r, type: "plans" };
    listError = "";
    updateChip();
  } else if (r?.ok === false) {
    listError = tx(r.error) || t("common.unknown_error");
  } else if (!last) {
    // l'accusé peut précéder l'évènement `plans` : on patiente un peu
    const v = view;
    setTimeout(() => {
      if (view !== v || last) return;
      listError = t("plans.nothing");
      render();
    }, 3000);
  }
  render();
}

async function loadPlan(pid) {
  if (!pid || loading.has(pid)) return;
  loading.add(pid);
  planErrors.delete(pid);
  const r = await command("plan_get", { plan_id: pid });
  loading.delete(pid);
  if (r?.plan?.id) full.set(r.plan.id, r.plan);
  else if (r?.ok === false) planErrors.set(pid, tx(r.error) || t("common.unknown_error"));
  render();
}

// Le plan affiché a changé sans qu'on ait reçu son détail (reconnexion) : on le redemande.
function staleCheck() {
  if (!view || !selected) return;
  const s = summary(selected);
  const p = full.get(selected);
  if (s && p && (s.updated !== p.updated || s.version !== p.version || s.status !== p.status)) loadPlan(selected);
}

// ------------------------------------------------------------------ actions

const remarksOf = (p) => (p.thread || []).filter((t) => t.who === "utilisateur" && !t.sent).length;

async function sendComment(p, point, ask) {
  const key = `${p.id}|${point}`;
  const text = (drafts.get(key) || "").trim();
  if (!text) { errors.set(key, t("plans.write_first")); render(); return; }
  if (busy.has(key)) return;
  busy.add(key);
  errors.delete(key);
  render();
  const r = await command("plan_comment", { plan_id: p.id, point, text, ask });
  busy.delete(key);
  if (r?.ok === false) {
    errors.set(key, tx(r.error) || t("plans.remark_refused"));
  } else {
    drafts.delete(key);
    editing.delete(key);
    focusNext = `comment|${key}`;
    if (ask) toast(t("plans.question_sent"), "info", "help");
  }
  render();
}

async function validate(p) {
  const version = p.version; // la version AFFICHÉE : le cœur refuse si elle est périmée
  const n = remarksOf(p);
  const yes = await openModal({
    title: t("plans.validate_title", { id: p.id }),
    text: t("plans.validate_text", { version, title: stripEmoji(p.title || "") }),
    items: n ? [t("plans.validate_remarks", { n })] : [],
    ok: t("plans.validate_ok"),
  });
  if (!yes) return;
  await act(p, "plan_validate", { version }, () => {
    toast(t("plans.validated", { id: p.id }), "info", "check");
    filter = "validated";
  });
}

async function revise(p) {
  const n = remarksOf(p);
  await act(p, "plan_revise", {}, () => {
    toast(t("plans.revise_asked", { id: p.id, remarks: t("plans.remarks", { n }) }), "info", "refresh");
  });
}

async function reject(p) {
  const values = await openModal({
    title: t("plans.reject_title", { id: p.id }),
    text: t("plans.reject_text", { title: stripEmoji(p.title || "") }),
    fields: [{ name: "reason", label: t("plans.reason"), multiline: true, rows: 3, placeholder: t("plans.reason_ph"),
      hint: t("plans.reason_hint") }],
    ok: t("plans.reject_ok"), danger: true,
  });
  if (!values) return;
  await act(p, "plan_reject", { reason: values.reason }, () => {
    toast(t("plans.rejected", { id: p.id }), "info", "x");
    filter = "rejected";
  });
}

async function act(p, cmd, args, done) {
  const key = `${p.id}|action`;
  if (busy.has(key)) return;
  busy.add(key);
  errors.delete(key);
  render();
  const r = await command(cmd, { ...args, plan_id: p.id });
  busy.delete(key);
  if (r?.ok === false) {
    const msg = cmd === "plan_validate" && Number.isInteger(r.version)
      ? t("plans.stale", { version: r.version })
      : tx(r.error) || t("plans.refused");
    errors.set(key, msg);
    toast(msg, "warning");
    loadPlan(p.id); // l'état du cœur fait foi
  } else {
    done();
  }
  render();
}

async function setMode(value) {
  if (!last || last.mode === value) return;
  const before = last.mode;
  last.mode = value; // tout de suite à l'écran ; le cœur republie `plans`
  render();
  const r = await command("settings_set", { key: "plan_mode", value });
  if (r?.ok === false) {
    if (last.mode === value) last.mode = before;
    toast(t("plans.mode_unchanged", { error: tx(r.error) || t("common.refused_short") }), "error");
    render();
  }
}

// ------------------------------------------------------------------ rendu

function render() {
  if (!view) return;
  if (composing) { deferred = true; return; }
  deferred = false;
  const { panel, status, list, scroll } = view;
  // garder défilements, champ focalisé, sélection dans la saisie en cours
  const active = document.activeElement;
  const inside = panel.box.contains(active);
  const fkey = inside ? active.dataset?.fkey : null;
  const range = fkey && active.tagName === "TEXTAREA" ? [active.selectionStart, active.selectionEnd] : null;
  const listTop = list.scrollTop;
  const mainTop = scroll.scrollTop;

  const items = Array.isArray(last?.items) ? last.items : null;
  status.classList.toggle("err", !!listError);
  status.textContent = listError ? t("plans.unavailable", { error: listError }) : items ? "" : t("plans.loading");
  status.classList.toggle("hidden", !status.textContent);
  renderMode();
  if (items) {
    const shown = items.filter((i) => groupOf(i.status) === filter);
    if (!selected || !items.some((i) => i.id === selected)) selected = shown[0]?.id || "";
    renderFilters(items);
    renderList(shown);
  }
  renderDetail();

  list.scrollTop = listTop;
  scroll.scrollTop = view.shown === selected ? mainTop : 0;
  view.shown = selected;
  const want = focusNext || fkey;
  focusNext = null;
  if (want) {
    const again = [...panel.box.querySelectorAll("[data-fkey]")].find((x) => x.dataset.fkey === want);
    if (again && again !== document.activeElement) {
      again.focus({ preventScroll: true });
      if (range && again.tagName === "TEXTAREA") again.setSelectionRange(range[0], range[1]);
    }
  }
  // l'élément focalisé a disparu (ou est désactivé) : le focus reste dans la fenêtre
  if ((inside || want) && !panel.box.contains(document.activeElement)) panel.box.focus({ preventScroll: true });
}

function renderMode() {
  const mode = last?.mode;
  view.mode.replaceChildren(...MODES.map(([value, label, help]) => {
    const b = el("button", `plans-mode-opt m-${value}${mode === value ? " on" : ""}`, t(label));
    b.type = "button";
    b.title = t(help);
    b.dataset.fkey = `mode|${value}`;
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", mode === value ? "true" : "false");
    b.disabled = !last;
    b.onclick = () => setMode(value);
    return b;
  }));
}

function renderFilters(items) {
  view.filters.replaceChildren(...FILTERS.map(([value, label]) => {
    const n = items.filter((i) => groupOf(i.status) === value).length;
    const b = el("button", `plans-filter${filter === value ? " on" : ""}`, t(label));
    b.type = "button";
    b.dataset.fkey = `filter|${value}`;
    b.dataset.filter = value;
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", filter === value ? "true" : "false");
    b.append(el("span", "plans-filter-n", String(n)));
    b.onclick = () => {
      if (filter === value) return;
      filter = value;
      selected = items.find((i) => groupOf(i.status) === value)?.id || "";
      render();
    };
    return b;
  }));
}

function renderList(shown) {
  if (!shown.length) {
    view.list.replaceChildren(el("p", "plans-empty", t(`plans.empty_${filter}`)));
    return;
  }
  view.list.replaceChildren(...shown.map((i) => {
    const b = el("button", `plan-item s-${i.status}${i.id === selected ? " on" : ""}`);
    b.type = "button";
    b.dataset.fkey = `item|${i.id}`;
    b.dataset.plan = i.id;
    b.setAttribute("aria-current", i.id === selected ? "true" : "false");
    const top = el("div", "plan-item-top");
    top.append(el("b", "plan-item-id", i.id), el("span", `plan-pill s-${i.status}`, statusLabel(i.status)),
      el("span", "plan-item-date", when(i.updated)));
    const meta = [`v${i.version}`, t("plans.points", { n: i.points })];
    if (i.remarks) meta.push(t("plans.remarks", { n: i.remarks }));
    if (i.status === "validated") meta.push(t(i.by === "auto" ? "plans.validated_auto" : "plans.validated_you"));
    b.append(top, el("div", "plan-item-title", stripEmoji(i.title || "")), el("div", "plan-item-meta", meta.join(" · ")));
    b.onclick = () => { selected = i.id; render(); };
    return b;
  }));
}

function renderDetail() {
  const { scroll, actions } = view;
  if (!selected) {
    const hint = !last ? "" : t(view.list.querySelector(".plan-item") ? "plans.choose" : "plans.intro");
    scroll.replaceChildren(el("p", "plans-empty", hint));
    actions.classList.add("hidden");
    return;
  }
  const p = full.get(selected);
  if (!p) {
    if (planErrors.has(selected)) {
      const box = el("div", "plans-load-error");
      box.setAttribute("role", "alert");
      const retry = button("btn plans-retry", "refresh", t("common.retry"), `retry|${selected}`);
      const pid = selected;
      retry.onclick = () => loadPlan(pid);
      box.append(el("p", "", t("plans.plan_unavailable", { id: selected, error: planErrors.get(selected) })), retry);
      scroll.replaceChildren(box);
    } else {
      scroll.replaceChildren(el("p", "plans-status", t("plans.plan_loading", { id: selected })));
      loadPlan(selected);
    }
    actions.classList.add("hidden");
    return;
  }
  const open = p.status === "pending" || p.status === "revising";
  const doc = el("article", "plan-doc");
  doc.dataset.plan = p.id;
  doc.append(planHead(p));
  for (const s of p.sections || []) doc.append(section(p, s, open));
  doc.append(threadBlock(p, open));
  scroll.replaceChildren(doc);
  renderActions(p, open);
}

function planHead(p) {
  const head = el("header", "plan-head");
  const top = el("div", "plan-head-top");
  top.append(el("h2", "plan-title", stripEmoji(p.title || p.id)), el("span", `plan-pill s-${p.status}`, statusLabel(p.status)));
  const meta = [`${p.id}`, t("plans.version", { version: p.version }),
    t("plans.points", { n: (p.sections || []).reduce((n, s) => n + (s.points || []).length, 0) }),
    t("plans.created", { when: when(p.created) })];
  if (p.updated && p.updated !== p.created) meta.push(t("plans.updated", { when: when(p.updated) }));
  if (p.channel) meta.push(t("plans.channel", { channel: p.channel }));
  head.append(top, el("div", "plan-meta", meta.join(" · ")));
  if (p.request) {
    const req = el("div", "plan-request");
    req.append(el("span", "plan-label", t("plans.request")), el("p", "plan-request-text", stripEmoji(p.request)));
    head.append(req);
  }
  const by = p.validated_by || p.by;
  let note = "";
  if (p.status === "revising") note = t("plans.note_revising", { version: p.version + 1 });
  else if (p.status === "validated") note = t(by === "auto" ? "plans.note_auto" : "plans.note_you");
  else if (p.status === "rejected") note = p.reject_reason ? t("plans.note_rejected", { reason: p.reject_reason }) : t("plans.note_rejected_none");
  if (note) head.append(el("p", `plan-note s-${p.status}`, stripEmoji(note)));
  return head;
}

function section(p, s, open) {
  const box = el("section", "plan-section");
  if (s.title) box.append(el("h3", "plan-section-title", stripEmoji(s.title)));
  if (s.intro) {
    const intro = el("div", "md plan-intro");
    intro.append(renderMarkdown(s.intro));
    box.append(intro);
  }
  const list = el("ol", "plan-points");
  for (const pt of s.points || []) list.append(point(p, pt, open));
  if ((s.points || []).length) box.append(list);
  return box;
}

function point(p, pt, open) {
  const key = `${p.id}|${pt.id}`;
  const li = el("li", "plan-point");
  li.dataset.point = pt.id;
  const row = el("div", "plan-point-row");
  const text = el("div", "md plan-point-text");
  text.append(renderMarkdown(pt.text));
  row.append(el("span", "plan-point-id", pt.id), text);
  li.append(row);
  // remarques et réponses posées sur CE point de CETTE version (les autres vont au fil général)
  const here = (p.thread || []).filter((t) => t.point === pt.id && t.version === p.version);
  if (here.length) {
    const th = el("div", "plan-entries");
    for (const t of here) th.append(entry(p, t, false));
    li.append(th);
  }
  if (open) {
    if (editing.has(key)) li.append(editor(p, pt.id));
    else {
      const b = button("plan-comment-btn", "message", t("plans.comment"), `comment|${key}`);
      b.title = t("plans.comment_title", { point: pt.id });
      b.onclick = () => { editing.add(key); focusNext = `ta|${key}`; render(); };
      li.append(b);
    }
  }
  return li;
}

function entry(p, e, general) {
  const astrel = e.who === "astrel";
  const row = el("div", `plan-entry ${astrel ? "astrel" : "utilisateur"}`);
  const head = el("div", "plan-entry-head");
  const who = el("span", "plan-entry-who");
  setLabel(who, astrel ? "bot" : "user", astrel ? assistantName() : t("plans.you"), "lead"); // nom affiché (P31)
  head.append(who);
  const meta = [];
  if (general && e.point) meta.push(t("plans.point", { point: e.point }));
  if (e.version !== p.version) meta.push(t("plans.version", { version: e.version }));
  meta.push(when(e.ts));
  head.append(el("span", "plan-entry-meta", meta.join(" · ")));
  if (!astrel) head.append(el("span", `plan-entry-state${e.sent ? "" : " unsent"}`, t(e.sent ? "plans.sent" : "plans.unsent")));
  row.append(head);
  if (astrel) {
    const body = el("div", "md plan-entry-text");
    body.append(renderMarkdown(e.text));
    row.append(body);
  } else {
    row.append(el("p", "plan-entry-text plain", stripEmoji(e.text)));
  }
  return row;
}

function threadBlock(p, open) {
  const box = el("section", "plan-thread");
  box.append(el("h3", "plan-section-title", t("plans.thread")));
  const general = (p.thread || []).filter((t) => !t.point || t.version !== p.version);
  if (general.length) {
    const list = el("div", "plan-entries");
    for (const t of general) list.append(entry(p, t, true));
    box.append(list);
  } else {
    box.append(el("p", "plans-empty small", t("plans.thread_empty")));
  }
  const key = `${p.id}|`;
  if (open) {
    if (editing.has(key)) box.append(editor(p, ""));
    else {
      const b = button("plan-comment-btn", "message", t("plans.remark_all"), `comment|${key}`);
      b.onclick = () => { editing.add(key); focusNext = `ta|${key}`; render(); };
      box.append(b);
    }
  }
  return box;
}

function editor(p, point) {
  const key = `${p.id}|${point}`;
  const box = el("div", "plan-editor");
  const ta = el("textarea", "plan-editor-text");
  ta.rows = 3;
  ta.value = drafts.get(key) || "";
  ta.placeholder = point ? t("plans.remark_point_ph", { point }) : t("plans.remark_all_ph");
  ta.setAttribute("aria-label", point ? t("plans.remark_point", { point }) : t("plans.remark_all"));
  ta.dataset.fkey = `ta|${key}`;
  ta.spellcheck = true;
  ta.addEventListener("input", () => {
    drafts.set(key, ta.value);
    if (errors.has(key)) { errors.delete(key); err.classList.add("hidden"); }
  });
  ta.addEventListener("compositionstart", () => { composing = true; });
  ta.addEventListener("compositionend", () => { composing = false; if (deferred) render(); });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing) { e.preventDefault(); sendComment(p, point, false); }
  });
  const err = el("div", "plan-error", errors.get(key) || "");
  err.setAttribute("role", "alert");
  err.classList.toggle("hidden", !errors.has(key));
  const row = el("div", "plan-editor-actions");
  const sending = busy.has(key);
  const add = button("btn ok plan-add", "check", t("plans.add"), `add|${key}`);
  add.title = t("plans.add_title");
  add.disabled = sending;
  add.onclick = () => sendComment(p, point, false);
  const ask = button("btn plan-ask", "help", t("plans.ask"), `ask|${key}`);
  ask.title = t("plans.ask_title");
  ask.disabled = sending;
  ask.onclick = () => sendComment(p, point, true);
  const cancel = button("btn modal-cancel plan-cancel", "", t("common.cancel"), `cancel|${key}`);
  cancel.onclick = () => {
    editing.delete(key);
    drafts.delete(key);
    errors.delete(key);
    focusNext = `comment|${key}`;
    render();
  };
  row.append(cancel, ask, add);
  box.append(ta, err, row);
  return box;
}

function renderActions(p, open) {
  const { actions } = view;
  actions.classList.toggle("hidden", !open);
  if (!open) { actions.replaceChildren(); return; }
  const key = `${p.id}|action`;
  const sending = busy.has(key);
  const n = remarksOf(p);
  const err = el("div", "plan-error", errors.get(key) || "");
  err.setAttribute("role", "alert");
  err.classList.toggle("hidden", !errors.has(key));
  const info = el("span", "plans-actions-info", p.status === "revising"
    ? t("plans.revising_info")
    : n ? t("plans.unsent_remarks", { n }) : t("plans.version_shown", { version: p.version }));
  const reject_ = button("btn danger plan-reject", "x", t("plans.reject"), "act|reject");
  reject_.disabled = sending;
  reject_.onclick = () => reject(p);
  const revise_ = button("btn plan-revise", "refresh", n ? t("plans.revise_n", { n }) : t("plans.revise"), "act|revise");
  revise_.disabled = sending || !n;
  revise_.title = t(n ? "plans.revise_title" : "plans.revise_first");
  revise_.onclick = () => revise(p);
  const ok = button("btn ok strong plan-validate", "check", t("plans.validate_version", { version: p.version }), "act|validate");
  ok.disabled = sending || p.status !== "pending";
  ok.title = t(p.status === "pending" ? "plans.validate_btn_title" : "plans.revising_wait");
  ok.onclick = () => validate(p);
  const row = el("div", "plans-actions-row");
  row.append(info, reject_, revise_, ok);
  actions.replaceChildren(err, row);
}
