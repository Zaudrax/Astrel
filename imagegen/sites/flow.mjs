// Ce que le worker vidéo (video.mjs) sait de Google Flow (plan P41 lot B1, 28/10/2026).
//
// Relevé B0 de l'utilisateur (vault idees/releve-b0-flow-2026-10-28.md, config/ia-web.json) : adresse
// https://flow.google.com/ (un projet sous /u/<n>/project/<id>) ; barre de création : bascule
// Image/Video, en vidéo sous-modes Frames (cases Start et End, bouton d'échange) et Ingredients
// (bouton +), formats 16:9 et 9:16, menu des modèles (Veo 3.1 - Lite par défaut, Fast, Quality,
// Omni 1.1 Flash), x1 à x4, texte « Generating will use N credits », bouton d'envoi flèche
// (orange avec une icône d'info si les crédits manquent), fenêtre d'annonce violette « Got it ».
// Omni : 360p/720p et 4/6/8/10 s ; Veo : 8 s fixe. Coûts 720p x1 : Lite 10, Fast 20, Quality
// 100, Omni 8 s 12 (4 s 7, 10 s 15). Page protégée par un reCAPTCHA invisible.
//
// ⚠️ Les sélecteurs ci-dessous ne viennent QUE de captures d'écran : aucun n'a été essayé sur le
// vrai site (consigne : aucun essai réel avant le pilote avec l'utilisateur). Tous passent par le RÔLE
// et le TEXTE VISIBLE (jamais une classe CSS générée), tolérants (FR + EN, icône en ligature
// devant le texte). Un réglage introuvable = échec `page` avec capture, jamais un réglage deviné ;
// le coût annoncé, vérifié avant l'envoi (video.mjs), rattrape un réglage mal lu (modèle,
// résolution, durée, x1 à x4 changent tous le prix).
//
// Interface : celle d'un site du noyau (noyau.mjs, en-tête) + celle d'un site VIDÉO (video.mjs,
// en-tête ; P41 B5 : ce que le parcours faisait pour Flow seul est ici, `preparer` et `regler`).

import { Echec, nombre, note, visible } from '../noyau.mjs'
import { PAUSE_COURTE_MS, TEST, cliquer, dormir, pause, survoler } from '../humain.mjs'
import { ROLES_ALERTE, nomDe, plusProche, premier, videosDe, videosInfosDe } from './commun.mjs'

const CONNEXION = /^\s*(sign in|se connecter|connexion)\s*$/i
const NOUVEAU_PROJET = /^\s*(?:add\s+)?(new project|nouveau projet|create project|créer un projet)\s*$/i
const CHAMP = /what do you want|create|prompt|describe|décri|imagine|que (voulez|veux)|générer|generate/i
// 30/10 (relevé de la nouvelle interface, profil amw, sans générer) : bouton « Start generation »
const ENVOI = /arrow_forward|^\s*start generation\s*$|^\s*(create|generate|send|submit|créer|générer|envoyer)\s*$/i
const ANNONCE = /^\s*(got it|compris|j'ai compris|ok,? got it|ok,? compris)\s*$/i
const ANNONCE_DIALOGUE = /^\s*(close|fermer|dismiss|ignorer|no thanks|non merci|not now|plus tard)\s*$/i
// 30/10 : le panneau des réglages de la création s'ouvre par « Settings trigger » (« Video · 720p · 8s ·
// 9:16 · x1 ») ; la roue « Settings » de l'en-tête règle l'AFFICHAGE de la grille : jamais elle
const REGLAGES = /^\s*settings trigger\s*$/i
const MENU_MODELES = /^\s*(select model family|choisir (la famille de )?modèles?)\s*$/i
// Dépôt d'une image de début/fin (30/10) : attestation « Rights to use this image » (I agree, décision
// de l'utilisateur du 30/10, une fois par compte), puis « Add to prompt » une fois le téléversement fini
const ATTESTATION_IMAGE = /rights to use this (image|media)|droits d.utilisation de cette image/i
const ACCEPTER = /^\s*(i agree|agree|j.accepte|accepter)\s*(\(.*\))?\s*$/i
const AJOUTER_AU_PROMPT = /^\s*(add to prompt|ajouter (au|à la) (prompt|requête))\s*$/i
const TELECHARGER = /download|télécharger/i
// Menu de téléchargement : la taille d'origine (720p), jamais le GIF ni l'agrandissement payant
const TAILLE_ORIGINE = /original size|taille d.origine|original|^\s*720p\s*$/i
const TAILLE_REFUS = /gif|upscal|agrandi|1080p|4k/i
const RECADRAGE = /^\s*(crop and save|crop & save|recadrer et enregistrer|save|enregistrer|done|terminé|ok|confirm|confirmer|use( image)?|utiliser)\s*$/i
const IMPORTER = /upload|importer|téléverser|from (your )?(computer|device)|depuis (l'|votre )?(ordinateur|appareil)|parcourir|browse/i
const CONFIRMER_ENVOI = /^\s*(generate|confirm|continue|create|générer|confirmer|continuer|créer)\s*$/i

// Modèles du pilote, par id du CATALOGUE (config/ia-web.json) : libellé sur la page, durées
// permises, coût annoncé attendu par durée en 720p x1 (relevé B0 ; Omni 6 s pas relevé), choix
// de la résolution (Omni seulement : 360p par défaut sur la page).
export const MODELES = {
  lite: { libelle: 'Veo 3.1 Lite', re: /veo\s*3\.1\s*[-–—:]?\s*lite/i, durees: [8], cout: { 8: 10 }, resolution: false },
  fast: { libelle: 'Veo 3.1 Fast', re: /veo\s*3\.1\s*[-–—:]?\s*fast/i, durees: [8], cout: { 8: 20 }, resolution: false },
  quality: { libelle: 'Veo 3.1 Quality', re: /veo\s*3\.1\s*[-–—:]?\s*quality/i, durees: [8], cout: { 8: 100 }, resolution: false },
  'omni-flash': { libelle: 'Omni 1.1 Flash', re: /omni\s*1\.1\s*flash/i, durees: [4, 6, 8, 10], cout: { 4: 7, 8: 12, 10: 15 }, resolution: true }
}
const TOUS_MODELES = /veo\s*3\.1|omni\s*1\.1/i
export const FORMATS = ['16:9', '9:16']

// ----------------------------------------------------------------- textes de la page

// Lus dans le texte RENDU (innerText : un modèle d'erreur caché ne compte pas), prompt retiré.
// Avant l'envoi : toute la page ; après : seulement les lignes NOUVELLES (le pied de page
// « Privacy Policy » du reCAPTCHA ne doit jamais passer pour un refus).
// Resserrés le 28/10 (relecture B1, fiches 8 et 9) : un texte FIXE de Flow (« Upgrade for higher
// generation limits », « Peak time pricing », « Acheter plus de crédits ») ne bloque plus l'envoi.
const ACTIVITE = /unusual activity|activité (inhabituelle|anormale|suspecte)|suspicious activity/i
const CREDITS = /not enough credits|insufficient credits|out of credits|(don.t|do not) have enough credits|no credits (left|remaining)|crédits insuffisants|pas assez de crédits|n.(avez|as) plus (de|aucun) crédits?|(ne (vous|te) reste|il n.y a) plus (de|aucun) crédits?|plus aucun crédit|crédits épuisés/i
const QUOTA = /(reached|hit|exceeded|used up|used all)( of)? (your|the|today.s)( daily| generation| usage)? (limit|quota)|(daily|generation|usage) (limit|quota) (has been |was |is )?(reached|exceeded|hit)|limit reached|(quota|rate limit) (has been |was |is )?(exceeded|reached)|atteint (votre|ta|la) limite|limite (quotidienne|de génération) (est )?(atteinte|dépassée)/i
const POINTE = /high demand|heavy (demand|load|traffic)|experiencing (high|heavy|a high|unusually high)|at capacity|peak (hours|time)(?! (pricing|prices?|rates?|tarifs?|costs?))|(?<!(tarifs?|prix) (des |aux |en )?)heures? de pointe|forte (demande|affluence)|servers? (are )?(busy|overloaded)|too many (people|users|requests)|surcharg/i
const REFUS = /violat|against (our|the) (policies|guidelines|terms)|couldn.t (generate|create)|could not (generate|create)|unable to (generate|create)|failed to generate|generation failed|failed generation|n.a pas pu (être )?(généré|créé)|impossible de (générer|créer)|enfrein|contraire (à|aux) (nos )?règles|content (filter|guidelines)|prominent people|personnalités/i

// Heure : précédée d'un mot entier (pas « format 16:9 »), « 5:00 PM », « 5 PM », « 17h », « 17h30 » ;
// une heure nue (« at 5 ») n'est pas une heure. Fuseau éventuel lu juste après (fuseauApres).
const AMPM = '(a\\.?\\s?m\\.?|p\\.?\\s?m\\.?)'
const REPRISE_HEURE = new RegExp(`(?<![\\p{L}\\p{N}])(?:after|at|until|après|à partir de|à|vers|jusqu.à)\\s+(\\d{1,2})(?:\\s*:\\s*(\\d{2})(?!\\d)\\s*${AMPM}?|\\s*h\\s*(\\d{2})?(?![\\p{L}\\d])|\\s*${AMPM})(?![\\p{L}])`, 'iu')
const REPRISE_DELAI = /(?<![\p{L}\p{N}])(?:in|dans)\s+(\d{1,3})\s*(minutes?|mins?|hours?|hrs?|heures?|h)(?![\p{L}])/iu

// Fuseaux annoncés par un site américain : abréviation → zone IANA. Abréviation inconnue : pas
// d'heure (jamais une heure d'ailleurs prise pour l'heure locale).
const FUSEAUX = {
  PT: 'America/Los_Angeles', PST: 'America/Los_Angeles', PDT: 'America/Los_Angeles',
  MT: 'America/Denver', MST: 'America/Denver', MDT: 'America/Denver',
  CT: 'America/Chicago', CST: 'America/Chicago', CDT: 'America/Chicago',
  ET: 'America/New_York', EST: 'America/New_York', EDT: 'America/New_York',
  UTC: 'UTC', GMT: 'UTC', Z: 'UTC',
  CET: 'Europe/Paris', CEST: 'Europe/Paris', BST: 'Europe/London'
}
const FUSEAUX_LONGS = [
  [/^\s*\(?\s*pacific( (standard|daylight))?( time)?/i, 'America/Los_Angeles'],
  [/^\s*\(?\s*mountain( (standard|daylight))?( time)?/i, 'America/Denver'],
  [/^\s*\(?\s*central( (standard|daylight))?( time)?/i, 'America/Chicago'],
  [/^\s*\(?\s*eastern( (standard|daylight))?( time)?/i, 'America/New_York'],
  [/^\s*\(?\s*(heure de paris|paris time|heure française)/i, 'Europe/Paris']
]

/** Fuseau écrit juste après l'heure : null (aucun : heure locale), {zone} IANA, {decalageMin}
 * (UTC+2), ou {inconnu: true}. Abréviations en MAJUSCULES seulement (« 5 PM. Please » n'en
 * est pas une). */
function fuseauApres (suite) {
  const s = String(suite || '')
  const u = /^\s*\(?\s*(?:UTC|GMT)\s*([+\-−])\s*(\d{1,2})(?::?(\d{2}))?(?!\d)/.exec(s)
  if (u) return { decalageMin: (u[1] === '+' ? 1 : -1) * (Number(u[2]) * 60 + Number(u[3] || 0)) }
  for (const [re, zone] of FUSEAUX_LONGS) if (re.test(s)) return { zone }
  const a = /^\s*\(?\s*([A-Z]{1,5})(?![\p{L}\p{N}])/u.exec(s)
  if (!a) return null
  return FUSEAUX[a[1]] ? { zone: FUSEAUX[a[1]] } : { inconnu: true }
}

/** Décalage (ms) de la zone IANA à l'instant t (heure murale de la zone − UTC). */
function decalageZone (zone, t) {
  const f = new Intl.DateTimeFormat('en-US', { timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const p = Object.fromEntries(f.formatToParts(new Date(t)).map((x) => [x.type, x.value]))
  return Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), Number(p.hour) % 24, Number(p.minute), Number(p.second)) - Math.floor(t / 1000) * 1000
}

/** Prochain instant (> maintenant) où il est heure:min dans le fuseau donné, ou null. */
function prochainInstant (heure, min, fuseau, maintenant) {
  try {
    const dec = (t) => (fuseau.zone ? decalageZone(fuseau.zone, t) : fuseau.decalageMin * 60000)
    const mur = new Date(maintenant + dec(maintenant)) // date murale de la zone (lue en UTC)
    for (let j = -1; j <= 2; j++) {
      const naif = Date.UTC(mur.getUTCFullYear(), mur.getUTCMonth(), mur.getUTCDate() + j, heure, min)
      let t = naif - dec(naif)
      t = naif - dec(t) // changement d'heure : deuxième passe
      if (t > maintenant) return t
    }
  } catch {}
  return null
}

const hhmm = (d) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`

/** Heure de reprise LOCALE « HH:MM » lue dans un message (« at 5:00 PM », « 5 PM PT »,
 * « dans 2 heures »), vide si le message n'en donne pas : jamais devinée. Un fuseau écrit
 * (PT, ET, UTC+2…) est CONVERTI en heure locale ; un fuseau inconnu donne vide (le cœur se
 * rabat sur sa propre attente), jamais l'heure lue telle quelle. */
export function reprise (texte, maintenant = Date.now()) {
  const t = String(texte || '')
  const h = REPRISE_HEURE.exec(t)
  if (h) {
    let heure = Number(h[1])
    const min = Number(h[2] ?? h[4] ?? 0)
    const ampm = (h[3] || h[5] || '').toLowerCase().replace(/[^ap]/g, '')
    if (ampm && (heure < 1 || heure > 12)) return ''
    if (ampm === 'p' && heure < 12) heure += 12
    if (ampm === 'a' && heure === 12) heure = 0
    if (heure > 23 || min > 59) return ''
    const fuseau = fuseauApres(t.slice(h.index + h[0].length))
    if (!fuseau) return `${String(heure).padStart(2, '0')}:${String(min).padStart(2, '0')}`
    if (fuseau.inconnu) return ''
    const instant = prochainInstant(heure, min, fuseau, maintenant)
    return instant === null ? '' : hhmm(new Date(instant))
  }
  const d = REPRISE_DELAI.exec(t)
  if (d) {
    const n = Number(d[1])
    const ms = /^h/i.test(d[2]) ? n * 3600000 : n * 60000
    return hhmm(new Date(maintenant + ms + 59999)) // arrondi à la minute suivante
  }
  return ''
}

/** Forme comparable d'un texte (prompt et page) : casse, espaces, apostrophes et guillemets. */
const comparable = (s) => String(s || '').normalize('NFKC').toLowerCase()
  .replace(/[’‘`´]/g, '\'').replace(/[“”«»„]/g, '"').replace(/\s+/g, ' ').trim()

/**
 * Classe un texte de la page : {code, message, reprise} ou null. Ordre : activité inhabituelle
 * (rien débité, arrêt), crédits ou limite, créneau de pointe, refus (après l'envoi seulement).
 * `apres` (après l'envoi ; correctif du 28/10, relecture B1 fiche 9) : un faux quota, crédits
 * ou pointe ferait passer le cœur au compte suivant ou reporter, donc une DEUXIÈME génération.
 * Alors ces trois codes ne sont lus QUE dans `alertes` (texte des éléments de rôle alert,
 * status ou dialog), et un passage qui figure aussi dans `prompt` (écho tronqué ou retouché
 * du prompt, que textePage ne retire pas) ne compte pour aucun code.
 */
export function classerPage (texte, { apres = false, maintenant = Date.now(), prompt = '', alertes = '' } = {}) {
  const net = (s) => String(s || '').replace(/[ \t]+/g, ' ').trim()
  const t = net(texte)
  const a = apres ? net(alertes) : t
  if (!t && !a) return null
  const p = apres ? comparable(prompt) : ''
  /** Première ligne de `s` où `re` trouve un passage qui n'est pas un écho du prompt. */
  const ligne = (re, s) => {
    const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : `${re.flags}g`)
    for (const l of s.split('\n')) {
      for (const m of l.matchAll(g)) {
        if (p && p.includes(comparable(m[0]))) continue
        return l.trim().slice(0, 500)
      }
    }
    return ''
  }
  let l
  if ((l = ligne(ACTIVITE, t))) return { code: 'activite', message: l, reprise: '' }
  if ((l = ligne(CREDITS, a))) return { code: 'credits', message: l, reprise: reprise(l, maintenant) }
  if ((l = ligne(QUOTA, a))) return { code: 'quota', message: l, reprise: reprise(l, maintenant) }
  if ((l = ligne(POINTE, a))) return { code: 'pointe', message: l, reprise: reprise(l, maintenant) }
  if (apres && (l = ligne(REFUS, t))) return { code: 'refus', message: l, reprise: '' }
  return null
}

/** Rôles où un message d'état du site est attendu (après l'envoi) : voir classerPage. */
export { ROLES_ALERTE }

/** Coût annoncé « Generating will use N credits » (FR aussi) ; null s'il n'est pas affiché,
 * NaN s'il y en a plusieurs différents (page ambiguë). */
export function lireCout (texte) {
  const re = /(?:will use|va utiliser|utilisera|consommera)\s+(\d{1,5})\s+(?:credits?|crédits?)|(\d{1,5})\s+(?:credits?|crédits?)\s+(?:will be used|seront utilisés)/gi
  const vus = new Set()
  for (const m of String(texte || '').matchAll(re)) vus.add(Number(m[1] || m[2]))
  if (!vus.size) return null
  return vus.size === 1 ? [...vus][0] : Number.NaN
}

// ----------------------------------------------------------------- outils de page

/** État « choisi » d'un bouton, onglet ou option : vrai, faux, ou null si la page ne le dit pas. */
const choisi = (loc) => loc.evaluate((el) => {
  for (const a of ['aria-selected', 'aria-pressed', 'aria-checked', 'aria-current']) {
    const v = el.getAttribute(a)
    if (v != null) return v === 'true' || v === 'page' || v === 'step'
  }
  const s = el.getAttribute('data-state')
  if (s) return ['active', 'on', 'checked', 'selected'].includes(s)
  return null
}).catch(() => null)

const ROLES_OPTION = ['menuitemradio', 'menuitemcheckbox', 'menuitem', 'option', 'tab', 'radio', 'button']
function options (page, re) {
  let loc = page.getByRole(ROLES_OPTION[0], { name: re })
  for (const r of ROLES_OPTION.slice(1)) loc = loc.or(page.getByRole(r, { name: re }))
  return loc
}

/** Bouton qui OUVRE un menu de valeurs (aria-haspopup ou aria-expanded) ; sinon le SEUL bouton
 * visible qui porte une de ces valeurs (deux visibles = boutons à bascule, pas un menu). */
async function declencheur (page, re) {
  if (!re) return null
  const tous = page.getByRole('button', { name: re }).filter({ visible: true })
  const avecMenu = tous.and(page.locator('[aria-haspopup], [aria-expanded]'))
  if (await nombre(avecMenu)) return avecMenu.first()
  return (await nombre(tous)) === 1 ? tous.first() : null
}

async function fermerMenu (page) {
  if (await nombre(page.locator('[role="menu"], [role="listbox"]').filter({ visible: true }))) {
    await page.keyboard.press('Escape')
    await pause(PAUSE_COURTE_MS)
  }
}

/**
 * Règle une valeur de la barre de création (mode, format, modèle, durée, résolution, x1) :
 * déjà affichée par son menu → rien ; option visible → clic puis vérification (état choisi, ou
 * menu refermé qui l'affiche) ; sinon ouvre son menu, ou le panneau des réglages, et recommence.
 * Rend 'affiche' | 'choisi' | 'clique' ('clique' : la page ne dit pas l'état, seul le coût
 * vérifié ensuite le rattrape). Introuvable : échec `page`.
 */
export async function regler (page, { nom, option, menu = null }) {
  let reglagesOuverts = false
  for (let tour = 0; tour < 4; tour++) {
    const trig = await declencheur(page, menu)
    if (trig && option.test(await nomDe(trig))) { await fermerMenu(page); return 'affiche' }
    const opt = await premier(options(page, option))
    if (opt) {
      if (await choisi(opt) === true) { await fermerMenu(page); return 'choisi' }
      await cliquer(page, opt, { timeout: 5000 })
      await pause(PAUSE_COURTE_MS)
      const apres = await premier(options(page, option))
      const etat = apres ? await choisi(apres) : null
      if (etat === true) { await fermerMenu(page); return 'choisi' }
      const t2 = await declencheur(page, menu)
      if (t2 && option.test(await nomDe(t2))) return 'affiche'
      if (apres && etat === null && !menu) return 'clique'
      continue
    }
    if (trig) { await cliquer(page, trig, { timeout: 5000 }); await pause(PAUSE_COURTE_MS); continue }
    const reg = reglagesOuverts ? null : await premier(page.getByRole('button', { name: REGLAGES }))
    if (reg) { reglagesOuverts = true; await cliquer(page, reg, { timeout: 5000 }); await pause(PAUSE_COURTE_MS); continue }
    break
  }
  throw new Echec('page', `réglage « ${nom} » introuvable sur Flow : la page a peut-être changé`)
}

/** 30/10 : bandeau « flow.google.com uses cookies… » (seul bouton : « Understood ») qui couvre le bas de
 * la page et prend les clics. Accepter un bandeau de cookies revient à l'utilisateur : il est seulement
 * MASQUÉ dans la page (style local), jamais cliqué. */
export async function masquerBandeauCookies (page) {
  await page.addStyleTag({ content: '.glue-cookie-notification-bar{display:none!important}' }).catch(() => {})
}

const VIGNETTE_VIDEO = 'flow-video-tile img[alt="Generated video thumbnail"]'

/** Tuiles vidéo finies de la grille : [{src, visible, duree, tuile}] (une vidéo en cours n'a pas encore
 * de vignette). */
async function tuilesVideo (page) {
  const srcs = await page.locator(VIGNETTE_VIDEO).evaluateAll((imgs) =>
    imgs.map((i) => (i.getClientRects().length ? i.currentSrc || i.src || '' : ''))).catch(() => [])
  return srcs.map((s, i) => (s ? { src: `tuile:${s}`, visible: true, duree: null, tuile: i } : null)).filter(Boolean)
}

/** Téléchargement d'une tuile (relevé du 30/10) : survol, « More options » de la tuile, « Download »,
 * puis la taille d'origine (« 720p ») dans le sous-menu. */
export async function telechargerTuile (page, n) {
  // Au survol, Flow REMPLACE la vignette par un aperçu vidéo (relevé du 30/10) : la tuile est repérée
  // par sa position parmi les `flow-video-tile` AVANT le survol, jamais par son image
  const rangs = await page.locator('flow-video-tile').evaluateAll((ts) =>
    ts.map((t, i) => (t.querySelector('img[alt="Generated video thumbnail"]') ? i : -1)).filter((i) => i >= 0)).catch(() => [])
  if (rangs[n] === undefined) throw new Echec('page', 'vidéo introuvable dans la grille du projet')
  const composant = page.locator('flow-video-tile').nth(rangs[n])
  const vignette = composant
  await survoler(page, vignette)
  await pause(PAUSE_COURTE_MS)
  // Le « More options » de la TUILE (relevé du 30/10 : le plus proche à l'écran était celui de l'en-tête,
  // dont le menu propose « Download project »)
  const options = composant.getByRole('button', { name: /^\s*(more options|plus d.options)\s*$/i }).first()
  if (!await options.count().catch(() => 0)) throw new Echec('page', 'menu « More options » de la vidéo introuvable')
  await cliquer(page, options, { timeout: 10000 })
  await pause(PAUSE_COURTE_MS)
  const dl = await premier(page.getByRole('menuitem', { name: /^\s*(download\s+)?(download|télécharger)\s*$/i }))
  if (!dl) throw new Echec('page', 'entrée « Download » absente du menu de la vidéo')
  await cliquer(page, dl, { timeout: 10000 })
  // Sous-menu relevé le 30/10 : « 270p Animated GIF », « 720p Original size », « 1080p Upscaled », « 4K
  // Upscaled ». Cherché pendant quelques secondes (il s'ouvre après le clic) ; jamais d'attente d'un
  // téléchargement qui ne partira pas (1er essai réel : 2 min perdues)
  const taillePrise = () => page.getByRole('menuitem', { name: /^\s*720p\b.*original|original size|taille d.origine/i })
  let taille = null
  const fin = Date.now() + (TEST ? 2000 : 8000)
  while (!taille && Date.now() < fin) {
    taille = await premier(taillePrise())
    if (!taille) await dormir(TEST ? 50 : 300)
  }
  if (!taille) throw new Echec('page', 'sous-menu de téléchargement sans « 720p Original size »')
  if (TAILLE_REFUS.test(await nomDe(taille))) throw new Echec('page', 'menu de téléchargement sans la taille d\'origine')
  const attente = page.waitForEvent('download', { timeout: TEST ? 10000 : 60000 })
  attente.catch(() => {})
  // Clic DIRECT : le trajet de souris « humain » vers ce sous-menu latéral le refermait (rien ne partait)
  await taille.click({ timeout: 10000 })
  return attente
}

/** Relevé du 30/10 : une image téléversée dans la bibliothèque n'est PAS sélectionnée si une autre
 * l'était (l'image de début reste choisie pour la case End). La choisir (clic), et retirer les autres
 * de la sélection. Rien tant qu'elle est encore « Uploading ». */
async function choisirNotreImage (page, nom) {
  const debut = String(nom || '').slice(0, 24)
  if (!debut) return
  const opts = page.getByRole('option').filter({ hasText: debut })
  const n = await nombre(opts)
  for (let i = 0; i < n; i++) {
    const o = opts.nth(i)
    const t = (await o.innerText().catch(() => '')).trim()
    if (/^\s*uploading\b/i.test(t)) return
    if (await o.getAttribute('aria-selected').catch(() => null) !== 'true') {
      await cliquer(page, o, { timeout: 5000 })
      await pause(PAUSE_COURTE_MS)
    }
    break
  }
  const autres = page.getByRole('option', { selected: true }).filter({ hasNotText: debut })
  for (let k = 0; k < 4 && await nombre(autres); k++) {
    await cliquer(page, autres.first(), { timeout: 5000 })
    await pause(PAUSE_COURTE_MS)
  }
}

/** Bibliothèque de Flow : l'image `nom` (nom de fichier déposé) est téléversée, sélectionnée, et c'est
 * la SEULE sélectionnée. */
async function notreImageChoisie (page, nom) {
  const debut = String(nom || '').slice(0, 24)
  if (!debut) return false
  return page.evaluate((debut) => {
    const opts = [...document.querySelectorAll('[role="option"]')]
    const choisies = opts.filter((o) => o.getAttribute('aria-selected') === 'true')
    if (choisies.length !== 1) return false
    const t = (choisies[0].innerText || choisies[0].getAttribute('aria-label') || '').trim()
    return !/^\s*uploading\b/i.test(t) && t.includes(debut)
  }, debut).catch(() => false)
}

/** 30/10 : Flow n'affiche plus le coût. Il se déduit des réglages lus sur « Settings trigger »
 * (« Video · 720p · 8s · crop_9_16 · x1 ») : vidéo, durée demandée, x1 ; le modèle a été vérifié par
 * choisirModele. Rend {debite, liste} = coût catalogue, ou null si les réglages ne se lisent pas. */
async function coutParReglages (page, job) {
  const b = await premier(page.getByRole('button', { name: REGLAGES }))
  if (!b) return null
  const t = await b.innerText().catch(() => '')
  const ok = /video|vidéo/i.test(t) && new RegExp(`(^|\\D)${job.duree}\\s*s\\b`, 'i').test(t) && /\bx\s*1\b/i.test(t)
  if (!ok) throw new Echec('page', `réglages lus « ${t.replace(/\s+/g, ' ').trim()} » : pas vidéo, ${job.duree} s et x1 : rien n'est envoyé`)
  const c = job.coutExact ?? job.modele.cout
  return { debite: c, liste: c }
}

// ----------------------------------------------------------------- le site

export const FLOW = {
  nom: 'flow',
  libelle: 'Google Flow',
  type: 'video',
  url: 'https://flow.google.com/', // repli : l'adresse du catalogue config/ia-web.json prime
  // PAS d'`affichage` imposé (correction de consigne du 28/10 : l'utilisateur travaille en entreprise,
  // aucune fenêtre ne doit surgir) : le job choisit, `masquee` par défaut, `visible` sur demande.
  hotesConnexion: /(^|\.)accounts\.google\.com$/i,
  modeles: MODELES,
  formats: FORMATS,
  modeleDefaut: 'lite', // `defaut` du job = Veo 3.1 Lite (défaut de Flow, relevé B0)
  dureeDefaut: 8,
  libelleCout: '« Generating will use N credits »',
  classer: classerPage,
  rolesAlerte: ROLES_ALERTE,

  /** Projet ouvert, annonces fermées, mode Video, sous-mode Frames (video.mjs l'appelle une
   * seule fois par page). */
  async preparer (page) {
    await masquerBandeauCookies(page)
    await FLOW.fermerAnnonces(page)
    await FLOW.ouvrirProjet(page)
    await FLOW.fermerAnnonces(page)
    await FLOW.modeVideo(page)
    await pause(PAUSE_COURTE_MS)
    await FLOW.modeFrames(page)
    await pause(PAUSE_COURTE_MS)
  },

  /** Format, résolution (Omni), durée (Omni), x1. */
  async regler (page, job) {
    const fiche = MODELES[job.modele.id]
    await FLOW.format(page, job.format)
    if (fiche.resolution) await FLOW.resolution(page, job.resolution)
    if (fiche.durees.length > 1) await FLOW.duree(page, job.duree)
    // x1 : le réglage peut manquer (déjà x1, sans menu) ; le coût exact, vérifié ensuite, refuse x2 à x4
    await FLOW.nombre1(page).catch((e) => { if (!(e instanceof Echec)) throw e; note('réglage x1 introuvable : le coût annoncé tranchera') })
  },

  /** Coût annoncé : {debite, liste} (Flow n'affiche qu'un prix), null s'il n'est pas affiché,
   * NaN s'il y en a plusieurs différents. */
  coutAnnonce (page, texte, job = null) {
    const n = lireCout(texte)
    if (n === null && job) return coutParReglages(page, job) // 30/10 : plus de coût affiché
    return n === null || Number.isNaN(n) ? n : { debite: n, liste: n }
  },

  /** Champ du prompt d'un projet. */
  // Parmi les éléments VISIBLES seulement : un champ caché (autre vue de la même page) placé
  // avant dans le document ferait sinon échouer l'arrivée.
  prompt: (page) => page.getByRole('textbox', { name: CHAMP })
    .or(page.locator('textarea, [contenteditable="true"]')).filter({ visible: true }).first(),
  nouveauProjet: (page) => page.getByRole('button', { name: NOUVEAU_PROJET })
    .or(page.getByRole('link', { name: NOUVEAU_PROJET })).filter({ visible: true }).first(),
  /** « Page prête » pour noyau.arriver : le champ d'un projet, OU l'accueil connecté (bouton
   * « New project ») ; le parcours ouvre alors un projet neuf (video.mjs, preparer). */
  champ: (page) => page.getByRole('textbox', { name: CHAMP })
    .or(page.locator('textarea, [contenteditable="true"]'))
    .or(page.getByRole('button', { name: NOUVEAU_PROJET }))
    .or(page.getByRole('link', { name: NOUVEAU_PROJET })).filter({ visible: true }).first(),
  connexion: (page) => page.getByRole('link', { name: CONNEXION }).or(page.getByRole('button', { name: CONNEXION })),
  envoi: (page) => page.getByRole('button', { name: ENVOI }).filter({ visible: true }).last(),

  /** Ferme les fenêtres d'annonce (« Got it », violette ; « Close » dans une boîte de dialogue). */
  async fermerAnnonces (page) {
    for (let i = 0; i < 3; i++) {
      const b = await premier(page.getByRole('button', { name: ANNONCE })) ||
        await premier(page.getByRole('dialog').getByRole('button', { name: ANNONCE_DIALOGUE }))
      if (!b) return
      await cliquer(page, b, { timeout: 5000 })
      await pause(PAUSE_COURTE_MS)
    }
  },

  /** Ouvre un projet neuf si la page est l'accueil ; rend le champ du prompt. */
  async ouvrirProjet (page) {
    const champ = FLOW.prompt(page)
    if (await visible(champ)) return champ
    const bouton = await premier(FLOW.nouveauProjet(page))
    if (!bouton) throw new Echec('page', 'ni champ du prompt ni bouton « New project » : la page a peut-être changé')
    await cliquer(page, bouton, { timeout: 10000 })
    await champ.waitFor({ state: 'visible', timeout: TEST ? 5000 : 30000 })
      .catch(() => { throw new Echec('page', 'projet neuf ouvert sans champ du prompt : la page a peut-être changé') })
    await pause()
    return champ
  },

  modeVideo: (page) => regler(page, { nom: 'Video', option: /^\s*(?:videocam|movie|smart_display)?\s*(video|vidéo)\s*$/i }),
  modeFrames: (page) => regler(page, { nom: 'Frames', option: /^\s*(?:[a-z_]+\s+)?(frames|images clés)\s*$/i }),
  format: (page, f) => regler(page, { nom: f, option: new RegExp(`(^|\\s)${f.replace(':', '\\s*:\\s*')}(\\s|$)`), menu: /(^|\s)(16\s*:\s*9|9\s*:\s*16)(\s|$)/ }),
  nombre1: (page) => regler(page, { nom: 'x1', option: /^\s*(x\s*1|1\s*x)\s*$/i, menu: /^\s*(x\s*[1-4]|[1-4]\s*x)\s*$/i }),
  duree: (page, s) => regler(page, { nom: `${s} s`, option: new RegExp(`^\\s*${s}\\s*(s|sec|secondes?|seconds?)\\s*$`, 'i'), menu: /^\s*(4|6|8|10)\s*(s|sec|secondes?|seconds?)\s*$/i }),
  resolution: (page, r) => regler(page, { nom: r, option: new RegExp(`^\\s*${r}\\s*$`, 'i'), menu: /^\s*(360|480|720|1080)p\s*$/i }),

  /**
   * Modèle demandé (id du catalogue), APRÈS avoir préparé la page (projet, annonces, Video,
   * Frames : le menu des modèles vidéo n'existe qu'alors). Rend l'id seulement si le menu des
   * modèles affiche ensuite ce modèle ; sinon l'id lu (autre modèle) ou '' : le noyau refuse.
   */
  async choisirModele (page, modele, outils = {}) {
    const fiche = MODELES[modele.id]
    if (!fiche) throw new Echec('page', `modèle ${modele.id} inconnu du pilote Flow`)
    if (typeof outils.preparer === 'function') await outils.preparer(page)
    // 30/10 : le menu des modèles est le bouton « Select model family » du panneau des réglages
    const menuModeles = async () => premier(page.getByRole('button', { name: MENU_MODELES }))
    const ouvrirReglages = async () => {
      const reg = await premier(page.getByRole('button', { name: REGLAGES }))
      if (reg) { await cliquer(page, reg, { timeout: 5000 }); await pause(PAUSE_COURTE_MS) }
      return menuModeles()
    }
    let trigModeles = await menuModeles() || await ouvrirReglages()
    if (trigModeles && !fiche.re.test(await nomDe(trigModeles))) {
      await cliquer(page, trigModeles, { timeout: 5000 })
      await pause(PAUSE_COURTE_MS)
      const opt = await premier(options(page, fiche.re))
      if (!opt) throw new Echec('page', `modèle « ${modele.libelle || modele.id} » absent du menu de Flow`)
      await cliquer(page, opt, { timeout: 5000 })
      await pause(PAUSE_COURTE_MS)
      // 30/10 (fausse page &ui=2026&modele_refuse=1) : si le choix referme AUSSI le panneau des réglages,
      // il est rouvert pour RELIRE le modèle pris. Sans cela le libellé était vide, l'id demandé était
      // rendu tel quel, et un modèle non pris partait (Quality à 100 crédits compté 10) : Flow n'affiche
      // plus de coût qui rattraperait l'erreur
      trigModeles = await menuModeles() || await ouvrirReglages()
    } else if (!trigModeles) {
      await regler(page, { nom: modele.libelle || modele.id, option: fiche.re, menu: TOUS_MODELES })
    }
    const trig = trigModeles || await declencheur(page, TOUS_MODELES)
    const affiche = trig ? await nomDe(trig) : ''
    if (!affiche) return modele.id // menu sans libellé lisible : le coût vérifié avant l'envoi tranche
    const lu = Object.entries(MODELES).find(([, m]) => m.re.test(affiche))
    return lu ? lu[0] : ''
  },

  /**
   * Dépose une image dans la case Start ou End (mode Frames) : clic sur la case (sélecteur de
   * fichier, ou menu « Upload » d'abord), fichier donné EN MÉMOIRE (les octets vérifiés à
   * l'entrée, jamais relus sur le disque), recadrage validé s'il est proposé, puis attend une
   * vignette NOUVELLE à la place de la case. Échec `page` sinon.
   */
  async deposerImage (page, quelle, image) {
    const re = quelle === 'debut'
      ? /^\s*(?:add\s+)?(start|début|first frame|première image|image de début)\s*$/i
      : /^\s*(?:add\s+)?(end|fin|last frame|dernière image|image de fin)\s*$/i
    const nom = quelle === 'debut' ? 'Start' : 'End'
    const kase = await premier(page.getByRole('button', { name: re }).or(page.getByText(re)))
    if (!kase) throw new Echec('page', `case « ${nom} » introuvable (mode Frames) : la page a peut-être changé`)
    const box = await kase.boundingBox()
    const avant = box ? await vignettes(page, box) : []
    const ingredients = await nombre(page.getByRole('button', { name: /^\s*image ingredient\s*$/i }))
    let fc = await Promise.all([
      page.waitForEvent('filechooser', { timeout: TEST ? 1500 : 6000 }).catch(() => null),
      cliquer(page, kase, { timeout: 10000 })
    ]).then(([f]) => f)
    if (!fc) {
      // 30/10 : « Upload media » de la bibliothèque d'abord : le motif large prenait l'entrée « Uploads »
      // du menu de gauche (navigation, pas de choix de fichier)
      const exact = /^\s*(?:upload\s+)?(upload media|importer (un |des )?médias?)\s*$/i
      await pause(PAUSE_COURTE_MS)
      const item = await premier(page.getByRole('button', { name: exact })) ||
        await premier(page.getByRole('menuitem', { name: IMPORTER }).or(page.getByRole('option', { name: IMPORTER })))
      if (item) {
        fc = await Promise.all([
          page.waitForEvent('filechooser', { timeout: TEST ? 3000 : 10000 }).catch(() => null),
          cliquer(page, item, { timeout: 10000 })
        ]).then(([f]) => f)
      }
    }
    if (!fc) throw new Echec('page', `case « ${nom} » : pas de choix de fichier proposé`)
    await pause(PAUSE_COURTE_MS)
    await fc.setFiles({ name: image.nom, mimeType: image.mime, buffer: image.octets })
    // Recadrage éventuel (boîte de dialogue) : validé une fois, tel quel
    const fin = Date.now() + (TEST ? 3000 : 90000) // téléversement compris
    let recadre = false
    let ajoute = false
    while (Date.now() < fin) {
      const attest = await premier(page.getByRole('dialog').filter({ hasText: ATTESTATION_IMAGE }).getByRole('button', { name: ACCEPTER }))
      if (attest) {
        await cliquer(page, attest, { timeout: 5000 })
        note('Flow : attestation « Rights to use this image » acceptée (décision du 30/10)')
        await pause(PAUSE_COURTE_MS)
        continue
      }
      // 30/10 : nouvelle interface : la bibliothèque s'ouvre, l'image se téléverse (sélectionnée), puis
      // « Add to prompt » (inactif pendant le téléversement) la met dans la case
      // 30/10 (1er essai réel) : « Add to prompt » ajoute ce qui est SÉLECTIONNÉ dans la bibliothèque ;
      // la case End avait reçu l'image de début, encore sélectionnée. Attendre que l'image envoyée ICI
      // soit téléversée (plus « Uploading ») et sélectionnée, seule.
      const ajouter = ajoute ? null : await premier(page.getByRole('button', { name: AJOUTER_AU_PROMPT }))
      if (ajouter && !await notreImageChoisie(page, image.nom)) await choisirNotreImage(page, image.nom)
      if (ajouter && await ajouter.isEnabled().catch(() => false) && await notreImageChoisie(page, image.nom)) {
        await cliquer(page, ajouter, { timeout: 5000 })
        ajoute = true
        await pause(PAUSE_COURTE_MS)
      }
      const ok = recadre ? null : await premier(page.getByRole('dialog').getByRole('button', { name: RECADRAGE }))
      if (ok) { await pause(PAUSE_COURTE_MS); await cliquer(page, ok, { timeout: 5000 }); recadre = true }
      if (ajoute && await nombre(page.getByRole('button', { name: /^\s*image ingredient\s*$/i })) > ingredients) return
      if (box) {
        const apres = await vignettes(page, box)
        if (apres.some((v) => !avant.includes(v))) return
      }
      await dormir(TEST ? 50 : 500)
    }
    throw new Echec('page', `image « ${nom} » déposée sans vignette visible : la page a peut-être changé`)
  },

  // 30/10 (1er essai réel) : la grille du projet ne montre plus de balise <video> mais des TUILES
  // (`flow-video-tile`, image « Generated video thumbnail », bouton lecture) : la vidéo était prête et
  // le pilote attendait 15 min. Une tuile vidéo compte comme une vidéo (`src` = « tuile: » + vignette).
  videos: async (page) => [...await videosDe(page), ...(await tuilesVideo(page)).map((t) => t.src)],
  videosInfos: async (page, job) => [...await videosInfosDe(page, job), ...await tuilesVideo(page)],

  /**
   * Télécharge la vidéo `index` (balise video n° index) : survol, bouton « Download » le plus
   * proche, puis la taille d'origine (720p) si un menu propose plusieurs tailles. Rend le
   * téléchargement Playwright.
   */
  async telecharger (page, index, job) {
    const infos = await FLOW.videosInfos(page, job)
    if (infos[index]?.tuile !== undefined) return telechargerTuile(page, infos[index].tuile)
    const video = page.locator('video').nth(index)
    await survoler(page, video)
    await pause(PAUSE_COURTE_MS)
    const bouton = await plusProche(page.getByRole('button', { name: TELECHARGER }).or(page.getByRole('link', { name: TELECHARGER })), video)
    if (!bouton) throw new Echec('page', 'bouton « Download » introuvable sur la vidéo')
    const attente = page.waitForEvent('download', { timeout: TEST ? 10000 : 120000 })
    attente.catch(() => {})
    await cliquer(page, bouton, { timeout: 10000 })
    await pause(PAUSE_COURTE_MS)
    // Taille cherchée DANS le menu ouvert seulement : le sélecteur de résolution de la barre
    // (« 720p » en Omni) porte le même nom et partait à sa place (vu sur la fausse page).
    const ouvert = page.locator('[role="menu"], [role="listbox"], [role="dialog"]').filter({ visible: true })
    const taille = await premier(page.getByRole('menuitem', { name: TAILLE_ORIGINE }).or(page.getByRole('menuitemradio', { name: TAILLE_ORIGINE }))
      .or(ouvert.getByRole('button', { name: TAILLE_ORIGINE })).or(ouvert.getByRole('link', { name: TAILLE_ORIGINE })))
    if (taille) {
      if (TAILLE_REFUS.test(await nomDe(taille))) throw new Echec('page', 'menu de téléchargement sans la taille d\'origine')
      await cliquer(page, taille, { timeout: 10000 })
    } else if (await nombre(page.getByRole('menu').filter({ visible: true }))) {
      throw new Echec('page', 'menu de téléchargement sans la taille d\'origine (720p)')
    }
    return attente
  },

  /** Crédits restants affichés (élément dont le texte est « 50 credits » / « 50 crédits »), ou null. */
  credits: (page) => page.evaluate(() => {
    for (const el of document.querySelectorAll('body *')) {
      if (el.children.length > 2 || !el.offsetParent) continue
      const m = /^\s*(\d{1,5})\s*(?:ai\s+)?(?:credits?|crédits?)\s*$/i.exec(el.innerText || '')
      if (m) return Number(m[1])
    }
    return null
  }).catch(() => null),

  /** Bouton d'envoi qui signale des crédits insuffisants (icône d'info, titre, désactivé). */
  envoiBloque: (loc) => loc.evaluate((el) => {
    const texte = `${el.innerText || ''} ${el.getAttribute('title') || ''} ${el.getAttribute('aria-description') || ''} ${el.getAttribute('aria-label') || ''}`
    const info = /(^|\s)info(_outline)?(\s|$)|not enough|insufficient|crédits insuffisants|pas assez/i.test(texte)
    const inactif = el.disabled || el.getAttribute('aria-disabled') === 'true'
    return { info, inactif }
  }).catch(() => ({ info: false, inactif: false })),

  confirmerEnvoi: (page) => premier(page.getByRole('dialog').filter({ hasText: /credit|crédit/i }).getByRole('button', { name: CONFIRMER_ENVOI }))
}

/** Signatures des vignettes sous le centre d'une case (img, canvas, image de fond). */
function vignettes (page, box) {
  return page.evaluate(({ x, y }) => document.elementsFromPoint(x, y).flatMap((el) => {
    const out = []
    if (el.tagName === 'IMG' && el.currentSrc) out.push(`img:${el.currentSrc}`)
    if (el.tagName === 'CANVAS') out.push(`canvas:${el.width}x${el.height}`)
    const fond = getComputedStyle(el).backgroundImage
    if (fond && fond.startsWith('url(')) out.push(`fond:${fond}`)
    return out
  }), { x: box.x + box.width / 2, y: box.y + box.height / 2 }).catch(() => [])
}
