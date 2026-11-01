// Vue « Tâches » du HUD (26/10, plan P38 lot 2, demande de l'utilisateur : une todo par projet,
// colonnes à planifier / à lancer / en cours / fini, lue à l'écran). Dorsal : astrel/todo.py.
//
// Évènements : `todo` (sticky : {etats, projets: [{id, label, compteurs}], cartes}) et `tasks`
// (agents vivants ; `refs` = plans et missions cités par leur consigne, core._todo_refs).
// Commandes : todos_get, todo_get, todo_add, todo_move, todo_done, todo_drop, la carte nommée
// `todo_id` (« id » est le numéro de la requête, conn.js). Le cœur fait foi : après une
// action, la vue suit l'évènement `todo` qu'il republie.
// Titres, notes, raisons = contenu de l'utilisateur ou de Claude : textContent seul, jamais
// traduits ni rendus en markdown. Avancé seulement (styles.css, bloc « P38 todo »).
// Pas de glisser-déposer (lot 4) : clic ou clavier (Tab, flèches, Entrée).

import { command, on } from "./conn.js";
import { openModal, openPanel } from "./modal.js";
import { setLabel, stripEmoji } from "./icons.js";
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

export const COLONNES = ["planifier", "lancer", "cours", "fini"];
const OUVERTS = ["planifier", "lancer", "cours"];
const FINI_MAX = 30; // colonne « Fini » : les plus récentes, le reste compté
const PLAN_STATUTS = ["pending", "revising", "validated", "rejected"];

export const etatLabel = (e) => (COLONNES.includes(e) ? t(`todo.col_${e}`) : e === "abandonne" ? t("todo.s_abandonne") : String(e || ""));
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString(locale(), {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "");
const titre = (c) => stripEmoji(String(c?.titre || "")).trim() || String(c?.id || "");

/** Ligne de l'aperçu : « Boutique · 2 à lancer · 1 en cours » (cartes ouvertes seulement). */
export function apercuParts(compteurs) {
  return OUVERTS.filter((e) => Number(compteurs?.[e]) > 0).map((e) => t(`todo.n_${e}`, { n: Number(compteurs[e]) }));
}

/** Références d'une carte comparées aux `refs` des agents : « p38 », mission en minuscules. */
export function carteRefs(c) {
  const refs = [];
  const m = /^P(\d{1,4})$/i.exec(String(c?.plan || "").trim());
  if (m) refs.push(`p${Number(m[1])}`);
  const mission = String(c?.mission || "").trim().toLowerCase();
  if (mission) refs.push(mission);
  return refs;
}

/** Agents vivants qui travaillent sur cette carte (consigne qui cite son plan ou sa mission). */
export function agentsDe(c, items) {
  const refs = carteRefs(c);
  if (!refs.length) return [];
  return (items || []).filter((k) => k && k.kind === "agent" && Array.isArray(k.refs) && k.refs.some((r) => refs.includes(r)));
}

let last = null; // dernier évènement `todo`
let listError = "";
let agents = []; // tâches vivantes (évènement `tasks`)
let agentSig = "";
let view = null;
let filter = ""; // id du projet, "" = tous
let selected = ""; // carte ouverte en détail
const full = new Map(); // id → carte avec `hist` (todo_get)
const loading = new Set();
const cardErrors = new Map();
const busy = new Set();
let toast = () => {};
let focusNext = null;
let drag = null; // carte qu'on glisse {id, etat} (T26 : priorité dans sa colonne)

export function initTodo(toastFn) {
  toast = toastFn || toast;
  document.querySelector("#todo-chip").onclick = () => open();
  on("todo", (ev) => {
    last = ev;
    listError = "";
    updateChip();
    renderApercu();
    staleCheck();
    render();
  });
  on("tasks", (ev) => {
    agents = Array.isArray(ev.items) ? ev.items : [];
    // `tasks` arrive chaque seconde ou presque : on ne redessine que si un badge change.
    const sig = cartes().map((c) => `${c.id}:${agentsDe(c, agents).length}`).join(",");
    if (sig === agentSig) return;
    agentSig = sig;
    render();
  });
}

const cartes = () => (Array.isArray(last?.cartes) ? last.cartes : []);
const projets = () => (Array.isArray(last?.projets) ? last.projets : []);
const projetLabel = (id) => projets().find((p) => p.id === id)?.label || id;
const ouvertes = (p) => OUVERTS.reduce((n, e) => n + (Number(p?.compteurs?.[e]) || 0), 0);

// ------------------------------------------------------------------ pastille et aperçu

function updateChip() {
  const n = cartes().filter((c) => c.attend === "utilisateur" && OUVERTS.includes(c.etat)).length;
  const badge = document.querySelector("#todo-badge");
  badge.textContent = String(n);
  badge.classList.toggle("hidden", !n);
  document.querySelector("#todo-chip").title = n ? t("todo.chip_waiting", { n }) : t("todo.chip_title");
}

function renderApercu() {
  const box = document.querySelector("#todo-apercu");
  const rows = projets().filter((p) => ouvertes(p) > 0);
  box.replaceChildren();
  box.classList.toggle("hidden", !rows.length);
  document.querySelector("#todo-title").classList.toggle("hidden", !rows.length);
  for (const p of rows) {
    const row = el("div", "todo-apercu-row");
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.dataset.projet = p.id;
    row.title = t("todo.apercu_row_title");
    row.append(el("span", "todo-apercu-name", String(p.label || p.id)), el("span", "time", apercuParts(p.compteurs).join(" · ")));
    row.onclick = () => open(p.id);
    row.onkeydown = (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      e.stopPropagation(); // Entrée ne doit pas répondre à la carte de confirmation
      open(p.id);
    };
    box.append(row);
  }
}

// ------------------------------------------------------------------ fenêtre

async function open(projet) {
  if (typeof projet === "string") { filter = projet; selected = ""; }
  if (view) { view.panel.box.focus(); render(); return; }
  // Échap dans le détail = retour au tableau (fiche 2 du lot 2) ; Échap sur le tableau ferme la vue.
  const panel = openPanel({ title: t("todo.chip"), icon: "layers", cls: "todo",
    onEscape: () => { if (!selected) return false; backToBoard(); return true; } });
  const head = panel.box.querySelector(".modal-head");
  const add = button("btn ok todo-add", "check", t("todo.add"), "add");
  add.title = t("todo.add_title");
  add.onclick = () => ajouter();
  head.insertBefore(add, head.querySelector(".modal-x"));
  const status = el("p", "todo-status", t("todo.loading"));
  status.setAttribute("role", "status");
  const filters = el("div", "todo-filters");
  filters.setAttribute("role", "tablist");
  filters.setAttribute("aria-label", t("todo.filters_aria"));
  const main = el("div", "todo-main");
  panel.body.append(status, filters, main);
  view = { panel, status, filters, main, add, shown: "" };
  panel.closed.then(() => { if (view?.panel === panel) view = null; });
  render(); // l'état connu tout de suite (évènement sticky), le cœur le rafraîchit
  await refresh();
}

async function refresh() {
  const r = await command("todos_get");
  if (!view) return;
  if (Array.isArray(r?.cartes)) {
    last = { ...r, type: "todo" };
    listError = "";
    updateChip();
    renderApercu();
  } else if (r?.ok === false) {
    listError = tx(r.error) || t("common.unknown_error");
  } else if (!last) {
    const v = view;
    setTimeout(() => {
      if (view !== v || last) return;
      listError = t("todo.nothing");
      render();
    }, 3000);
  }
  render();
}

async function loadCard(id) {
  if (!id || loading.has(id)) return;
  loading.add(id);
  cardErrors.delete(id);
  const r = await command("todo_get", { todo_id: id });
  loading.delete(id);
  if (r?.carte?.id) full.set(r.carte.id, r.carte);
  else cardErrors.set(id, tx(r?.error) || t("common.unknown_error"));
  render();
}

// La carte affichée a changé (action, Claude, autre HUD) : son historique est redemandé.
function staleCheck() {
  if (!view || !selected) return;
  const s = cartes().find((c) => c.id === selected);
  const f = full.get(selected);
  if (s && f && (s.maj !== f.maj || s.etat !== f.etat)) loadCard(selected);
}

// ------------------------------------------------------------------ actions

async function ajouter() {
  const projet = filter;
  const fields = [];
  if (!projet) {
    const noms = projets().map((p) => p.label).join(", ");
    fields.push({ name: "projet", label: t("todo.f_projet"), required: true, hint: t("todo.f_projet_hint", { list: noms }) });
  }
  fields.push({ name: "titre", label: t("todo.f_titre"), required: true, placeholder: t("todo.f_titre_ph") },
    { name: "note", label: t("todo.f_note"), multiline: true, rows: 3, placeholder: t("todo.f_note_ph") });
  // Le cœur répond DANS la boîte (fiche 2 du lot 2) : un refus (projet inconnu, Général, titre vide)
  // la laisse ouverte avec son message et le texte tapé ; un doublon proche la ferme pour la question.
  let args = null;
  let r = null;
  const values = await openModal({
    title: projet ? t("todo.add_modal_title", { projet: projetLabel(projet) }) : t("todo.add_modal_title_any"),
    text: t("todo.add_modal_text"),
    fields, ok: t("todo.add"),
    check: async (v) => {
      args = { projet: projet || v.projet, titre: v.titre, note: v.note || "" };
      r = await command("todo_add", args);
      return r?.ok === false && !r.doublon?.id ? t("todo.refused", { error: tx(r.error) || t("common.unknown_error") }) : "";
    },
  });
  if (!r?.id && (!values || r?.ok === false && !r.doublon?.id)) return; // annulé, ou refus déjà dit dans la boîte
  if (r?.ok === false && r.doublon?.id) {
    const yes = await openModal({
      title: t("todo.dup_title"),
      text: t("todo.dup_text", { id: r.doublon.id, titre: stripEmoji(String(r.doublon.titre || "")) }),
      ok: t("todo.dup_ok"),
    });
    if (!yes) return;
    r = await command("todo_add", { ...args, force: true });
  }
  if (r?.ok === false) { toast(t("todo.refused", { error: tx(r.error) || t("common.unknown_error") }), "warning"); return; }
  if (r?.id) {
    toast(t("todo.added", { id: r.id, projet: projetLabel(r.carte?.projet || args.projet) }), "info", "check");
    focusNext = `card|${r.id}`;
    render();
  }
}

async function deplacer(c, etat) {
  const key = `${c.id}|action`;
  if (busy.has(key)) return;
  let raison = "";
  if (etat === "abandonne") {
    const values = await openModal({
      title: t("todo.drop_title", { id: c.id }),
      text: t("todo.drop_text", { titre: titre(c) }),
      fields: [{ name: "raison", label: t("todo.drop_reason"), required: true, multiline: true, rows: 2,
        placeholder: t("todo.drop_reason_ph") }],
      ok: t("todo.drop_ok"), danger: true,
    });
    if (!values) return;
    raison = values.raison;
  }
  busy.add(key);
  render();
  const cmd = etat === "fini" ? "todo_done" : etat === "abandonne" ? "todo_drop" : "todo_move";
  const r = await command(cmd, { todo_id: c.id, etat, raison });
  busy.delete(key);
  if (r?.ok === false) toast(t("todo.refused", { error: tx(r.error) || t("common.unknown_error") }), "warning");
  else toast(t("todo.moved", { id: c.id, etat: etatLabel(etat) }), "info", "check");
  loadCard(c.id); // historique à jour ; le cœur republie `todo` pour le tableau
  focusNext = "back";
  render();
}

// ------------------------------------------------------------------ rendu

function render() {
  if (!view) return;
  const { panel, status, main } = view;
  const active = document.activeElement;
  const inside = panel.box.contains(active);
  const fkey = inside ? active.dataset?.fkey : null;
  const tops = [...main.querySelectorAll("[data-scroll]")].map((x) => [x.dataset.scroll, x.scrollTop]);

  const known = Array.isArray(last?.cartes);
  status.classList.toggle("err", !!listError);
  status.textContent = listError ? t("todo.unavailable", { error: listError }) : known ? "" : t("todo.loading");
  status.classList.toggle("hidden", !status.textContent);
  if (filter && !projets().some((p) => p.id === filter)) filter = "";
  renderFilters();
  if (selected && !cartes().some((c) => c.id === selected)) selected = "";
  if (selected) renderDetail(); else renderBoard();

  for (const [k, top] of tops) {
    const x = main.querySelector(`[data-scroll="${k}"]`);
    if (x) x.scrollTop = top;
  }
  const want = focusNext || fkey;
  focusNext = null;
  if (want) {
    const again = [...panel.box.querySelectorAll("[data-fkey]")].find((x) => x.dataset.fkey === want);
    if (again && again !== document.activeElement) again.focus({ preventScroll: false });
  }
  if ((inside || want) && !panel.box.contains(document.activeElement)) panel.box.focus({ preventScroll: true });
}

function renderFilters() {
  const all = cartes().filter((c) => OUVERTS.includes(c.etat)).length;
  const opts = [["", t("todo.all"), all], ...projets().map((p) => [p.id, String(p.label || p.id), ouvertes(p)])];
  view.filters.replaceChildren(...opts.map(([id, label, n]) => {
    const b = el("button", `todo-filter${filter === id ? " on" : ""}`, label);
    b.type = "button";
    b.dataset.fkey = `filter|${id}`;
    b.dataset.projet = id;
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", filter === id ? "true" : "false");
    b.title = t("todo.filter_title", { n });
    b.append(el("span", "todo-filter-n", String(n)));
    b.onclick = () => {
      if (filter === id && !selected) return;
      filter = id;
      selected = "";
      render();
    };
    return b;
  }));
}

// T26 (29/10) : colonnes ouvertes dans l'ordre de priorité de l'utilisateur (`rang`, fourni par le cœur),
// la première est celle qu'Astrel prend quand on lui dit « continue » ; fini et abandonné : les plus récentes.
function shownCards() {
  // Comparaison cohérente : ouvertes d'abord (par rang), puis les autres (les plus récentes d'abord).
  const ouverte = (c) => OUVERTS.includes(c.etat);
  return cartes().filter((c) => !filter || c.projet === filter).sort((a, b) =>
    (ouverte(b) - ouverte(a))
    || (ouverte(a) ? (Number(a.rang) || 0) - (Number(b.rang) || 0) : (Number(b.maj) || 0) - (Number(a.maj) || 0)));
}

// On classe dans UN projet (filtre choisi) : la priorité est celle de chaque projet.
const classable = (etat) => !!filter && OUVERTS.includes(etat);

/** Nouvel ordre d'une colonne : affiché tout de suite, puis `todo_ordre` ; refusé = la vue du cœur revient. */
async function reorder(ids) {
  ids.forEach((id, i) => { const c = cartes().find((x) => x.id === id); if (c) c.rang = i; });
  render();
  const r = await command("todo_ordre", { ids });
  if (r?.ok === false) {
    toast(t("todo.order_failed", { error: tx(r.error) || t("common.send_failed") }), "error");
    command("todos_get");
  }
}

function renderBoard() {
  const list = shownCards();
  const board = el("div", "todo-board");
  board.setAttribute("aria-keyshortcuts", "ArrowLeft ArrowRight ArrowUp ArrowDown");
  for (const etat of COLONNES) {
    const mine = list.filter((c) => c.etat === etat);
    const col = el("section", `todo-col c-${etat}`);
    col.dataset.etat = etat;
    const headId = `todo-col-${etat}`;
    const head = el("h3", "todo-col-head", etatLabel(etat));
    head.id = headId;
    head.append(el("span", "todo-col-n", String(mine.length)));
    col.setAttribute("aria-labelledby", headId);
    const ul = el("div", "todo-col-list");
    ul.dataset.scroll = etat;
    const shown = etat === "fini" ? mine.slice(0, FINI_MAX) : mine;
    for (const c of shown) ul.append(cardButton(c));
    if (classable(etat)) {
      ul.addEventListener("dragover", (e) => {
        if (drag?.etat !== etat) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
      });
      ul.addEventListener("drop", (e) => {
        if (drag?.etat !== etat) return;
        e.preventDefault();
        const ids = mine.map((c) => c.id).filter((id) => id !== drag.id);
        const over = e.target.closest?.(".todo-card");
        let i = over ? ids.indexOf(over.dataset.todo) : -1;
        if (i < 0) i = ids.length;
        else if (e.clientY > over.getBoundingClientRect().top + over.offsetHeight / 2) i += 1;
        ids.splice(i, 0, drag.id);
        focusNext = `card|${drag.id}`;
        reorder(ids);
      });
    }
    if (!mine.length) ul.append(el("p", "todo-empty", t("todo.empty_col")));
    if (mine.length > shown.length) ul.append(el("p", "todo-more", t("todo.more_done", { n: mine.length - shown.length })));
    col.append(head, ul);
    board.append(col);
  }
  board.addEventListener("keydown", arrows);
  const parts = [board];
  const dropped = list.filter((c) => c.etat === "abandonne");
  if (dropped.length) {
    const det = el("details", "todo-dropped");
    det.open = !!view.droppedOpen;
    det.addEventListener("toggle", () => { if (view) view.droppedOpen = det.open; });
    const sum = el("summary", "", t("todo.dropped", { n: dropped.length }));
    sum.dataset.fkey = "dropped";
    const box = el("div", "todo-dropped-list");
    for (const c of dropped) box.append(cardButton(c));
    det.append(sum, box);
    parts.push(det);
  }
  if (!list.length && Array.isArray(last?.cartes)) {
    parts.unshift(el("p", "todo-intro", t("todo.empty_all", { name: assistantName() })));
  }
  parts.push(el("p", "todo-keys", t("todo.keys_hint")));
  view.main.replaceChildren(...parts);
}

function badges(c, withProjet) {
  const row = el("div", "todo-badges");
  if (withProjet) row.append(el("span", "todo-tag todo-projet", projetLabel(c.projet)));
  if (c.plan) {
    const s = PLAN_STATUTS.includes(c.plan_statut) ? c.plan_statut : "";
    const pill = el("span", `todo-tag todo-plan${s ? ` s-${s}` : ""}`, c.plan);
    pill.title = t("todo.badge_plan_title", { plan: c.plan, statut: s ? t(`plans.s_${s}`) : t("todo.badge_plan_unknown") });
    row.append(pill);
  }
  if (c.attend === "utilisateur" && OUVERTS.includes(c.etat)) {
    const w = el("span", "todo-tag todo-wait", t("todo.badge_waiting"));
    w.title = t("todo.badge_waiting_title");
    row.append(w);
  }
  // Lot 3 : LU par le cœur dans les fiches de passation (relecteur sans réserve), jamais recopié.
  if (c.pret_a_clore && OUVERTS.includes(c.etat)) {
    const r = el("span", "todo-tag todo-ready", t("todo.badge_ready"));
    r.title = t("todo.badge_ready_title", { mission: String(c.pret_a_clore.mission || ""), n: String(c.pret_a_clore.fiche || "") });
    row.append(r);
  }
  const live = OUVERTS.includes(c.etat) ? agentsDe(c, agents) : [];
  if (live.length) {
    const a = el("span", "todo-tag todo-agent");
    setLabel(a, "bot", t("todo.badge_agent", { n: live.length }), "lead");
    a.title = t("todo.badge_agent_title", { list: live.map((k) => stripEmoji(tx(k.desc || ""))).join(", ") });
    row.append(a);
  }
  return row;
}

function cardButton(c) {
  const b = el("button", `todo-card e-${c.etat}`);
  b.type = "button";
  b.dataset.fkey = `card|${c.id}`;
  b.dataset.todo = c.id;
  b.title = t("todo.open_card", { id: c.id }); // le lecteur d'écran lit le contenu : id, titre, badges
  const top = el("div", "todo-card-top");
  top.append(el("span", "todo-card-id", c.id), el("span", "todo-card-title ad-data", titre(c)));
  b.append(top);
  const bs = badges(c, !filter);
  if (bs.childElementCount) b.append(bs);
  b.onclick = () => { selected = c.id; focusNext = "back"; render(); };
  if (classable(c.etat)) {
    b.draggable = true;
    b.title = t("todo.open_card_drag", { id: c.id });
    b.addEventListener("dragstart", (e) => {
      drag = { id: c.id, etat: c.etat };
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", c.id);
      b.classList.add("dragging");
    });
    b.addEventListener("dragend", () => { drag = null; b.classList.remove("dragging"); });
  }
  return b;
}

// Flèches : ← → d'une colonne à l'autre (même rang, borné), ↑ ↓ dans la colonne ; Alt+↑ ↓ (projet choisi,
// colonne ouverte) monte ou descend la carte dans l'ordre de priorité.
function arrows(e) {
  if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
  const card = e.target.closest?.(".todo-card");
  if (!card) return;
  if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
    const c = cartes().find((x) => x.id === card.dataset.todo);
    e.preventDefault();
    if (!c || !classable(c.etat)) return;
    const ids = shownCards().filter((x) => x.etat === c.etat).map((x) => x.id);
    const i = ids.indexOf(c.id);
    const j = i + (e.key === "ArrowDown" ? 1 : -1);
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    focusNext = `card|${c.id}`;
    reorder(ids);
    return;
  }
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  const cols = [...view.main.querySelectorAll(".todo-col")];
  const ci = cols.findIndex((x) => x.contains(card));
  const items = (x) => [...x.querySelectorAll(".todo-card")];
  const ri = items(cols[ci]).indexOf(card);
  let target = null;
  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    target = items(cols[ci])[ri + (e.key === "ArrowDown" ? 1 : -1)] || null;
  } else {
    const step = e.key === "ArrowRight" ? 1 : -1;
    for (let j = ci + step; j >= 0 && j < cols.length && !target; j += step) {
      const list = items(cols[j]);
      if (list.length) target = list[Math.min(ri, list.length - 1)];
    }
  }
  e.preventDefault();
  if (target) target.focus();
}

// Retour au tableau (bouton ou Échap), le focus revient à la carte qu'on vient de quitter.
function backToBoard() {
  const id = selected;
  selected = "";
  focusNext = `card|${id}`;
  render();
}

function renderDetail() {
  const s = cartes().find((c) => c.id === selected);
  const c = full.get(selected) ? { ...full.get(selected), ...s } : s;
  const box = el("article", "todo-detail");
  box.dataset.todo = selected;
  box.dataset.scroll = "detail";
  const back = button("btn todo-back", "chevron", t("todo.back"), "back"); // chevron retourné (styles.css)
  back.title = t("todo.back_title");
  back.onclick = backToBoard;
  const head = el("header", "todo-detail-head");
  const top = el("div", "todo-detail-top");
  top.append(el("span", "todo-card-id", c.id), el("h2", "todo-detail-title ad-data", titre(c)),
    el("span", `todo-state e-${c.etat}`, etatLabel(c.etat)));
  const meta = [projetLabel(c.projet), t("todo.meta_created", { when: when(c.cree) })];
  if (c.maj && c.maj !== c.cree) meta.push(t("todo.meta_updated", { when: when(c.maj) }));
  if (c.mission) meta.push(t("todo.meta_mission", { mission: c.mission }));
  head.append(top, el("div", "todo-detail-meta", meta.join(" · ")));
  const bs = badges(c, false);
  if (bs.childElementCount) head.append(bs);
  box.append(back, head);
  // Note et raisons : émojis retirés à l'affichage comme le titre (règle du HUD du 15/10, le plan P38
  // n'en dit rien) ; le texte gardé par le cœur n'est pas touché, sauts de ligne compris.
  if (c.etat === "abandonne" && c.raison) {
    box.append(el("p", "todo-reason ad-data", t("todo.reason", { raison: stripEmoji(String(c.raison)) })));
  }
  // Notes et raisons posées par le cœur (« un agent y travaille »…) : tx() les traduit ; un texte écrit par
  // l'utilisateur ou Claude n'est pas au catalogue et reste tel quel.
  const noteText = tx(stripEmoji(String(c.note || "")).trim());
  const note = el("section", "todo-section");
  note.append(el("h3", "todo-section-title", t("todo.f_note")),
    noteText ? el("p", "todo-note ad-data", noteText) : el("p", "todo-empty", t("todo.no_note")));
  box.append(note, actions(c), history(c));
  view.main.replaceChildren(box);
}

function actions(c) {
  const sec = el("section", "todo-section todo-actions");
  const sending = busy.has(`${c.id}|action`);
  const open = OUVERTS.includes(c.etat);
  sec.append(el("h3", "todo-section-title", t(open ? "todo.move_to" : "todo.reopen")));
  const row = el("div", "todo-actions-row");
  for (const e of OUVERTS.filter((x) => x !== c.etat)) {
    const b = button("btn todo-move", "", etatLabel(e), `move|${e}`);
    b.dataset.etat = e;
    b.disabled = sending;
    b.onclick = () => deplacer(c, e);
    row.append(b);
  }
  if (open) {
    const done = button("btn ok strong todo-done", "check", t("todo.done"), "move|fini");
    done.disabled = sending;
    done.onclick = () => deplacer(c, "fini");
    const drop = button("btn danger todo-drop", "x", t("todo.drop"), "move|abandonne");
    drop.disabled = sending;
    drop.onclick = () => deplacer(c, "abandonne");
    row.append(done, drop);
  }
  sec.append(row);
  return sec;
}

function history(c) {
  const sec = el("section", "todo-section");
  sec.append(el("h3", "todo-section-title", t("todo.history")));
  const f = full.get(c.id);
  if (!f) {
    if (cardErrors.has(c.id)) {
      const err = el("p", "todo-error", t("todo.card_unavailable", { id: c.id, error: cardErrors.get(c.id) }));
      err.setAttribute("role", "alert");
      const retry = button("btn todo-retry", "refresh", t("common.retry"), "retry");
      retry.onclick = () => loadCard(c.id);
      sec.append(err, retry);
    } else {
      sec.append(el("p", "todo-empty", t("todo.card_loading", { id: c.id })));
      loadCard(c.id);
    }
    return sec;
  }
  const ol = el("ol", "todo-hist");
  for (const h of [...(Array.isArray(f.hist) ? f.hist : [])].reverse()) {
    let what;
    if (Array.isArray(h.modif)) what = t("todo.hist_edited", { champs: h.modif.map((k) => t(`todo.field_${k}`)).join(", ") });
    else if (h.de) what = `${etatLabel(h.de)} → ${etatLabel(h.a)}`;
    else what = t("todo.hist_created", { etat: etatLabel(h.a) });
    const who = h.par === "auto" ? t("todo.by_auto", { name: assistantName() })
      : h.par === "claude" ? assistantName() : t("todo.you");
    const li = el("li", "todo-hist-row");
    li.append(el("span", "todo-hist-when", when(h.t)), el("span", "todo-hist-what", `${what} · ${t("todo.hist_by", { who })}`));
    if (h.raison) li.append(el("span", "todo-hist-reason ad-data", tx(stripEmoji(String(h.raison)))));
    ol.append(li);
  }
  if (!ol.childElementCount) ol.append(el("li", "todo-empty", t("todo.history_empty")));
  sec.append(ol);
  return sec;
}
