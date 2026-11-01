// Boîte de dialogue du HUD (15/10, demande de l'utilisateur) : remplace confirm(),
// prompt() et alert() du navigateur (titre « 127.0.0.1:4719 indique », boutons
// bleus hors thème). Asynchrone (Promise), texte TOUJOURS posé en textContent
// (jamais de HTML), Échap = annuler, focus piégé dans la boîte, rendu au
// précédent élément à la fermeture. Une seule boîte à la fois : en ouvrir une
// autre annule la première.

import { icon } from "./icons.js";
import { t } from "./i18n.js";

let current = null; // { close(value) }

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const FOCUSABLE = "button, input, textarea, select, summary, [tabindex]:not([tabindex='-1'])";

/**
 * Ouvre la boîte et rend une Promise :
 * - sans champ : true (bouton principal) ou false (Annuler, Échap, clic hors) ;
 * - avec champs : un objet { nom: valeur } ou null si annulé.
 *
 * opts : title, text (paragraphe), items (liste courte), note (petit texte),
 *        fields [{ name, label, value, placeholder, required, hint,
 *                  type: "number" (min, max, step) ou "password" (saisie masquée), multiline (Entrée = à la ligne,
 *                  Ctrl+Entrée valide) }],
 *        ok (libellé du bouton principal), cancel, danger (bouton rouge),
 *        check (26/10, plan P38 : async (valeurs) → message ; non vide = la boîte RESTE ouverte avec ce
 *               message et le texte tapé, vide = elle se ferme ; bouton principal grisé pendant l'attente,
 *               Échap, Annuler et clic à côté ignorés).
 */
export function openModal(opts = {}) {
  if (current) current.close(undefined);
  const fields = opts.fields || [];
  const back = el("div", "modal-back");
  const box = el("div", `modal${opts.danger ? " danger" : ""}`);
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  const titleId = `modal-title-${Date.now()}`;
  if (opts.title) {
    const h = el("div", "modal-title", opts.title);
    h.id = titleId;
    box.setAttribute("aria-labelledby", titleId);
    box.append(h);
  }
  if (opts.text) box.append(el("p", "modal-text", opts.text));
  if (opts.items?.length) {
    const ul = el("ul", "modal-items");
    for (const it of opts.items) ul.append(el("li", "", it));
    box.append(ul);
  }
  if (opts.note) box.append(el("p", "modal-note", opts.note));

  const inputs = {};
  const form = el("form", "modal-fields");
  for (const f of fields) {
    const label = el("label", "modal-field");
    label.append(el("span", "modal-label", f.label + (f.required ? "" : t("modal.optional"))));
    const input = el(f.multiline ? "textarea" : "input");
    if (f.multiline) input.rows = f.rows || 2;
    // 20/10 (plan P11) : « password » pour une clé API, jamais affichée en clair ni gardée par le HUD.
    else input.type = f.type === "number" ? "number" : f.type === "password" ? "password" : "text";
    if (f.type === "number") {
      for (const k of ["min", "max", "step"]) if (f[k] != null) input[k] = String(f[k]);
      input.inputMode = "numeric";
    }
    input.name = f.name;
    input.value = f.value ?? "";
    input.placeholder = f.placeholder ?? "";
    input.autocomplete = "off";
    input.spellcheck = false;
    if (f.required) input.required = true;
    label.append(input);
    if (f.hint) label.append(el("span", "modal-hint", f.hint));
    form.append(label);
    inputs[f.name] = input;
  }
  if (fields.length) box.append(form);

  const error = el("div", "modal-error hidden");
  box.append(error);
  const actions = el("div", "modal-actions");
  const cancel = el("button", "btn modal-cancel", opts.cancel || t("common.cancel"));
  cancel.type = "button";
  const ok = el("button", `btn ${opts.danger ? "danger strong" : "ok"} modal-ok`, opts.ok || "OK");
  ok.type = "button";
  actions.append(cancel, ok);
  box.append(actions);
  back.append(box);

  const previous = document.activeElement;
  return new Promise((resolve) => {
    const close = (value) => {
      if (current?.close !== close) return;
      current = null;
      back.remove();
      removeEventListener("keydown", onKey, true);
      document.removeEventListener("focusin", trap, true);
      if (previous && document.contains(previous) && typeof previous.focus === "function") previous.focus();
      resolve(value === undefined ? (fields.length ? null : false) : value);
    };
    let checking = false;
    // 26/10 (p38-lot2, fiches 4 et 5) : pendant un `check` en vol, Échap, Annuler et le clic à côté
    // sont ignorés ; sinon la boîte se fermait, l'appelant sortait sans réponse et un refus restait muet.
    const dismiss = () => { if (!checking) close(undefined); };
    const submit = async () => {
      if (checking) return;
      if (!fields.length) { close(true); return; }
      const values = {};
      for (const f of fields) {
        values[f.name] = inputs[f.name].value.trim();
        const bad = (f.required && !values[f.name])
          || (f.type === "number" && values[f.name] && !inputs[f.name].checkValidity());
        if (bad) {
          error.textContent = f.type === "number" && values[f.name]
            ? t("modal.number", { label: f.label, min: f.min ?? "…", max: f.max ?? "…" }) : t("modal.required", { label: f.label });
          error.classList.remove("hidden");
          inputs[f.name].focus();
          return;
        }
      }
      if (opts.check) {
        checking = true;
        ok.disabled = true;
        let msg = "";
        try { msg = String((await opts.check(values)) || ""); } catch (e) { msg = String(e?.message || e || "?"); }
        checking = false;
        ok.disabled = false;
        if (current?.close !== close) return; // fermée pendant l'attente (Échap, autre boîte)
        if (msg) {
          error.textContent = msg;
          error.classList.remove("hidden");
          inputs[fields[0].name].focus();
          return;
        }
      }
      close(values);
    };
    // Écouté en CAPTURE sur la fenêtre : la boîte passe avant les raccourcis du
    // HUD (Entrée/Échap de la carte de confirmation, Alt+chiffre des canaux).
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); dismiss(); return; }
      if (e.key === "Tab") {
        const list = [...box.querySelectorAll(FOCUSABLE)].filter((x) => !x.disabled);
        if (!list.length) return;
        const first = list[0];
        const last = list[list.length - 1];
        const inside = box.contains(document.activeElement);
        if (e.shiftKey && (!inside || document.activeElement === first)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && (!inside || document.activeElement === last)) { e.preventDefault(); first.focus(); }
        e.stopPropagation();
        return;
      }
      const tag = document.activeElement?.tagName;
      // Champ d'une ligne : Entrée valide ; champ multiligne : Entrée passe à la ligne, Ctrl+Entrée valide.
      if (e.key === "Enter" && !e.isComposing && box.contains(document.activeElement)
          && (tag === "INPUT" || (tag === "TEXTAREA" && (e.ctrlKey || e.metaKey)))) {
        e.preventDefault(); e.stopPropagation(); submit(); return;
      }
      // le reste (lettres, Entrée sur un bouton) agit dans la boîte, pas ailleurs
      e.stopPropagation();
    };
    current = { close };
    form.addEventListener("input", () => error.classList.add("hidden"));
    cancel.onclick = dismiss;
    ok.onclick = submit;
    form.onsubmit = (e) => { e.preventDefault(); submit(); };
    back.addEventListener("pointerdown", (e) => { if (e.target === back) dismiss(); });
    // le focus ne sort pas de la boîte, même par un clic à côté
    const trap = (e) => { if (!box.contains(e.target)) (inputs[fields[0]?.name] || cancel).focus(); };
    document.addEventListener("focusin", trap, true);
    addEventListener("keydown", onKey, true);
    document.body.append(back);
    // focus : premier champ, sinon Annuler pour une action dangereuse, sinon le bouton principal
    const first = fields.length ? inputs[fields[0].name] : opts.danger ? cancel : ok;
    first.focus();
    if (fields.length) first.select();
  });
}

// Fenêtre large PERSISTANTE (15/10, page « Réglages ») : elle reste ouverte sous une
// boîte openModal (la confirmation « Appliquer maintenant » s'ouvre PAR-DESSUS) ; c'est
// pourquoi elle a sa propre variable et ne passe pas par `current`.
let panel = null; // { close() }

/**
 * Ouvre une fenêtre large et rend { box, body, close, closed } (`closed` : Promise
 * résolue à la fermeture). Une seule fenêtre de ce type à la fois.
 * opts : title, icon (nom d'icône de icons.js, devant le titre), cls (classe en plus sur la boîte),
 *        onEscape (26/10, plan P38 : () → vrai si Échap a été pris par la fenêtre, un détail qui se
 *        referme par exemple ; sinon Échap ferme la fenêtre).
 * Échap, la croix ou clic à côté ferment ; le focus reste dans la fenêtre et revient à
 * l'élément précédent à la fermeture. Les touches tapées DANS la fenêtre ne
 * remontent pas aux raccourcis du HUD (Alt+chiffre, chiffres des choix, Entrée/Échap
 * de la carte de confirmation).
 */
export function openPanel(opts = {}) {
  if (panel) panel.close();
  const back = el("div", "modal-back panel-back");
  const box = el("div", `modal wide${opts.cls ? ` ${opts.cls}` : ""}`);
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.tabIndex = -1; // un clic sur une zone non focusable garde le focus dans la fenêtre
  const head = el("div", "modal-head");
  const title = el("div", "modal-title", opts.title || "");
  if (opts.icon) title.prepend(icon(opts.icon, "lead")); // 15/10 : icône vectorielle, plus d'émoji
  title.id = `panel-title-${Date.now()}`;
  box.setAttribute("aria-labelledby", title.id);
  const x = el("button", "modal-x");
  x.append(icon("x"));
  x.type = "button";
  x.title = t("common.close_esc");
  x.setAttribute("aria-label", t("common.close"));
  head.append(title, x);
  const body = el("div", "modal-body");
  box.append(head, body);
  back.append(box);

  const previous = document.activeElement;
  let resolveClosed;
  const closed = new Promise((r) => { resolveClosed = r; });
  const visible = (e) => !e.disabled && e.getClientRects().length > 0; // pas dans un groupe replié
  const onKey = (e) => {
    if (current) return; // une boîte openModal par-dessus : c'est elle qui répond
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); if (!opts.onEscape?.()) close(); return; }
    if (e.key === "Tab") {
      const list = [...box.querySelectorAll(FOCUSABLE)].filter(visible);
      e.stopPropagation();
      if (!list.length) { e.preventDefault(); return; }
      const first = list[0];
      const last = list[list.length - 1];
      const inside = box.contains(document.activeElement);
      if (e.shiftKey && (!inside || document.activeElement === first)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (!inside || document.activeElement === last)) { e.preventDefault(); first.focus(); }
      return;
    }
    // Hors de la fenêtre (focus sur la page) : rien ne passe, et Entrée ne doit pas
    // répondre « Oui » à la carte de confirmation du HUD. DANS la fenêtre, on laisse
    // la touche aux champs : elle est arrêtée en remontant (écouteur sur la boîte).
    if (!box.contains(e.target)) {
      e.stopPropagation();
      if (e.key === "Enter") e.preventDefault();
    }
  };
  const stopBubble = (e) => e.stopPropagation();
  const trap = (e) => { if (!current && !box.contains(e.target)) box.focus(); };
  function close() {
    if (panel?.close !== close) return;
    panel = null;
    back.remove();
    removeEventListener("keydown", onKey, true);
    document.removeEventListener("focusin", trap, true);
    if (previous && document.contains(previous) && typeof previous.focus === "function") previous.focus();
    resolveClosed();
  }
  box.addEventListener("keydown", stopBubble);
  x.onclick = close;
  back.addEventListener("pointerdown", (e) => { if (e.target === back) close(); });
  document.addEventListener("focusin", trap, true);
  addEventListener("keydown", onKey, true);
  panel = { close };
  document.body.append(back);
  box.focus();
  return { box, body, close, closed };
}

/** Une boîte est-elle ouverte ? (les raccourcis du HUD s'abstiennent) */
export const modalOpen = () => current !== null || panel !== null;
