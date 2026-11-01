// Vue « Tâches » du HUD (26/10, plan P38 lot 2) : hud/src/todo.js. Lancés par
// `node --test hud/tests/*.test.mjs`. Fonctions pures seulement (aperçu en mots, badge « agent en
// cours ») ; le rendu est contrôlé en Chrome headless (tools/hud_check_rendu.py, check_todo).

import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window ??= { addEventListener() {} };
globalThis.location ??= { hash: "", protocol: "http:", host: "127.0.0.1" };
globalThis.sessionStorage ??= { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.localStorage ??= { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.document ??= { querySelector: () => null, addEventListener() {}, activeElement: null }; // conn.js à l'import

const { apercuParts, agentsDe, carteRefs, etatLabel, COLONNES } = await import("../src/todo.js");
const { useLang } = await import("../src/i18n.js");

test("aperçu : cartes ouvertes seulement, dans l'ordre des colonnes, pluriel", () => {
  useLang("fr");
  assert.deepEqual(apercuParts({ planifier: 0, lancer: 2, cours: 1, fini: 9, abandonne: 3 }), ["2 à lancer", "1 en cours"]);
  assert.deepEqual(apercuParts({ planifier: 3 }), ["3 à planifier"]);
  assert.deepEqual(apercuParts({}), []);
  useLang("en");
  assert.deepEqual(apercuParts({ lancer: 2, cours: 1 }), ["2 to launch", "1 in progress"]);
  useLang("fr");
});

test("colonnes et libellés dans les deux langues", () => {
  assert.deepEqual(COLONNES, ["planifier", "lancer", "cours", "fini"]);
  useLang("fr");
  assert.deepEqual(COLONNES.map(etatLabel), ["À planifier", "À lancer", "En cours", "Fini"]);
  useLang("en");
  assert.deepEqual([...COLONNES, "abandonne"].map(etatLabel), ["To plan", "To launch", "In progress", "Done", "Dropped"]);
  useLang("fr");
});

test("références d'une carte : plan normalisé, mission en minuscules", () => {
  assert.deepEqual(carteRefs({ plan: "P038", mission: "P38-Lot2" }), ["p38", "p38-lot2"]);
  assert.deepEqual(carteRefs({ plan: "", mission: "" }), []);
  assert.deepEqual(carteRefs({ plan: "PX" }), []);
});

test("badge agent en cours : agents vivants dont la consigne cite le plan ou la mission", () => {
  const items = [
    { kind: "agent", desc: "Codeur", refs: ["p38", "p38-lot2"] },
    { kind: "local_bash", desc: "build", refs: ["p38"] },     // une commande n'est pas un agent
    { kind: "agent", desc: "Testeur" },                       // cœur d'avant P38 : pas de refs
    { kind: "agent", desc: "Relecteur", refs: ["p12"] },
  ];
  assert.deepEqual(agentsDe({ plan: "P38" }, items).map((k) => k.desc), ["Codeur"]);
  assert.deepEqual(agentsDe({ mission: "p38-lot2" }, items).map((k) => k.desc), ["Codeur"]);
  assert.deepEqual(agentsDe({ plan: "P39" }, items), []);
  assert.deepEqual(agentsDe({}, items), []);
  assert.deepEqual(agentsDe({ plan: "P12" }, null), []);
});
