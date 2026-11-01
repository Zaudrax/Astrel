// Couleurs partagées par l'orbe et l'interface.
// 21/10 (plan P17) : liste et couleurs des thèmes dans config/themes.json (source unique, aussi
// lue par la bulle : astrel/theme.py). 25/10 (plan P35) : Premium est le seul thème du HUD.
// STATE donne les états de l'orbe et leur nom ; ses couleurs (palette « classique » de
// themes.json, gardée pour les icônes) ne servent plus au HUD : l'orbe prend premiumStateColors.

import THEMES from "../../config/themes.json" with { type: "json" };

const STATE_LABELS = {
  booting: "Démarrage", sleeping: "En veille", listening: "J'écoute", thinking: "Je réfléchis",
  working: "J'agis", speaking: "Je parle", asking: "Confirmation", off: "Micro coupé",
};

export const STATE = Object.fromEntries(Object.entries(THEMES.themes.classique.states)
  .map(([name, [color, accent]]) => [name, { label: STATE_LABELS[name], color, accent }]));

/** Couleurs d'un thème dans un mode (« dark »/« light »), accent compris ; même règle que
 *  astrel/theme.py `palette()`. */
export function palette(theme = "premium", mode = "dark", accent = "bleu") {
  const t = THEMES.themes[theme] || THEMES.themes.premium;
  const pal = { ...(t.palettes[mode] || t.palettes.dark || Object.values(t.palettes)[0]) };
  const accents = t.accents || null;
  if (accents) {
    const a = accents[accent] || Object.values(accents)[0];
    const m = mode === "light" ? "light" : "dark";
    pal.accent = a[m];
    pal.accent_ink = a[`${m}_ink`];
  }
  pal.total = THEMES.semantics.total;
  pal.levee = pal[THEMES.semantics.levee];
  return pal;
}

/** Couleur de chaque état de l'orbe Premium (une teinte par état). */
export function premiumStateColors(mode = "dark", accent = "bleu") {
  const pal = palette("premium", mode, accent);
  return Object.fromEntries(Object.entries(THEMES.themes.premium.states).map(([s, ref]) => [s, pal[ref]]));
}

/** Look de l'orbe Premium « Constellation » (même formule que `look()` de astrel/bubble_premium.py) :
 *  encre de l'étoile du repos, lueur en sombre seulement, liseré de la teinte contraire (noir en
 *  sombre, blanc en clair). 28/10 : l'orbe n'a plus de disque de verre ; `fill`/`line`/`shade` restent
 *  pour qui lirait encore l'ancien verre. */
export function premiumGlass(mode = "dark") {
  const p = palette("premium", mode);
  const light = mode === "light";
  return {
    fill: `rgba(${p.glass}, ${light ? 0.95 : 0.92})`,
    line: `rgba(${p.line}, ${light ? 0.08 : 0.12})`,
    shade: `rgba(0, 0, 0, ${light ? 0.12 : 0.35})`,
    ink: p.ink,
    dark: !light,
    edge: light ? "255, 255, 255" : "0, 0, 0",
    edgeAlpha: light ? 0.55 : 0.40,
  };
}

export const GROUP = {
  core:     { label: "Cœur",        color: "#ffffff", size: 14 },
  brain:    { label: "Cerveau",     color: "#b39dff", size: 9 },
  hub:      { label: "Pôles",       color: "#ffc54d", size: 5 },
  tool:     { label: "Outils",      color: "#4d8dff", size: 2.2 },
  skill:    { label: "Skills",      color: "#27d7f5", size: 2 },
  agent:    { label: "Agents",      color: "#ff78c8", size: 2 },
  mcp:      { label: "Connecteurs", color: "#b36bff", size: 4 },
  mcp_tool: { label: "Outils MCP",  color: "#d9a6ff", size: 1.6 },
  sensor:   { label: "Capteurs",    color: "#34e3a4", size: 3 },
  note:     { label: "Notes",       color: "#3ee08f", size: 1.6 },
  project:  { label: "Projets",     color: "#ff9d3a", size: 3 },
  voice:    { label: "Voix",        color: "#5ae1ff", size: 3 },
};

// 21/10 (plan P17, lot 5) : couleurs de groupe foncées pour le Premium CLAIR (les claires
// disparaissaient sur fond clair) ; groupe absent : sa couleur d'origine.
export const GROUP_LIGHT = {
  core: "#1d1d1f", brain: "#6e4bd8", hub: "#b07400", skill: "#0a84a8", sensor: "#1f7a35",
  note: "#1f8a4c", voice: "#0a84a8", mcp_tool: "#8e5cc8", project: "#c25e00",
};

// Groupes dont le nom reste affiché en permanence (les autres au survol).
export const ALWAYS_LABELED = new Set(["core", "brain", "hub", "mcp", "project", "sensor", "voice"]);
