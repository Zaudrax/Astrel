// Heure des messages du fil (23/10, demande de l'utilisateur : « afficher l'heure sur chaque message,
// envoyé et reçu », discrète). Chaque évènement du cœur porte `ts` (secondes, astrel/events.py
// `publish`), y compris l'historique rejoué à un HUD qui se reconnecte : c'est l'heure d'origine.
// Sans `ts` (cas imprévu), l'heure d'arrivée dans le HUD. Plus d'un jour : la date courte aussi.

const DAY_S = 24 * 3600;

/**
 * Libellé de l'heure d'un message.
 * @param {number} ts secondes (epoch) ; autre chose = maintenant
 * @param {number} now secondes (epoch)
 * @param {string} loc locale du HUD (« fr-FR », « en-GB »…)
 * @returns {{ text: string, iso: string, title: string }}
 */
export function msgTime(ts, now = Date.now() / 1000, loc = "fr-FR") {
  const s = typeof ts === "number" && Number.isFinite(ts) && ts > 0 ? ts : now;
  const d = new Date(s * 1000);
  const hm = d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
  const old = now - s >= DAY_S;
  const text = old ? `${d.toLocaleDateString(loc, { day: "2-digit", month: "2-digit" })} ${hm}` : hm;
  const title = d.toLocaleString(loc, { dateStyle: "full", timeStyle: "short" });
  return { text, iso: d.toISOString(), title };
}
