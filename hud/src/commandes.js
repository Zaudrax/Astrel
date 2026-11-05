// 05/11 (demande de l'utilisateur : « pouvoir mettre des commandes dans le chat, genre /clear ») : commandes « / » de la
// barre. Table et règles IDENTIQUES à astrel/commandes_chat.py (tests/test_commandes_chat.py compare les deux) :
// c'est une commande seulement si le message commence par « / » suivi d'un MOT (lettres, tirets internes) puis
// d'une espace ou de la fin ; « /data/x… », « /tmp.log », « / » seul restent du texte. Un mot hors de la table est
// une commande INCONNUE (rien n'est envoyé) ; une espace devant le « / » envoie le texte tel quel.
// Fonctions PURES (pas de DOM) : analyse, et propositions de la liste qui s'ouvre en tapant « / » (commandes-ui.js).

// `arg` : "" (rien après le nom), "texte" (texte libre exigé), "choix" (une des `valeurs`).
export const COMMANDES = [
  { nom: "aide", en: "help", alias: ["help"], arg: "" },
  { nom: "clear", en: "clear", alias: ["nouveau", "new"], arg: "" },
  { nom: "local", en: "local", alias: [], arg: "texte" },
  { nom: "claude", en: "claude", alias: [], arg: "texte" },
  // 05/11 (lot 8 du moteur local) : la question au modèle local, réflexion activée pour elle seule (cœur)
  { nom: "reflechir", en: "think", alias: ["think", "reflexion"], arg: "texte" },
  { nom: "mode", en: "mode", alias: [], arg: "choix", valeurs: ["auto", "local", "claude", "hybride"] },
  { nom: "modele", en: "model", alias: ["model"], arg: "choix", valeurs: ["sonnet", "opus", "haiku"] },
  { nom: "horsligne", en: "offline", alias: ["hors-ligne", "offline"], arg: "" },
  { nom: "enligne", en: "online", alias: ["en-ligne", "online"], arg: "" },
  { nom: "silence", en: "silent", alias: ["silencieux", "silent"], arg: "" },
  { nom: "vocal", en: "voice", alias: ["voix", "voice", "conversation"], arg: "" },
  { nom: "dictee", en: "dictation", alias: ["dictation"], arg: "" },
  { nom: "stop", en: "stop", alias: [], arg: "" },
  { nom: "tout-arreter", en: "stop-all", alias: ["toutarreter", "stop-tout", "stop-all"], arg: "" },
];
export const ALIAS_VALEURS = { hybrid: "hybride" };
// Préfixes que le routeur du cœur connaît déjà (`routeur.forced_engine`) : /local et /claude ne font que les écrire.
export const PREFIXES = { local: "en local : ", claude: "avec Claude : " };
export const MOT_MAX = 30;

/** Minuscules sans accents (« /Dictée » = « /dictee »), comme `commandes_chat.plier`. */
export function plier(s) {
  return String(s ?? "").toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");
}

const INDEX = new Map();
for (const c of COMMANDES) for (const n of [c.nom, c.en, ...c.alias]) INDEX.set(plier(n), c);
const MOT = /^(?:\p{L}+)(?:-\p{L}+)*$/u;

/** Nom montré dans la langue de l'interface. */
export const nomAffiche = (c, lang = "fr") => (lang === "en" ? c.en : c.nom);

/**
 * Commande tapée, ou null : le texte part comme un message ordinaire. `texte` tel que tapé (« ␣/clear » n'est pas
 * une commande). Rend { nom, arg, code, inconnue } : `code` vide si la commande est bonne, sinon « inconnue »,
 * « sans_argument », « texte_manquant » ou « choix » (rien n'est fait).
 */
export function analyser(texte) {
  const s = String(texte ?? "");
  const tok = /^\/(\S*)/u.exec(s);
  if (!tok) return null;
  const mot0 = tok[1];
  if (!mot0 || mot0.length > MOT_MAX || !MOT.test(mot0)) return null;
  const mot = plier(mot0);
  let reste = s.slice(tok[0].length).trim();
  const c = INDEX.get(mot);
  if (!c) return { nom: "", arg: "", code: "inconnue", inconnue: mot };
  if (c.arg === "" && reste) return { nom: c.nom, arg: "", code: "sans_argument", inconnue: "" };
  if (c.arg === "texte" && !reste) return { nom: c.nom, arg: "", code: "texte_manquant", inconnue: "" };
  if (c.arg === "choix") {
    let v = plier(reste);
    v = ALIAS_VALEURS[v] || v;
    if (!c.valeurs.includes(v)) return { nom: c.nom, arg: "", code: "choix", inconnue: "" };
    reste = v;
  }
  return { nom: c.nom, arg: reste, code: "", inconnue: "" };
}

export const commande = (nom) => COMMANDES.find((c) => c.nom === nom) || null;

/** /local et /claude : le message avec le préfixe que le routeur reconnaît (« en local : … »). */
export function texteRoutage(a) {
  return a && !a.code && PREFIXES[a.nom] ? PREFIXES[a.nom] + a.arg : "";
}

/**
 * Propositions de la liste « / » pour ce qui est tapé (une seule ligne, rien devant le « / ») :
 * - « /cl » : les commandes dont un nom commence par « cl » (nom montré dans la langue, alias compris) ;
 * - « /mode a » : les valeurs de /mode qui commencent par « a ».
 * Chaque proposition : { c (la commande), valeur, texte (ce que la barre devient), complete (rien à ajouter) }.
 */
export function propositions(texte, lang = "fr") {
  const s = String(texte ?? "");
  if (!s.startsWith("/") || /[\r\n]/.test(s)) return [];
  const sp = s.search(/\s/u);
  if (sp < 0) {
    const debut = plier(s.slice(1));
    if (debut && !/^[\p{L}-]+$/u.test(debut)) return [];
    const out = [];
    for (const c of COMMANDES) {
      const noms = [nomAffiche(c, lang), c.nom, c.en, ...c.alias].map(plier);
      if (!noms.some((n) => n.startsWith(debut))) continue;
      const nom = "/" + nomAffiche(c, lang);
      out.push({ c, valeur: "", texte: c.arg ? `${nom} ` : nom, complete: !c.arg });
    }
    return out;
  }
  const a = INDEX.get(plier(s.slice(1, sp)));
  if (!a || a.arg !== "choix") return [];
  const reste = s.slice(sp).replace(/^\s+/u, "");
  if (/\s/u.test(reste)) return [];
  const debut = plier(reste);
  const nom = "/" + nomAffiche(a, lang);
  return a.valeurs.filter((v) => v.startsWith(debut) || (debut && Object.entries(ALIAS_VALEURS)
    .some(([al, vrai]) => vrai === v && al.startsWith(debut))))
    .map((v) => ({ c: a, valeur: v, texte: `${nom} ${v}`, complete: true }));
}
