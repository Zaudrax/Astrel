// Un seul HUD à l'écran (19/10, demande : « HUD » sur la bulle ou Ctrl+Alt+H
// ouvrait une DEUXIÈME fenêtre au lieu de ramener la première).
//
// Sous GNOME Wayland, rien ne peut ramener au premier plan une fenêtre de Chrome
// déjà ouverte (pas de wmctrl, Shell.Eval et Introspect refusés, et
// `window.focus()` sans clic dans la page est ignoré). `./hud.sh` ouvre donc la
// nouvelle fenêtre, attend qu'elle soit connectée (chaque page envoie `hud_here`
// avec son heure de chargement), puis le cœur envoie `hud_close {before}` :
// chaque page chargée AVANT cet instant se ferme. La nouvelle (chargée après) reste.
//
// Relecture du 19/10 : ce qui est tapé et pas encore envoyé (barre de chat, réponse
// à un plan) ne doit pas disparaître avec l'ancienne fenêtre. Avant de se fermer,
// la page le met à l'abri dans localStorage (une clé par page, avec l'heure et
// `before`) ; la nouvelle page le reprend (évènement `storage`, ou à son premier
// « hello »), une seule fois, puis efface la clé. Plus de 2 minutes : jeté.

export const STASH_PREFIX = "astrel-hud-relais:";
export const STASH_MAX_AGE_MS = 2 * 60 * 1000;

export function replacedBy(ev, timeOrigin) {
  const before = Number(ev && ev.before);
  if (!Number.isFinite(before) || before <= 0 || !Number.isFinite(timeOrigin)) return false;
  return timeOrigin < before * 1000; // `before` en secondes (time.time()), timeOrigin en ms
}

// Ce qui se met à l'abri : nom → { save() → valeur ou rien, restore(valeur) }.
const keepers = new Map();

export function keepOnReplace(name, keeper) {
  keepers.set(name, keeper);
}

function filled(v) {
  if (v == null || v === "") return false;
  if (typeof v === "string") return v.trim() !== "";
  if (typeof v === "object") return Object.keys(v).length > 0;
  return true;
}

// Page qui va se fermer : écrit ce qu'elle garde. Vrai si quelque chose a été écrit.
export function stashForReplace(storage, before, now = Date.now(), pageId = String(Math.random()).slice(2)) {
  const items = {};
  for (const [name, k] of keepers) {
    try {
      const v = k.save();
      if (filled(v)) items[name] = v;
    } catch (err) { console.error(`mise à l'abri ${name}`, err); }
  }
  if (!Object.keys(items).length) return false;
  try {
    storage.setItem(STASH_PREFIX + pageId, JSON.stringify({ ts: now, before: Number(before) || 0, items }));
    return true;
  } catch { return false; } // stockage plein ou interdit : tant pis, comme avant
}

// Nouvelle page : reprend ce que les anciennes ont laissé. Une page plus ANCIENNE que
// `before` (elle va se fermer aussi) n'y touche pas. Rend les noms repris.
export function restoreAfterReplace(storage, timeOrigin, now = Date.now()) {
  const done = [];
  let keys = [];
  try {
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i);
      if (k && k.startsWith(STASH_PREFIX)) keys.push(k);
    }
  } catch { return done; }
  for (const key of keys) {
    let stash = null;
    try { stash = JSON.parse(storage.getItem(key) || "null"); } catch { stash = null; }
    const ts = Number(stash && stash.ts);
    if (!stash || !Number.isFinite(ts) || now - ts > STASH_MAX_AGE_MS || ts - now > STASH_MAX_AGE_MS) {
      try { storage.removeItem(key); } catch { /* sans stockage */ }
      continue;
    }
    if (!Number.isFinite(timeOrigin) || timeOrigin < Number(stash.before) * 1000) continue; // pas pour nous
    try { storage.removeItem(key); } catch { continue; } // effacée AVANT : jamais reprise deux fois
    for (const [name, value] of Object.entries(stash.items || {})) {
      const k = keepers.get(name);
      if (!k) continue;
      try { k.restore(value); done.push(name); } catch (err) { console.error(`reprise ${name}`, err); }
    }
  }
  return done;
}

// Barre de chat (#ask) : gardée ici plutôt que dans main.js. Repris dans une barre
// vide tel quel ; sinon ajouté à la suite (rien n'est écrasé).
export function askKeeper(doc) {
  return {
    save: () => doc.querySelector("#ask")?.value || "",
    restore: (text) => {
      const ask = doc.querySelector("#ask");
      if (!ask || typeof text !== "string" || !text.trim()) return;
      const cur = ask.value;
      if (cur.includes(text.trim())) return;
      ask.value = cur.trim() ? `${cur.trimEnd()}\n${text}` : text;
      ask.dispatchEvent(new Event("input", { bubbles: true })); // hauteur de la barre (fitAsk)
    },
  };
}
