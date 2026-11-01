// Connexion au cœur d'Astrel : WebSocket local, reconnu par un cookie de session.
//
// 15/10 : plus de jeton dans l'adresse. `./hud.sh` ouvre `/login?k=ticket`
// (ticket à usage unique, 60 s) ; le serveur pose un cookie HttpOnly
// SameSite=Strict, envoyé tout seul avec le WebSocket et après un
// rechargement. Le jeton durable ne quitte plus `runtime/hud-token`.
// 15/10 : le cookie est signé par une clé stable côté serveur, il survit à une
// relance d'Astrel : après une coupure, la page se reconnecte seule.

import { askKeeper, keepOnReplace, replacedBy, restoreAfterReplace, STASH_PREFIX, stashForReplace } from "./remplacement.js";
import { t } from "./i18n.js";

// 19/10 : un seul HUD (`./hud.sh` fait fermer les anciennes fenêtres). Ce qui est tapé
// dans l'ancienne passe dans la nouvelle (remplacement.js) ; plans.js garde ses réponses.
keepOnReplace("ask", askKeeper(document));
let closing = false; // `hud_close` reçu : cette page ne reprend plus rien
function restoreStash() {
  if (closing) return;
  try { restoreAfterReplace(localStorage, performance.timeOrigin); } catch { /* sans stockage */ }
}
window.addEventListener("storage", (e) => {
  if (e.key && e.key.startsWith(STASH_PREFIX) && e.newValue) restoreStash();
});

const listeners = new Map();
let socket = null;
let nextId = 1;
const pending = new Map();
let retry = 500;
let hudVersion = null; // empreinte des fichiers du HUD vue au premier « hello »
const openWaiters = new Set(); // commandes en attente d'une reconnexion
let lostWarned = false; // « rouvre le HUD » déjà affiché

// Options de l'adresse (« #demo ») : lues avant tout changement de la barre d'adresse.
export const initialHash = location.hash;

// Ancien jeton gardé par une version précédente du HUD : inutile désormais.
try { sessionStorage.removeItem("astrel-token"); } catch { /* navigation privée */ }

export function on(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
  return () => listeners.get(type).delete(fn);
}

function emit(type, payload) {
  for (const fn of listeners.get(type) || []) {
    try { fn(payload); } catch (err) { console.error(`écouteur ${type}`, err); }
  }
  for (const fn of listeners.get("*") || []) {
    try { fn(payload); } catch (err) { console.error("écouteur *", err); }
  }
}

// La page est-elle encore reconnue ? Non si la session a expiré (30 jours sans
// usage) ou si la clé de session a été refaite : il faut rouvrir le HUD.
// Cet appel renouvelle aussi le cookie.
async function sessionLost() {
  try {
    const r = await fetch("/api/session", { credentials: "same-origin", cache: "no-store" });
    return r.status === 401;
  } catch { return false; } // Astrel arrêté : on réessaie simplement
}

export function connect() {
  const url = `ws://${location.host}/ws`;
  socket = new WebSocket(url);
  socket.onopen = () => {
    retry = 500;
    // 19/10 : heure de chargement de CETTE page, pour que `./hud.sh` reconnaisse la sienne.
    try { socket.send(JSON.stringify({ cmd: "hud_here", time_origin: performance.timeOrigin / 1000 })); } catch { /* fermée */ }
    emit("connection", { online: true });
    for (const wake of openWaiters) wake();
    openWaiters.clear();
  };
  socket.onmessage = (msg) => {
    let ev;
    try { ev = JSON.parse(msg.data); } catch { return; }
    if (ev.type === "ack") {
      const resolve = pending.get(ev.id);
      if (resolve) { pending.delete(ev.id); resolve(ev.result); }
      return;
    }
    if (ev.type === "hello") {
      // Astrel relancé avec un HUD reconstruit : recharger pour prendre le
      // nouveau code (le jeton survit, il est en sessionStorage).
      if (ev.hud_version) {
        if (hudVersion && hudVersion !== ev.hud_version) { location.reload(); return; }
        hudVersion = ev.hud_version;
      }
      for (const sticky of ev.sticky || []) emit(sticky.type, sticky);
      for (const old of ev.history || []) emit(old.type, { ...old, replay: true });
      emit("hello", ev);
      restoreStash(); // après « hello » : plans.js s'est déclaré, la barre existe
      return;
    }
    if (ev.type === "hud_close") {
      // 19/10 : un HUD plus récent vient de s'ouvrir (`./hud.sh`) ; celui-ci s'efface,
      // après avoir mis à l'abri ce qui était tapé et pas envoyé.
      if (replacedBy(ev, performance.timeOrigin)) {
        closing = true;
        try { stashForReplace(localStorage, ev.before); } catch { /* sans stockage */ }
        window.close();
      }
      return;
    }
    emit(ev.type, ev);
  };
  socket.onclose = async () => {
    for (const resolve of pending.values()) resolve({ ok: false, error: t("conn.disconnected") });
    pending.clear();
    if (await sessionLost()) {
      // Message une seule fois (toast), puis on réessaie sans insister.
      emit("connection", lostWarned ? { online: false } : { online: false,
        reason: t("conn.expired") });
      lostWarned = true;
      setTimeout(connect, 8000);
      return;
    }
    emit("connection", { online: false });
    setTimeout(connect, retry);
    retry = Math.min(retry * 1.8, 8000);
  };
}

// Pendant une coupure, une commande ATTEND la reconnexion (jusqu'à `waitMs`)
// au lieu d'échouer : le 26/09, des messages tapés pendant une reconnexion
// disparaissaient, avec un simple avertissement que l'utilisateur n'a pas vu.
function waitOpen(waitMs) {
  if (socket && socket.readyState === WebSocket.OPEN) return Promise.resolve(true);
  return new Promise((resolve) => {
    const wake = () => { clearTimeout(timer); resolve(true); };
    const timer = setTimeout(() => { openWaiters.delete(wake); resolve(false); }, waitMs);
    openWaiters.add(wake);
  });
}

export async function command(cmd, args = {}, waitMs = 10000, replyMs = 15000) {
  if (!(await waitOpen(waitMs))) return { ok: false, error: t("conn.unreachable") };
  return new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, resolve);
    // L'identifiant de la requête passe APRÈS les arguments : avant le 14/10, le « id » de la
    // confirmation l'écrasait, l'accusé ne revenait jamais et « Oui pour 30 min » affichait
    // « pas de réponse » 15 s plus tard, comme si le clic avait échoué.
    socket.send(JSON.stringify({ ...args, cmd, id }));
    setTimeout(() => {
      if (pending.has(id)) { pending.delete(id); resolve({ ok: false, error: t("conn.no_reply") }); }
    }, replyMs); // une pièce jointe de quelques Mo met plus longtemps à partir
  });
}
