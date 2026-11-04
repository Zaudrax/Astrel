// Icônes vectorielles du HUD (15/10, demande de l'utilisateur : les émojis faisaient
// « généré par IA »). Trait fin sur une grille de 24 (bouts ronds), couleur héritée
// (currentColor) : les couleurs d'état du thème s'appliquent telles quelles. Taille
// réglée par la classe `.ico` (styles.css). Tracés dans l'esprit de Lucide (ISC).
//
// Sûreté : les tracés sont des CONSTANTES du code ; chaque nœud est créé par
// createElementNS et ses attributs posés un à un, jamais d'innerHTML. Aucun texte
// venu du serveur ne passe par ici, sauf dans `stripEmoji`, qui ne fait qu'en RETIRER
// des caractères (le résultat est ensuite posé en textContent par l'appelant).

const NS = "http://www.w3.org/2000/svg";

// Une chaîne seule = <path d="…"> ; sinon [balise, attributs].
const ICONS = {
  mic: ["M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z", "M19 10v2a7 7 0 0 1-14 0v-2", "M12 19v3"],
  "mic-off": ["M2 2l20 20", "M18.89 13.23A7.12 7.12 0 0 0 19 12v-2", "M5 10v2a7 7 0 0 0 12 5",
    "M15 9.34V5a3 3 0 0 0-5.68-1.33", "M9 9v3a3 3 0 0 0 5.12 2.12", "M12 19v3"],
  volume: ["M11 5 6 9H2v6h4l5 4V5Z", "M15.54 8.46a5 5 0 0 1 0 7.07", "M19.07 4.93a10 10 0 0 1 0 14.14"],
  "volume-off": ["M11 5 6 9H2v6h4l5 4V5Z", "M22 9l-6 6", "M16 9l6 6"],
  // 16/10 : micro ET voix coupés (haut-parleur en haut à gauche, micro en bas à droite, barrés)
  "mute-all": ["M9 3 5.5 6H3v5h2.5L9 14V3Z", "M17 8a2 2 0 0 0-2 2v4a2 2 0 0 0 4 0v-4a2 2 0 0 0-2-2Z",
    "M21.5 13.5a4.5 4.5 0 0 1-9 0", "M17 18v4", "M2 2l20 20"],
  zap: ["M13 2 3 14h9l-1 8 10-12h-9l1-8Z"],
  lock: [["rect", { x: 3, y: 11, width: 18, height: 11, rx: 2 }], "M7 11V7a5 5 0 0 1 10 0v4"],
  unlock: [["rect", { x: 3, y: 11, width: 18, height: 11, rx: 2 }], "M7 11V7a5 5 0 0 1 9.9-1"],
  target: [["circle", { cx: 12, cy: 12, r: 10 }], ["circle", { cx: 12, cy: 12, r: 6 }], ["circle", { cx: 12, cy: 12, r: 2 }]],
  "no-entry": ["M7.86 2h8.28L22 7.86v8.28L16.14 22H7.86L2 16.14V7.86L7.86 2Z", "M8 12h8"],
  power: ["M12 2v10", "M18.4 6.6a9 9 0 1 1-12.77.04"],
  scan: ["M3 7V5a2 2 0 0 1 2-2h2", "M17 3h2a2 2 0 0 1 2 2v2", "M21 17v2a2 2 0 0 1-2 2h-2", "M7 21H5a2 2 0 0 1-2-2v-2",
    ["circle", { cx: 12, cy: 12, r: 3 }]],
  orbit: ["M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8", "M21 3v5h-5"],
  settings: ["M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38"
    + "a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73"
    + "l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25"
    + "a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74"
    + "l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z",
  ["circle", { cx: 12, cy: 12, r: 3 }]],
  alert: ["M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z", "M12 9v4", "M12 17h.01"],
  pause: [["rect", { x: 6, y: 4, width: 4, height: 16, rx: 1 }], ["rect", { x: 14, y: 4, width: 4, height: 16, rx: 1 }]],
  play: ["M6 3l14 9-14 9V3Z"],
  stop: [["rect", { x: 5, y: 5, width: 14, height: 14, rx: 2 }]],
  brain: ["M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z",
    "M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z",
    "M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4", "M17.6 6.5a3 3 0 0 0 .4-1.38", "M6 5.12a3 3 0 0 0 .4 1.38",
    "M3.48 10.9a4 4 0 0 1 .58-.4", "M19.94 10.5a4 4 0 0 1 .58.4", "M6 18a4 4 0 0 1-1.97-.52", "M19.97 17.48A4 4 0 0 1 18 18"],
  waves: ["M2 10v3", "M6 6v11", "M10 3v18", "M14 8v7", "M18 5v13", "M22 10v3"],
  listen: [["circle", { cx: 12, cy: 12, r: 2 }], "M16.24 7.76a6 6 0 0 1 0 8.49", "M7.76 16.24a6 6 0 0 1 0-8.49",
    "M19.07 4.93a10 10 0 0 1 0 14.14", "M4.93 19.07a10 10 0 0 1 0-14.14"],
  message: ["M7.9 20A9 9 0 1 0 4 16.1L2 22Z"],
  pen: ["M12 20h9", "M16.38 3.62a1 1 0 0 1 3 3L7.37 18.64a2 2 0 0 1-.86.5l-2.87.84a.5.5 0 0 1-.62-.62l.84-2.87a2 2 0 0 1 .5-.85Z"],
  clip: ["m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"],
  image: [["rect", { x: 3, y: 3, width: 18, height: 18, rx: 2 }], ["circle", { cx: 9, cy: 9, r: 2 }],
    "m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"],
  keyboard: [["rect", { x: 2, y: 4, width: 20, height: 16, rx: 2 }],
    "M6 8h.01", "M10 8h.01", "M14 8h.01", "M18 8h.01", "M8 12h.01", "M12 12h.01", "M16 12h.01", "M7 16h10"],
  "new-chat": ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z", "M12 7v6", "M9 10h6"],
  activity: ["M22 12h-4l-3 9L9 3l-3 9H2"],
  "arrow-down": ["M12 5v14", "m19 12-7 7-7-7"],
  x: ["M18 6 6 18", "M6 6l12 12"],
  check: ["M20 6 9 17l-5-5"],
  help: [["circle", { cx: 12, cy: 12, r: 10 }], "M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3", "M12 17h.01"],
  copy: [["rect", { x: 8, y: 8, width: 14, height: 14, rx: 2 }], "M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"],
  // 04/11 : lien du fil, ouvert dans le navigateur (markdown.js)
  "external-link": ["M15 3h6v6", "M10 14 21 3", "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"],
  bot: ["M12 8V4H8", ["rect", { x: 4, y: 8, width: 16, height: 12, rx: 2 }], "M2 14h2", "M20 14h2", "M15 13v2", "M9 13v2"],
  terminal: ["m4 17 6-6-6-6", "M12 19h8"],
  timer: ["M10 2h4", "M12 14l3-3", ["circle", { cx: 12, cy: 14, r: 8 }]],
  cloud: ["M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"],
  user: ["M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2", ["circle", { cx: 12, cy: 7, r: 4 }]],
  "user-x": ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", ["circle", { cx: 9, cy: 7, r: 4 }], "m17 8 5 5", "m22 8-5 5"],
  "thumbs-up": ["M7 10v12", "M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8"
    + "a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"],
  "thumbs-down": ["M17 14V2", "M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8"
    + "a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"],
  hand: ["M18 11V6a2 2 0 0 0-4 0", "M14 10V4a2 2 0 0 0-4 0v2", "M10 10.5V6a2 2 0 0 0-4 0v8",
    "M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"],
  pointer: ["M22 14a8 8 0 0 1-8 8", "M18 11v-1a2 2 0 0 0-4 0", "M14 10V9a2 2 0 0 0-4 0v1", "M10 9.5V4a2 2 0 0 0-4 0v10",
    "M18 11a2 2 0 1 1 4 0v3a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"],
  // 15/10 : page « Plans » (plan à relire, demande de révision)
  plans: [["rect", { x: 8, y: 2, width: 8, height: 4, rx: 1 }],
    "M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2", "M12 11h4", "M12 16h4", "M8 11h.01", "M8 16h.01"],
  refresh: ["M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8", "M21 3v5h-5",
    "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16", "M8 16H3v5"],
  // 19/10 : page « Réglages » (une icône par section et par réglage important)
  chevron: ["m9 18 6-6-6-6"],
  rocket: ["M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09Z",
    "m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2Z",
    "M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0", "M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"],
  shield: ["M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72"
    + "a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1Z"],
  "shield-check": ["M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72"
    + "a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1Z", "m9 12 2 2 4-4"],
  download: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", "m7 10 5 5 5-5", "M12 15V3"],
  monitor: [["rect", { x: 2, y: 3, width: 20, height: 14, rx: 2 }], "M8 21h8", "M12 17v4"],
  smartphone: [["rect", { x: 5, y: 2, width: 14, height: 20, rx: 2 }], "M12 18h.01"],
  globe: [["circle", { cx: 12, cy: 12, r: 10 }], "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20", "M2 12h20"],
  plug: ["M12 22v-5", "M9 8V2", "M15 8V2", "M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"],
  sparkles: ["M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14"
    + "a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.14 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44"
    + "l-1.58 6.14a.5.5 0 0 1-.96 0Z", "M20 3v4", "M22 5h-4"],
  puzzle: ["M15.39 4.39a1 1 0 0 0 1.68-.47 2.5 2.5 0 1 1 3.01 3.01 1 1 0 0 0-.47 1.68l1.68 1.68a2.41 2.41 0 0 1 0 3.42"
    + "l-1.68 1.68a1 1 0 0 1-1.68-.47 2.5 2.5 0 1 0-3.01 3.01 1 1 0 0 1 .47 1.68l-1.68 1.68a2.41 2.41 0 0 1-3.42 0"
    + "l-1.68-1.68a1 1 0 0 0-1.68.47 2.5 2.5 0 1 1-3.01-3.01 1 1 0 0 0 .47-1.68l-1.68-1.68a2.41 2.41 0 0 1 0-3.42"
    + "l1.68-1.68a1 1 0 0 1 1.68.47 2.5 2.5 0 1 0 3.01-3.01 1 1 0 0 1-.47-1.68l1.68-1.68a2.41 2.41 0 0 1 3.42 0Z"],
  bell: ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  gauge: ["m12 14 4-4", "M3.34 19a10 10 0 1 1 17.32 0"],
  clock: [["circle", { cx: 12, cy: 12, r: 10 }], "M12 6v6l4 2"],
  languages: ["m5 8 6 6", "m4 14 6-6 2-3", "M2 5h12", "M7 2h1", "m22 22-5-10-5 10", "M14 18h6"],
  eye: ["M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0",
    ["circle", { cx: 12, cy: 12, r: 3 }]],
  history: ["M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8", "M3 3v5h5", "M12 7v5l4 2"],
  database: ["M3 5a9 3 0 1 0 18 0a9 3 0 1 0-18 0", "M3 5v14a9 3 0 0 0 18 0V5", "M3 12a9 3 0 0 0 18 0"],
  layers: ["M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z",
    "m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65", "m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"],
  book: ["M12 7v14", "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6"
    + "a3 3 0 0 0-3 3 3 3 0 0 0-3-3Z"],
  trash: ["M3 6h18", "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6", "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"],
  // 21/10 (plan P17) : Réglages › Apparence (thème, clair ou sombre, accent)
  palette: [["circle", { cx: 13.5, cy: 6.5, r: 0.5 }], ["circle", { cx: 17.5, cy: 10.5, r: 0.5 }],
    ["circle", { cx: 8.5, cy: 7.5, r: 0.5 }], ["circle", { cx: 6.5, cy: 12.5, r: 0.5 }],
    "M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.93 0 1.65-.75 1.65-1.69 0-.44-.18-.84-.44-1.13-.29-.29-.44-.65-.44-1.13"
    + "a1.64 1.64 0 0 1 1.67-1.67h2c3.05 0 5.55-2.5 5.55-5.55C21.97 6.01 17.46 2 12 2Z"],
  "sun-moon": ["M12 8a2.83 2.83 0 0 0 4 4 4 4 0 1 1-4-4", "M12 2v2", "M12 20v2", "m4.9 4.9 1.4 1.4", "m17.7 17.7 1.4 1.4",
    "M2 12h2", "M20 12h2", "m6.3 17.7-1.4 1.4", "m19.1 4.9-1.4 1.4"],
  droplet: ["M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7Z"],
};

export const ICON_NAMES = Object.freeze(Object.keys(ICONS));

const BASE = {
  viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.75",
  "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false",
};

function setAttrs(node, attrs) {
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
}

/**
 * Élément <svg> de l'icône `name` (classe `ico`, plus `cls`). `lead` : icône posée
 * DEVANT un texte dans un conteneur sans `gap` (marge à droite).
 */
export function icon(name, cls = "", doc = globalThis.document) {
  const spec = ICONS[name];
  if (!spec) throw new Error(`icône inconnue : ${name}`);
  const svg = doc.createElementNS(NS, "svg");
  setAttrs(svg, BASE);
  svg.setAttribute("class", cls ? `ico ${cls}` : "ico");
  svg.setAttribute("data-icon", name);
  for (const part of spec) {
    const [tag, attrs] = typeof part === "string" ? ["path", { d: part }] : part;
    const e = doc.createElementNS(NS, tag);
    setAttrs(e, attrs);
    svg.append(e);
  }
  return svg;
}

/** Change l'icône de tête d'un élément (sans toucher au reste). */
export function setIcon(target, name, cls = "") {
  const old = [...target.children].find((c) => c.classList.contains("ico"));
  if (old?.getAttribute("data-icon") === name) return;
  const ico = icon(name, cls || (old ? [...old.classList].filter((c) => c !== "ico").join(" ") : ""));
  if (old) old.replaceWith(ico); else target.prepend(ico);
}

/** Remplace tout le contenu : icône puis texte (en nœud texte, jamais interprété). */
export function setLabel(target, name, text, cls = "") {
  target.replaceChildren(icon(name, cls), document.createTextNode(text));
}

/**
 * Les marques du HTML statique (`<i data-icon="mic" class="lead"></i>`) deviennent
 * des <svg> ; leurs autres classes sont gardées.
 */
export function hydrateIcons(root = document) {
  for (const m of root.querySelectorAll("i[data-icon]")) {
    const extra = [...m.classList].filter((c) => c !== "ico").join(" ");
    m.replaceWith(icon(m.dataset.icon, extra));
  }
}

// ------------------------------------------------------------------ texte venu du serveur

// Émoji AFFICHÉ en couleur : un caractère de présentation émoji par défaut (cible
// U+1F3AF, coche verte U+2705, haut-parleur U+1F50A, drapeaux…) ou un pictogramme suivi
// du sélecteur U+FE0F (triangle U+26A0 + FE0F), avec ses modificateurs (teint, FE0F,
// keycap) et ses séquences ZWJ. Les symboles de texte monochromes (copyright, marque,
// flèche U+2192, coche U+2713, triangle U+25B6, U+26A0 sans FE0F) restent : ils ne
// sont pas « émoji » à l'écran. (Pas d'émoji en clair dans ce fichier : contrôlé.)
const EMOJI_CORE = "(?:\\p{Emoji_Presentation}|\\p{Extended_Pictographic}\\uFE0F)(?:\\p{Emoji_Modifier}|\\uFE0F|\\u20E3)*";
const EMOJI = new RegExp(`([ \\t]?)${EMOJI_CORE}(?:\\u200D(?:\\p{Extended_Pictographic}|\\p{Emoji_Presentation})(?:\\p{Emoji_Modifier}|\\uFE0F)*)*([ \\t]?)`, "gu");
const KEYCAP = new RegExp("([#*0-9])\\uFE0F?\\u20E3", "gu"); // « 1 » + FE0F + 20E3 : garde le chiffre
const MAYBE = new RegExp("[\\u231A-\\u{1FAFF}\\uFE0F\\u20E3]", "u"); // rien de tel : chemin rapide

/**
 * Retire les émojis d'un texte à AFFICHER (réponses, notices, titres de tâche).
 * Un seul espace autour est absorbé (« Fait [émoji]. » → « Fait. », « [émoji] Mission »
 * → « Mission ») ; les sauts de ligne et l'indentation ne bougent pas (markdown).
 * Le texte brut gardé ailleurs (copie, lecture à voix haute) n'est pas touché.
 */
export function stripEmoji(text) {
  const s = String(text ?? "");
  if (!MAYBE.test(s)) return s;
  return s.replace(KEYCAP, "$1").replace(EMOJI, (_m, before, after) => (before && after ? before : ""));
}
