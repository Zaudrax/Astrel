// Rythme et gestes HUMAINS imposés par le worker lui-même (plan P14 étape 6, plan P40 lot A2) :
// le cœur ne peut ni les accélérer ni les couper. Commun aux images et à la vidéo (plan P41) :
// un module de site s'en sert tel quel, sans rien y changer.
//
// - pauses de 2 à 5 s entre deux gestes, frappe de 40 à 120 ms par caractère (un peu plus
//   longue après un mot), un seul essai, aucune relance ;
// - souris en COURBE (Bézier, vitesse variable), clic HORS du centre de l'élément, défilement
//   par crans de molette ;
// - jamais la nuit (NUIT) ; 30 s au moins entre deux GÉNÉRATIONS d'un même compte SUR UN MÊME SITE
//   (30/10, l'utilisateur : 10 min tous sites confondus le gênait, sans fondement mesuré) ; 20 s au moins
//   entre deux ouvertures du site.
//
// Ce module ne contourne RIEN : pas de module « stealth », pas de faux navigateur, aucun défi
// anti-robot résolu (un défi = arrêt, voir noyau.captcha).
//
// ASTREL_IMAGEGEN_RYTHME=test : pauses et gestes de quelques millisecondes, nuit ignorée, pour
// `node --test` sur des FAUSSES pages seulement. Le cœur (astrel/images_ia.py) ne la pose jamais
// et la retire de l'environnement du worker. L'écart de 10 min reste actif même en test.

export const TEST = process.env.ASTREL_IMAGEGEN_RYTHME === 'test'

export const PAUSE_MS = TEST ? [5, 20] : [2000, 5000]
export const PAUSE_COURTE_MS = TEST ? [5, 10] : [800, 2000]
export const FRAPPE_MS = TEST ? [0, 2] : [40, 120]
// Après une espace ou une ponctuation, une fois sur quatre : le temps de trouver le mot suivant.
export const FRAPPE_MOT_MS = TEST ? [0, 1] : [120, 400]
// Entre deux OUVERTURES du site sur un même compte (échec compris) : attendu sur place.
export const ECART_MIN_MS = TEST ? 0 : 20000
// Entre deux GÉNÉRATIONS d'un même compte sur un même site (prompt envoyé) : attendu sur place
// jusqu'à ATTENTE_SUR_PLACE_MS, au-delà refus `rythme` (le cœur reporte). Actif aussi en test, où
// rien n'est attendu sur place (refus aussitôt, tests rapides).
export const ECART_GENERATION_MS = 30 * 1000
export const ATTENTE_SUR_PLACE_MS = TEST ? 0 : 2 * 60 * 1000
// Heures locales sans génération : [debut, fin[ (0 h à 7 h). Hors test seulement.
export const NUIT = TEST ? null : { debut: 0, fin: 7 }
export const SONDE_MS = TEST ? 50 : 1000
// Texte sans image depuis ce temps : refus de contenu (une image peut suivre « voici ton image »).
export const ATTENTE_REFUS_MS = TEST ? 600 : 8000
// Au-delà, le reste du prompt est COLLÉ d'un bloc (comme un humain qui colle un long texte) :
// 2 000 caractères tapés un par un prendraient près de trois minutes.
export const FRAPPE_MAX_CARACTERES = 300
// Souris : durée d'un pas de la courbe, nombre de pas, appui du clic.
const PAS_MS = TEST ? [0, 1] : [6, 16]
const PAS_MIN = TEST ? 4 : 18
const PAS_MAX = TEST ? 6 : 60
const APPUI_MS = TEST ? [0, 2] : [60, 160]
const CRAN_PX = [80, 140]
const CRAN_MS = TEST ? [0, 2] : [40, 120]

export const hasard = (min, max) => min + Math.random() * (max - min)
export const dormir = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
export const pause = (plage = PAUSE_MS) => dormir(hasard(plage[0], plage[1]))

const hhmm = (d) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`

// --------------------------------------------------------------- horaires

/**
 * Génération permise maintenant sur ce compte ? `null` si oui, sinon {code, message, reprise}
 * (`reprise` « HH:MM » locale, lue par ia_web.reprise_apres) :
 * - `nuit` : heure locale dans [nuit.debut, nuit.fin[ ;
 * - `rythme` : moins de `ecart` ms depuis le dernier prompt envoyé sur ce compte et ce site ; `reste`
 *   (ms) dit combien attendre.
 * `maintenant` et les réglages sont injectables pour les tests.
 */
export function autorise ({ maintenant = Date.now(), dernierEnvoi = 0, nuit = NUIT, ecart = ECART_GENERATION_MS } = {}) {
  const d = new Date(maintenant)
  if (nuit && d.getHours() >= nuit.debut && d.getHours() < nuit.fin) {
    const reprise = new Date(d)
    reprise.setHours(nuit.fin, 0, 0, 0)
    return { code: 'nuit', message: `pas de génération la nuit (${nuit.debut} h à ${nuit.fin} h)`, reprise: hhmm(reprise) }
  }
  const reste = (Number(dernierEnvoi) || 0) + ecart - maintenant
  if (reste > 0) {
    const reprise = new Date(maintenant + reste + 59999) // arrondi à la minute suivante
    return {
      code: 'rythme',
      message: `${Math.round(ecart / 1000)} s au moins entre deux générations d'un compte sur un même site : encore ${Math.ceil(reste / 1000)} s`,
      reprise: hhmm(reprise),
      reste
    }
  }
  return null
}

// --------------------------------------------------------------- souris

/** Point de clic dans la boîte, JAMAIS au centre exact : décalé de 10 à 30 % de la demi-taille
 * sur chaque axe (signe au hasard), toujours à l'intérieur (15 % du bord au moins). */
export function pointCible (box, alea = Math.random) {
  const axe = (debut, taille) => {
    const decale = (0.1 + 0.2 * alea()) * (alea() < 0.5 ? -1 : 1) // fraction de la demi-taille
    const f = Math.min(0.85, Math.max(0.15, 0.5 + decale / 2))
    return debut + taille * f
  }
  return { x: axe(box.x, box.width), y: axe(box.y, box.height) }
}

/** Points d'une courbe de Bézier cubique de `de` à `a` : deux points de contrôle écartés de la
 * droite (côté au hasard), `n` pas, vitesse lente au départ et à l'arrivée. Le dernier point
 * est exactement `a`. */
export function courbe (de, a, n, alea = Math.random) {
  const dx = a.x - de.x
  const dy = a.y - de.y
  const long = Math.hypot(dx, dy) || 1
  // Normale à la droite, écart de 10 à 30 % de la longueur (40 px au moins)
  const nx = -dy / long
  const ny = dx / long
  const ecart = () => Math.max(40, long * (0.1 + 0.2 * alea())) * (alea() < 0.5 ? -1 : 1)
  const e1 = ecart()
  const e2 = ecart()
  const c1 = { x: de.x + dx * (0.2 + 0.2 * alea()) + nx * e1, y: de.y + dy * (0.2 + 0.2 * alea()) + ny * e1 }
  const c2 = { x: de.x + dx * (0.6 + 0.2 * alea()) + nx * e2, y: de.y + dy * (0.6 + 0.2 * alea()) + ny * e2 }
  const pts = []
  for (let i = 1; i <= n; i++) {
    const s = i / n
    const t = s < 0.5 ? 2 * s * s : 1 - (-2 * s + 2) ** 2 / 2 // lent, rapide, lent
    const u = 1 - t
    pts.push({
      x: u ** 3 * de.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t ** 3 * a.x,
      y: u ** 3 * de.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t ** 3 * a.y
    })
  }
  if (pts.length) pts[pts.length - 1] = { x: a.x, y: a.y }
  return pts
}

// Dernière position connue de la souris, par page (Playwright ne la donne pas).
const positions = new WeakMap()

function position (page) {
  let p = positions.get(page)
  if (!p) {
    const vue = page.viewportSize() || { width: 1366, height: 860 }
    p = { x: hasard(0.3, 0.7) * vue.width, y: hasard(0.2, 0.5) * vue.height }
    positions.set(page, p)
  }
  return p
}

/** Amène la souris en `cible` par une courbe, pas à pas, vitesse variable. */
export async function deplacer (page, cible) {
  const de = position(page)
  const n = Math.round(Math.min(PAS_MAX, Math.max(PAS_MIN, Math.hypot(cible.x - de.x, cible.y - de.y) / 14)))
  for (const pt of courbe(de, cible, n)) {
    await page.mouse.move(pt.x, pt.y)
    await dormir(hasard(PAS_MS[0], PAS_MS[1]))
  }
  positions.set(page, { x: cible.x, y: cible.y })
}

/** Survole l'élément (souris en courbe jusqu'à un point hors du centre). Faux s'il n'a pas de boîte. */
export async function survoler (page, loc) {
  const box = await loc.boundingBox({ timeout: 3000 }).catch(() => null)
  if (!box || box.width < 1 || box.height < 1) return false
  await deplacer(page, pointCible(box))
  return true
}

/**
 * Clic d'humain : défile jusqu'à l'élément si besoin, souris en courbe jusqu'à un point hors du
 * centre, court temps d'arrêt, appui de durée variable. Élément sans boîte (masqué, hors
 * page) : clic ordinaire de Playwright, qui attend qu'il soit cliquable ou échoue.
 */
export async function cliquer (page, loc, { timeout = 10000 } = {}) {
  await loc.waitFor({ state: 'visible', timeout }).catch(() => {})
  await defilerVers(page, loc)
  const box = await loc.boundingBox({ timeout: 3000 }).catch(() => null)
  if (!box || box.width < 1 || box.height < 1) {
    await loc.click({ timeout })
    return
  }
  const pt = pointCible(box)
  await deplacer(page, pt)
  await dormir(hasard(PAUSE_COURTE_MS[0], PAUSE_COURTE_MS[1]) / 4)
  await page.mouse.down()
  await dormir(hasard(APPUI_MS[0], APPUI_MS[1]))
  await page.mouse.up()
}

// --------------------------------------------------------------- défilement

/** Défile de `dy` pixels (négatif = vers le haut) par crans de molette, comme une vraie molette. */
export async function defiler (page, dy) {
  let reste = dy
  while (Math.abs(reste) >= 1) {
    const cran = Math.sign(reste) * Math.min(Math.abs(reste), hasard(CRAN_PX[0], CRAN_PX[1]))
    await page.mouse.wheel(0, cran)
    reste -= cran
    await dormir(hasard(CRAN_MS[0], CRAN_MS[1]))
  }
}

// Défilement DOUX (molette animée par Chrome ou par la page) : mouse.wheel rend la main avant la
// fin du défilement, l'élément bouge encore. On attend deux mesures égales (à STABLE_PX près)
// prises à STABLE_PAS_MS d'écart, STABLE_MAX_MS au plus (même en rythme de test : c'est la page
// qui fixe la durée, pas le geste). Plan P40 lot A2, fiche 17.
const STABLE_PAS_MS = 50
const STABLE_MAX_MS = 2000
const STABLE_PX = 0.5

/** Boîte de l'élément une fois sa position stable (deux mesures égales consécutives), ou la
 * dernière mesurée au bout de `max` ms ; `null` s'il n'a plus de boîte. */
export async function attendreStable (loc, { pas = STABLE_PAS_MS, max = STABLE_MAX_MS } = {}) {
  const fin = Date.now() + max
  let avant = null
  for (;;) {
    await dormir(pas) // d'abord un pas : un défilement qui vient d'être demandé n'a peut-être pas commencé
    const box = await loc.boundingBox({ timeout: 1000 }).catch(() => null)
    if (!box) return null
    if (avant && Math.abs(box.x - avant.x) < STABLE_PX && Math.abs(box.y - avant.y) < STABLE_PX) return box
    avant = box
    if (Date.now() >= fin) return box
  }
}

/** Le centre de l'élément est-il bien sur lui (pas rogné par un conteneur défilant, la page à
 * côté) ? Illisible : vrai, comme avant. */
async function atteignable (loc) {
  return loc.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const t = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
    return !!t && (t === el || el.contains(t))
  }).catch(() => true)
}

/** Amène l'élément dans la vue à la molette (12 crans au plus), sinon par le navigateur, et ne
 * rend la main qu'une fois sa position stable (attendreStable) : le clic part sur la vraie place.
 * Dans la vue mais pas atteignable (bouton d'un conteneur défilant, rogné) : par le navigateur,
 * qui défile aussi le conteneur.
 * Mode visible (`viewport: null`, noyau.optionsLancement) : viewportSize() est nul, la hauteur
 * de la vue est lue dans la page (innerHeight) ; illisible : défilement par le navigateur seul. */
export async function defilerVers (page, loc) {
  const vue = page.viewportSize() ||
    await page.evaluate(() => ({ width: innerWidth, height: innerHeight })).catch(() => null)
  if (!vue || !(vue.height > 0)) {
    await loc.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => {})
    await attendreStable(loc)
    return
  }
  let box = await attendreStable(loc)
  for (let i = 0; i < 12; i++) {
    if (!box) return
    const haut = box.y
    const bas = box.y + box.height
    if (haut >= 0 && bas <= vue.height) {
      if (await atteignable(loc)) return
      break
    }
    await defiler(page, haut < 0 ? Math.max(haut - vue.height * 0.2, -400) : Math.min(bas - vue.height * 0.8, 400))
    const apres = await attendreStable(loc)
    if (!apres || Math.abs(apres.y - box.y) < 1) break // la page ne défile pas à la molette
    box = apres
  }
  await loc.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => {})
  await attendreStable(loc)
}

// --------------------------------------------------------------- clavier

/** Tape `texte` dans le champ qui a le focus, caractère par caractère, délai variable ; au-delà
 * de FRAPPE_MAX_CARACTERES, le reste est collé d'un bloc. */
export async function taper (page, texte) {
  const tape = texte.slice(0, FRAPPE_MAX_CARACTERES)
  for (const c of tape) {
    await page.keyboard.type(c)
    await dormir(hasard(FRAPPE_MS[0], FRAPPE_MS[1]))
    if (/[\s,.;:!?]/.test(c) && Math.random() < 0.25) await dormir(hasard(FRAPPE_MOT_MS[0], FRAPPE_MOT_MS[1]))
  }
  const reste = texte.slice(FRAPPE_MAX_CARACTERES)
  if (reste) {
    await pause(PAUSE_COURTE_MS)
    await page.keyboard.insertText(reste)
  }
}
