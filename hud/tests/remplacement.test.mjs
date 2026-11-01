// Tests de la fermeture des anciens HUD (hud/src/remplacement.js, 19/10).
// Lancés par `node --test hud/tests/` (et par tests/test_hud_unique.py dans pytest).

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  askKeeper, keepOnReplace, replacedBy, restoreAfterReplace, STASH_MAX_AGE_MS, STASH_PREFIX, stashForReplace,
} from "../src/remplacement.js";

class FakeStorage {
  constructor() { this.m = new Map(); }
  get length() { return this.m.size; }
  key(i) { return [...this.m.keys()][i] ?? null; }
  getItem(k) { return this.m.has(k) ? this.m.get(k) : null; }
  setItem(k, v) { this.m.set(k, String(v)); }
  removeItem(k) { this.m.delete(k); }
}

function fakeDoc(value) {
  const ask = { value, events: 0, dispatchEvent() { this.events++; } };
  return { ask, querySelector: (sel) => (sel === "#ask" ? ask : null) };
}

const T = 1_760_000_000; // secondes, comme time.time() côté cœur

test("une page chargée avant l'ouverture du nouveau HUD se ferme", () => {
  assert.equal(replacedBy({ type: "hud_close", before: T }, T * 1000 - 60_000), true);
});

test("la nouvelle page, chargée après, reste", () => {
  assert.equal(replacedBy({ type: "hud_close", before: T }, T * 1000 + 800), false);
});

test("évènement illisible : rien ne se ferme", () => {
  for (const ev of [{}, { before: "x" }, { before: 0 }, { before: -5 }, { before: null }, null, undefined]) {
    assert.equal(replacedBy(ev, T * 1000 - 60_000), false, JSON.stringify(ev));
  }
  assert.equal(replacedBy({ before: T }, NaN), false);
});

// ------------------------------------------------ texte non envoyé passé au nouveau HUD

const NOW = T * 1000 + 1500; // ms : 1,5 s après l'ouverture du nouveau HUD

function setup(oldText, newText = "") {
  const plans = { saved: { "P3|2": "réponse en cours" }, got: null };
  keepOnReplace("plans", { save: () => plans.saved, restore: (v) => { plans.got = v; } });
  const oldDoc = fakeDoc(oldText);
  const newDoc = fakeDoc(newText);
  return { plans, oldDoc, newDoc };
}

test("l'ancienne page met à l'abri, la nouvelle reprend une seule fois", () => {
  const st = new FakeStorage();
  const { plans, oldDoc, newDoc } = setup("message pas encore envoyé");
  keepOnReplace("ask", askKeeper(oldDoc));
  assert.equal(stashForReplace(st, T, NOW - 500, "a"), true);
  assert.equal(st.length, 1);
  assert.ok(st.key(0).startsWith(STASH_PREFIX));
  keepOnReplace("ask", askKeeper(newDoc));
  assert.deepEqual(restoreAfterReplace(st, T * 1000 + 800, NOW).sort(), ["ask", "plans"]);
  assert.equal(newDoc.ask.value, "message pas encore envoyé");
  assert.equal(newDoc.ask.events, 1); // la barre se redimensionne
  assert.deepEqual(plans.got, { "P3|2": "réponse en cours" });
  assert.equal(st.length, 0); // effacé
  newDoc.ask.value = "";
  assert.deepEqual(restoreAfterReplace(st, T * 1000 + 800, NOW), []);
  assert.equal(newDoc.ask.value, "");
});

test("une autre ANCIENNE page (qui va se fermer) ne prend rien", () => {
  const st = new FakeStorage();
  const { oldDoc } = setup("texte");
  keepOnReplace("ask", askKeeper(oldDoc));
  stashForReplace(st, T, NOW, "a");
  assert.deepEqual(restoreAfterReplace(st, T * 1000 - 60_000, NOW), []);
  assert.equal(st.length, 1); // laissé pour la nouvelle
});

test("plus de 2 minutes : jeté, pas repris", () => {
  const st = new FakeStorage();
  const { oldDoc, newDoc } = setup("vieux texte");
  keepOnReplace("ask", askKeeper(oldDoc));
  stashForReplace(st, T, NOW, "a");
  keepOnReplace("ask", askKeeper(newDoc));
  assert.deepEqual(restoreAfterReplace(st, T * 1000 + 800, NOW + STASH_MAX_AGE_MS + 1), []);
  assert.equal(newDoc.ask.value, "");
  assert.equal(st.length, 0);
});

test("rien de tapé : rien d'écrit", () => {
  const st = new FakeStorage();
  const { plans, oldDoc } = setup("   ");
  plans.saved = {};
  keepOnReplace("ask", askKeeper(oldDoc));
  assert.equal(stashForReplace(st, T, NOW, "a"), false);
  assert.equal(st.length, 0);
});

test("barre déjà commencée dans le nouveau HUD : ajouté à la suite, sans doublon", () => {
  const st = new FakeStorage();
  const { oldDoc, newDoc } = setup("ancien", "nouveau");
  keepOnReplace("ask", askKeeper(oldDoc));
  stashForReplace(st, T, NOW, "a");
  keepOnReplace("ask", askKeeper(newDoc));
  restoreAfterReplace(st, T * 1000 + 800, NOW);
  assert.equal(newDoc.ask.value, "nouveau\nancien");
  st.setItem(STASH_PREFIX + "b", JSON.stringify({ ts: NOW, before: T, items: { ask: "ancien" } }));
  restoreAfterReplace(st, T * 1000 + 800, NOW);
  assert.equal(newDoc.ask.value, "nouveau\nancien");
});

test("deux anciennes fenêtres : les deux textes sont repris", () => {
  const st = new FakeStorage();
  const { plans, newDoc } = setup("");
  plans.saved = {};
  keepOnReplace("ask", askKeeper(fakeDoc("premier")));
  stashForReplace(st, T, NOW, "a");
  keepOnReplace("ask", askKeeper(fakeDoc("second")));
  stashForReplace(st, T, NOW, "b");
  keepOnReplace("ask", askKeeper(newDoc));
  restoreAfterReplace(st, T * 1000 + 800, NOW);
  assert.equal(newDoc.ask.value, "premier\nsecond");
});

test("relais illisible : effacé sans erreur", () => {
  const st = new FakeStorage();
  st.setItem(STASH_PREFIX + "x", "{pas du json");
  st.setItem("astrel-chat-h", "300"); // autre clé du HUD : intacte
  assert.deepEqual(restoreAfterReplace(st, T * 1000 + 800, NOW), []);
  assert.equal(st.length, 1);
  assert.equal(st.getItem("astrel-chat-h"), "300");
});
