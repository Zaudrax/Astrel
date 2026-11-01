// Heure des messages du fil (23/10, demande de l'utilisateur) : hud/src/heure.js.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { msgTime } from "../src/heure.js";

const NOW = Date.UTC(2026, 9, 23, 12, 0, 0) / 1000;

test("heure seule le jour même, HH:MM, datetime ISO", () => {
  const ts = NOW - 3600;
  const r = msgTime(ts, NOW, "fr-FR");
  assert.match(r.text, /^\d{2}:\d{2}$/);
  assert.equal(r.iso, new Date(ts * 1000).toISOString());
  assert.ok(r.title.length > r.text.length);
});

test("plus d'un jour : date courte et heure", () => {
  const r = msgTime(NOW - 2 * 86400, NOW, "fr-FR");
  assert.match(r.text, /^\d{2}\/\d{2} \d{2}:\d{2}$/);
  const en = msgTime(NOW - 2 * 86400, NOW, "en-GB");
  assert.match(en.text, /^\d{2}\/\d{2} \d{2}:\d{2}$/);
});

test("ts absent ou illisible : l'heure d'arrivée", () => {
  for (const bad of [undefined, null, "12", NaN, -5, 0]) {
    assert.equal(msgTime(bad, NOW, "fr-FR").iso, new Date(NOW * 1000).toISOString());
  }
});

test("main.js : balise <time> sur chaque message, ts du cœur transmis", () => {
  const src = readFileSync(new URL("../src/main.js", import.meta.url), "utf8");
  assert.match(src, /el\("time", "msg-time"/);
  assert.match(src, /time\.dateTime = when\.iso/);
  assert.equal((src.match(/ts: ev\.ts/g) || []).length, 4); // transcript, reply_delta, reply, report
  assert.equal((src.match(/timeEl\(ev\.ts\)/g) || []).length, 2); // carte de médias, question à choix
});
