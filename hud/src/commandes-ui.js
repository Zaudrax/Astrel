// 05/11 (demande de l'utilisateur) : commandes « / » de la barre de chat, côté écran. Table et analyse : commandes.js.
// 1. Liste qui s'ouvre en tapant « / » en tête de la barre (rien devant, une seule ligne) : filtrée en tapant,
//    flèches ↑ ↓, Tab complète, Entrée complète (et lance une commande complète), Échap ferme SANS arrêter de
//    réponse ; souris : un clic vaut Entrée. ARIA : listbox/option, la barre porte aria-controls,
//    aria-activedescendant et aria-autocomplete (le focus reste dans la barre). Rien en innerHTML.
// 2. `runSlash` (appelée par l'envoi de main.js AVANT tout `say`) : une commande n'est JAMAIS envoyée à un modèle.
//    /aide : liste dans le fil (rien envoyé) ; /stop : la commande du bouton Arrêter ; /tout-arreter : la boîte du bouton
//    « Stop tout » ; /local et /claude : le texte à envoyer avec le préfixe du routeur ; le reste : `chat_commande
//    {nom, arg, channel}` (cœur, HUD seul). Erreur ou refus : toast, le texte reste dans la barre.

import { analyser, commande, COMMANDES, nomAffiche, propositions, texteRoutage } from "./commandes.js";
import { lang, t, tx } from "./i18n.js";

let deps = null;
let list = null;          // élément de la liste (dans <body>, position fixe au-dessus de la barre)
let items = [];           // propositions affichées
let active = -1;
const ask = () => document.querySelector("#ask");
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

const argHint = (c) => (c.arg === "texte" ? ` ${t("slash.arg_text")}` : c.arg === "choix" ? ` ${c.valeurs.join("|")}` : "");
const isOpen = () => !!list && !list.hidden;
const sep = () => (lang() === "en" ? ": " : " : "); // typographie de la langue

function place() {
  const box = document.querySelector("#composer")?.getBoundingClientRect();
  if (!box || !list) return;
  list.style.left = `${Math.max(8, box.left)}px`;
  list.style.width = `${Math.min(box.width, 560)}px`;
  list.style.bottom = `${Math.max(8, innerHeight - box.top + 6)}px`;
}

function close() {
  if (!list) return;
  list.hidden = true;
  items = [];
  active = -1;
  const a = ask();
  a.removeAttribute("aria-activedescendant");
}

function setActive(i) {
  active = i;
  const opts = list.querySelectorAll("[role=option]");
  opts.forEach((o, k) => o.setAttribute("aria-selected", k === i ? "true" : "false"));
  const cur = opts[i];
  if (cur) {
    ask().setAttribute("aria-activedescendant", cur.id);
    cur.scrollIntoView({ block: "nearest" });
  } else ask().removeAttribute("aria-activedescendant");
}

function render() {
  const a = ask();
  const v = a.value;
  const caretAtEnd = a.selectionStart === v.length && a.selectionEnd === v.length;
  const found = caretAtEnd && !a.dataset.sending ? propositions(v, lang()) : [];
  if (!found.length) { close(); return; }
  const keep = items[active]?.texte;
  items = found;
  list.replaceChildren(el("div", "slash-head small muted", t("slash.menu_title")));
  list.firstChild.setAttribute("aria-hidden", "true");
  found.forEach((p, k) => {
    const o = el("div", "slash-opt");
    o.id = `slash-opt-${k}`;
    o.setAttribute("role", "option");
    const name = el("span", "slash-name", p.valeur ? p.texte : `/${nomAffiche(p.c, lang())}${argHint(p.c)}`);
    const desc = el("span", "slash-desc small muted",
      p.valeur ? t(`slash.val.${p.c.nom}.${p.valeur}`) : t(`slash.desc.${p.c.nom}`));
    o.append(name, desc);
    // la souris ne prend pas le focus à la barre ; un clic vaut Entrée
    o.addEventListener("pointerdown", (e) => e.preventDefault());
    o.addEventListener("click", (e) => { e.preventDefault(); pick(k, true); });
    list.append(o);
  });
  list.hidden = false;
  place();
  const again = found.findIndex((p) => p.texte === keep);
  setActive(again >= 0 ? again : 0);
}

/** Complète la barre avec la proposition `k` ; `go` : une commande complète part aussitôt (Entrée, clic). */
function pick(k, go) {
  const p = items[k];
  if (!p) return;
  const a = ask();
  const already = a.value.trim() === p.texte.trim();
  a.value = p.texte;
  a.setSelectionRange(a.value.length, a.value.length);
  deps.fit();
  close();
  if (go && p.complete) deps.submit();
  else if (!already) render(); // « /mode » complété : ses valeurs s'affichent
}

function onKey(e) {
  if (e.target !== ask() || !isOpen() || e.isComposing || e.keyCode === 229 || deps.composing()) return;
  if (e.altKey || e.ctrlKey || e.metaKey) return;
  const stop = () => { e.preventDefault(); e.stopPropagation(); };
  if (e.key === "ArrowDown") { stop(); setActive((active + 1) % items.length); }
  else if (e.key === "ArrowUp") { stop(); setActive((active - 1 + items.length) % items.length); }
  else if (e.key === "Tab" && !e.shiftKey) { stop(); pick(active, false); }
  else if (e.key === "Enter" && !e.shiftKey) {
    const p = items[active];
    // Déjà tapée en entier (« /clear », « /mode auto ») : Entrée l'envoie comme d'habitude.
    if (p && ask().value.trim() === p.texte.trim() && p.complete) { close(); return; }
    stop();
    pick(active, true);
  } else if (e.key === "Escape") {
    // Échap ferme la liste, et seulement elle (main.js n'arrête pas la réponse : `defaultPrevented`).
    e.preventDefault();
    close();
  }
}

// ------------------------------------------------------------------ exécution

function helpNode() {
  const box = el("div", "cmd-help-body");
  box.append(el("p", "cmd-help-title", t("slash.help_title")));
  const ul = el("ul", "cmd-help-list");
  for (const c of COMMANDES) {
    const li = el("li");
    const other = [c.nom, c.en, ...c.alias].filter((n, i, all) => n !== nomAffiche(c, lang()) && all.indexOf(n) === i);
    li.append(el("code", "cmd-help-name", `/${nomAffiche(c, lang())}${argHint(c)}`));
    if (other.length) li.append(el("span", "cmd-help-alias small muted", ` (${other.map((n) => `/${n}`).join(", ")})`));
    li.append(document.createTextNode(`${sep()}${t(`slash.desc.${c.nom}`)}`));
    ul.append(li);
  }
  box.append(ul, el("p", "cmd-help-note small muted", t("slash.help_note")));
  return box;
}

function helpText() {
  return [t("slash.help_title"), ...COMMANDES.map((c) => `/${nomAffiche(c, lang())}${argHint(c)}${sep()}${t(`slash.desc.${c.nom}`)}`),
    t("slash.help_note")].join("\n");
}

function errorText(a) {
  const c = commande(a.nom);
  const nom = c ? nomAffiche(c, lang()) : a.inconnue;
  if (a.code === "choix") return t("slash.err.choix", { nom, choix: c.valeurs.join(", ") });
  return t(`slash.err.${a.code}`, { nom });
}

// Mode d'usage déjà en place : le cœur ne publie rien (sa notice ne part qu'au changement), on le dit ici.
const USAGE_DEJA = { silence: ["silencieux", "slash.already.silence"], vocal: ["conversation", "slash.already.vocal"],
  dictee: ["dictee", "slash.already.dictee"] };

/**
 * Appelée par l'envoi (Entrée, bouton Envoyer) avec le texte TEL QUE TAPÉ. Rend null (pas une commande : le texte
 * part comme d'habitude), { send } (/local, /claude : le texte à envoyer à la place) ou { done } (commande
 * traitée ou refusée : rien ne part ; `ok` dit si la barre peut être vidée).
 */
export async function runSlash(raw) {
  const a = analyser(raw);
  if (!a) return null;
  close();
  if (a.code) { deps.toast(errorText(a), "warning"); return { done: true, ok: false }; }
  const send = texteRoutage(a);
  if (send) return { send };
  if (a.nom === "aide") { deps.note(helpText(), helpNode()); return { done: true, ok: true }; }
  if (a.nom === "stop") {
    const r = await deps.stop();
    if (r?.ok === false) { deps.toast(tx(r.error) || t("stop.failed"), "error"); return { done: true, ok: false }; }
    deps.toast(t("slash.done.stop"), "info");
    return { done: true, ok: true };
  }
  if (a.nom === "tout-arreter") return { done: true, ok: await deps.stopAll() };
  const before = deps.usage();
  const r = await deps.command("chat_commande", { nom: a.nom, arg: a.arg, channel: deps.channel() });
  if (r?.ok === false) {
    if (r.stop_tout) deps.offerStopAll(r.error); // /horsligne refusé : une tâche tourne, « Tout arrêter » proposé
    // HUD rechargé sur le nouveau code, cœur pas encore relancé : il ne connaît pas `chat_commande`
    else if (/^commande inconnue : chat_commande/.test(String(r.error || ""))) deps.toast(t("slash.core_old"), "warning");
    else deps.toast(tx(r.error) || t("common.refused"), "error");
    return { done: true, ok: false };
  }
  if (a.nom === "clear" && r?.vider) deps.clearView(deps.channel());
  // Les autres disent leur effet par la notice du cœur (nouvelle conversation, modèle, mode « rien ne sort »).
  if (a.nom === "mode") deps.toast(t("engine.set", { mode: t(`engine.label.${a.arg}`) }));
  else if (r?.deja && r.text) deps.toast(tx(r.text));
  else if (USAGE_DEJA[a.nom] && before === USAGE_DEJA[a.nom][0]) deps.toast(t(USAGE_DEJA[a.nom][1]));
  return { done: true, ok: true };
}

/** `d` : { command, toast, stop, stopAll, offerStopAll, clearView, note, channel, fit, submit, composing, usage }. */
export function initCommandes(d) {
  deps = d;
  list = el("div", "slash-menu");
  list.id = "slash-menu";
  list.hidden = true;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", t("slash.menu_title"));
  document.body.append(list);
  const a = ask();
  a.setAttribute("aria-controls", "slash-menu");
  a.setAttribute("aria-autocomplete", "list");
  a.addEventListener("input", render);
  a.addEventListener("click", render);
  a.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== ask()) close(); }, 0));
  a.addEventListener("compositionend", () => setTimeout(render, 0));
  document.addEventListener("keydown", onKey, true); // avant ceux de la barre (Entrée envoie, ↑ reprend)
  addEventListener("resize", () => { if (isOpen()) place(); });
}

export const slashOpen = isOpen;
