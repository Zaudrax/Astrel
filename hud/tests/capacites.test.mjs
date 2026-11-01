// Page « Capacités » du HUD (21/10, plan P22, lot 1 bis) : hud/src/capacites.js.
// Lancés par `node --test hud/tests/`. Pas de navigateur : seules les fonctions pures (recherche
// sans accents, filtre, tri) sont appelées ; conn.js a besoin de quelques globales à l'import.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

globalThis.window ??= { addEventListener() {} };
globalThis.location ??= { hash: "", protocol: "http:", host: "127.0.0.1" };
globalThis.sessionStorage ??= { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.localStorage ??= { getItem: () => null, setItem() {}, removeItem() {} };
// Faux DOM minimal : assez pour construire une section et lire ce qui a été créé.
const created = [];
class FakeEl {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.className = ""; this.dataset = {}; this.attrs = {};
    this.own = ""; this.open = false; this.listeners = {};
    created.push(this);
  }
  get classList() {
    const parts = () => this.className.split(" ").filter(Boolean);
    return { add: (c) => { if (!parts().includes(c)) this.className = [...parts(), c].join(" "); },
      remove: (c) => { this.className = parts().filter((x) => x !== c).join(" "); },
      contains: (c) => parts().includes(c) };
  }
  get textContent() { return this.own + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.own = String(v); this.children = []; }
  set innerHTML(_v) { throw new Error("innerHTML interdit"); }
  append(...n) { this.children.push(...n); }
  prepend(...n) { this.children.unshift(...n); }
  replaceChildren(...n) { this.children = n; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  all() { return [this, ...this.children.flatMap((c) => c.all())]; }
}
globalThis.document ??= {
  querySelector: () => null, addEventListener() {}, activeElement: null,
  createElement: (tag) => new FakeEl(tag),
  createElementNS: (_ns, tag) => new FakeEl(tag),
};

const { compter, filtrer, fold, groupeDom, trier, words } = await import("../src/capacites.js");
const { dictionaries } = await import("../src/i18n.js");
const SRC = readFileSync(new URL("../src/capacites.js", import.meta.url), "utf8");

const GROUPES = [
  { id: "voix", titre: "Ce que tu peux dire", entrees: [
    { nom: "« Hey Jarvis »", texte: "Réveille Astrel : il t'écoute." },
    { nom: "« Mode silencieux »", texte: "Il t'écoute et répond à l'écran, sans parler." },
  ] },
  { id: "memoire", titre: "Mémoire et historique", entrees: [
    { nom: "memory_write", texte: "Écrit dans la mémoire.", gravite: "libre", usage: 227 },
    { nom: "memory_read", texte: "Lit une note de la mémoire.", gravite: "libre", usage: 105 },
    { nom: "history_search", texte: "Cherche dans l'HISTORIQUE DES CONVERSATIONS passées.", usage: 25 },
  ] },
  { id: "capteurs", titre: "Capteurs", entrees: [
    { nom: "EYES", texte: "La webcam sait si tu es là", etat: "éteint" },
    { nom: "FOCUS", texte: "Ne pas déranger", etat: "allumé" },
  ] },
  { id: "reglages", titre: "Pages des Réglages", entrees: [
    { nom: "Écran", lien: { type: "reglages", id: "x" } },
    { nom: "Cœur", lien: { type: "reglages", id: "y" } },
    { nom: "apparence" },
    { nom: "Zèbre" },
  ] },
];

test("fold : sans accents, sans casse, ligatures et apostrophes unifiées", () => {
  assert.equal(fold("Écrit dans la MÉMOIRE"), "ecrit dans la memoire");
  assert.equal(fold("Cœur  et   Æther"), "coeur et aether");
  assert.equal(fold("l’écran"), "l'ecran");
  assert.equal(fold(null), "");
  assert.deepEqual(words("  « Mémoire »,  ÉCRAN. "), ["memoire", "ecran"]);
});

test("filtre : un mot sans accent trouve le texte accentué, et l'inverse", () => {
  const a = filtrer(GROUPES, "memoire");
  assert.deepEqual(a.map((g) => g.id), ["memoire"]); // titre du groupe : toutes ses entrées
  assert.equal(compter(a), 3);
  assert.equal(compter(filtrer(GROUPES, "écran")), 2); // « l'écran » de la voix + « Écran »
  assert.equal(compter(filtrer(GROUPES, "ECRAN")), 2);
  assert.equal(compter(filtrer(GROUPES, "coeur")), 1);
  assert.equal(compter(filtrer(GROUPES, "t’ecoute")), 2); // apostrophe typographique
});

test("filtre : tous les mots, n'importe où (nom, texte, état, titre du groupe)", () => {
  const r = filtrer(GROUPES, "capteurs allume");
  assert.deepEqual(r.map((g) => g.id), ["capteurs"]);
  assert.deepEqual(r[0].entrees.map((e) => e.nom), ["FOCUS"]);
  assert.equal(compter(filtrer(GROUPES, "memory lit")), 1);
  assert.deepEqual(filtrer(GROUPES, "introuvable"), []);
});

test("filtre vide : tout, sans toucher aux données reçues", () => {
  const r = filtrer(GROUPES, "   ");
  assert.equal(compter(r), compter(GROUPES));
  r[0].entrees.pop();
  assert.equal(GROUPES[0].entrees.length, 2);
  assert.deepEqual(filtrer(undefined, "x"), []);
  assert.equal(compter(filtrer([{ id: "g", titre: "G", entrees: [null, { texte: "sans nom" }, { nom: "ok" }] }], "")), 1);
});

test("tri : ordre du cœur par défaut, A→Z accents compris, plus utilisées d'abord", () => {
  const regl = GROUPES[3].entrees;
  assert.deepEqual(trier(regl).map((e) => e.nom), ["Écran", "Cœur", "apparence", "Zèbre"]);
  assert.deepEqual(trier(regl, "az").map((e) => e.nom), ["apparence", "Cœur", "Écran", "Zèbre"]);
  assert.deepEqual(trier(regl, "az", "en-GB").map((e) => e.nom), ["apparence", "Cœur", "Écran", "Zèbre"]);
  const mem = GROUPES[1].entrees;
  assert.deepEqual(trier(mem, "usage").map((e) => e.nom), ["memory_write", "memory_read", "history_search"]);
  assert.deepEqual(trier([{ nom: "b" }, { nom: "a", usage: 0 }, { nom: "c", usage: 2 }], "usage").map((e) => e.nom),
    ["c", "a", "b"]); // à égalité : par nom
  assert.deepEqual(trier(GROUPES[0].entrees, "az").map((e) => e.nom), ["« Hey Jarvis »", "« Mode silencieux »"]);
  assert.equal(trier(mem, "az") === mem, false); // copie : la liste reçue n'est pas réordonnée
  assert.deepEqual(mem.map((e) => e.nom), ["memory_write", "memory_read", "history_search"]);
});

test("aucun innerHTML ni insertAdjacentHTML : tout en textContent", () => {
  assert.doesNotMatch(SRC, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
});

test("rendu en faux DOM : le HTML reçu reste du texte, aucune balise créée par les données", () => {
  const evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
  created.length = 0;
  const d = groupeDom({ id: "x<b>", titre: evil, lien: { type: "guide", id: "parler" }, entrees: [
    { nom: evil, texte: evil, etat: evil, gravite: "<i>critique</i>", usage: "3", lien: { type: "javascript:alert(1)", id: evil } },
  ] }, false, "az");
  const tags = new Set(created.map((e) => e.tagName));
  for (const tag of tags) assert.ok(["DETAILS", "SUMMARY", "SPAN", "UL", "LI", "DIV", "P"].includes(tag), tag);
  assert.equal(d.tagName, "DETAILS");
  const texts = d.all().map((e) => e.own);
  assert.ok(texts.includes(evil)); // titre, nom, texte, état : tels quels, en texte
  assert.equal(texts.filter((x) => x === evil).length, 4);
  const grav = d.all().find((e) => e.className.startsWith("cap-grav"));
  assert.equal(grav.className, "cap-grav grav-libre"); // gravité inconnue : jamais une classe venue des données
  assert.ok(!d.all().some((e) => e.tagName === "BUTTON")); // lien de type inconnu, et sans initCapacites : pas de bouton
});

test("groupe dont le sous-appel a échoué : message d'erreur, pas une liste vide muette", () => {
  const d = groupeDom({ id: "bureau", titre: "Bureau", entrees: [], erreur: "commande refusée" });
  const err = d.all().find((e) => e.className === "cap-status err");
  assert.ok(err, "message d'erreur affiché");
  assert.equal(err.attrs.role, "status");
  assert.match(err.textContent, /commande refusée/);
  // Sans recherche, le groupe en erreur reste affiché ; une recherche sur son titre le garde aussi.
  const g = { id: "bureau", titre: "Bureau", entrees: [], erreur: "x" };
  assert.deepEqual(filtrer([g], "").map((x) => x.id), ["bureau"]);
  assert.deepEqual(filtrer([g], "bureau").map((x) => x.id), ["bureau"]);
  assert.deepEqual(filtrer([g], "autre"), []);
});

// Lot 2 : clés capacites.* de hud/src/langues.js, en français ET en anglais.
test("clés de langue présentes en français et en anglais", () => {
  const used = new Set([...SRC.matchAll(/\bt\(\s*"([a-z_][\w.]*)"/g)].map((m) => m[1]));
  const { FR, EN } = dictionaries();
  const missing = [...used].filter((k) => !(k in FR) || !(k in EN));
  assert.deepEqual(missing, []);
});
