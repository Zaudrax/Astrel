// Tests des fonctions pures des questions à choix (hud/src/choix.js, 21/10, plan P21).
// Lancés par `node --test hud/tests/`.

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  badge, deplacer, entrees, indexChiffre, indexLettre, lettre, MAX_OPTIONS, normaliser, toucheAutre,
} from "../src/choix.js";

test("lettres et badges a) b) c)", () => {
  assert.deepEqual([0, 1, 2, 3, 4].map(lettre), ["a", "b", "c", "d", "e"]);
  assert.equal(badge(0), "a)");
  assert.equal(badge(4), "e)");
});

test("la recommandée en premier, 4 réponses au plus, vides écartées", () => {
  const opts = normaliser([{ label: "B" }, { label: "A", recommended: true }, { label: " " }, "C", "D", "E"]);
  assert.deepEqual(opts.map((o) => o.label), ["A", "B", "C", "D"]);
  assert.equal(opts[0].recommended, true);
  assert.equal(opts.length, MAX_OPTIONS);
  // Aucune marque : la première l'est (contrat du cœur).
  assert.deepEqual(normaliser(["X", "Y"]).map((o) => o.recommended), [true, false]);
  for (const bad of [null, undefined, "A,B", 3, {}]) assert.deepEqual(normaliser(bad), []);
});

test("« Autre » ajouté d'office en dernière lettre", () => {
  const items = entrees([{ label: "Oui", recommended: true }, { label: "Non" }, { label: "Plus tard" }]);
  assert.deepEqual(items.map((o) => [o.lettre, o.autre]), [["a", false], ["b", false], ["c", false], ["d", true]]);
  assert.equal(items.at(-1).label, "");
  const sans = entrees(["Oui", "Non"], false);
  assert.equal(sans.length, 2);
  assert.ok(!sans.some((o) => o.autre));
  assert.equal(entrees(["A", "B", "C", "D"]).at(-1).lettre, "e");
});

test("touche lettre → index, dans la limite des entrées", () => {
  assert.equal(indexLettre({ key: "a" }, 3), 0);
  assert.equal(indexLettre({ key: "C" }, 3), 2); // Maj ou verrouillage : même lettre
  assert.equal(indexLettre({ key: "d" }, 3), -1);
  for (const e of [{ key: "a", ctrlKey: true }, { key: "a", altKey: true }, { key: "a", metaKey: true },
    { key: "a", isComposing: true }, { key: "é" }, { key: "Enter" }, { key: "1" }, {}, null]) {
    assert.equal(indexLettre(e, 5), -1, JSON.stringify(e));
  }
});

test("chiffre par le code physique (AZERTY : « 1 » est « & »)", () => {
  assert.equal(indexChiffre({ code: "Digit1", key: "&" }, 4), 0);
  assert.equal(indexChiffre({ code: "Numpad5" }, 5), 4);
  assert.equal(indexChiffre({ code: "Digit5" }, 4), -1);
  assert.equal(indexChiffre({ code: "Digit0" }, 9), -1);
  for (const e of [{ code: "Digit1", repeat: true }, { code: "Digit1", ctrlKey: true }, { code: "Digit1", altKey: true },
    { code: "KeyA" }, {}, null]) {
    assert.equal(indexChiffre(e, 5), -1, JSON.stringify(e));
  }
});

test("flèches en boucle, Début, Fin", () => {
  assert.equal(deplacer("ArrowDown", 0, 4), 1);
  assert.equal(deplacer("ArrowRight", 3, 4), 0);
  assert.equal(deplacer("ArrowUp", 0, 4), 3);
  assert.equal(deplacer("ArrowLeft", 2, 4), 1);
  assert.equal(deplacer("Home", 2, 4), 0);
  assert.equal(deplacer("End", 0, 4), 3);
  assert.equal(deplacer("Tab", 0, 4), -1);
  assert.equal(deplacer("ArrowDown", 0, 0), -1);
});

test("champ « Autre » : Entrée envoie, Maj+Entrée va à la ligne, Échap revient, IME respecté", () => {
  assert.equal(toucheAutre({ key: "Enter" }), "envoyer");
  assert.equal(toucheAutre({ key: "Enter", shiftKey: true }), "ligne");
  assert.equal(toucheAutre({ key: "Escape" }), "retour");
  assert.equal(toucheAutre({ key: "Enter", isComposing: true }), "");
  assert.equal(toucheAutre({ key: "Enter", keyCode: 229 }), "");
  assert.equal(toucheAutre({ key: "a" }), "");
  assert.equal(toucheAutre(null), "");
});
