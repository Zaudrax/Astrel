// Assistant de calibrage du micro (02/11, demande de l'utilisateur) : Réglages › Voix › « Calibrer ce micro »
// (action `hud: "calibrage"` décrite par le cœur), ou la notice « Ce micro n'est pas calibré » quand
// l'utilisateur choisit un micro sans profil (évènement `calibrage_propose`, jamais lancé seul).
// Pas à pas dans une fenêtre du HUD (openPanel, jamais confirm()/prompt()) : présentation, silence 5 s,
// « Astrel » trois fois (vumètre EN DIRECT, évènements `calibrage` du cœur), puis proposition à côté
// des réglages actuels : Enregistrer / Refaire / Annuler. Le cœur mesure (astrel/calibrage.py) et
// n'écrit le profil qu'à `calibrage_save` ; fermer la fenêtre envoie `calibrage_cancel`.
// Tout texte en textContent ; textes du cœur (erreurs, avertissement) traduits par tx().

import { command, on } from "./conn.js";
import { openPanel } from "./modal.js";
import { icon } from "./icons.js";
import { onHudAction, openSettings } from "./settings.js";
import { assistantName, locale, t, tx } from "./i18n.js";

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

const MESURE = new Set(["bruit", "parole", "analyse"]);
let vue = null; // { panel, body, etat, micro, live: {bar, reste, voix}, fromSettings }
let micro = ""; // nom lisible du micro en cours (évènement `mic`)

const num = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? "—"
  : Number(v).toLocaleString(locale(), { minimumFractionDigits: 0, maximumFractionDigits: d }));

export function initCalibrage() {
  onHudAction("calibrage", () => ouvrir({ fromSettings: true }));
  on("mic", (ev) => { micro = String(ev.label || ""); });
  on("calibrage", (ev) => { if (!ev.replay) suivre(ev); });
  on("calibrage_propose", (ev) => { if (!ev.replay) proposer(ev); });
}

/** Ouvre l'assistant (présentation : rien ne démarre avant « Commencer »). */
export function ouvrir(opts = {}) {
  if (vue) { vue.panel.box.focus(); return; }
  const panel = openPanel({ title: t("calib.title"), icon: "mic", cls: "calibrage" });
  vue = { panel, body: panel.body, etat: "intro", micro: opts.micro || micro, live: null,
    fromSettings: !!opts.fromSettings };
  const v = vue;
  panel.closed.then(() => {
    if (vue !== v) return;
    vue = null;
    if (MESURE.has(v.etat) || v.etat === "resultat" || v.etat === "attente") command("calibrage_cancel");
    if (v.fromSettings) openSettings("audio.calibrage");
  });
  peindre({ etat: "intro" });
}

function suivre(ev) {
  if (!vue) return; // séance lancée d'un autre HUD : rien à montrer ici
  if (ev.micro) vue.micro = tx(String(ev.micro));
  if ((ev.etat === "bruit" || ev.etat === "parole") && vue.etat === ev.etat && vue.live) {
    vivre(ev); // même étape : seulement le vumètre et le temps restant
    return;
  }
  if (vue.etat === "intro" && !MESURE.has(ev.etat)) return; // fin d'une ancienne séance
  if (ev.etat === "annule" && ev.raison === "nouveau calibrage") return; // « Refaire » : la suivante arrive
  peindre(ev);
}

function boutons(...list) {
  const bar = el("div", "calib-actions");
  for (const [label, fn, cls = ""] of list) {
    const b = el("button", `btn ${cls}`.trim(), label);
    b.type = "button";
    b.onclick = fn;
    bar.append(b);
  }
  return bar;
}

const fermer = () => vue?.panel.close();

async function lancer() {
  if (!vue) return;
  peindre({ etat: "attente" });
  const r = await command("calibrage_start");
  if (r?.ok === false && vue) peindre({ etat: "erreur", erreur: r.error || t("common.unknown_error") });
}

async function enregistrer(btn) {
  btn.disabled = true;
  const r = await command("calibrage_save");
  if (!vue) return;
  if (r?.ok === false) { peindre({ etat: "erreur", erreur: r.error, refaire: true }); return; }
  peindre({ etat: "enregistre", profil: r?.profil || "" });
}

function etapes(n) {
  const box = el("ol", "calib-steps");
  [t("calib.step_silence"), t("calib.step_name", { name: assistantName() }), t("calib.step_result")].forEach((s, i) => {
    const li = el("li", i + 1 < n ? "done" : i + 1 === n ? "on" : "", s);
    box.append(li);
  });
  return box;
}

function peindre(ev) {
  const v = vue;
  if (!v) return;
  v.etat = ev.etat;
  v.live = null;
  const b = v.body;
  b.replaceChildren();
  const head = el("p", "calib-mic");
  head.append(icon("mic", "lead"), el("span", "", v.micro || t("mic.system")));
  b.append(head);
  switch (ev.etat) {
    case "intro": {
      b.append(etapes(0), el("p", "calib-text", t("calib.intro", { name: assistantName() })),
        el("p", "calib-note", t("calib.intro_note")),
        boutons([t("common.cancel"), fermer], [t("calib.start"), lancer, "ok"]));
      b.querySelector(".calib-actions .ok")?.focus();
      return;
    }
    case "attente":
      b.append(etapes(1), el("p", "calib-text", t("calib.starting")), boutons([t("common.cancel"), fermer]));
      return;
    case "bruit":
    case "parole": {
      const parole = ev.etat === "parole";
      b.append(etapes(parole ? 2 : 1));
      b.append(el("p", "calib-big", parole ? t("calib.say_name", { name: assistantName() }) : t("calib.silence")));
      b.append(el("p", "calib-text", parole ? t("calib.say_name_help") : t("calib.silence_help")));
      const meter = el("div", "calib-meter");
      meter.setAttribute("role", "meter");
      meter.setAttribute("aria-label", t("calib.level"));
      meter.setAttribute("aria-valuemin", "0");
      meter.setAttribute("aria-valuemax", "100");
      const bar = el("div", "calib-bar");
      meter.append(bar);
      const info = el("div", "calib-info");
      const reste = el("span", "calib-left");
      const voix = el("span", "calib-voice", parole ? t("calib.voice_heard") : "");
      info.append(reste, voix);
      b.append(meter, info, boutons([t("common.cancel"), fermer]));
      v.live = { bar, reste, voix, meter };
      vivre(ev);
      return;
    }
    case "analyse":
      b.append(etapes(3), el("p", "calib-text", t("calib.analysing")), boutons([t("common.cancel"), fermer]));
      return;
    case "resultat":
      resultat(b, ev);
      return;
    case "enregistre":
      b.append(el("p", "calib-big", t("calib.saved")), el("p", "calib-text", tx(String(ev.profil || ""))),
        boutons([t("common.close"), fermer, "ok"]));
      b.querySelector(".calib-actions .ok")?.focus();
      return;
    case "annule":
      b.append(el("p", "calib-text", ev.raison === "Stop tout" ? t("calib.cancelled_estop") : t("calib.cancelled")),
        boutons([t("common.close"), fermer], [t("calib.redo"), lancer, "ok"]));
      return;
    default: // erreur
      b.append(el("p", "calib-error", tx(String(ev.erreur || "")) || t("common.unknown_error")),
        boutons([t("common.close"), fermer], [t("calib.redo"), lancer, "ok"]));
  }
}

function vivre(ev) {
  const l = vue?.live;
  if (!l) return;
  const level = Math.max(0, Math.min(1, Number(ev.niveau) || 0));
  l.bar.style.transform = `scaleX(${level.toFixed(3)})`;
  l.meter.setAttribute("aria-valuenow", String(Math.round(level * 100)));
  if (ev.reste_s != null) l.reste.textContent = t("calib.left", { s: Math.ceil(Number(ev.reste_s) || 0) });
  l.voix.classList.toggle("on", !!ev.parole);
}

const canaux = (c) => (Array.isArray(c) && c.length ? c.join(" + ") : t("calib.all_channels"));

function resultat(b, ev) {
  const a = ev.ancien || {};
  const p = ev.propose || {};
  b.append(etapes(3));
  const table = el("table", "calib-table");
  const head = el("tr");
  head.append(el("th", "", ""), el("th", "", t("calib.current")), el("th", "", t("calib.proposed")));
  table.append(head);
  const rows = [
    [t("calib.gain"), `×${num(a.gain, 1)}`, `×${num(p.gain, 1)}`],
    [t("calib.vad"), num(a.vad), num(p.vad)],
    [t("calib.wake"), num(a.wake), `${num(p.wake)} · ${t("calib.unchanged")}`],
  ];
  if ((ev.n_canaux || 1) > 1) rows.push([t("calib.channels"), canaux(a.canaux), canaux(p.canaux)]);
  for (const [label, now, next] of rows) {
    const tr = el("tr");
    const changed = now !== next && !next.endsWith(t("calib.unchanged"));
    tr.append(el("th", "", label), el("td", "", now), el("td", changed ? "changed" : "", next));
    table.append(tr);
  }
  b.append(table);
  const facts = el("ul", "calib-facts");
  facts.append(el("li", "", t("calib.levels", { noise: num(ev.bruit, 0), voice: num(ev.voix, 0), ratio: num(ev.rapport, 1) })));
  if (ev.nom) {
    facts.append(el("li", "", t("calib.name_count", { name: assistantName(), p: ev.nom.propose, a: ev.nom.ancien, n: ev.nom.sur })));
  } else {
    facts.append(el("li", "", t("calib.name_unknown", { name: assistantName() })));
  }
  if (Array.isArray(ev.parts) && ev.parts.length > 1) {
    const pct = (x) => Number(x).toLocaleString(locale(), { style: "percent", maximumFractionDigits: 0 });
    facts.append(el("li", "", t("calib.parts", { list: ev.parts.map((x, i) => t("calib.part", { c: i, p: pct(x) })).join(", ") })));
  }
  b.append(facts);
  if (ev.avertissement) b.append(el("p", "calib-warn", tx(String(ev.avertissement))));
  b.append(el("p", "calib-note", t("calib.save_note")));
  const bar = boutons([t("common.cancel"), fermer], [t("calib.redo"), lancer]);
  const save = el("button", "btn ok", t("common.save"));
  save.type = "button";
  save.onclick = () => enregistrer(save);
  bar.append(save);
  b.append(bar);
  save.focus();
}

// Micro choisi sans profil : notice NON bloquante avec un bouton (12 s, ou fermée par le bouton).
function proposer(ev) {
  if (vue) return;
  const box = document.querySelector("#toasts");
  if (!box) return;
  const n = el("div", "toast info calib-propose");
  n.setAttribute("role", "status");
  n.append(el("span", "", t("calib.propose", { mic: tx(String(ev.micro || "")) || t("mic.system") })));
  const go = el("button", "btn ok", t("calib.propose_go"));
  go.type = "button";
  go.onclick = () => { n.remove(); ouvrir({ micro: tx(String(ev.micro || "")) }); };
  const x = el("button", "modal-x");
  x.type = "button";
  x.setAttribute("aria-label", t("common.close"));
  x.append(icon("x"));
  x.onclick = () => n.remove();
  n.append(go, x);
  box.append(n);
  setTimeout(() => n.remove(), 12000);
}
