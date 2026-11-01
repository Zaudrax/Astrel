// Thème du HUD (21/10, plan P17) : hud/src/apparence.js et l'orbe Premium (orb_premium.js).
// 25/10 (plan P35) : Premium est le seul thème ; le réglage `theme` est ignoré.
// Lancés par `node --test hud/tests/` (et par tests/test_themes.py dans pytest). Pas de
// navigateur : <html>, localStorage, matchMedia et requestAnimationFrame sont simulés.

import assert from "node:assert/strict";
import { test } from "node:test";

const store = new Map();
let systemDark = true;
const media = { change: null };
const root = { dataset: {} };
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) };
globalThis.matchMedia = (q) => ({
  get matches() { return q.includes("dark") ? systemDark : q.includes("light") ? !systemDark : false; },
  addEventListener: (_e, fn) => { if (q.includes("dark")) media.change = fn; },
});
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
const styles = [];
globalThis.document = {
  documentElement: root,
  head: { append: (s) => { styles.push(s); s.remove = () => styles.splice(styles.indexOf(s), 1); } },
  body: { offsetHeight: 0 },
  createElement: () => ({ textContent: "" }),
};

const { applyTheme, askTheme, onTheme, resolveMode, syncTheme, themeNow } = await import("../src/apparence.js");
const ev = (theme, mode = "auto", accent = "bleu") => ({
  groups: [{ id: "apparence", items: [{ key: "theme", value: theme }, { key: "theme_mode", value: mode },
    { key: "theme_accent", value: accent }] }],
});

test("Premium seul (P35) : sans réglage ni cache, Premium automatique bleu", () => {
  applyTheme();
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "dark", accent: "bleu" });
  assert.equal(themeNow().theme, "premium");
});

test("mode et accent sans rechargement, gardés pour theme-boot.js", () => {
  const seen = [];
  onTheme((th) => seen.push(`${th.theme}/${th.mode}/${th.accent}`));
  assert.equal(syncTheme(ev("premium", "light", "vert")), true);
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "light", accent: "vert" });
  assert.deepEqual(JSON.parse(store.get("astrel-theme")), { theme: "premium", mode: "light", accent: "vert" });
  assert.equal(syncTheme(ev("premium", "light", "vert")), false); // même choix : rien ne bouge
  assert.deepEqual(seen, ["premium/light/vert"]);
  assert.equal(styles.length, 1); // transitions coupées le temps du changement…
});

test("… puis rétablies à l'image suivante", async () => {
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(styles.length, 0);
});

test("Automatique suit le système, en direct", () => {
  systemDark = true;
  syncTheme(ev("premium", "auto"));
  assert.equal(root.dataset.mode, "dark");
  systemDark = false;
  media.change();
  assert.equal(root.dataset.mode, "light");
  assert.equal(resolveMode({ mode: "dark" }), "dark");
  assert.equal(resolveMode({ mode: " LIGHT " }), "light");
  systemDark = true;
  media.change();
});

test("réglage `theme` ignoré (cœur d'avant P35) : « classique » ou inconnu ne retire rien", () => {
  syncTheme(ev("classique", "light", "orange"));
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "light", accent: "orange" });
  syncTheme(ev("néon", "dark", "orange"));
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "dark", accent: "orange" });
  syncTheme({ groups: [{ items: [{ key: "theme", value: "classique" }] }] }); // `theme` seul : rien à suivre
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "dark", accent: "orange" });
});

test("valeurs inconnues : défauts (Automatique, bleu) ; aucun réglage de thème : rien ne change", () => {
  syncTheme(ev("premium", "nuit", "rose"));
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "dark", accent: "bleu" });
  assert.equal(syncTheme({ groups: [{ items: [{ key: "ui_language", value: "en" }] }] }), false);
});

test("majuscules comme la bulle (theme.py)", () => {
  syncTheme(ev(" PREMIUM ", "LIGHT", "Vert"));
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "light", accent: "vert" });
});

test("à la connexion : réglages demandés au cœur (settings_get), cache localStorage périmé corrigé", async () => {
  store.set("astrel-theme", JSON.stringify({ theme: "classique", mode: "auto", accent: "bleu" })); // ancien cache
  const asked = [];
  const answer = (groups) => async (cmd) => { asked.push(cmd); return { ok: true, groups }; };
  assert.equal(await askTheme(answer(ev("premium", "dark", "graphite").groups)), true);
  assert.deepEqual(asked, ["settings_get"]);
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "dark", accent: "graphite" });
  assert.deepEqual(JSON.parse(store.get("astrel-theme")), { theme: "premium", mode: "dark", accent: "graphite" });
  // cœur injoignable, réponse sans réglages (démo), commande qui lève : rien ne change
  assert.equal(await askTheme(async () => ({ ok: false, error: "déconnecté" })), false);
  assert.equal(await askTheme(async () => ({ ok: true })), false);
  assert.equal(await askTheme(async () => { throw new Error("x"); }), false);
  assert.equal(root.dataset.theme, "premium");
  await askTheme(answer(ev("classique").groups)); // un cœur qui dit encore « classique » : Premium reste
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "dark", accent: "bleu" });
});

// Faux contexte 2D : note chaque appel (nom + arguments) ; les dégradés rendent un objet à addColorStop.
function fakeCtx() {
  const calls = [];
  const ctx = new Proxy({}, {
    get: (t, k) => (k in t ? t[k] : (...a) => {
      calls.push([k, a, t.globalCompositeOperation]);
      return /Gradient$/.test(String(k)) ? { addColorStop: () => {} } : undefined;
    }),
    set: (t, k, v) => { t[k] = v; return true; },
  });
  return { ctx, calls };
}
const named = (calls, k) => calls.filter(([n]) => n === k);
const STARS = (calls) => named(calls, "bezierCurveTo").length / 4;   // une étoile = 4 courbes

test("orbe Constellation (28/10) : motifs de la bulle, état par état, sans disque de verre", async () => {
  const { drawState } = await import("../src/orb_premium.js");
  const look = { ink: "#f5f5f7", dark: true };
  const draw = (s, t = 0, lvl = 0, lk = look) => {
    const { ctx, calls } = fakeCtx();
    drawState(ctx, s, 130, 130, 130, t, lvl, "#0a84ff", lk);
    return calls;
  };
  const r = 130 * 0.78;
  // repos : UNE étoile d'encre de demi-envergure r × 0,40 (± 2 % de respiration), lueur en sombre
  const rest = draw("sleeping");
  assert.equal(STARS(rest), 1);
  const top = named(rest, "moveTo")[0][1];
  assert.ok(Math.abs((130 - top[1]) / (r * 0.40) - 1.01) < 0.011, `respiration ${top}`);
  assert.equal(named(rest, "createRadialGradient").length, 1);
  assert.equal(named(draw("sleeping", 0, 0, { ...look, dark: false }), "createRadialGradient").length, 0);
  // aucun disque : pas d'arc plein de la taille du verre (r), ni ombre
  for (const s of ["sleeping", "listening", "thinking", "working", "booting", "speaking", "asking", "off"]) {
    const arcs = named(draw(s, 1.3, 0.6), "arc").map(([, a]) => a[2]);
    assert.ok(arcs.every((rad) => rad < r * 0.7), `${s} : arc ${arcs}`);
  }
  // écoute : étoile centrale + 4 petites ; la 4e ne s'allume qu'à niveau haut
  assert.equal(STARS(draw("listening", 0, 0)), 5);
  // réflexion : 8 étoiles en cercle + 1 au centre, 3 s le tour ; travail et démarrage : 4 s
  assert.equal(STARS(draw("thinking")), 9);
  const head = (s, t) => named(draw(s, t), "moveTo")[0][1].map((v) => v.toFixed(3)).join();
  assert.equal(head("thinking", 0), head("thinking", 3));
  assert.notEqual(head("thinking", 0), head("thinking", 1.5));
  assert.equal(head("working", 0), head("working", 4));
  assert.notEqual(head("working", 0), head("working", 3));
  assert.equal(head("booting", 0), head("booting", 4));
  // parole : 7 barres arrondies, plus hautes avec le niveau
  const bars = (lvl) => named(draw("speaking", 0.2, lvl), "moveTo").map(([, a]) => a[1]);
  assert.equal(bars(0).length, 7);
  assert.ok(Math.min(...bars(1)) < Math.min(...bars(0)));
  // demande : étoile + anneau qui pulse (2 s)
  const ask = draw("asking", 0.5);
  assert.equal(STARS(ask), 1);
  assert.equal(named(ask, "stroke").length, 1);
  // micro coupé : étoile + barre, détourée par un trait effacé (destination-out)
  const off = draw("off");
  assert.equal(STARS(off), 1);
  assert.ok(named(off, "stroke").some(([, , op]) => op === "destination-out"));
  // niveau absurde : borné, pas d'exception
  assert.equal(STARS(draw("listening", 0, Number.NaN)), 5);
});

test("orbe Premium : un dessin par état, état courant jamais perdu", async () => {
  globalThis.ResizeObserver = class { observe() {} };
  globalThis.devicePixelRatio = 1;
  const { ctx } = fakeCtx();
  const { PremiumOrb } = await import("../src/orb_premium.js");
  const orb = new PremiumOrb({ clientWidth: 260, clientHeight: 260, getContext: () => ctx });
  orb.setPalette({ sleeping: "#0a84ff", asking: "#ffd60a" });
  orb.setState("asking");
  orb.last = 1e9; // horodatage d'image plus ancien que setActive : jamais un pas négatif
  orb.active = true;
  orb.frame(1e9 - 50);
  assert.ok(orb.layers.has("asking"));
  orb.active = false;
});

test("orbe Premium : setActive(true), (false), (true) dans la même image, une seule boucle", async () => {
  globalThis.ResizeObserver = class { observe() {} };
  const { PremiumOrb } = await import("../src/orb_premium.js");
  const { ctx } = fakeCtx();
  const orb = new PremiumOrb({ clientWidth: 260, clientHeight: 260, width: 260, height: 260, getContext: () => ctx });
  let draws = 0;
  const draw = orb.draw.bind(orb);
  orb.draw = (now) => { draws++; return draw(now); };
  // images à la main : chaque appel de requestAnimationFrame attend la prochaine image
  const raf = globalThis.requestAnimationFrame;
  let pending = [];
  globalThis.requestAnimationFrame = (fn) => { pending.push(fn); return pending.length; };
  try {
    orb.setActive(true); orb.setActive(false); orb.setActive(true);
    for (let i = 0; i < 3; i++) orb.setActive(false), orb.setActive(true); // rafale
    let now = performance.now();
    for (let img = 0; img < 5; img++) {
      const due = pending; pending = []; draws = 0; now += 16;
      for (const fn of due) fn(now);
      assert.equal(draws, 1, `image ${img} : un seul dessin`);
      assert.equal(pending.length, 1, `image ${img} : une seule boucle reprogrammée`);
    }
    orb.setActive(false);
    const due = pending; pending = []; draws = 0;
    for (const fn of due) fn(now + 16);
    assert.equal(draws, 0);
    assert.equal(pending.length, 0, "arrêtée : plus aucune boucle");
  } finally {
    globalThis.requestAnimationFrame = raf;
  }
});

test("orbe Premium : prefers-reduced-motion, rien ne bouge (temps et niveau figés)", async () => {
  globalThis.ResizeObserver = class { observe() {} };
  const { PremiumOrb } = await import("../src/orb_premium.js");
  const { ctx, calls } = fakeCtx();
  const orb = new PremiumOrb({ clientWidth: 260, clientHeight: 260, width: 260, height: 260, getContext: () => ctx });
  orb.setPalette({ sleeping: "#0a84ff", thinking: "#0a84ff" }, { ink: "#1d1d1f", dark: false });
  orb.layers = new Map([["thinking", 1]]);
  orb.level = 0.8;
  orb.calm = { matches: true };
  const snap = (now) => { calls.length = 0; orb.draw(now); return JSON.stringify(calls); };
  assert.equal(snap(1000), snap(2400));
  orb.calm = { matches: false };
  assert.notEqual(snap(1000), snap(2400));
});
