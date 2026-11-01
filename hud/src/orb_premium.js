// Orbe du HUD, thème Premium : la « Constellation » (28/10, demande de l'utilisateur : « sur le HUD c'est
// toujours l'ancienne bulle »). PORTAGE en canvas 2D du dessin de la bulle flottante,
// astrel/bubble_premium.py (`draw_state`, `draw`, `_contour`), mêmes formes, tailles, durées et
// couleurs ; il remplace le disque de verre + anneaux et points du 21/10 (plan P17).
//   repos : étoile à quatre branches couleur de l'encre, qui respire (4 s, ±2 %) ;
//   écoute : étoile d'encre + quatre petites étoiles d'accent allumées une à une par le niveau ;
//   réflexion, travail, démarrage : cercle de huit étoiles qui tourne (3 s le tour, 4 s hors
//     réflexion), la tête brille, la traîne s'éteint ; petite étoile d'encre au centre ;
//   parole : sept barres arrondies qui suivent le niveau ; confirmation : étoile et anneau ambre qui
//     pulsent (2 s) ; micro coupé : étoile grise barrée (trait détouré).
// SANS disque de fond, comme la bulle : sur le panneau du HUD (fond uni connu, sombre ou clair), encre,
// accent, ambre et gris passent le contraste sans verre. Liseré fin de la teinte contraire autour des
// formes (`contour`) et lueur douce sous les étoiles allumées, en sombre seulement.
// Changement d'état : fondu de 300 ms. prefers-reduced-motion : rien ne tourne, ne respire ni ne suit
// le niveau (temps et niveau figés à 0, comme la bulle quand GNOME coupe les animations) ; le fondu reste.

const TAU = Math.PI * 2;
const FADE_MS = 300;
const DISC = 0.78;   // rayon de référence / demi-côté (DISC de bubble_premium.py)
const WHITE = [255, 255, 255];

// écoute : quatre petites étoiles en diagonale (dx, dy, taille), en fraction du rayon de référence
const SPOTS = [[-0.48, -0.40, 0.16], [0.47, -0.30, 0.12], [0.40, 0.44, 0.17], [-0.42, 0.40, 0.11]];
// parole : enveloppe des sept barres (la plus haute au centre)
const ENVELOPE = [0.42, 0.66, 0.86, 1.0, 0.86, 0.66, 0.42];
// contour : huit directions
const AROUND = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];

// Look par défaut (Premium sombre), le temps que setPalette donne celui du thème.
const DEFAULT_LOOK = { ink: "#f5f5f7", dark: true, edge: "0, 0, 0", edgeAlpha: 0.40 };

export class PremiumOrb {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.state = "sleeping";
    this.layers = new Map([["sleeping", 1]]);
    this.level = 0;
    this.target = 0;
    this.colors = {};
    this.glass = { ...DEFAULT_LOOK };
    this.active = false;
    this.loop = 0;       // jeton de la boucle de dessin en cours (setActive)
    this.calm = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
    this.last = 0;
    this.t0 = performance.now();
    this.buf = null;     // toiles hors écran (motif, formes sans lueur, liseré, calque d'un état en fondu)
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.resize();
  }

  /** Couleurs du thème : `states` = { état: "#rrggbb" } ; `glass` = premiumGlass(mode) de theme.js
   *  (encre `ink`, `dark`, liseré `edge`/`edgeAlpha` ; `fill`/`line`/`shade` ne servent plus). */
  setPalette(states, glass) {
    this.colors = { ...states };
    if (glass) this.glass = { ...this.glass, ...glass };
    this.draw();
  }

  // Jeton de boucle (28/10) : chaque bascule le change, une image d'une boucle périmée s'arrête.
  // Sans lui, setActive(true), (false), (true) dans la même image empilait deux boucles de dessin.
  setActive(on) {
    if (on === this.active) return;
    this.active = on;
    const loop = ++this.loop;
    if (on) { this.last = performance.now(); requestAnimationFrame((n) => this.frame(n, loop)); }
  }

  setState(name) {
    if (name === this.state) return;
    this.state = name;
    if (!this.layers.has(name)) this.layers.set(name, 0);
  }

  setLevel(v) { this.target = Math.max(0, Math.min(1, Number(v) || 0)); }

  resize() {
    const r = Math.min(devicePixelRatio || 1, 2);
    const { clientWidth: w, clientHeight: h } = this.canvas;
    if (!w || !h) return;
    this.canvas.width = Math.round(w * r);
    this.canvas.height = Math.round(h * r);
    this.ctx.setTransform(r, 0, 0, r, 0, 0);
    this.draw();
  }

  frame(now, loop = this.loop) {
    if (!this.active || loop !== this.loop) return;
    // l'horodatage d'une image peut précéder performance.now() pris dans setActive : jamais négatif
    const dt = Math.max(0, Math.min(100, now - this.last));
    this.last = now;
    const step = dt / FADE_MS;
    for (const [s, w] of this.layers) {
      if (s === this.state) { this.layers.set(s, Math.min(1, w + step)); continue; }
      const nw = w - step;
      if (nw <= 0) this.layers.delete(s); else this.layers.set(s, nw);
    }
    if (!this.layers.has(this.state)) this.layers.set(this.state, 0);
    // montée vive, descente douce (comme la bulle, bubble.py)
    const k = this.target > this.level ? 0.45 : 0.12;
    this.level += (this.target - this.level) * k;
    this.draw(now);
    requestAnimationFrame((n) => this.frame(n, loop));
  }

  /** Toiles hors écran à la taille de la toile (pixels réels) ; null sans OffscreenCanvas ni DOM
   *  (tests en Node) : on dessine alors directement, sans liseré. */
  buffers() {
    const W = this.canvas.width, H = this.canvas.height;
    if (this.buf && this.buf.W === W && this.buf.H === H) return this.buf;
    const make = () => {
      let c = null;
      if (typeof OffscreenCanvas === "function") c = new OffscreenCanvas(W, H);
      else if (typeof document !== "undefined" && typeof document.createElement === "function") {
        c = document.createElement("canvas");
        if (!c || typeof c.getContext !== "function") return null;
        c.width = W; c.height = H;
      }
      const ctx = c && c.getContext("2d");
      return ctx ? { c, ctx } : null;
    };
    const motif = make(), shapes = make(), edge = make(), layer = make();
    this.buf = motif && shapes && edge && layer ? { W, H, motif, shapes, edge, layer } : null;
    if (!this.buf) this.buf = { W, H, none: true };
    return this.buf;
  }

  draw(now = performance.now()) {
    const ctx = this.ctx;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    const calm = this.calm.matches;
    const t = calm ? 0 : (now - this.t0) / 1000;
    const lvl = calm ? 0 : this.level;
    const R = Math.min(w, h) / 2;
    const cx = w / 2, cy = h / 2;
    const look = this.glass;
    const layers = [...this.layers].filter(([, a]) => a > 0);
    const color = (s) => this.colors[s] || this.colors.sleeping || "#0a84ff";
    ctx.clearRect(0, 0, w, h);
    const b = this.buffers();
    if (b.none) {   // repli : dessin direct, fondu par l'opacité globale, sans liseré
      for (const [s, a] of layers) {
        ctx.save();
        ctx.globalAlpha = Math.min(1, a);
        drawState(ctx, s, cx, cy, R, t, lvl, color(s), look);
        ctx.restore();
      }
      return;
    }
    const scale = this.canvas.width / w;
    // Le liseré se calcule en pixels CSS (`sc` = 1, 4 fois moins de pixels qu'en ×2, mesuré : seize
    // recopies par image) puis s'étire sous le motif, peint, lui, en pixels réels (`scale`). Tout se
    // passe dans le carré qui contient les motifs : 0,85 r au plus loin d'un axe (tête de la ronde et
    // sa lueur) + la place du liseré.
    const ec = Math.min(1, scale);
    const d = edgeStep(R * ec);
    const boxAt = (sc) => {
      const ext = Math.ceil(R * DISC * 0.92 * sc + 2 * d + 2);
      const X = Math.max(0, Math.floor(cx * sc - ext)), Y = Math.max(0, Math.floor(cy * sc - ext));
      return { x: X, y: Y, w: Math.min(Math.round(w * sc), Math.ceil(cx * sc + ext)) - X,
               h: Math.min(Math.round(h * sc), Math.ceil(cy * sc + ext)) - Y };
    };
    const box = boxAt(scale);
    const reset = (c) => { c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha = 1; c.globalCompositeOperation = "source-over"; };
    // comme `paint` de bubble_premium.draw : chaque état en fondu passe par son propre calque (un
    // groupe), sinon l'opacité s'appliquerait forme par forme (lueur et étoile se cumuleraient)
    const paint = (into, lk, sc, bx) => {
      reset(into.ctx);
      into.ctx.clearRect(bx.x, bx.y, bx.w, bx.h);
      for (const [s, a] of layers) {
        if (a >= 1) {
          into.ctx.setTransform(sc, 0, 0, sc, 0, 0);
          drawState(into.ctx, s, cx, cy, R, t, lvl, color(s), lk);
          continue;
        }
        const l = b.layer.ctx;
        reset(l);
        l.clearRect(bx.x, bx.y, bx.w, bx.h);
        l.setTransform(sc, 0, 0, sc, 0, 0);
        drawState(l, s, cx, cy, R, t, lvl, color(s), lk);
        reset(into.ctx);
        into.ctx.globalAlpha = Math.max(0, Math.min(1, a));
        blit(into.ctx, b.layer.c, bx);
      }
      reset(into.ctx);
    };
    paint(b.motif, look, scale, box);
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    // le liseré suit les FORMES sans les lueurs (sinon une lueur devient une tache sombre) : masque
    // tiré d'un rendu sans lueur, peint directement dans la teinte du liseré (`mono` : même opacité
    // forme par forme, sans passe « source-in », mesurée lente), puis le vrai motif par-dessus
    if ((Number(look.edgeAlpha) || 0) > 0) {
      const ebox = boxAt(ec);
      paint(b.shapes, { ...look, dark: false, mono: look.edge || "0, 0, 0" }, ec, ebox);
      contour(ctx, b, look, d, ebox, scale / ec);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    blit(ctx, b.motif.c, box);
    ctx.restore();
  }
}

/** Pas du liseré en pixels pour un rayon `R` (`_contour` : 1 px pour la bulle de 148 px ; 2 px CSS
 *  pour l'orbe de 260 px, 1 pour celle de 110 px de l'affichage Simple). */
function edgeStep(R) { return Math.max(1, Math.round(R * 0.014)); }

/** Recopie le carré `box` d'une toile hors écran au même endroit (décalé de dx, dy). */
function blit(ctx, src, box, dx = 0, dy = 0) {
  ctx.drawImage(src, box.x, box.y, box.w, box.h, box.x + dx, box.y + dy, box.w, box.h);
}

/** Liseré de lisibilité (`_contour` de la bulle) : le masque des formes (déjà dans la teinte
 *  contraire, toile `shapes`) décalé dans 8 directions sur deux rayons de `d` px ; anneau extérieur
 *  plus léger. Chaque rayon est réuni dans la toile `edge` puis posé d'un coup, étiré de `k`
 *  (pixels réels / pixels du masque). */
function contour(ctx, b, look, d, box, k) {
  const alpha = Number(look.edgeAlpha) || 0;
  const e = b.edge.ctx;
  for (const [n, a] of [[1, alpha], [2, alpha * 0.45]]) {
    e.setTransform(1, 0, 0, 1, 0, 0);
    e.globalAlpha = 1;
    e.globalCompositeOperation = "source-over";
    e.clearRect(box.x, box.y, box.w, box.h);
    for (const [dx, dy] of AROUND) blit(e, b.shapes.c, box, dx * d * n, dy * d * n);
    ctx.globalAlpha = a;
    ctx.drawImage(b.edge.c, box.x, box.y, box.w, box.h, box.x * k, box.y * k, box.w * k, box.h * k);
  }
}

// ------------------------------------------------------------------ couleurs

/** « #rgb », « #rrggbb », « r, g, b », « rgb(…) » / « rgba(…) » ou [r, g, b] → [r, g, b] (0..255). */
export function rgbOf(c) {
  if (Array.isArray(c)) return c.slice(0, 3).map(Number);
  const s = String(c || "").trim();
  if (s[0] === "#") {
    const h = s.length === 4 ? [...s.slice(1)].map((x) => x + x).join("") : s.slice(1, 7);
    const n = parseInt(h, 16);
    if (h.length === 6 && !Number.isNaN(n)) return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const m = s.match(/-?[\d.]+/g);
  if (m && m.length >= 3) return m.slice(0, 3).map(Number);
  return [10, 132, 255];
}

function css(rgb, a = 1) {
  return `rgba(${Math.round(rgb[0])}, ${Math.round(rgb[1])}, ${Math.round(rgb[2])}, ${clamp(a)})`;
}

function clamp(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }

function mix(a, b, k) { return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]; }

// ------------------------------------------------------------------ formes (celles de bubble_premium.py)

/** Étoile à quatre branches de demi-envergure `a` : côtés concaves (Bézier vers le centre). */
function star(ctx, x, y, a, rgb, alpha = 1, pinch = 0.20) {
  if (a <= 0.2 || alpha <= 0) return;
  const k = a * pinch;
  ctx.beginPath();
  ctx.moveTo(x, y - a);
  ctx.bezierCurveTo(x, y - k, x + k, y, x + a, y);
  ctx.bezierCurveTo(x + k, y, x, y + k, x, y + a);
  ctx.bezierCurveTo(x, y + k, x - k, y, x - a, y);
  ctx.bezierCurveTo(x - k, y, x, y - k, x, y - a);
  ctx.closePath();
  ctx.fillStyle = css(rgb, alpha);
  ctx.fill();
}

/** Lueur sous une étoile (sombre seulement) : dégradé radial qui s'efface, pas de flou. */
function glow(ctx, x, y, rad, rgb, alpha) {
  if (rad <= 0.2 || alpha <= 0) return;
  const g = ctx.createRadialGradient(x, y, 0, x, y, rad);
  g.addColorStop(0, css(rgb, alpha));
  g.addColorStop(1, css(rgb, 0));
  ctx.beginPath();
  ctx.fillStyle = g;
  ctx.arc(x, y, rad, 0, TAU);
  ctx.fill();
}

function ring(ctx, cx, cy, r, rgb, width, alpha = 1) {
  ctx.beginPath();
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.strokeStyle = css(rgb, alpha);
  ctx.arc(cx, cy, r, 0, TAU);
  ctx.stroke();
}

function slash(ctx, cx, cy, d, rgb, width) {
  ctx.beginPath();
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.strokeStyle = css(rgb, 1);
  ctx.moveTo(cx - d, cy + d);
  ctx.lineTo(cx + d, cy - d);
  ctx.stroke();
}

/** Motif d'un état (rayon `R` = demi-côté), dans SA couleur ; `look` = { ink, dark } (exporté pour
 *  les tests du dessin). Même calcul que `draw_state` de astrel/bubble_premium.py. */
export function drawState(ctx, s, cx, cy, R, t, lvl, color, look = DEFAULT_LOOK) {
  const r = R * DISC;
  // `mono` : tout dans une seule teinte (masque du liseré, opacités inchangées)
  const col = rgbOf(look.mono || color);
  const ink = look.mono ? col : rgbOf(look.ink || DEFAULT_LOOK.ink);
  const halo = look.dark !== false;
  lvl = Number.isFinite(lvl) ? clamp(lvl) : 0;     // niveau borné à 0..1 (NaN = silence)
  switch (s) {
    case "off": {
      star(ctx, cx, cy, r * 0.36, col, 0.85);
      // barre détourée (symbole « barré ») : un trait EFFACÉ isole la barre de l'étoile
      const w = Math.max(2, r * 0.05);
      ctx.save();
      ctx.globalCompositeOperation = "destination-out";
      slash(ctx, cx, cy, r * 0.40, [0, 0, 0], w + Math.max(2, r * 0.06));
      ctx.restore();
      slash(ctx, cx, cy, r * 0.40, col, w);
      break;
    }
    case "listening":
      star(ctx, cx, cy, r * 0.30, ink);
      SPOTS.forEach(([dx, dy, sz], i) => {
        const lit = clamp(lvl * 3.2 - i * 0.55);          // allumées une à une quand la voix monte
        const tw = 0.85 + 0.15 * Math.sin(t * 9.0 + i * 2.1);
        const x = cx + dx * r, y = cy + dy * r;
        if (halo) glow(ctx, x, y, r * sz * 1.8, col, 0.30 * lit);
        star(ctx, x, y, r * sz * (0.65 + 0.35 * lit) * tw, col, 0.22 + 0.78 * lit);
      });
      break;
    case "thinking":
    case "working":
    case "booting": {
      const n = 8;
      const a0 = (t * TAU) / (s === "thinking" ? 3.0 : 4.0);
      const rr = r * 0.52;
      for (let i = 0; i < n; i++) {
        const ang = a0 - (i * TAU) / n;
        const trail = 1 - i / n;                          // la tête brille, la traîne s'éteint
        const x = cx + rr * Math.cos(ang), y = cy + rr * Math.sin(ang);
        const sz = r * (0.075 + 0.075 * trail);
        if (halo && i === 0) glow(ctx, x, y, sz * 2.2, col, 0.35);
        star(ctx, x, y, sz, col, 0.18 + 0.82 * trail ** 1.4);
      }
      star(ctx, cx, cy, r * 0.13, ink, 0.55);
      break;
    }
    case "speaking": {
      const gap = r * 0.135, bw = r * 0.078;
      const top = halo ? mix(col, WHITE, 0.35) : col;   // `mono` n'a jamais de lueur (dark faux)
      ctx.lineCap = "round";
      ctx.lineWidth = bw;
      ENVELOPE.forEach((e, i) => {
        const x = cx + (i - 3) * gap;
        const wob = 0.55 + 0.45 * Math.sin(t * TAU * (1.25 + 0.31 * i) + i * 1.7);
        const h = r * (0.10 + 0.62 * lvl * e * wob);
        const lin = ctx.createLinearGradient(0, cy - h / 2, 0, cy + h / 2);
        lin.addColorStop(0, css(top));
        lin.addColorStop(0.5, css(col));
        lin.addColorStop(1, css(top));
        ctx.beginPath();
        ctx.strokeStyle = lin;
        ctx.moveTo(x, cy - h / 2);
        ctx.lineTo(x, cy + h / 2);
        ctx.stroke();
      });
      break;
    }
    case "asking": {
      const pulse = 0.5 + 0.5 * Math.sin((t * TAU) / 2.0);
      if (halo) glow(ctx, cx, cy, r * 0.62, col, 0.10 + 0.12 * pulse);
      star(ctx, cx, cy, r * (0.34 + 0.03 * pulse), col);
      ring(ctx, cx, cy, r * 0.66, col, Math.max(1.5, r * 0.035), 0.30 + 0.55 * pulse);
      break;
    }
    default: { // repos (veille)
      const breath = 0.5 + 0.5 * Math.sin((t * TAU) / 4.0);
      if (halo) glow(ctx, cx, cy, r * 0.62, ink, 0.07 + 0.03 * breath);
      star(ctx, cx, cy, r * 0.40 * (1 + 0.02 * breath), ink);
    }
  }
}
