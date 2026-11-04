// Tests des fonctions pures de la recherche dans tous les canaux (hud/src/recherche.js, 04/11).
// Lancés par `node --test hud/tests/` (et par tests/test_hud_recherche.py dans pytest).

import assert from "node:assert/strict";
import { test } from "node:test";
import { grouper, MARK_CLOSE, MARK_OPEN, marquer } from "../src/recherche.js";

const O = MARK_OPEN;
const F = MARK_CLOSE;

test("marques du cœur (FTS5) → morceaux texte et mots trouvés", () => {
  assert.deepEqual(marquer(`le ${O}chemin${F} du ${O}projet${F}.`), [
    { t: "text", v: "le " }, { t: "mark", v: "chemin" }, { t: "text", v: " du " }, { t: "mark", v: "projet" },
    { t: "text", v: "." },
  ]);
});

test("marques mal formées : ouverte sans fin jusqu'au bout, fermeture seule ignorée, contrôles retirés", () => {
  assert.deepEqual(marquer(`a ${F}b ${O}fin`), [{ t: "text", v: "a b " }, { t: "mark", v: "fin" }]);
  assert.deepEqual(marquer(`${O}${O}x${F}${F}`), [{ t: "mark", v: "x" }]);
  assert.deepEqual(marquer("a\u0000b\u0007c\u001bd"), [{ t: "text", v: "a b c d" }]);
  assert.deepEqual(marquer(null), []);
  assert.deepEqual(marquer(""), []);
});

test("ATTAQUE : du HTML dans un extrait reste du texte (le HUD en fait des nœuds texte)", () => {
  const out = marquer(`<img src=x onerror=alert(1)> ${O}<b>${F}`);
  assert.deepEqual(out, [{ t: "text", v: "<img src=x onerror=alert(1)> " }, { t: "mark", v: "<b>" }]);
});

test("groupes par canal affiché, dans l'ordre du premier résultat, ordre des résultats gardé", () => {
  const r = [{ channel: "astrel", mid: "1" }, { channel: "general", mid: "2" }, { channel: "astrel", mid: "3" },
    { channel: "supprime", mid: "4" }, null, "x", { mid: "5" }];
  const shownIn = (id) => (["astrel", "general"].includes(id) ? id : "general");
  const g = grouper(r, shownIn);
  assert.deepEqual(g.map((x) => x.channel), ["astrel", "general"]);
  assert.deepEqual(g[0].items.map((x) => x.mid), ["1", "3"]);
  assert.deepEqual(g[1].items.map((x) => x.mid), ["2", "4", "5"]);
  assert.deepEqual(grouper(undefined), []);
});
