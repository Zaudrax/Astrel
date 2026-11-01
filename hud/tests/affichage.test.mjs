// Affichage Simple / Avancé du HUD (22/10, plan P23) : hud/src/apparence.js et hud/theme-boot.js.
// Lancés par `node --test hud/tests/`. Pas de navigateur : <html>, localStorage, matchMedia et
// requestAnimationFrame sont simulés (comme apparence.test.mjs, dans un processus à part).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const store = new Map();
const root = { dataset: {} };
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) };
globalThis.matchMedia = () => ({ matches: false, addEventListener: () => {} });
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
globalThis.document = {
  documentElement: root,
  head: { append: (s) => { s.remove = () => {}; } },
  body: { offsetHeight: 0 },
  createElement: () => ({ textContent: "" }),
};

const { affichageNow, applyAffichage, applyTheme, askTheme, onAffichage, syncTheme, themeNow } = await import("../src/apparence.js");
const ev = (affichage, theme = "premium") => ({
  groups: [{ id: "apparence", items: [{ key: "theme", value: theme }, { key: "theme_mode", value: "dark" },
    { key: "theme_accent", value: "bleu" }, { key: "affichage", value: affichage }] }],
});

const PREMIUM = { theme: "premium", mode: "dark", accent: "bleu" }; // 25/10 (plan P35) : seul thème

test("Avancé par défaut : aucun attribut d'affichage (thème Premium seul)", () => {
  applyTheme();
  applyAffichage();
  assert.equal(affichageNow(), "avance");
  assert.deepEqual({ ...root.dataset }, PREMIUM);
});

test("Simple : attribut posé sans rechargement, gardé pour theme-boot.js ; le thème ne bouge pas", () => {
  const seen = [];
  onAffichage((a) => seen.push(a));
  assert.equal(syncTheme(ev("simple")), false); // rend vrai seulement si le THÈME change
  assert.deepEqual({ ...root.dataset }, { ...PREMIUM, affichage: "simple" });
  assert.equal(store.get("astrel-affichage"), "simple");
  assert.equal(themeNow().theme, "premium");
  syncTheme(ev("simple"));
  assert.deepEqual(seen, ["simple"]); // même affichage : pas de second appel
});

test("Simple et Premium ensemble ; retour à Avancé : seul l'attribut d'affichage part", () => {
  syncTheme(ev("simple", "classique")); // `theme` d'un cœur d'avant P35 : ignoré
  assert.deepEqual({ ...root.dataset }, { affichage: "simple", theme: "premium", mode: "dark", accent: "bleu" });
  syncTheme(ev("avance"));
  assert.deepEqual({ ...root.dataset }, { theme: "premium", mode: "dark", accent: "bleu" });
  assert.equal(store.get("astrel-affichage"), "avance");
});

test("valeur inconnue ou en majuscules ; réglage absent : rien ne change", () => {
  syncTheme(ev(" SIMPLE "));
  assert.equal(root.dataset.affichage, "simple");
  syncTheme(ev("geek"));
  assert.equal(root.dataset.affichage, undefined);
  syncTheme(ev("simple"));
  syncTheme({ groups: [{ items: [{ key: "ui_language", value: "en" }] }] });
  assert.equal(root.dataset.affichage, "simple");
});

test("à la connexion (askTheme) : l'affichage du cœur l'emporte sur le cache", async () => {
  store.set("astrel-affichage", "simple");
  await askTheme(async () => ({ ok: true, groups: ev("avance").groups }));
  assert.equal(affichageNow(), "avance");
  assert.equal(root.dataset.affichage, undefined);
});

test("theme-boot.js : Premium toujours, Simple avant la première peinture (cache), rien en Avancé", () => {
  const code = readFileSync(new URL("../theme-boot.js", import.meta.url), "utf8");
  const run = (cache) => {
    const attrs = {};
    const ls = new Map(Object.entries(cache));
    const mm = () => ({ matches: light });
    vm.runInNewContext(code, {
      localStorage: { getItem: (k) => { if (broken) throw new Error("sans stockage"); return ls.get(k) ?? null; } },
      document: { documentElement: { setAttribute: (k, v) => { attrs[k] = v; } } },
      window: { matchMedia: mm }, matchMedia: mm,
    });
    return attrs;
  };
  let light = false, broken = false;
  const dark = { "data-theme": "premium", "data-mode": "dark", "data-accent": "bleu" };
  assert.deepEqual(run({ "astrel-affichage": "simple" }), { "data-affichage": "simple", ...dark });
  assert.deepEqual(run({ "astrel-affichage": "avance" }), dark);
  assert.deepEqual(run({}), dark);
  assert.deepEqual(run({ "astrel-affichage": "simple", "astrel-theme": JSON.stringify({ theme: "premium", mode: "light", accent: "vert" }) }),
    { "data-affichage": "simple", "data-theme": "premium", "data-mode": "light", "data-accent": "vert" });
  // 25/10 (plan P35) : un ancien cache « classique » ne retire plus le Premium ; son mode et son accent comptent
  assert.deepEqual(run({ "astrel-theme": JSON.stringify({ theme: "classique", mode: "light", accent: "orange" }) }),
    { "data-theme": "premium", "data-mode": "light", "data-accent": "orange" });
  assert.deepEqual(run({ "astrel-theme": "{pas du json" }), dark);
  light = true; // « auto » (ou rien) suit le système
  assert.deepEqual(run({ "astrel-theme": JSON.stringify({ theme: "classique", mode: "auto", accent: "rose" }) }),
    { "data-theme": "premium", "data-mode": "light", "data-accent": "bleu" });
  broken = true; // localStorage qui lève : Premium quand même
  assert.deepEqual(run({}), { "data-theme": "premium", "data-mode": "light", "data-accent": "bleu" });
});
