// Mission : chemins de l'objectif hors du périmètre (02/11, essai réel P43) : hud/src/mission-chemins.js.
import { test } from "node:test";
import assert from "node:assert/strict";
import { missionPathsOutside } from "../src/mission-chemins.js";

const A = "/home/user/Projets/Astrel";
const E = "/home/user/Projets/Essai-P43";

test("objectif hors du périmètre par défaut (dossier du canal) : signalé", () => {
  assert.deepEqual(missionPathsOutside(`Écris ${E}/SALUT.md avec un poème`, A, A), [`${E}/SALUT.md`]);
});

test("dans le périmètre, guillemets, ponctuation, / final : rien", () => {
  assert.deepEqual(missionPathsOutside(`Écris ${E}/SALUT.md avec un poème`, E, A), []);
  assert.deepEqual(missionPathsOutside(`Écris « ${E}/SALUT.md ».`, `${E}/`, A), []);
});

test("/tmp toujours permis ; « et/ou » n'est pas un chemin", () => {
  assert.deepEqual(missionPathsOutside("Lis /tmp/x.log et/ou la doc", E, A), []);
});

test("préfixe sans séparateur : hors du périmètre", () => {
  assert.deepEqual(missionPathsOutside(`écris ${E}x/a`, E, A), [`${E}x/a`]);
});

test("périmètre relatif lu depuis le dossier du canal", () => {
  assert.deepEqual(missionPathsOutside(`écris ${A}/docs/a.md`, `docs\n${E}`, A), []);
});
