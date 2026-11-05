// Tests des fonctions pures des commandes « / » de la barre (hud/src/commandes.js, 05/11).
// Lancés par `node --test hud/tests/` (et par tests/test_commandes_chat.py dans pytest, qui compare aussi
// l'analyse à celle du cœur, astrel/commandes_chat.py, sur les mêmes textes).

import assert from "node:assert/strict";
import { test } from "node:test";
import { analyser, COMMANDES, propositions, texteRoutage } from "../src/commandes.js";

test("commande connue en premier mot : nom canonique, alias, accents, majuscules", () => {
  assert.deepEqual(analyser("/clear"), { nom: "clear", arg: "", code: "", inconnue: "" });
  assert.equal(analyser("/nouveau").nom, "clear");
  assert.equal(analyser("/New  ").nom, "clear");
  assert.equal(analyser("/Dictée").nom, "dictee");
  assert.equal(analyser("/tout-arrêter").nom, "tout-arreter");
  assert.equal(analyser("/stop-all").nom, "tout-arreter");
  assert.deepEqual(analyser("/modèle Opus"), { nom: "modele", arg: "opus", code: "", inconnue: "" });
  assert.deepEqual(analyser("/mode hybrid"), { nom: "mode", arg: "hybride", code: "", inconnue: "" });
});

test("un chemin, un « / » seul ou une espace devant ne sont PAS des commandes", () => {
  for (const s of ["/home/user/Projets/x.py est-ce que ça marche ?", "/tmp.log", "/1 truc", "/", "/ clear", "//clear",
    " /clear", "/clear.", "/clear!", "/mode-", "~/clear", "clear", "", "/home/user"]) {
    assert.equal(analyser(s), null, s);
  }
});

test("mot inconnu : commande inconnue, rien n'est envoyé ; arguments contrôlés", () => {
  assert.deepEqual(analyser("/xyz"), { nom: "", arg: "", code: "inconnue", inconnue: "xyz" });
  assert.equal(analyser("/tmp est plein ?").code, "inconnue"); // une espace devant l'envoie tel quel
  assert.equal(analyser("/clear maintenant").code, "sans_argument");
  assert.equal(analyser("/local").code, "texte_manquant");
  assert.equal(analyser("/local   ").code, "texte_manquant");
  assert.equal(analyser("/mode").code, "choix");
  assert.equal(analyser("/mode turbo").code, "choix");
  assert.equal(analyser("/modele gpt").code, "choix");
});

test("/local et /claude : le préfixe du routeur, texte multiligne gardé", () => {
  const a = analyser("/local résume ce fichier\nen 3 points");
  assert.equal(texteRoutage(a), "en local : résume ce fichier\nen 3 points");
  assert.equal(texteRoutage(analyser("/claude salut")), "avec Claude : salut");
  assert.equal(texteRoutage(analyser("/clear")), "");
  assert.equal(texteRoutage(analyser("/local")), "");
});

test("propositions : « / » seul liste tout, filtre par nom et alias, langue", () => {
  assert.equal(propositions("/").length, COMMANDES.length);
  assert.deepEqual(propositions("/cl").map((p) => p.texte), ["/clear", "/claude "]);
  assert.deepEqual(propositions("/nou").map((p) => p.texte), ["/clear"]); // alias /nouveau
  assert.deepEqual(propositions("/he", "en").map((p) => p.texte), ["/help"]);
  assert.deepEqual(propositions("/ai", "en").map((p) => p.texte), ["/help"]); // nom français tapé, montré en anglais
  assert.equal(propositions("/mod")[0].texte, "/mode ");
  assert.equal(propositions("/mod")[0].complete, false);
  assert.equal(propositions("/clear")[0].complete, true);
});

test("propositions : valeurs de /mode et /modele ; rien pour un chemin, du texte libre ou plusieurs lignes", () => {
  assert.deepEqual(propositions("/mode ").map((p) => p.texte), ["/mode auto", "/mode local", "/mode claude", "/mode hybride"]);
  assert.deepEqual(propositions("/mode h").map((p) => p.valeur), ["hybride"]);
  assert.deepEqual(propositions("/model o", "en").map((p) => p.texte), ["/model opus"]);
  assert.deepEqual(propositions("/modele o").map((p) => p.texte), ["/modele opus"]);
  for (const s of ["/data/x", "/local ", "/clear ", "/mode auto x", "/cl\nx", " /cl", "bonjour", "/1"]) {
    assert.deepEqual(propositions(s), [], s);
  }
});
