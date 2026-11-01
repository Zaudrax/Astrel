// Questions à choix du HUD : fonctions PURES (21/10, plan P21, demande de l'utilisateur : répondre
// « comme dans Claude Code », a) b) c), la recommandée en premier, puis « Autre » pour taper).
// Sans DOM : testées par hud/tests/choix.test.mjs ; la carte elle-même est dans main.js.

export const MAX_OPTIONS = 4; // contrat de astrel/choices.py (2 à 4 réponses)

/** Lettre de la i-ème entrée (0 → « a »). */
export function lettre(i) {
  return String.fromCharCode(97 + i);
}

/** Badge affiché devant une entrée (0 → « a) »). */
export function badge(i) {
  return `${lettre(i)})`;
}

/** Réponses de l'évènement `choice` → [{label, recommended, image?}] : 4 au plus, libellés vides
 *  écartés, la recommandée EN PREMIER (sans marque : la première l'est, contrat du cœur). */
export function normaliser(options) {
  const opts = (Array.isArray(options) ? options : [])
    .map((o) => (typeof o === "string" ? { label: o, recommended: false }
      : { label: String(o?.label ?? ""), recommended: !!o?.recommended,
        // 22/10 (plan P26) : image de la réponse (descripteur de medias.js), validée à l'affichage
        ...(o?.image && typeof o.image === "object" ? { image: o.image } : {}) }))
    .filter((o) => o.label.trim())
    .slice(0, MAX_OPTIONS);
  if (opts.length && !opts.some((o) => o.recommended)) opts[0].recommended = true;
  return [...opts.filter((o) => o.recommended), ...opts.filter((o) => !o.recommended)];
}

/** Entrées de la liste, dans l'ordre affiché : les réponses puis « Autre » (si permis),
 *  chacune avec sa lettre. `autre: true` marque la dernière. */
export function entrees(options, allowOther = true) {
  const out = normaliser(options).map((o, i) => ({ ...o, autre: false, lettre: lettre(i) }));
  if (allowOther) out.push({ label: "", recommended: false, autre: true, lettre: lettre(out.length) });
  return out;
}

/** Touche lettre (a, B…) → index d'entrée, ou -1. Modificateurs (sauf Maj) refusés. */
export function indexLettre(e, n) {
  if (!e || e.ctrlKey || e.altKey || e.metaKey || e.isComposing) return -1;
  const key = typeof e.key === "string" ? e.key.toLowerCase() : "";
  if (!/^[a-z]$/.test(key)) return -1;
  const i = key.charCodeAt(0) - 97;
  return i < n ? i : -1;
}

/** Chiffre (code physique Digit1…/Numpad1…, AZERTY compris) → index d'entrée, ou -1. */
export function indexChiffre(e, n) {
  if (!e || e.ctrlKey || e.altKey || e.metaKey || e.repeat || e.isComposing) return -1;
  const m = /^(?:Digit|Numpad)([1-9])$/.exec(String(e.code || ""));
  if (!m) return -1;
  const i = +m[1] - 1;
  return i < n ? i : -1;
}

/** Flèches, Début, Fin dans le groupe de boutons radio → nouvel index (en boucle), ou -1. */
export function deplacer(key, i, n) {
  if (n <= 0) return -1;
  if (key === "ArrowDown" || key === "ArrowRight") return (i + 1 + n) % n;
  if (key === "ArrowUp" || key === "ArrowLeft") return (i - 1 + n) % n;
  if (key === "Home") return 0;
  if (key === "End") return n - 1;
  return -1;
}

/** Entrée dans le champ « Autre » : envoyer (Entrée seule, hors composition IME),
 *  retour à la ligne (Maj+Entrée), revenir à la liste (Échap), sinon rien. */
export function toucheAutre(e) {
  if (!e || e.isComposing || e.keyCode === 229) return "";
  if (e.key === "Escape") return "retour";
  if (e.key !== "Enter") return "";
  return e.shiftKey ? "ligne" : "envoyer";
}
