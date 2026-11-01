// Guide de démarrage interactif du HUD (18/10, plan P5, demande de l'utilisateur : « un
// guide de démarrage interactif qui explique tous les fonctionnements d'Astrel, tous
// les réglages que la personne doit faire pour l'installation »).
//
// Source : docs/GUIDE-DEMARRAGE.md, lu par le cœur (astrel/guide.py) et rendu ici par
// markdown.js (jamais d'innerHTML). Commandes : guide_get, guide_mark {topic, done},
// guide_mark {finish: true} (fin du premier lancement) ; les boutons « Tester » et les
// réglages montés passent par les commandes EXISTANTES de la page « Réglages »
// (settings_action, settings_set). Pastille « Guide » créée ici (pas dans index.html) ;
// le guide s'ouvre seul au premier lancement. Clavier : flèches haut/bas, Début, Fin
// dans la liste des sujets ; « Suivant » met le focus sur le titre du sujet.

import { command, on } from "./conn.js";
import { modalOpen, openPanel } from "./modal.js";
import { icon, setLabel } from "./icons.js";
import { renderMarkdown } from "./markdown.js";
import { loadSettings, mountSetting, onGuideHelp } from "./settings.js";
import { openCapacites } from "./capacites.js"; // plan P22 : page « Tout ce qu'Astrel sait faire »
// 20/10 : `tr` (et non `t`, déjà le nom des sujets ici) ; le guide est demandé dans la langue
// de l'interface (`lang` de guide_get : docs/GUIDE-DEMARRAGE.en.md en anglais).
import { lang, t as tr, tx } from "./i18n.js";

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

const WELCOME = "bienvenue"; // la page d'accueil (introduction du guide)

let data = null; // dernière réponse de guide_get
let view = null; // { panel, status, progress, count, nav, main, finish }
let selected = WELCOME;
let mount = null; // réglage monté dans le sujet affiché ({ el, dispose })
let toast = () => {};
let firstRunShown = false;
const results = new Map(); // id du sujet → résultat du bouton « Tester » {text, level}
const busy = new Set();

export function initGuide(toastFn) {
  toast = toastFn || toast;
  // 19/10 (barre du haut allégée) : icône « ? » seule, nom dans title et aria-label.
  const chip = el("button", "chip icon-only");
  chip.id = "guide-chip";
  chip.type = "button";
  chip.title = tr("guide.chip_title");
  chip.setAttribute("aria-label", tr("guide.chip"));
  chip.append(icon("help"));
  chip.onclick = () => openGuide();
  const settingsChip = document.querySelector("#settings-chip");
  if (settingsChip) settingsChip.before(chip);
  onGuideHelp((topic) => openGuide(topic));
  on("settings", (ev) => {
    // Premier lancement : le guide s'ouvre seul, une fois par page.
    if (ev.first_run && !firstRunShown && !view && !modalOpen()) { firstRunShown = true; openGuide(); }
  });
}

// ------------------------------------------------------------------ fenêtre

export async function openGuide(topic) {
  if (topic) selected = String(topic);
  if (view) { select(selected, true); return; }
  const panel = openPanel({ title: tr("guide.title"), icon: "help", cls: "guide" });
  const status = el("p", "guide-status", tr("guide.loading"));
  status.setAttribute("role", "status");
  const head = el("div", "guide-progress");
  const count = el("span", "guide-count");
  const progress = el("progress", "guide-bar");
  progress.setAttribute("aria-label", tr("guide.progress_aria"));
  const finish = el("button", "btn ok guide-finish", tr("guide.done"));
  finish.type = "button";
  finish.dataset.fkey = "guide|finish";
  finish.onclick = done;
  // Plan P22 (demande : entrée par le Guide, pas de nouvelle pastille). openPanel ferme le
  // guide : une seule fenêtre large à la fois.
  const caps = el("button", "btn guide-capacites");
  caps.type = "button";
  caps.dataset.fkey = "guide|capacites";
  setLabel(caps, "sparkles", tr("capacites.open"), "lead");
  caps.onclick = () => openCapacites();
  head.append(count, progress, caps, finish);
  const nav = el("nav", "guide-nav");
  nav.setAttribute("aria-label", tr("guide.nav_aria"));
  nav.addEventListener("keydown", navKeys);
  const main = el("section", "guide-main");
  main.setAttribute("aria-label", tr("guide.main_aria"));
  panel.body.append(status, head, nav, main);
  view = { panel, status, progress, count, nav, main, finish };
  panel.closed.then(() => {
    if (view?.panel !== panel) return;
    mount?.dispose();
    mount = null;
    view = null;
  });
  await loadSettings(); // les réglages montés dans le guide en ont besoin
  await refresh();
}

async function refresh() {
  const r = await command("guide_get", { lang: lang() });
  if (!view) return;
  if (r?.ok === false || !Array.isArray(r?.topics)) {
    view.status.textContent = tr("guide.unavailable", { error: tx(r?.error) || tr("guide.nothing") });
    view.status.classList.add("err");
    return;
  }
  data = r;
  view.status.classList.add("hidden");
  render();
}

function topics() { return Array.isArray(data?.topics) ? data.topics : []; }
function topicOf(id) { return topics().find((t) => t.id === id) || null; }
// Ordre de lecture : accueil, étapes, puis le fonctionnement.
function order() {
  const t = topics();
  return [WELCOME, ...t.filter((x) => x.etape).map((x) => x.id), ...t.filter((x) => !x.etape).map((x) => x.id)];
}

function render() {
  if (!view || !data) return;
  const steps = data.steps || 0;
  const doneCount = data.steps_done || 0;
  view.count.textContent = tr("guide.count", { n: doneCount, steps });
  view.progress.max = Math.max(1, steps);
  view.progress.value = doneCount;
  view.finish.textContent = tr(data.first_run ? "guide.done_first" : "guide.done");
  view.finish.title = tr(data.first_run ? "guide.done_first_title" : "guide.done_title");
  renderNav();
  if (!order().includes(selected)) selected = WELCOME;
  renderMain();
}

function navButton(id, label, t) {
  const b = el("button", "guide-topic");
  b.type = "button";
  b.dataset.topic = id;
  b.dataset.fkey = `guide|nav|${id}`;
  b.setAttribute("aria-current", id === selected ? "true" : "false");
  if (t?.etape) {
    b.classList.add("step");
    b.classList.toggle("done", !!t.done);
    const mark = el("span", "guide-check");
    mark.append(icon(t.done ? "check" : "target"));
    mark.setAttribute("aria-hidden", "true");
    b.append(mark, el("span", "guide-topic-label", label));
    b.append(el("span", "visually-hidden", tr(t.done ? "guide.sr_done" : "guide.sr_todo")));
  } else {
    b.append(el("span", "guide-topic-label", label));
  }
  b.onclick = () => select(id, false);
  return b;
}

function renderNav() {
  const { nav } = view;
  const fkey = nav.contains(document.activeElement) ? document.activeElement.dataset.fkey : null;
  const all = topics();
  const make = (title, list) => {
    const g = el("div", "guide-group");
    const h = el("h3", "guide-group-title", title);
    const ul = el("ul", "guide-list");
    for (const t of list) {
      const li = el("li");
      li.append(navButton(t.id, t.titre, t));
      ul.append(li);
    }
    g.append(h, ul);
    return g;
  };
  const welcome = el("div", "guide-group");
  welcome.append(navButton(WELCOME, tr("guide.welcome"), null));
  nav.replaceChildren(welcome, make(tr("guide.install"), all.filter((t) => t.etape)),
    make(tr("guide.how"), all.filter((t) => !t.etape)));
  if (fkey) [...nav.querySelectorAll("[data-fkey]")].find((x) => x.dataset.fkey === fkey)?.focus({ preventScroll: true });
}

function navKeys(e) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
  const list = [...view.nav.querySelectorAll(".guide-topic")];
  const i = list.indexOf(document.activeElement);
  if (i < 0) return;
  e.preventDefault();
  const next = e.key === "Home" ? 0 : e.key === "End" ? list.length - 1
    : Math.min(list.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)));
  list[next].focus();
}

function select(id, focusTitle) {
  selected = id;
  if (!view || !data) return;
  renderNav();
  renderMain();
  view.main.scrollTop = 0;
  if (focusTitle) view.main.querySelector(".guide-title")?.focus({ preventScroll: true });
  else view.nav.querySelector(`[data-topic="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" });
}

function renderMain() {
  const { main } = view;
  mount?.dispose();
  mount = null;
  const ids = order();
  const at = ids.indexOf(selected);
  const t = topicOf(selected);
  const title = el("h2", "guide-title", t ? t.titre : (data.title || tr("guide.welcome")));
  title.tabIndex = -1;
  const parts = [title];
  if (t?.etape) {
    const steps = topics().filter((x) => x.etape);
    const n = steps.findIndex((x) => x.id === t.id) + 1;
    const line = el("div", "guide-step-line");
    line.append(el("span", "guide-step-n", tr("guide.step", { n, steps: steps.length })));
    const lab = el("label", "guide-done");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = !!t.done;
    cb.disabled = !!t.auto || busy.has(t.id);
    cb.dataset.fkey = `guide|done|${t.id}`;
    cb.onchange = () => mark(t.id, cb.checked);
    lab.append(cb, el("span", "", tr(t.auto ? "guide.auto_done" : "guide.mark_done")));
    line.append(lab);
    parts.push(line);
  }
  const body = el("div", "md guide-body");
  body.append(renderMarkdown(t ? t.corps : (data.intro || "")));
  parts.push(body);
  if (t?.test) {
    const box = el("div", "guide-test");
    const b = el("button", "btn guide-test-btn");
    b.type = "button";
    b.dataset.fkey = `guide|test|${t.id}`;
    const running = busy.has(`test|${t.id}`);
    setLabel(b, "play", tr(running ? "guide.testing" : "guide.test"), "lead");
    b.disabled = running;
    b.onclick = () => runTest(t);
    box.append(b);
    const res = results.get(t.id);
    if (res) {
      const r = el("p", `guide-test-result lv-${res.level}`, tx(res.text));
      r.setAttribute("role", "status");
      box.append(r);
    }
    parts.push(box);
  }
  if (t?.reglage) {
    const sec = el("div", "guide-setting");
    sec.append(el("h3", "guide-setting-title", tr("guide.set_here")));
    mount = mountSetting(t.reglage);
    sec.append(mount.el);
    parts.push(sec);
  }
  const foot = el("div", "guide-foot");
  const prev = el("button", "btn guide-prev", tr("guide.prev"));
  prev.type = "button";
  prev.dataset.fkey = "guide|prev";
  prev.disabled = at <= 0;
  prev.onclick = () => select(ids[at - 1], true);
  const next = el("button", "btn ok guide-next", tr(at === 0 ? "guide.start" : "guide.next"));
  next.type = "button";
  next.dataset.fkey = "guide|next";
  next.disabled = at >= ids.length - 1;
  next.onclick = () => select(ids[at + 1], true);
  foot.append(prev, next);
  parts.push(foot);
  const fkey = main.contains(document.activeElement) ? document.activeElement.dataset.fkey : null;
  main.replaceChildren(...parts);
  if (fkey) [...main.querySelectorAll("[data-fkey]")].find((x) => x.dataset.fkey === fkey)?.focus({ preventScroll: true });
}

// ------------------------------------------------------------------ actions

async function mark(id, done) {
  busy.add(id);
  const r = await command("guide_mark", { topic: id, done, lang: lang() });
  busy.delete(id);
  if (Array.isArray(r?.topics)) data = r;
  else toast(tr("guide.step_not_saved", { error: tx(r?.error) || tr("common.error") }), "error");
  render();
}

async function runTest(t) {
  const tag = `test|${t.id}`;
  if (busy.has(tag)) return;
  busy.add(tag);
  results.delete(t.id);
  renderMain();
  const r = await command("settings_action", { key: t.test.key, action: t.test.action }, 10000, 30000);
  busy.delete(tag);
  results.set(t.id, r?.ok === false
    ? { text: r.error || tr("guide.test_failed"), level: "error" }
    : { text: r?.text || tr("guide.test_started"), level: ["ok", "info", "warn", "error"].includes(r?.level) ? r.level : "ok" });
  if (view && selected === t.id) renderMain();
}

async function done() {
  const r = await command("guide_mark", { finish: true, lang: lang() });
  if (Array.isArray(r?.topics)) {
    data = r;
    toast(tr("guide.finished"));
    render();
  } else {
    toast(tr("guide.not_saved", { error: tx(r?.error) || tr("common.error") }), "error");
  }
}
