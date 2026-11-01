// Langue de l'interface du HUD (20/10) : hud/src/i18n.js et hud/src/langues.js.
// Lancés par `node --test hud/tests/` (et par tests/test_i18n.py dans pytest).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { dictionaries, lang, t, tx, useLang } from "../src/i18n.js";

const { FR, EN } = dictionaries();
const PH = /\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g;
const forms = (v) => (typeof v === "object" ? Object.values(v) : [v]);
const names = (v) => new Set(forms(v).flatMap((s) => [...s.matchAll(PH)].map((m) => m[1])));

test("chaque clé française a sa clé anglaise, et inversement", () => {
  assert.deepEqual(Object.keys(EN).filter((k) => !(k in FR)), []);
  assert.deepEqual(Object.keys(FR).filter((k) => !(k in EN)), []);
});

test("mêmes valeurs à remplir des deux côtés, aucune traduction vide", () => {
  for (const k of Object.keys(FR)) {
    for (const s of forms(EN[k])) assert.ok(String(s).length > 0 || k === "grants.write", k);
    // l'anglais n'invente pas de {nom} que le code ne passe pas
    for (const n of names(EN[k])) assert.ok(names(FR[k]).has(n) || n === "n", `${k} : {${n}}`);
  }
});

test("français par défaut, repli sur le français, pluriels", () => {
  assert.equal(lang(), "fr"); // pas de localStorage ici : défaut
  assert.equal(t("estop.stop_all"), "Stop tout");
  assert.equal(t("lift.files", { n: 1 }), "1 fichier modifié");
  assert.equal(t("lift.files", { n: 0 }), "0 fichier modifié"); // règle française : 0 au singulier
  assert.equal(t("lift.files", { n: 3 }), "3 fichiers modifiés");
  assert.equal(t("clé.inconnue"), "clé.inconnue");
  useLang("en");
  try {
    assert.equal(t("estop.stop_all"), "Stop all");
    assert.equal(t("lift.files", { n: 1 }), "1 file changed");
    assert.equal(t("lift.files", { n: 0 }), "0 files changed");
    assert.equal(t("plans.chip_to_review", { n: 2 }), "Project plans: 2 plans to review");
  } finally { useLang("fr"); }
});

test("textes du cœur : clé exacte, modèle à parties variables, morceaux, unités, repli", () => {
  assert.equal(tx("Réglages appliqués : cerveau redémarré, conversation reprise."),
    "Réglages appliqués : cerveau redémarré, conversation reprise."); // français : inchangé
  useLang("en");
  try {
    assert.equal(tx("Garde-fou levé."), "Guardrail lifted.");
    assert.equal(tx("Voix Edge revenue."), "Edge voice is back.");
    assert.equal(tx("Attention, une commande administrateur et une publication git (push). Je continue ?"),
      "Heads up: an admin command and a git publish (push). Shall I go ahead?");
    assert.equal(tx("NVIDIA RTX, 8,0 Go de mémoire graphique"), "NVIDIA RTX, 8.0 GB of graphics memory");
    assert.equal(tx("Texte de Claude, inconnu du catalogue."), "Texte de Claude, inconnu du catalogue.");
    // « avant de » ne devient pas « avant of » : un modèle sans début fixe doit avoir des mots fixes
    assert.equal(tx("Qwen 3.5, moyen, réfléchit avant de répondre"), "Qwen 3.5, medium, thinks before answering");
  } finally { useLang("fr"); }
});

test("chaque clé utilisée par le HUD existe (t(), data-i18n)", () => {
  const files = ["main.js", "settings.js", "plans.js", "guide.js", "audit.js", "conn.js", "modal.js", "markdown.js",
    "viewer.js", "capacites.js"].map((f) => readFileSync(new URL(`../src/${f}`, import.meta.url), "utf8"));
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  const used = new Set();
  for (const src of files) {
    for (const m of src.matchAll(/\b(?:t|tr)\(\s*"([a-z_][\w.]*)"/g)) used.add(m[1]);
    for (const m of src.matchAll(/\b(?:t|tr)\([^)"]*?\?\s*"([a-z_][\w.]*)"\s*:\s*"([a-z_][\w.]*)"/g)) { used.add(m[1]); used.add(m[2]); }
  }
  for (const m of html.matchAll(/data-i18n(?:-title|-aria|-placeholder)?="([^"]+)"/g)) used.add(m[1]);
  assert.ok(used.size > 300, `${used.size} clés`);
  assert.deepEqual([...used].filter((k) => !(k in FR)), []);
});

// 23/10 (plan P31, demande de l'utilisateur) : nom affiché « Astrel » par défaut, configurable,
// remplacé dans les textes du HUD seulement (jamais dans un paramètre ni un texte du cœur).
test("nom affiché : Astrel par défaut, élision, mot d'éveil et identifiants intacts", async () => {
  const { DEFAULT_NAME, assistantName, cleanName, useName, withName } = await import("../src/i18n.js");
  assert.equal(DEFAULT_NAME, "Astrel");
  assert.equal(assistantName(), "Astrel"); // pas de localStorage ici : défaut
  assert.equal(t("settings.title"), "Réglages d'Astrel");
  assert.equal(withName("Tout ce qu'Astrel sait faire"), "Tout ce qu'Astrel sait faire");
  assert.equal(withName("Tu peux toujours parler à Astrel."), "Tu peux toujours parler à Astrel.");
  assert.equal(withName("tu peux enchaîner, sans « Hey Jarvis »"), "tu peux enchaîner, sans « Hey Jarvis »");
  assert.equal(withName('no "Hey Jarvis" needed'), 'no "Hey Jarvis" needed');
  assert.equal(withName("systemctl --user start astrel, /data/Studio/Astrel/run.sh, ASTREL_THEME, astrel-hud"),
    "systemctl --user start astrel, /data/Studio/Astrel/run.sh, ASTREL_THEME, astrel-hud");
  // un paramètre (texte reçu de Claude) n'est jamais touché
  assert.ok(t("choice.waiting_toast", { question: "Astrel ?" }).includes("Astrel ?"));
  useLang("en");
  try {
    assert.equal(withName("Shut down Astrel"), "Shut down Astrel");
    assert.equal(withName("Connection to Astrel's core"), "Connection to Astrel's core");
  } finally { useLang("fr"); }
  try {
    useName("Nova");
    assert.equal(t("settings.title"), "Réglages de Nova"); // consonne : pas d'élision
    useName("  $& <b>Iris</b>\u0007  ");
    assert.equal(assistantName(), "$& Iris"); // ni balise, ni caractère de contrôle ; « $& » reste littéral
    assert.equal(withName("Éteindre Astrel"), "Éteindre $& Iris");
    assert.equal(cleanName(""), "Astrel");
    assert.equal(cleanName("x".repeat(80)).length, 32);
  } finally { useName("Astrel"); }
});

// 23/10 (plan P31, fiches 4 et 5) : écarts corrigés.
test("nom affiché : Astrel.service, OK Astrel, élision Unicode, balises, textes du cœur", async () => {
  const { cleanName, withName } = await import("../src/i18n.js");
  assert.equal(withName("systemctl --user restart Astrel.service"), "systemctl --user restart Astrel.service");
  assert.equal(withName("dis « OK Astrel » ou « Hey Jarvis »"), "dis « OK Astrel » ou « Hey Jarvis »");
  assert.equal(withName("Parle à Astrel."), "Parle à Astrel.");
  assert.equal(withName("une bibliothèque Astrel"), "une bibliothèque Astrel"); // pas « bibliothèqu'Astrel »
  assert.equal(cleanName("<b>Nova</b>"), "Nova");
  assert.equal(cleanName("<>"), "Astrel");
  // textes du cœur connus du catalogue : le nom dans les parties fixes, en français comme en anglais
  assert.equal(tx("réponse refusée par Astrel"), "réponse refusée par Astrel");
  assert.equal(tx("une modification de Astrel.txt, fichier protégé d'Astrel"),
    "une modification de Astrel.txt, fichier protégé d'Astrel");
  assert.equal(tx("Astrel : texte inconnu du catalogue"), "Astrel : texte inconnu du catalogue"); // texte de Claude
  useLang("en");
  try {
    assert.equal(tx("réponse refusée par Astrel"), "reply refused by Astrel");
    assert.equal(tx("une modification de Astrel.txt, fichier protégé d'Astrel"), "a change to Astrel.txt, a protected Astrel file");
  } finally { useLang("fr"); }
});
