// Médias du fil (hud/src/medias.js, 22/10, plan P26) : fonctions pures (adresses, nettoyage des
// descripteurs, tailles), et règles de sûreté lues dans la source. Lancés par `node --test hud/tests/`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { cleanItem, cleanItems, formatSize, KINDS, MAX_ITEMS, MEDIA_ID, mediaUrl } from "../src/medias.js";
import { entrees, normaliser } from "../src/choix.js";

const ID = "0123456789abcdef01234567";
const SRC = readFileSync(new URL("../src/medias.js", import.meta.url), "utf8");

test("adresse seulement pour un id du cœur (24 hexadécimaux)", () => {
  assert.equal(mediaUrl(ID), `/api/media/${ID}`);
  assert.equal(mediaUrl(ID, "thumb"), `/api/media/${ID}?v=thumb`);
  assert.equal(mediaUrl(ID, "page", 3), `/api/media/${ID}?v=page&n=3`);
  for (const bad of ["", "../config", `${ID}x`, ID.toUpperCase(), `${ID}/..`, "javascript:alert(1)", null, 42, {}]) {
    assert.equal(mediaUrl(bad), "", String(bad));
  }
  for (const n of [0, -1, 2.5, 2001, NaN, "3"]) assert.equal(mediaUrl(ID, "page", n), "");
  assert.ok(MEDIA_ID.test(ID));
});

test("descripteur nettoyé : genre connu, nom sans contrôles, nombres sûrs", () => {
  const d = cleanItem({ id: ID, kind: "pdf", name: "rapport‮.pdf\n", size: 1234.6, pages: 3, w: -1, h: "9",
    open_only: 1, path: "/home/user/Projets/a\u0000b.pdf", extra: "<img onerror>" });
  assert.deepEqual(d, { id: ID, kind: "pdf", name: "rapport .pdf", size: 1234, w: null, h: null, pages: 3,
    openOnly: true, path: "/home/user/Projets/a b.pdf" });
  for (const bad of [null, "x", { id: ID, kind: "svg" }, { id: "x", kind: "image" }, { kind: "image" }]) {
    assert.equal(cleanItem(bad), null);
  }
  assert.equal(cleanItem({ id: ID, kind: "image", name: "" }).name.length > 0, true);
  const many = Array.from({ length: 14 }, () => ({ id: ID, kind: "image" }));
  assert.equal(cleanItems(many).length, MAX_ITEMS);
  assert.deepEqual(cleanItems("nope"), []);
  assert.deepEqual(KINDS, ["image", "pdf", "audio", "video"]);
});

test("taille lisible en français et en anglais", () => {
  assert.equal(formatSize(512), "512 o");
  assert.equal(formatSize(4.3 * 1024 * 1024), "4,3 Mo");
  assert.equal(formatSize(4.3 * 1024 * 1024, "en-GB"), "4.3 MB");
  assert.equal(formatSize(250 * 1024 * 1024), "250 Mo");
  assert.equal(formatSize(-1), "");
  assert.equal(formatSize(undefined), "");
});

test("question à choix : l'image suit sa réponse, même réordonnée", () => {
  const img = { id: ID, kind: "image" };
  const opts = normaliser([{ label: "B" }, { label: "A", recommended: true, image: img }, { label: "C", image: "x" }]);
  assert.deepEqual(opts.map((o) => [o.label, !!o.image]), [["A", true], ["B", false], ["C", false]]);
  assert.equal(entrees([{ label: "A", image: img }, { label: "B" }])[0].image, img);
});

test("sûreté : jamais de HTML injecté, vidéo créée au clic, audio sans préchargement", () => {
  for (const bad of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "autoplay", "iframe"]) {
    assert.ok(!SRC.includes(bad), bad);
  }
  assert.ok(SRC.includes('a.preload = "none"'));
  const play = SRC.slice(SRC.indexOf("play.onclick"), SRC.indexOf("box.append(play)"));
  assert.ok(play.includes('el("video"') && play.includes("v.play()"));
  assert.ok(!SRC.slice(0, SRC.indexOf("play.onclick")).includes('el("video"'), "vidéo jamais créée avant le clic");
  assert.ok(SRC.includes('hooks.command("media_playing"'));
  assert.ok(SRC.includes('img.loading = "lazy"'));
});

test("micro retenu redemandé après une reconnexion (relecture P26, 23/10)", async () => {
  const { initMedias, resendMicHold, watch } = await import("../src/medias.js");
  const sent = [];
  initMedias({ command: async (cmd, args) => { sent.push([cmd, args.playing]); return { ok: true }; } });
  assert.equal(resendMicHold(), false, "rien ne joue : rien à redemander");
  const on = {};
  const media = { addEventListener: (type, fn) => { on[type] = fn; } };
  watch(media, () => {});
  on.play();
  assert.deepEqual(sent, [["media_playing", true]]);
  assert.equal(resendMicHold(), true);
  assert.deepEqual(sent.at(-1), ["media_playing", true]);
  on.pause();
  assert.deepEqual(sent.at(-1), ["media_playing", false]);
  assert.equal(resendMicHold(), false);
  assert.equal(sent.length, 3);
});

test("pièce ratée mais déjà retirée : la prochaine Entrée n'est pas bloquée (relecture P26, 23/10)", () => {
  const main = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
  const fn = main.slice(main.indexOf("function noteFailed(a)"), main.indexOf("const attached = []"));
  assert.ok(fn.indexOf("if (!attached.includes(a)) return;") > 0
    && fn.indexOf("if (!attached.includes(a)) return;") < fn.indexOf("recentFailures.push"));
  assert.ok(main.includes('on("hello", () => resendMicHold());'));
});
