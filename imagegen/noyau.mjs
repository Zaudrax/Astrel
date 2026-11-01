// Noyau COMMUN des workers du navigateur (plan P40 lot A2 ; images aujourd'hui, vidéo au plan
// P41) : tout ce qui ne dépend ni du site ni du type de média. Un worker (worker.mjs pour les
// images, video.mjs plus tard) n'écrit que son parcours propre et appelle `principal` et `seance`.
//
// Ce que fait le noyau : lecture de l'entrée (une ligne JSON sur stdin), champs communs du
// contrat, catalogue `config/ia-web.json` (lu aussi par astrel/ia_web.py), verrou et compteur
// du COMPTE, horaires et écart (humain.mjs), ouverture d'un Chrome RÉEL sur le profil persistant
// du compte, arrivée sur le site (défi anti-robot, session), choix du modèle, captures d'échec,
// enregistrement d'un téléchargement reconnu par ses OCTETS, sortie JSON.
//
// ---------------------------------------------------------------- contrat (plan P40 étape 7)
// Entrée, une ligne JSON :
//   {service, moteur (ancien nom de service, accepté), modele, type: image|video,
//    affichage: masquee|visible, fenetre: jour|24h, plafond, profil, sortie, prompt,
//    fichiers: [], delai_s, debug, url}
//   - `affichage` : Chrome sans fenêtre (`masquee`, défaut) ou fenêtre VISIBLE ordinaire
//     (`visible`), jamais cachée hors de l'écran ni réduite. Un site peut l'imposer visible
//     (`site.affichage`). Absent ou vide = `masquee` ; toute autre valeur = échec `entree`.
//     ÉCART ASSUMÉ au texte du plan (étape 7 : « `fenetre` (masquee|visible) »), décidé au
//     lot A1 et gardé au lot A2 : `fenetre` désignait déjà la fenêtre du QUOTA, lue par le
//     compteur du compte depuis P14 ; lui donner un second sens aurait cassé les anciens jobs.
//     Le choix caché/visible a donc son PROPRE champ, `affichage`, et `fenetre` garde son sens.
//     astrel/images_ia.py envoie les deux (`AFFICHAGE` et `ia_web.fenetre`) ;
//   - `fenetre` : fenêtre du QUOTA, `jour` (remis à zéro à minuit) ou `24h` (24 h après la
//     première unité) ; absente ou inconnue = celle du catalogue, sinon `jour` ;
//   - `modele` : id du catalogue ; vide ou `defaut` = modèle par défaut du site, rien n'est
//     touché. Un modèle introuvable sur la page = échec `page`, JAMAIS de substitution ;
//   - `plafond` : unités par compte et par service sur la fenêtre (1 image ; N crédits vidéo,
//     coût du modèle lu dans le catalogue) ;
//   - `url` : page à ouvrir à la place du vrai site (fausses pages de test seulement).
// Sortie, lignes JSON sur stdout (journal humain sur stderr) :
//   {t:"etape", etape}            ouverture | saisie | generation | telechargement
//   {t:"envoye"}                  prompt parti : compte dans le plafond, date l'écart de 10 min
//   {t:"fini", ..., modele_utilise, credits_restants?}   `credits_restants` si le site l'affiche
//   {t:"echec", code, message, reprise?, capture?}
//   codes : quota, captcha, refus, session, page, delai, plafond, occupe, fichiers, entree,
//           interne, nuit, rythme (`reprise` « HH:MM » pour ces deux derniers : P40 A2)
// Codes de sortie : 0 fini, 1 échec (raison dans la dernière ligne).
//
// ---------------------------------------------------------------- interface d'un site
// Un module de site (moteurs.mjs aujourd'hui, sites/*.mjs au lot A3, sites/flow.mjs au plan
// P41) est un objet ; le noyau n'en lit que :
//   nom, type ('image'|'video'), url (repli si le catalogue n'en donne pas),
//   hotesConnexion: RegExp      hôte d'une page de connexion = compte déconnecté
//   champ(page): Locator        champ du prompt (attendu visible : page prête)
//   connexion(page): Locator    lien ou bouton « Se connecter »
//   horsDefi?: string           sélecteurs CSS de la CONVERSATION, exclus de la recherche d'un défi
//   affichage?: 'visible'       impose une fenêtre visible (vidéo, plan P41)
//   choisirModele?(page, modele, outils) → id du modèle réellement sélectionné ; absent : seul
//                               le modèle par défaut est possible. `modele` vient du catalogue
//                               ({id, libelle, cout, motif?}) ; le job porte aussi `modeles` (ceux
//                               du service) et `menuModeles` (motif `menu_modeles` du catalogue)
//   credits?(page) → nombre | null   crédits restants affichés par le site
// Le reste (saisie, pièces jointes, attente, téléchargement) appartient au parcours du worker
// (images : worker.mjs, sites/*.mjs ; P40 lot A3).
// Fichiers enregistrés (`enregistrer`) : jamais un fichier existant écrasé, « -2 », « -3 »… si
// le nom est pris (variantes d'une même génération, plan P40 A3).

import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'
import { ATTENTE_SUR_PLACE_MS, ECART_MIN_MS, TEST, autorise, dormir, pause } from './humain.mjs'

const ICI = path.dirname(fileURLToPath(import.meta.url))
export const CATALOGUE = process.env.ASTREL_IA_WEB_CATALOGUE || path.join(ICI, '..', 'config', 'ia-web.json')
export const CHROME = process.env.ASTREL_IMAGEGEN_CHROME || '/usr/bin/google-chrome'
export const TYPES = ['image', 'video']
export const AFFICHAGES = ['masquee', 'visible']
export const FENETRES = ['jour', '24h']
export const MODELE_DEFAUT = 'defaut'
const ID = /^[a-z0-9][a-z0-9_-]{0,31}$/
const ENTREE_MAX = 64 * 1024

export const emit = (obj) => process.stdout.write(JSON.stringify(obj) + '\n')
export const note = (texte) => process.stderr.write(`[ia-web] ${texte}\n`)
const ligne1 = (e) => String(e?.message || e).split('\n')[0].slice(0, 200)

export class Echec extends Error {
  constructor (code, message, extra = {}) {
    super(message)
    this.code = code
    this.extra = extra
  }
}

// --------------------------------------------------------------- catalogue

/** Motif (source d'expression régulière) RELEVÉ sur un site, gardé s'il est court et valide. */
function motif (brut) {
  if (typeof brut !== 'string' || !brut.trim() || brut.length > 200) return ''
  try { new RegExp(brut, 'i') } catch { return '' } // eslint-disable-line no-new
  return brut
}

/** Services du catalogue {nom: {type, url, modeles: [{id, libelle, cout, motif?}], plafond,
 * fenetre, menu_modeles?}}. Mêmes règles que ia_web._service ; illisible : {} (le job garde ses
 * propres valeurs). P40 A3 : `motif` (par modèle) et `menu_modeles` (par service) sont les textes
 * RELEVÉS sur le site pour choisir un modèle (sites/commun.mjs) ; absents tant que rien n'est
 * relevé (`modeles_releves` faux), et jamais inventés. */
export function lireCatalogue (fichier = CATALOGUE) {
  let data
  try { data = JSON.parse(fs.readFileSync(fichier, 'utf8')) } catch { return {} }
  const out = {}
  for (const [nom, s] of Object.entries(data?.services || {})) {
    if (!ID.test(nom) || !s || typeof s !== 'object' || !TYPES.includes(s.type)) continue
    const modeles = (Array.isArray(s.modeles) ? s.modeles : [])
      .filter((m) => m && ID.test(String(m.id || '')))
      .map((m) => {
        const entree = { id: String(m.id), libelle: String(m.libelle || m.id).slice(0, 80), cout: Math.max(1, Math.min(1000, Number.parseInt(m.cout, 10) || 1)) }
        if (motif(m.motif)) entree.motif = m.motif
        return entree
      })
    out[nom] = {
      type: s.type,
      url: /^https:\/\//.test(String(s.url || '')) ? String(s.url) : '',
      modeles: modeles.length ? modeles : [{ id: MODELE_DEFAUT, libelle: 'Modèle par défaut du site', cout: 1 }],
      plafond: Math.max(1, Math.min(1000, Number.parseInt(s.plafond, 10) || 20)),
      fenetre: FENETRES.includes(s.fenetre) ? s.fenetre : 'jour'
    }
    if (motif(s.menu_modeles)) out[nom].menu_modeles = s.menu_modeles
  }
  return out
}

// --------------------------------------------------------------- entrée

export function lireEntree () {
  return new Promise((resolve, reject) => {
    let brut = ''
    process.stdin.setEncoding('utf8')
    process.stdin.on('data', (d) => {
      brut += d
      if (brut.length > ENTREE_MAX) reject(new Echec('entree', 'entrée trop longue'))
      if (brut.includes('\n')) resolve(brut.slice(0, brut.indexOf('\n')))
    })
    process.stdin.on('end', () => resolve(brut))
    process.stdin.on('error', reject)
  })
}

/**
 * Champs COMMUNS du contrat, vérifiés. `sites` : modules de site connus de ce worker ; `type` :
 * type de média que ce worker sait produire. Bornes propres au worker dans `bornes`
 * ({promptMax, plafondDefaut, plafondMax, delaiDefautS, delaiMinS, delaiMaxS}). Le parcours
 * complète le résultat (pièces jointes, durée d'une vidéo, …).
 */
export function verifierBase (job, { sites, type, bornes = {}, catalogue = lireCatalogue() }) {
  const b = { promptMax: 4000, plafondDefaut: 20, plafondMax: 1000, delaiDefautS: 120, delaiMinS: 5, delaiMaxS: 300, ...bornes }
  if (!job || typeof job !== 'object' || Array.isArray(job)) throw new Echec('entree', 'job illisible')
  const nom = String(job.service || job.moteur || '')
  if (job.service && job.moteur && job.service !== job.moteur) throw new Echec('entree', 'service et moteur différents')
  const site = Object.hasOwn(sites, nom) ? sites[nom] : null
  if (!site) throw new Echec('entree', `service inconnu : ${nom}`)
  const typeJob = job.type == null || job.type === '' ? type : job.type
  if (typeJob !== type) throw new Echec('entree', `type ${typeJob} : ce worker ne produit que le type ${type}`)
  const entree = catalogue[nom]
  if (entree && entree.type !== type) throw new Echec('entree', `service ${nom} de type ${entree.type} au catalogue`)
  for (const cle of ['profil', 'sortie']) {
    if (typeof job[cle] !== 'string' || !path.isAbsolute(job[cle])) throw new Echec('entree', `${cle} : chemin absolu attendu`)
  }
  if (job.debug != null && job.debug !== '' && (typeof job.debug !== 'string' || !path.isAbsolute(job.debug))) {
    throw new Echec('entree', 'debug : chemin absolu attendu')
  }
  const prompt = String(job.prompt || '').replace(/\s+/g, ' ').trim()
  if (!prompt) throw new Echec('entree', 'prompt vide')
  if (prompt.length > b.promptMax) throw new Echec('entree', `prompt trop long (${b.promptMax} caractères au plus)`)
  const modeles = entree?.modeles || [{ id: MODELE_DEFAUT, libelle: 'Modèle par défaut du site', cout: 1 }]
  const demande = String(job.modele || '').trim().toLowerCase() || MODELE_DEFAUT
  const modele = demande === MODELE_DEFAUT
    ? (modeles.find((m) => m.id === MODELE_DEFAUT) || { id: MODELE_DEFAUT, libelle: 'Modèle par défaut du site', cout: modeles[0].cout })
    : modeles.find((m) => m.id === demande)
  if (!modele) throw new Echec('entree', `modèle inconnu pour ${nom} : ${demande}`)
  if (job.affichage != null && job.affichage !== '' && !AFFICHAGES.includes(job.affichage)) {
    throw new Echec('entree', `affichage : ${AFFICHAGES.join(' ou ')} attendu`)
  }
  const affichage = site.affichage === 'visible' ? 'visible' : (job.affichage || 'masquee')
  const plafond = Math.min(b.plafondMax, Math.max(1, Number.parseInt(job.plafond, 10) || entree?.plafond || b.plafondDefaut))
  const fenetre = FENETRES.includes(job.fenetre) ? job.fenetre : (entree?.fenetre || 'jour')
  const delai = Math.min(b.delaiMaxS, Math.max(b.delaiMinS, Number(job.delai_s) || b.delaiDefautS))
  if (job.url != null && !/^(https?:|file:)/.test(String(job.url))) throw new Echec('entree', 'url invalide')
  const fichiers = Array.isArray(job.fichiers) ? job.fichiers : []
  return {
    site, service: nom, type, modele, affichage, prompt, fichiers, plafond, fenetre,
    delaiMs: delai * 1000, profil: job.profil, sortie: job.sortie, debug: job.debug || '',
    url: job.url || entree?.url || site.url,
    // P40 A3 : modèles du service et motif du menu des modèles, pour site.choisirModele
    modeles, menuModeles: entree?.menu_modeles || ''
  }
}

// --------------------------------------------------------------- verrou et compteur du compte

const jour = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

function lireJson (fichier, defaut) {
  try { return JSON.parse(fs.readFileSync(fichier, 'utf8')) } catch { return defaut }
}
function ecrireJson (fichier, data) {
  const tmp = `${fichier}.${process.pid}.tmp`
  fs.writeFileSync(tmp, JSON.stringify(data), { mode: 0o600 })
  fs.renameSync(tmp, fichier)
}

/** Un job à la fois par compte : `<profil>.lock` (pid). Verrou d'un process mort : repris. Un
 * pid illisible (fichier vide : un autre worker entre son open et son write) vaut `occupe`,
 * jamais supprimé. Rendu par la fonction rendue ET à la sortie du process (process.exit de la
 * garde de 185 s, SIGINT : Playwright sort en 130), une seule fois. */
export function verrouiller (profil) {
  const fichier = `${profil}.lock`
  for (let essai = 0; essai < 2; essai++) {
    let fd
    try {
      fd = fs.openSync(fichier, 'wx', 0o600)
    } catch (e) {
      if (e.code !== 'EEXIST') throw e
      let brut
      try { brut = fs.readFileSync(fichier, 'utf8') } catch (l) {
        if (l.code === 'ENOENT') continue // rendu entre-temps : nouvel essai
        throw new Echec('occupe', 'verrou du compte illisible')
      }
      if (!/^\s*\d+\s*$/.test(brut)) throw new Echec('occupe', 'une génération démarre sur ce compte')
      const pid = Number.parseInt(brut, 10)
      let vivant = false
      try { if (pid > 0) { process.kill(pid, 0); vivant = true } } catch {}
      if (vivant) throw new Echec('occupe', 'une génération est déjà en cours sur ce compte')
      try { fs.unlinkSync(fichier) } catch {}
      continue
    }
    try {
      fs.writeSync(fd, String(process.pid))
    } catch (e) {
      try { fs.unlinkSync(fichier) } catch {} // pas de verrou vide laissé derrière soi
      throw e
    } finally {
      fs.closeSync(fd)
    }
    let rendu = false
    const liberer = () => {
      if (rendu) return
      rendu = true
      process.off('exit', liberer)
      try { fs.unlinkSync(fichier) } catch {}
    }
    process.on('exit', liberer)
    return liberer
  }
  throw new Echec('occupe', 'verrou du compte impossible à prendre')
}

/**
 * Compteur du COMPTE (`<profil>.compteur.json`), en plus de l'état du cœur. v2 :
 *   {v: 2, dernier, dernier_envoi, services: {<service>: {jour, n, debut}}}
 * `n` en UNITÉS par service (un compte Google sert Gemini ET Flow) ; `dernier` : dernière
 * ouverture du site (écart de 20 s), `dernier_envoi` : dernier prompt envoyé, tous services (gardé pour
 * le journal) ; `services.<s>.dernier_envoi` : celui de CE site, qui date l'écart de 30 s (30/10). Fenêtre « jour » : remis à zéro à minuit ; « 24h » : 24 h après la
 * première unité. v1 (P14 : {jour, n, debut, dernier}, Gemini seul) : lu comme service gemini.
 * À appeler SOUS le verrou du compte (lu, puis réécrit en entier).
 */
export function compteur (profil, service, fenetre = 'jour', maintenant = Date.now) {
  const fichier = `${profil}.compteur.json`
  const lu = lireJson(fichier, {})
  let data
  if (lu && lu.v === 2 && lu.services && typeof lu.services === 'object') {
    data = { v: 2, dernier: Number(lu.dernier) || 0, dernier_envoi: Number(lu.dernier_envoi) || 0, services: { ...lu.services } }
  } else {
    const v1 = lu && typeof lu === 'object' ? lu : {}
    const n = Number(v1.n) || 0
    data = {
      v: 2,
      dernier: Number(v1.dernier) || 0,
      dernier_envoi: n > 0 ? Number(v1.dernier) || 0 : 0, // prudent : la dernière activité
      services: n > 0 || v1.jour ? { gemini: { jour: v1.jour || '', n, debut: Number(v1.debut) || 0, dernier_envoi: n > 0 ? Number(v1.dernier) || 0 : 0 } } : {}
    }
  }
  const brut = data.services[service] && typeof data.services[service] === 'object' ? data.services[service] : {}
  const neuf = fenetre === '24h'
    ? !(brut.debut && maintenant() - brut.debut < 86400000)
    : brut.jour !== jour(new Date(maintenant()))
  const etat = neuf ? { jour: jour(new Date(maintenant())), n: 0, debut: 0 } : { n: 0, debut: 0, ...brut }
  data.services[service] = etat
  return {
    etat,
    get dernier () { return data.dernier },
    get dernierEnvoi () { return Number(etat.dernier_envoi) || 0 }, // 30/10 : par site, plus par compte
    ajouter (unites = 1) {
      const t = maintenant()
      if (!etat.n) etat.debut = t
      etat.n += Math.max(1, Number.parseInt(unites, 10) || 1)
      etat.jour = jour(new Date(t))
      data.dernier = t
      data.dernier_envoi = t
      etat.dernier_envoi = t
      ecrireJson(fichier, data)
    },
    fin () {
      data.dernier = maintenant()
      ecrireJson(fichier, data)
    }
  }
}

// --------------------------------------------------------------- navigateur

/** User-Agent du Chrome installé (« HeadlessChrome » sinon), pour le mode sans fenêtre seul. */
function userAgent () {
  try {
    const v = /(\d+)\./.exec(execFileSync(CHROME, ['--version'], { encoding: 'utf8', timeout: 10000 }))
    if (v) return `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${v[1]}.0.0.0 Safari/537.36`
  } catch {}
  return undefined
}

/**
 * Options de lancement d'un Chrome RÉEL (le binaire installé) sur un profil persistant :
 * bac à sable de Chrome actif, sans `--enable-automation`, aucune option « stealth ».
 * - `visible` : fenêtre ordinaire, taille et User-Agent NATIFS (rien de surchargé) ;
 * - `masquee` : sans fenêtre, vue 1366×860, User-Agent du Chrome installé (sinon il dit
 *   « HeadlessChrome »).
 */
export const IGNORES = ['--enable-automation', '--password-store=basic', '--use-mock-keychain']

export function optionsLancement ({ affichage = 'masquee', chrome = CHROME, ua = undefined } = {}) {
  const visible = affichage === 'visible'
  const options = {
    executablePath: chrome,
    headless: !visible,
    chromiumSandbox: true,
    // 30/10 : Playwright ajoute d'office --password-store=basic et --use-mock-keychain : le worker
    // chiffrait les cookies autrement que la fenêtre de connexion (Chrome normal, trousseau GNOME),
    // ne pouvait pas lire la session de l'utilisateur et Chrome la JETAIT à chaque génération. Retirés :
    // même stockage que la fenêtre de connexion (même binaire, même environnement : celui du cœur).
    ignoreDefaultArgs: IGNORES,
    acceptDownloads: true,
    timeout: 30000
  }
  if (visible) {
    options.viewport = null
  } else {
    options.viewport = { width: 1366, height: 860 }
    const agent = ua === undefined ? userAgent() : ua
    if (agent) options.userAgent = agent
  }
  return options
}

// Contextes dont le Chrome s'est fermé sous le worker : SIGTERM (Playwright ferme alors Chrome
// SANS faire sortir le process) ou Chrome tué de l'extérieur. Lu par `siFerme`.
const fermes = new WeakSet()

export async function ouvrir (profil, affichage = 'masquee') {
  fs.mkdirSync(profil, { recursive: true, mode: 0o700 })
  try {
    const ctx = await chromium.launchPersistentContext(profil, optionsLancement({ affichage }))
    ctx.once('close', () => fermes.add(ctx))
    return ctx
  } catch (e) {
    const msg = String(e?.message || e)
    if (/ProcessSingleton|SingletonLock|profile.*in use|already running/i.test(msg)) {
      throw new Echec('occupe', 'profil du compte déjà ouvert par un autre Chrome')
    }
    throw new Echec('interne', `Chrome ne démarre pas : ${ligne1(msg)}`)
  }
}

export async function capture (page, debug, code) {
  if (!debug || !page) return ''
  try {
    fs.mkdirSync(debug, { recursive: true, mode: 0o700 })
    const fichier = path.join(debug, `${new Date().toISOString().replace(/[:.]/g, '-')}-${code}.png`)
    await page.screenshot({ path: fichier, timeout: 5000 })
    fs.chmodSync(fichier, 0o600)
    // 30/10 (1er essai réel) : relevé texte à côté de la capture (boutons et fenêtres visibles, leur
    // nom, rôle et conteneurs) : diagnostiquer un site changé sans deviner ni rouvrir le profil.
    const plan = await page.evaluate(() => {
      const vu = (e) => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden'
      const nom = (e) => `${e.tagName.toLowerCase()}${e.getAttribute('role') ? `[role=${e.getAttribute('role')}]` : ''}` +
        `${e.getAttribute('aria-label') ? ` aria="${e.getAttribute('aria-label').slice(0, 80)}"` : ''}`
      const chaine = (e) => { const out = []; for (let p = e.parentElement, i = 0; p && i < 6; p = p.parentElement, i++) out.push(nom(p) + (p.className && typeof p.className === 'string' ? `.${p.className.split(' ')[0].slice(0, 30)}` : '')); return out.join(' < ') }
      return [...document.querySelectorAll('button, [role=button], [role=menuitem], [role=dialog], [role=alertdialog], mat-dialog-container, input[type=file], [contenteditable=true]')]
        .filter((e) => e.tagName === 'INPUT' || vu(e))
        .map((e) => `${nom(e)} texte="${(e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60)}" ⟵ ${chaine(e)}`).join('\n')
    }).catch(() => '')
    if (plan) { fs.writeFileSync(`${fichier}.txt`, plan + '\n', { mode: 0o600 }) }
    return fichier
  } catch { return '' }
}

export const visible = (loc) => loc.isVisible().catch(() => false)
export const nombre = (loc) => loc.count().catch(() => 0)

/** Navigateur fermé (page ou contexte) : échec `interne` aussitôt. À appeler dans toute boucle
 * de sonde : `visible` et `nombre` avalent les erreurs d'une page fermée, la boucle irait
 * sinon jusqu'au délai (fiche 14, D1). Le verrou est rendu par le `finally` de `seance`. */
export function siFerme (page) {
  if (page.isClosed() || fermes.has(page.context())) {
    throw new Echec('interne', 'navigateur fermé pendant la génération (worker arrêté ou Chrome tué)')
  }
}

// --------------------------------------------------------------- défi anti-robot

const CAPTCHA_TEXTE = /unusual traffic|trafic inhabituel|not a robot|pas un robot|verify (that )?you are (a )?human|vérifie[rz]? que vous êtes (un )?humain/i
const CAPTCHA_CADRE = /recaptcha|hcaptcha|challenges\.cloudflare|arkoselabs|funcaptcha/i

// Ce qui n'est PAS la page du site, quel qu'il soit : champ de saisie, barres latérales,
// scripts. Le site ajoute sa conversation (`site.horsDefi`). Relecture du 21/10 : un prompt
// « un chat qui n'est pas un robot » déclenchait un faux captcha.
const HORS_DEFI = 'script, style, noscript, template, [contenteditable], textarea, ' +
  'nav, aside, [role="navigation"], [role="complementary"], [role="log"]'

/** Cadre de défi réellement MONTRÉ : visible (ni caché ni masqué), assez grand, dans la vue. Le
 * badge d'un reCAPTCHA invisible (`size=invisible`, 256×60, posé par Google sur Flow) et le cadre
 * de défi qu'il garde caché hors de la vue (top -10000 px) ne comptent pas : mesuré le 28/10
 * (plan P41 lot B1, /tmp/p41-b1/recaptcha.mjs), l'ancien test les prenait pour un défi et
 * arrêtait chaque ouverture de Flow (« Page protégée par reCAPTCHA »). */
async function cadreMontre (page, f) {
  if (/[?&]size=invisible\b/i.test(f.url())) return false
  const el = await f.frameElement().catch(() => null)
  if (!el || !await el.isVisible().catch(() => false)) return false
  const box = await el.boundingBox().catch(() => null)
  if (!box || box.width < 100 || box.height < 60) return false
  const vue = page.viewportSize() ||
    await page.evaluate(() => ({ width: innerWidth, height: innerHeight })).catch(() => null)
  if (!vue) return true // vue illisible : prudent, le cadre compte
  return box.x < vue.width && box.y < vue.height && box.x + box.width > 0 && box.y + box.height > 0
}

/** Défi anti-robot VISIBLE : arrêt net, jamais contourné. Le reCAPTCHA invisible que Google
 * pose sur ses pages (badge, cadre caché) ne compte pas (`cadreMontre`). Le texte n'est lu que
 * HORS de la conversation, et le prompt (`ignorer`) en est retiré. */
export async function captcha (page, ignorer = '', horsDefi = '') {
  if (/\/sorry\/|\/challenge\b/i.test(page.url())) return true
  for (const f of page.frames()) {
    if (f === page.mainFrame() || !CAPTCHA_CADRE.test(f.url())) continue
    if (await cadreMontre(page, f)) return true
  }
  const hors = horsDefi ? `${HORS_DEFI}, ${horsDefi}` : HORS_DEFI
  let texte = await page.evaluate((h) => {
    const copie = document.body?.cloneNode(true)
    if (!copie) return ''
    copie.querySelectorAll(h).forEach((e) => e.remove())
    return copie.textContent || ''
  }, hors).catch(() => '')
  texte = texte.replace(/\s+/g, ' ')
  const prompt = String(ignorer || '').replace(/\s+/g, ' ').trim()
  if (prompt) texte = texte.split(prompt).join(' ')
  return CAPTCHA_TEXTE.test(texte.slice(0, 20000))
}

export async function defi (page, job) {
  if (await captcha(page, job.prompt, job.site.horsDefi || '')) {
    throw new Echec('captcha', 'défi anti-robot affiché : arrêt, jamais contourné')
  }
}

// --------------------------------------------------------------- arrivée sur le site

/** Ouvre le site, s'arrête sur un défi ou une session perdue, attend le champ du prompt. */
export async function arriver (page, job) {
  const { site } = job
  await page.goto(job.url, { waitUntil: 'domcontentloaded', timeout: 45000 })
    .catch((e) => { throw new Echec('page', `site injoignable : ${ligne1(e)}`) })
  await pause()
  await defi(page, job)
  const champ = site.champ(page)
  const hote = (() => { try { return new URL(page.url()).hostname } catch { return '' } })()
  if (site.hotesConnexion.test(hote)) throw new Echec('session', 'compte déconnecté : à reconnecter')
  if (!await visible(champ)) {
    await champ.waitFor({ state: 'visible', timeout: TEST ? 1500 : 15000 }).catch(() => {})
  }
  if (!await visible(champ)) {
    if (await visible(site.connexion(page).first())) throw new Echec('session', 'compte déconnecté : à reconnecter')
    throw new Echec('page', 'champ du prompt introuvable : la page a peut-être changé')
  }
  if (await visible(site.connexion(page).first())) throw new Echec('session', 'compte déconnecté : à reconnecter')
  return champ
}

/** Modèle demandé : `defaut` = rien n'est touché ; sinon le site le sélectionne et dit lequel il
 * a pris. Pas de sélecteur, ou un autre modèle : échec `page`, jamais de substitution. */
export async function appliquerModele (page, job, outils = {}) {
  const { modele, site } = job
  if (modele.id === MODELE_DEFAUT) return MODELE_DEFAUT
  if (typeof site.choisirModele !== 'function') {
    throw new Echec('page', `modèle ${modele.id} introuvable : choix du modèle pas pris en charge sur ce site`)
  }
  let pris
  try {
    pris = await site.choisirModele(page, modele, outils)
  } catch (e) {
    if (e instanceof Echec) throw e
    throw new Echec('page', `modèle ${modele.id} introuvable : ${ligne1(e)}`)
  }
  if (pris !== modele.id) throw new Echec('page', `modèle ${modele.id} introuvable (la page a pris ${String(pris || 'aucun').slice(0, 40)})`)
  return modele.id
}

// --------------------------------------------------------------- séance

/**
 * Une génération sur un compte, de bout en bout, autour du `parcours` propre au worker :
 *   verrou → compteur → plafond, nuit, écart de 10 min → écart de 20 s → Chrome →
 *   site prêt → modèle → parcours({page, champ, job, envoye, modele}) → crédits → fermeture.
 * `envoye()` est à appeler par le parcours au moment où le prompt part (une fois) : compte les
 * unités du modèle et émet {t:"envoye"}. Rend le résultat du parcours + `modele_utilise` et,
 * si le site les affiche, `credits_restants`. `emettre` : sortie des lignes (stdout ; tests).
 */
export async function seance (job, parcours, { outils = {}, emettre = emit } = {}) {
  fs.mkdirSync(path.dirname(job.profil), { recursive: true, mode: 0o700 })
  const liberer = verrouiller(job.profil)
  let ctx = null
  let page = null
  let ouvert = false
  let cpt = null
  try {
    cpt = compteur(job.profil, job.service, job.fenetre)
    const unites = job.modele.cout
    if (cpt.etat.n + unites > job.plafond) {
      throw new Echec('plafond', `plafond atteint (${cpt.etat.n} sur ${job.plafond} unités sur ce compte pour ${job.service}, fenêtre ${job.fenetre})`)
    }
    const refus = autorise({ dernierEnvoi: cpt.dernierEnvoi })
    if (refus?.code === 'rythme' && refus.reste <= ATTENTE_SUR_PLACE_MS) await dormir(refus.reste) // 30 s : attendu ici
    else if (refus) throw new Echec(refus.code, refus.message, { reprise: refus.reprise })
    const attente = cpt.dernier + ECART_MIN_MS - Date.now()
    if (attente > 0) await dormir(Math.min(attente, ECART_MIN_MS))

    emettre({ t: 'etape', etape: 'ouverture' })
    ouvert = true
    ctx = await ouvrir(job.profil, job.affichage)
    page = ctx.pages()[0] || await ctx.newPage()
    const champ = await arriver(page, job)
    const modele = await appliquerModele(page, job, outils)
    let envoyes = 0
    const envoye = () => {
      if (envoyes++) return // un seul envoi compté par séance : aucune relance
      cpt.ajouter(unites)
      emettre({ t: 'envoye' })
    }
    const res = await parcours({ page, champ, job, envoye, modele })
    const sortie = { ...res, modele_utilise: modele }
    if (typeof job.site.credits === 'function') {
      const n = await job.site.credits(page).catch(() => null)
      if (Number.isFinite(n)) sortie.credits_restants = n
    }
    return sortie
  } catch (e) {
    if (e instanceof Echec && ['captcha', 'page', 'delai', 'session'].includes(e.code)) {
      e.extra.capture = await capture(page, job.debug, e.code)
    }
    throw e
  } finally {
    if (ouvert && cpt) cpt.fin() // l'écart de 20 s vaut aussi après un échec sur le site
    if (ctx) await ctx.close().catch(() => {})
    liberer()
  }
}

// --------------------------------------------------------------- enregistrement

export const SIGNATURES_IMAGE = [
  ['png', (b) => b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47],
  ['jpg', (b) => b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff],
  ['webp', (b) => b.slice(0, 4).toString() === 'RIFF' && b.slice(8, 12).toString() === 'WEBP']
]

export function nomFichier (service, prompt, ext, d = new Date(), vide = 'media') {
  const stamp = `${jour(d).replace(/-/g, '')}-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}${String(d.getSeconds()).padStart(2, '0')}`
  const slug = prompt.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/^(genere une image|generate an image)\s*:?\s*/, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || vide
  return `${stamp}-${service}-${slug}.${ext}`
}

/** Pose `tmp` sous `nom` dans `dossier` SANS jamais écraser : nom pris (même seconde, même
 * prompt : les 4 variantes de Bing, plan P40 A3), alors « -2 », « -3 »… avant l'extension.
 * Lien puis effacement du temporaire : la création échoue si le nom existe (pas de course). */
export function poserSansEcraser (tmp, dossier, nom) {
  const ext = path.extname(nom)
  const base = nom.slice(0, nom.length - ext.length)
  for (let i = 1; i <= 100; i++) {
    const essai = i === 1 ? nom : `${base}-${i}${ext}`
    const fichier = path.join(dossier, essai)
    try {
      try {
        fs.linkSync(tmp, fichier)
      } catch (e) {
        // Système de fichiers sans liens durs : copie exclusive (échoue aussi si le nom existe)
        if (!['EPERM', 'ENOTSUP', 'EOPNOTSUPP', 'ENOSYS'].includes(e.code)) throw e
        fs.copyFileSync(tmp, fichier, fs.constants.COPYFILE_EXCL)
      }
    } catch (e) {
      if (e.code === 'EEXIST') continue
      throw e
    }
    fs.unlinkSync(tmp)
    return { fichier, nom: essai }
  }
  throw new Echec('interne', `trop de fichiers du même nom dans ${dossier}`)
}

/**
 * Enregistre un téléchargement Playwright (ou tout objet {saveAs}) dans `job.sortie` : fichier
 * temporaire, type reconnu par ses OCTETS (`signatures` : [[ext, test(tête de 16 octets)]]),
 * taille bornée, puis posé sous un nom LIBRE (jamais un fichier existant écrasé). Rend
 * {fichier, nom, octets, type: ext}. Rien n'est laissé en cas de refus.
 */
export async function enregistrer (dl, job, { signatures, octetsMax, refus = 'le fichier téléchargé n\'a pas le type attendu', vide = 'media' }) {
  fs.mkdirSync(job.sortie, { recursive: true })
  const tmp = path.join(job.sortie, `.telechargement-${process.pid}`)
  await dl.saveAs(tmp)
  try {
    const octets = fs.statSync(tmp).size
    const tete = Buffer.alloc(16)
    const fd = fs.openSync(tmp, 'r')
    fs.readSync(fd, tete, 0, 16, 0)
    fs.closeSync(fd)
    const sig = signatures.find(([, ok]) => ok(tete))
    if (!sig || octets > octetsMax) throw new Echec('page', refus)
    const { fichier, nom } = poserSansEcraser(tmp, job.sortie, nomFichier(job.service, job.prompt, sig[0], new Date(), vide))
    return { fichier, nom, octets, type: sig[0] }
  } finally {
    try { fs.unlinkSync(tmp) } catch {}
  }
}

// --------------------------------------------------------------- programme

/**
 * Programme d'un worker : lit l'entrée, `verifier(brut)` → job, `executer(job)` → résultat
 * émis en {t:"fini", ...}. Toute erreur devient {t:"echec", code, message, ...}. Garde-fou :
 * le worker ne traîne jamais seul au-delà de `delaiMs + margeMs`. Rend le code de sortie.
 */
export async function principal (verifier, executer, { margeMs = 180000 } = {}) {
  let job
  try {
    const brut = await lireEntree()
    let lu
    try { lu = JSON.parse(brut) } catch { throw new Echec('entree', 'JSON illisible') }
    job = verifier(lu)
  } catch (e) {
    emit({ t: 'echec', code: e.code || 'entree', message: String(e.message || e) })
    return 1
  }
  const garde = setTimeout(() => {
    emit({ t: 'echec', code: 'delai', message: 'worker arrêté : trop long' })
    process.exit(1)
  }, job.delaiMs + margeMs)
  garde.unref()
  try {
    const res = await executer(job)
    emit({ t: 'fini', ...res })
    note(`${job.type} enregistrée : ${res.nom || '?'}`)
    return 0
  } catch (e) {
    const code = e instanceof Echec ? e.code : 'interne'
    const message = String(e?.message || e).split('\n')[0].slice(0, 500)
    emit({ t: 'echec', code, message, ...(e instanceof Echec ? e.extra : {}) })
    note(`échec ${code} : ${message}`)
    return 1
  }
}

/** Lance `principal` si le module `url` est le script exécuté (pas un import de test). */
export function siPrincipal (url, verifier, executer, options) {
  if (!process.argv[1] || fileURLToPath(url) !== path.resolve(process.argv[1])) return
  principal(verifier, executer, options).then((code) => process.exit(code), (e) => {
    emit({ t: 'echec', code: 'interne', message: String(e?.message || e).slice(0, 300) })
    process.exit(1)
  })
}
