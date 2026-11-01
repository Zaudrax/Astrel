// Gestes communs aux sites d'images (plan P40 lot A3) : choix du modèle dans un menu, dépôt des
// pièces jointes. Sélecteurs par RÔLE et libellé accessible (FR + EN), jamais par classe CSS.
// Depuis P41 B5 (29/10), aussi ce que partagent les sites VIDÉO (Flow, Dreamina) : réglage par un
// déclencheur qui ouvre un panneau, lecture des balises vidéo, bouton le plus proche d'un élément,
// rôles où un site affiche ses messages d'état.
//
// ⚠️ Rien ici n'a été essayé sur un vrai site (consigne : aucun essai réel avant le pilote avec
// l'utilisateur) ; seulement sur les fausses pages de test/. Un geste qui n'aboutit pas = échec `page`
// avec capture, jamais un réglage deviné.

import { Echec, nombre, siFerme, visible } from '../noyau.mjs'
import { PAUSE_COURTE_MS, TEST, cliquer, dormir, pause, taper } from '../humain.mjs'

const premier = async (loc) => {
  const v = loc.filter({ visible: true })
  return (await nombre(v)) ? v.first() : null
}

const nomDe = (loc) => loc.evaluate((el) => `${el.getAttribute('aria-label') || ''} ${el.innerText || el.textContent || ''}`.replace(/\s+/g, ' ').trim()).catch(() => '')

/** État « choisi » d'une option : vrai, faux, ou null si la page ne le dit pas. */
const choisi = (loc) => loc.evaluate((el) => {
  for (const a of ['aria-checked', 'aria-selected', 'aria-current', 'aria-pressed']) {
    const v = el.getAttribute(a)
    if (v != null) return v === 'true' || v === 'page'
  }
  return null
}).catch(() => null)

const echapper = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** Motif d'un modèle du CATALOGUE : son `motif` (relevé sur le site), sinon son libellé exact.
 * Jamais un nom inventé : sans relevé, le catalogue n'a que `defaut`, qui ne touche à rien. */
export function motifModele (m) {
  if (m?.motif) return new RegExp(m.motif, 'i')
  return new RegExp(`(^|\\s)${echapper(String(m?.libelle || m?.id || '').trim())}(\\s|$)`, 'i')
}

const ROLES_OPTION = ['menuitemradio', 'menuitemcheckbox', 'menuitem', 'option', 'radio']
function options (page, re) {
  let loc = page.getByRole(ROLES_OPTION[0], { name: re })
  for (const r of ROLES_OPTION.slice(1)) loc = loc.or(page.getByRole(r, { name: re }))
  return loc
}

/** Id du modèle du catalogue dont le motif correspond au texte, '' sinon (ou si plusieurs). */
function modeleLu (texte, modeles) {
  const vus = modeles.filter((m) => m.id !== 'defaut' && motifModele(m).test(texte))
  return vus.length === 1 ? vus[0].id : ''
}

/**
 * Choisit `modele` (entrée du catalogue) dans le menu des modèles du site, puis LIT celui qui est
 * affiché. Bouton du menu : `menu` (motif relevé, `menu_modeles` du catalogue), sinon le bouton
 * à menu (aria-haspopup) qui affiche l'un des modèles du catalogue. Rend l'id lu ('' si la page
 * ne dit pas lequel est pris) : le noyau refuse tout autre id que celui demandé.
 */
export async function choisirModeleMenu (page, modele, { modeles = [], menu = '' } = {}) {
  const connus = modeles.length ? modeles : [modele]
  const reMenu = menu
    ? new RegExp(menu, 'i')
    : new RegExp(connus.filter((m) => m.id !== 'defaut').map((m) => motifModele(m).source).join('|') || '$^', 'i')
  const declencheur = async () => premier(page.getByRole('button', { name: reMenu }).and(page.locator('[aria-haspopup], [aria-expanded]')))
  const trig = await declencheur()
  if (!trig) throw new Echec('page', 'menu des modèles introuvable : la page a peut-être changé')
  const voulu = motifModele(modele)
  if (voulu.test(await nomDe(trig))) return modeleLu(await nomDe(trig), connus) || ''
  await cliquer(page, trig, { timeout: 5000 })
  await pause(PAUSE_COURTE_MS)
  const opt = await premier(options(page, voulu))
  if (!opt) {
    await page.keyboard.press('Escape').catch(() => {})
    throw new Echec('page', `modèle « ${modele.libelle || modele.id} » absent du menu : la page a peut-être changé`)
  }
  await cliquer(page, opt, { timeout: 5000 })
  await pause(PAUSE_COURTE_MS)
  // Menu resté ouvert : l'état de l'option fait foi ; sinon le libellé du bouton du menu.
  const encore = await premier(options(page, voulu))
  if (encore && await choisi(encore) === true) {
    await page.keyboard.press('Escape').catch(() => {})
    await pause(PAUSE_COURTE_MS)
  }
  const apres = await declencheur()
  return apres ? modeleLu(await nomDe(apres), connus) : ''
}

// --------------------------------------------------------------- pièces jointes

// 30/10 (1er essai réel) : Gemini nomme l'entrée « Importer des fichiers. Documents, données, fichiers de
// code » (« Upload files. Documents, data, code files ») : une précision après un point est permise.
const IMPORTER = /^\s*(upload (files?|images?|from computer)|importer (des |un )?(fichiers?|images?)|téléverser|choisir (des|un) fichiers?|depuis (l'|votre )?ordinateur|from (your )?(computer|device))\s*(?:[.:,].*)?$/i

/**
 * Dépose les pièces (octets vérifiés par pieces.mjs) : clic sur le bouton de pièce jointe du site
 * (`bouton`), puis, si un menu s'ouvre, sur « Upload files » ; le sélecteur de fichiers reçoit
 * les octets (FileChooser.setFiles). Sans sélecteur : setInputFiles sur le champ fichier de la
 * page. Puis attend un aperçu par pièce (`apercus`, comptés avant/après). Échec `page` sinon.
 */
export async function joindreFichiers (page, pieces, { bouton, apercus, attestation = null, redepot = false, unParUn = false }) {
  if (!pieces.length) return
  // 30/10 (Gemini réel : de 2 fichiers donnés d'un coup, 1 seul pris) : un fichier à la fois, son
  // aperçu attendu avant le suivant, comme une personne
  if (unParUn && pieces.length > 1) {
    for (const piece of pieces) {
      await joindreFichiers(page, [piece], { bouton, apercus, attestation, redepot })
      await pause(PAUSE_COURTE_MS)
    }
    return
  }
  const fichiers = pieces.map(({ name, mimeType, buffer }) => ({ name, mimeType, buffer }))
  const avant = apercus ? await nombre(apercus(page)) : 0
  const choix = async (loc) => {
    const attente = page.waitForEvent('filechooser', { timeout: TEST ? 1500 : 6000 }).catch(() => null)
    await cliquer(page, loc, { timeout: 5000 })
    return attente
  }
  let fc = null
  const entree = () => page.getByRole('menuitem', { name: IMPORTER }).or(page.getByRole('button', { name: IMPORTER })).or(page.getByRole('option', { name: IMPORTER }))
  // 30/10 (Gemini réel) : le menu du bouton s'était refermé avant le clic sur « Importer des fichiers »
  // (fenêtre surprise, lenteur) : deux essais, fenêtres refermées avant chacun, l'entrée du menu
  // cherchée dès qu'elle paraît (pas 6 s d'attente d'un sélecteur qui ne viendra pas du bouton).
  for (let essai = 0; essai < 2 && !fc && bouton; essai++) {
    if (attestation) await attestation(page)
    const b = await premier(bouton(page))
    if (!b) break
    const direct = page.waitForEvent('filechooser', { timeout: TEST ? 1500 : 6000 }).catch(() => null)
    await cliquer(page, b, { timeout: 5000 })
    let item = null
    const fin = Date.now() + (TEST ? 800 : 3000)
    while (!item && Date.now() < fin) {
      item = await premier(entree())
      if (!item) await dormir(TEST ? 30 : 200)
    }
    fc = item ? await choix(item) : await direct
    if (!fc) await page.keyboard.press('Escape').catch(() => {})
  }
  await pause(PAUSE_COURTE_MS)
  if (fc) {
    if (fc.isMultiple() || fichiers.length === 1) {
      await fc.setFiles(fichiers)
    } else {
      // Sélecteur d'un seul fichier : un dépôt par pièce, bouton rouvert à chaque fois.
      await fc.setFiles(fichiers[0])
      for (const f of fichiers.slice(1)) {
        await pause(PAUSE_COURTE_MS)
        const b2 = await premier(bouton(page))
        let fc2 = b2 ? await choix(b2) : null
        if (!fc2) {
          const item = await premier(page.getByRole('menuitem', { name: IMPORTER }).or(page.getByRole('button', { name: IMPORTER })))
          if (item) fc2 = await choix(item)
        }
        if (!fc2) throw new Echec('page', 'pièces jointes : le site n\'en prend qu\'une à la fois et ne rouvre pas le choix')
        await fc2.setFiles(f)
      }
    }
  } else {
    const champ = page.locator('input[type="file"]')
    const n = await nombre(champ)
    if (!n) throw new Echec('page', 'pièces jointes : ni bouton ni champ de fichier sur la page (elle a peut-être changé)')
    // Le champ qui accepte des images, sinon le premier
    let cible = champ.first()
    for (let i = 0; i < n; i++) {
      const accepte = await champ.nth(i).getAttribute('accept').catch(() => '') || ''
      if (!accepte || /image/.test(accepte)) { cible = champ.nth(i); break }
    }
    const multiple = await cible.evaluate((el) => el.multiple).catch(() => false)
    if (!multiple && fichiers.length > 1) throw new Echec('page', 'pièces jointes : le champ de fichier n\'en prend qu\'une')
    await cible.setInputFiles(multiple ? fichiers : fichiers[0])
  }
  if (!apercus) { await pause(); return }
  const fin = Date.now() + (TEST ? 3000 : 60000)
  while (Date.now() < fin) {
    siFerme(page)
    if (await nombre(apercus(page)) >= avant + fichiers.length) { await pause(PAUSE_COURTE_MS); return }
    // 30/10 : fenêtres du site pendant le dépôt (Gemini) : `attestation` rend « attestation » (1er
    // fichier d'un compte, acceptée par décision de l'utilisateur : le site a JETÉ le fichier, il est
    // redéposé une fois) ou « fenetre » (nouveautés refusées)
    const vu = attestation ? await attestation(page) : false
    if (vu === 'attestation' && !redepot) {
      await pause(PAUSE_COURTE_MS)
      return joindreFichiers(page, pieces, { bouton, apercus, attestation, redepot: true })
    }
    if (vu) continue
    await dormir(TEST ? 50 : 500)
  }
  throw new Echec('page', `pièces jointes déposées sans aperçu (${fichiers.length} attendues) : la page a peut-être changé`)
}

// --------------------------------------------------------------- texte d'une réponse sans image
// (déplacé de moteurs.mjs au lot A3, qui le réexporte : mêmes règles qu'au P14)

const QUOTA = new RegExp([
  "(reached|hit) (your|the) [\\w ]{0,40}limit",
  'limit (for|on) (image|creating|generating)',
  "can.t (create|generate|make) (any )?more images",
  'quota',
  "limite (de |d.)?(création|génération|images?)",
  'atteint (votre|ta|la|le) (limite|plafond|quota)',
  'plus de crédits',
  // « réessayez plus tard » / « try again later » SEULS ne suffisent pas (relecture du 21/10) :
  // un refus ou une panne le disent aussi ; il faut un mot de limite ou de crédit dans la phrase.
  "(limit|limite|quota|crédits?|credits?)[^.!?]{0,80}(réessayez|try again)",
  "(réessayez|try again)[^.!?]{0,80}(limit|limite|quota|crédits?|credits?)"
].join('|'), 'i')

const REPRISE = [
  /(?:after|at|après|à partir de|à|vers)\s+(\d{1,2})(?::|h)(\d{2})?\s*(am|pm)?/i,
  /(?:in|dans)\s+(\d{1,2})\s*(hours?|heures?|h)\b/i
]

/**
 * Classe le texte d'une réponse SANS image : quota (crédit gratuit épuisé, bascule possible)
 * ou refus de contenu (texte de l'IA montré tel quel, pas de bascule). `reprise` : heure ou
 * délai de reprise lus dans le message, si l'interface en donne un (« 15:00 », « +3h »,
 * « demain »), sinon vide ; jamais deviné.
 */
export function classer (texte) {
  const t = String(texte || '').replace(/\s+/g, ' ').trim()
  if (!QUOTA.test(t)) return { code: 'refus', message: t.slice(0, 500), reprise: '' }
  let reprise = ''
  const heure = REPRISE[0].exec(t)
  const delai = REPRISE[1].exec(t)
  if (heure) {
    let h = Number(heure[1])
    const m = Number(heure[2] || 0)
    const ampm = (heure[3] || '').toLowerCase()
    if (ampm === 'pm' && h < 12) h += 12
    if (ampm === 'am' && h === 12) h = 0
    if (h < 24 && m < 60) reprise = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
  } else if (delai) {
    reprise = `+${Number(delai[1])}h`
  } else if (/tomorrow|demain/i.test(t)) {
    reprise = 'demain'
  }
  return { code: 'quota', message: t.slice(0, 500), reprise }
}

// --------------------------------------------------------------- sites vidéo (P41 B5)

/** Rôles où un site affiche un message d'état (après l'envoi d'une vidéo, seul endroit où un
 * quota, des crédits ou un créneau de pointe sont crus : video.mjs, texteAlertes). */
export const ROLES_ALERTE = '[role="alert"], [role="alertdialog"], [role="status"], [role="dialog"], dialog[open]'

/** Vidéos de la page : adresses (src) dans l'ordre du document. */
export const videosDe = (page) => page.evaluate(() => [...document.querySelectorAll('video')]
  .map((v) => v.currentSrc || v.src || v.querySelector('source')?.src || '')).catch(() => [])

/** Mêmes vidéos, avec ce qui distingue un résultat d'une animation d'attente : affichée
 * (taille non nulle, pas masquée) et durée (s) si le navigateur l'a lue, sinon null. */
export const videosInfosDe = (page) => page.evaluate(() => [...document.querySelectorAll('video')].map((v) => {
  const r = v.getBoundingClientRect()
  const vue = r.width >= 8 && r.height >= 8 && (typeof v.checkVisibility !== 'function' || v.checkVisibility({ visibilityProperty: true, opacityProperty: true }))
  return {
    src: v.currentSrc || v.src || v.querySelector('source')?.src || '',
    visible: vue,
    duree: v.readyState >= 1 && Number.isFinite(v.duration) ? v.duration : null
  }
})).catch(() => [])

/** Parmi les éléments VISIBLES de `loc`, celui dont le centre est le plus proche de celui de
 * `cible` (bouton « Télécharger » d'une vidéo parmi ceux des autres) ; null s'il n'y en a aucun. */
export async function plusProche (loc, cible) {
  const v = loc.filter({ visible: true })
  const n = await nombre(v)
  if (!n) return null
  const box = await cible.boundingBox().catch(() => null)
  let meilleur = 0
  let distance = Infinity
  for (let i = 0; i < n && box; i++) {
    const b = await v.nth(i).boundingBox().catch(() => null)
    if (!b) continue
    const d = Math.hypot(b.x + b.width / 2 - (box.x + box.width / 2), b.y + b.height / 2 - (box.y + box.height / 2))
    if (d < distance) { distance = d; meilleur = i }
  }
  return v.nth(meilleur)
}

// Rôles d'une option dans un panneau ouvert ; le texte seul vient après (bulle sans rôles).
const ROLES_PANNEAU = ['option', 'menuitemradio', 'menuitem', 'radio', 'tab', 'button']

/** Premier élément visible de `loc` qui n'est ni le déclencheur, ni dedans, ni autour. */
async function horsDe (loc, poignee) {
  const v = loc.filter({ visible: true })
  const n = await nombre(v)
  for (let i = 0; i < n; i++) {
    const ok = await v.nth(i).evaluate((el, t) => !t || !(el === t || t.contains(el) || el.contains(t)), poignee).catch(() => false)
    if (ok) return v.nth(i)
  }
  return null
}

async function optionPanneau (page, re, poignee) {
  let loc = page.getByRole(ROLES_PANNEAU[0], { name: re })
  for (const r of ROLES_PANNEAU.slice(1)) loc = loc.or(page.getByRole(r, { name: re }))
  return await horsDe(loc, poignee) || await horsDe(page.getByText(re), poignee)
}

/**
 * Règle une valeur par un DÉCLENCHEUR qui ouvre un panneau (liste, menu ou bulle sans rôle) :
 * rien si le déclencheur l'affiche déjà (`affiche(texte)`) ; sinon clic sur le déclencheur, puis
 * sur chaque option voulue (`options`, motifs ; jamais le déclencheur lui-même), ou saisie de
 * `valeur` dans le champ numérique du panneau, Échap, et RELECTURE du déclencheur : il doit
 * afficher la valeur. Rend 'affiche' | 'choisi' ; introuvable ou pas pris : échec `page`.
 * Écrit pour Dreamina (P41 B5), dont les panneaux n'ont ni rôle ni état choisi relevés.
 */
export async function reglerPanneau (page, { nom, site, declencheur, affiche, options = [], valeur = null }) {
  const trig = await premier(declencheur(page))
  if (!trig) throw new Echec('page', `réglage « ${nom} » introuvable sur ${site} : la page a peut-être changé`)
  if (affiche(await nomDe(trig))) return 'affiche'
  await cliquer(page, trig, { timeout: 5000 })
  await pause(PAUSE_COURTE_MS)
  const poignee = await trig.elementHandle().catch(() => null)
  try {
    if (valeur !== null) {
      const champ = await horsDe(page.getByRole('spinbutton').or(page.locator('input[type="number"]')), poignee)
      if (!champ) throw new Echec('page', `réglage « ${nom} » : champ numérique introuvable sur ${site}`)
      await cliquer(page, champ, { timeout: 5000 })
      await page.keyboard.press('ControlOrMeta+A')
      await taper(page, String(valeur))
      await page.keyboard.press('Enter')
      await pause(PAUSE_COURTE_MS)
    }
    for (const re of options) {
      const opt = await optionPanneau(page, re, poignee)
      if (!opt) throw new Echec('page', `réglage « ${nom} » : option absente du panneau sur ${site} (la page a peut-être changé)`)
      await cliquer(page, opt, { timeout: 5000 })
      await pause(PAUSE_COURTE_MS)
    }
  } catch (e) {
    await page.keyboard.press('Escape').catch(() => {})
    throw e
  } finally {
    await poignee?.dispose().catch(() => {})
  }
  // Panneau resté ouvert (bulle à deux choix : format et résolution) : refermé avant la relecture
  await page.keyboard.press('Escape').catch(() => {})
  await pause(PAUSE_COURTE_MS)
  const apres = await premier(declencheur(page))
  const lu = apres ? await nomDe(apres) : ''
  if (!affiche(lu)) throw new Echec('page', `réglage « ${nom} » pas pris sur ${site} (affiché : « ${lu.slice(0, 80)} »)`)
  return 'choisi'
}

export { premier, nomDe, visible }
