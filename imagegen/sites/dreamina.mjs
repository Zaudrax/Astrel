// Ce que le worker vidéo (video.mjs) sait de Dreamina (CapCut ; plan P41 lot B5, 29/10/2026).
//
// Source unique : relevé du 29/10 (~/Projets/Videos-IA/releves/dreamina/releve.md,
// captures 0*.png, pages enregistrées dreamina-*.html, vidéo réelle essai-chat-roux.mp4) :
// - page https://dreamina.capcut.com/ai-tool/home?type=video, onglets à bascule (aria-pressed)
//   Agent, Image, Vidéo, Contenu audio ; compte Google connecté à la main (« Continuer avec Google ») ;
// - barre : éditeur tiptap (role textbox, aria-label « Invite »), liste des modèles (role combobox,
//   aria-label « Modèle » ; Seedance 2.0 Mini par défaut), bouton « 16:9 · 720P » qui ouvre une bulle
//   Format (21:9 16:9 4:3 1:1 3:4 9:16) + Résolution (720P 1080P 2K 4K), bouton « 5s » qui ouvre
//   « Durée totale » (curseur 0-15 s et champ numérique), prix près du bouton d'envoi (« 0 55 » :
//   débité 0, prix catalogue 55 BARRÉ), bouton d'envoi aria-label « Générer » ;
// - envoi : fenêtre « Avant de continuer » (role dialog, « Annuler » / « Confirmer » ; l'utilisateur :
//   confirmée à CHAQUE vidéo), puis /ai-tool/generate?workspace=<id> : fiche de la génération
//   (prompt, « Dreamina Seedance 2.0 Mini · 5s · 16:9 · 720P · Détails »), badge « 7% », animation
//   d'attente (vidéo en boucle) ; finie en ≈ 2 min : badge parti, <video> (CDN signé, temporaire) ;
// - téléchargement : au survol, ↓ puis « … » et ☆ en haut à droite ; le 1er clic ouvre « Supprime
//   les filigranes IA » (interrupteur, « Enregistrer la préférence », croix « Fermer ») ET le fichier
//   part quand même (mp4 H.264 1280×720, son AAC, filigrane accepté par l'utilisateur). Le pilote ne
//   touche JAMAIS à l'interrupteur ni à « S'abonner » : il ferme par la croix ;
// - crédits : 120 gratuits par jour (donnés à la visite connectée), Mini 720p 5 s = 55 (remises
//   vues : 0 la 1re fois, puis 25), Mini 1080p 5 s = 80. Solde affiché en haut (« 216 »).
// - 29/10 après-midi (relevé « Prix de TOUS les modèles ») : 10 modèles au menu (role=option),
//   prix catalogue par modèle, résolution et durée (`MODELES`) ; mode des références = second
//   combobox (sans aria-label ; Références de tout type, Première et dernière image, …) :
//   Seedance 1.5 Pro, 1.0 et 1.0 rapide sont GRISÉS en « Références de tout type » et demandent
//   « Première et dernière image » ; bulle Format/Résolution = deux radiogroup, options en <label> ;
//   durée = input role=spinbutton (bornes aria-valuemin/max), prix mis à jour ≈ 1 s plus tard.
//
// Classes CSS suffixées d'un code qui change à chaque version du site : visé par TEXTE, RÔLE et
// aria ; un PRÉFIXE de classe relevé (`progress-badge`, `credit-amount-text`, `controls-tr`)
// seulement en secours, là où la page n'offre rien d'autre. Libellés avec espaces insécables
// (`\s` les couvre). Sélecteurs éprouvés sur les pages enregistrées (test/dreamina.test.mjs) ;
// jamais essayés sur le vrai site.
//
// ⚠️ À RELEVER au premier essai réel (TODO) : nom accessible du bouton ↓ (inconnu :
// secours `controls-tr`, puis source de la balise vidéo), messages d'erreur (crédits épuisés, file
// chargée, contenu refusé : JAMAIS vus, donc jamais reconnus en dur : un message d'état sans vidéo
// = échec `page` avec capture), page vue sans connexion (bouton de connexion supposé).

import fs from 'node:fs'
import { Echec, nombre, note } from '../noyau.mjs'
import { PAUSE_COURTE_MS, TEST, cliquer, defilerVers, dormir, pause, survoler } from '../humain.mjs'
import { ROLES_ALERTE, nomDe, plusProche, premier, reglerPanneau, videosDe, videosInfosDe } from './commun.mjs'

const SITE = 'Dreamina'
// Bouton de connexion (page non connectée : NON relevée, textes supposés).
const CONNEXION = /^\s*(se connecter|connexion|s.inscrire|sign in|log in|continuer avec google|continue with google)\s*$/i
// 30/10 : l'onglet s'appelle aussi « Vidéo IA » (nouvelle page d'accueil)
const MODE_VIDEO = /^\s*(vidéo|video)(\s+ia)?\s*$/i
// 30/10 : bannière « Accept Dreamina's Cookies… » : REFUSÉE (« Decline all »), le choix le plus discret
const REFUSER_COOKIES = /^\s*(decline all|tout refuser|refuser tout)\s*$/i
const CHAMP = /^\s*(invite|prompt)\s*$/i
const ENVOI = /^\s*(générer|generate)\s*$/i
const LISTE_MODELES = /^\s*(modèle|model)\s*$/i
const TOUS_MODELES = /dreamina\s+seedance|happyhorse|\bwan\s*\d/i
// Mode des références (second combobox de la barre) : un modèle n'est permis que dans certains modes
const REFERENCES = {
  tout: { libelle: 'Références de tout type', re: /^\s*références de tout type\s*$/i, affiche: /références de tout type/i },
  images: { libelle: 'Première et dernière image', re: /^\s*première et dernière image\s*$/i, affiche: /première et dernière image/i }
}
const MODES_REFERENCES = /références de tout type|première et dernière image|images multiples|édition intelligente|vidéo longue/i
const AVANT = /avant de continuer|before (you )?continu/i
const CONFIRMER = /^\s*(confirmer|confirm)\s*$/i
const FILIGRANE = /filigrane|watermark/i
const FERMER = /^\s*(fermer|close)\s*$/i
const TELECHARGER = /télécharger|download/i
const FORMAT_RES = /\d{1,2}\s*:\s*\d{1,2}[\s·]*(\d{3,4}\s*p|[248]\s*k)\b/i
const DUREE = /^\s*\d{1,2}\s*s\s*$/i
const POURCENT = /^\s*\d{1,3}\s*%\s*$/

// Modèles du menu (relevé B5, captures 02 et relevé des prix du 29/10), par id stable (ids sans
// point : ID de noyau.lireCatalogue et ia_web.ALIAS). Prix CATALOGUE (le nombre barré s'il y a
// une remise) relevés par durée, à la résolution du modèle (`definition` : 720p, brouillon 480p
// seul, 1.0 en 1080p seul) ; `references` : mode des références à régler AVANT le modèle.
// Mêmes chiffres que config/ia-web.json (le prix lu sur la page doit être exactement celui-ci).
export const MODELES = {
  'seedance-2-mini': { libelle: 'Dreamina Seedance 2.0 Mini', re: /dreamina\s+seedance\s+2\.0\s+mini/i, definition: '720p', references: 'tout', durees: [5, 10, 15], cout: { 5: 55, 10: 110, 15: 165 } },
  'seedance-2-rapide': { libelle: 'Dreamina Seedance 2.0 rapide', re: /dreamina\s+seedance\s+2\.0\s+(rapide|fast)/i, definition: '720p', references: 'tout', durees: [5, 10, 15], cout: { 5: 60, 10: 120, 15: 180 } },
  'seedance-2': { libelle: 'Dreamina Seedance 2.0', re: /dreamina\s+seedance\s+2\.0(?!\s*(mini|rapide|fast))/i, definition: '720p', references: 'tout', durees: [5, 10, 15], cout: { 5: 75, 10: 150, 15: 225 } },
  'seedance-25-brouillon': { libelle: 'Brouillon Dreamina Seedance 2.5', re: /(brouillon|draft)\s+dreamina\s+seedance\s+2\.5|dreamina\s+seedance\s+2\.5\s+(draft|brouillon)/i, definition: '480p', references: 'tout', durees: [5], cout: { 5: 75 } },
  'seedance-25': { libelle: 'Dreamina Seedance 2.5', re: /(?<!(brouillon|draft)\s*)dreamina\s+seedance\s+2\.5(?!\s*(draft|brouillon))/i, definition: '720p', references: 'tout', durees: [5, 10, 15], cout: { 5: 160, 10: 320, 15: 480 } },
  'seedance-15-pro': { libelle: 'Dreamina Seedance 1.5 Pro', re: /dreamina\s+seedance\s+1\.5\s+pro/i, definition: '720p', references: 'images', durees: [5, 10], cout: { 5: 90, 10: 180 } },
  'seedance-1-rapide': { libelle: 'Dreamina Seedance 1.0 rapide', re: /dreamina\s+seedance\s+1\.0\s+(rapide|fast)/i, definition: '720p', references: 'images', durees: [5, 10], cout: { 5: 25, 10: 50 } },
  'seedance-1': { libelle: 'Dreamina Seedance 1.0', re: /dreamina\s+seedance\s+1\.0(?!\s*(rapide|fast))/i, definition: '1080p', references: 'images', durees: [5, 10], cout: { 5: 125, 10: 250 } },
  'happyhorse-11': { libelle: 'HappyHorse 1.1', re: /happyhorse\s*1\.1/i, definition: '720p', references: 'tout', durees: [5, 10, 15], cout: { 5: 135, 10: 270, 15: 405 } },
  'wan-3': { libelle: 'Wan 3.0', re: /\bwan\s*3\.0/i, definition: '720p', references: 'tout', durees: [5, 10, 15], cout: { 5: 80, 10: 160, 15: 240 } }
}
export const FORMATS = ['16:9', '21:9', '4:3', '1:1', '3:4', '9:16']

/** Id du modèle dont le motif correspond au texte (un seul), '' sinon. */
export function modeleLu (texte) {
  const vus = Object.entries(MODELES).filter(([, m]) => m.re.test(String(texte || '')))
  return vus.length === 1 ? vus[0][0] : ''
}

const echapper = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
/** Motif d'une valeur dans un texte (« 16:9 », « 720p ») : entourée de non-chiffres. */
const valeurDans = (v) => new RegExp(`(^|[^\\d])${echapper(v).replace(':', '\\s*:\\s*')}(?![\\d])`, 'i')
/** Motif d'une option du panneau : la valeur seule (icône sans texte autour). */
const optionSeule = (v) => new RegExp(`^\\s*${echapper(v).replace(':', '\\s*:\\s*')}\\s*$`, 'i')

// ----------------------------------------------------------------- textes de la page

/**
 * Classe un texte de la page : {code, message, reprise, lent} ou null. AUCUN message d'erreur de
 * Dreamina n'est relevé (crédits épuisés, file chargée, contenu refusé) : rien n'est reconnu avant
 * l'envoi (le solde lu contre le coût fait le contrôle des crédits), et après l'envoi, un message
 * dans un rôle d'alerte (`alertes`, écho du prompt retiré) ou une génération finie sans vidéo
 * (`finSansVideo`) = échec `page`, cru seulement s'il reste stable (`lent`). TODO à relever : les
 * vrais textes, pour les classer (crédits, quota, refus) comme ceux de Flow.
 */
export function classerPage (texte, { apres = false, alertes = '', finSansVideo = false } = {}) {
  if (!apres) return null
  const l = String(alertes || '').split('\n').map((x) => x.replace(/\s+/g, ' ').trim()).find(Boolean)
  if (l) return { code: 'page', message: `message de Dreamina sans vidéo (à relever) : « ${l.slice(0, 300)} »`, reprise: '', lent: true }
  if (finSansVideo) {
    return { code: 'page', message: 'génération terminée sans vidéo (progression disparue) : contenu refusé ou page changée, à relever', reprise: '', lent: true }
  }
  return null
}

// ----------------------------------------------------------------- génération suivie

// Fiches de génération suivies, par page : celles vues EN COURS (badge « N% ») avec le prompt du
// job. Seule une vidéo dans l'une d'elles est un résultat : la page de génération montre aussi le
// fil d'inspiration (vidéos chargées au fil du défilement) et les générations d'avant, peut-être
// du même prompt. Objet tenu hors de window (poignée Playwright), rien de visible pour la page.
const suivis = new WeakMap()

async function poigneeSuivi (page) {
  const h = suivis.get(page)
  if (h && await h.evaluate(() => true).catch(() => false)) return h
  const neuve = await page.evaluateHandle(() => ({ fiches: new Set(), perdues: false })).catch(() => null)
  if (neuve) suivis.set(page, neuve)
  return neuve
}

/**
 * Lit la page : fiches de génération du prompt (la plus petite boîte qui contient le début du
 * prompt, sans éditeur, de taille bornée), celles en cours (texte « N% », ou en secours un badge
 * `progress-badge`), et chaque balise vidéo avec `carte` vrai si elle est dans une fiche suivie
 * qui n'est plus en cours. Une fiche suivie remplacée par la page (rendu React) : la DERNIÈRE fiche
 * du prompt est reprise (TODO à vérifier : les nouvelles générations arrivent en bas de la liste).
 */
async function lireFiches (page, job) {
  const h = await poigneeSuivi(page)
  const base = await videosInfosDe(page)
  if (!h) return { videos: base.map((v) => ({ ...v, carte: false })), enCours: false, fini: false }
  const lu = await h.evaluate((s, { prompt, pourcent }) => {
    const net = (x) => String(x || '').replace(/\s+/g, ' ').trim()
    const debut = net(prompt).slice(0, 40)
    const re = new RegExp(pourcent)
    const fiche = (el) => {
      for (let e = el; e && e !== document.body; e = e.parentElement) {
        const t = net(e.innerText)
        if (!t.includes(debut)) continue
        if (e.querySelector('[contenteditable="true"], textarea') || t.length > debut.length + 1500) return null
        return e
      }
      return null
    }
    const enCours = (f) => [...f.querySelectorAll('*')].some((e) => !e.children.length && re.test(e.textContent || '')) ||
      !!f.querySelector('[class*="progress-badge"]')
    // Fiches en cours : depuis chaque « N% » (feuille) ou badge de secours
    const marques = [...document.querySelectorAll('body *')].filter((e) => (!e.children.length && re.test(e.textContent || '')) || /(^|\s)progress-badge/.test(e.className?.baseVal ?? e.className ?? ''))
    for (const m of marques) {
      const f = fiche(m)
      if (f) s.fiches.add(f)
    }
    for (const f of [...s.fiches]) if (!f.isConnected) { s.fiches.delete(f); s.perdues = true }
    if (s.perdues && !s.fiches.size) {
      const toutes = [...document.querySelectorAll('video')].map(fiche).filter(Boolean)
      if (toutes.length) s.fiches.add(toutes[toutes.length - 1])
    }
    const fiches = [...s.fiches]
    const cours = fiches.filter(enCours)
    const cartes = [...document.querySelectorAll('video')].map((v) => {
      const f = fiches.find((x) => x.contains(v))
      return !!f && !cours.includes(f)
    })
    return { cartes, enCours: cours.length > 0, fini: fiches.length > 0 && !cours.length }
  }, { prompt: job.prompt, pourcent: POURCENT.source }).catch(() => null)
  if (!lu) return { videos: base.map((v) => ({ ...v, carte: false })), enCours: false, fini: false }
  return { videos: base.map((v, i) => ({ ...v, carte: lu.cartes[i] === true })), enCours: lu.enCours, fini: lu.fini }
}

// ----------------------------------------------------------------- téléchargement

/** Bouton ↓ de la vidéo : par son nom (« Télécharger », TODO : à relever), sinon, en secours, le
 * premier élément cliquable du groupe d'icônes en haut à droite (`controls-tr`, relevé : ↓ puis
 * « … » puis ☆). */
async function boutonTelecharger (page, video) {
  const nomme = await plusProche(page.getByRole('button', { name: TELECHARGER }).or(page.getByRole('link', { name: TELECHARGER })), video)
  if (nomme) return nomme
  const groupe = await plusProche(page.locator('[class*="controls-tr"]'), video)
  return groupe ? premier(groupe.locator('button, [role="button"], a[href]')) : null
}

/** Fenêtre « Supprime les filigranes IA » : fermée par sa croix, rien d'autre touché
 * (ni l'interrupteur, ni « Enregistrer la préférence », ni « S'abonner »). */
async function fermerFiligrane (page) {
  const fin = Date.now() + (TEST ? 1500 : 6000)
  while (Date.now() < fin) {
    const croix = await premier(page.getByRole('dialog', { name: FILIGRANE }).getByRole('button', { name: FERMER }))
    if (croix) {
      await pause(PAUSE_COURTE_MS)
      await cliquer(page, croix, { timeout: 5000 })
      return true
    }
    await dormir(TEST ? 50 : 400)
  }
  return false
}

/** Secours : la vidéo reprise depuis la source de sa balise (adresse http(s) signée du CDN),
 * avec les cookies du contexte ; rend un objet {saveAs} pour noyau.enregistrer. */
async function depuisSource (page, src) {
  if (!/^https?:\/\//i.test(src || '')) throw new Echec('page', 'téléchargement impossible : ni bouton ↓ qui donne un fichier, ni source http de la vidéo')
  const r = await page.context().request.get(src, { timeout: TEST ? 10000 : 120000, maxRedirects: 3 })
    .catch((e) => { throw new Echec('page', `téléchargement impossible (source) : ${String(e?.message || e).split('\n')[0].slice(0, 150)}`) })
  if (!r.ok()) throw new Echec('page', `téléchargement impossible : source de la vidéo en ${r.status()}`)
  const corps = await r.body()
  return { saveAs: async (chemin) => fs.writeFileSync(chemin, corps, { mode: 0o600 }) }
}

/** 30/10 (1er essai réel) : la page avait défilé, la barre de création passait sous l'en-tête FIXE (solde,
 * « Acheter des crédits ») qui prenait les clics des menus : retour en haut avant chaque réglage. */
async function enHaut (page) {
  await page.evaluate(() => window.scrollTo(0, 0)).catch(() => {})
  await pause(PAUSE_COURTE_MS)
}

// ----------------------------------------------------------------- le site

export const DREAMINA = {
  nom: 'dreamina',
  libelle: 'Dreamina',
  type: 'video',
  url: 'https://dreamina.capcut.com/ai-tool/home?type=video', // repli : le catalogue prime
  hotesConnexion: /(^|\.)accounts\.google\.com$/i,
  modeles: MODELES,
  formats: FORMATS,
  modeleDefaut: 'seedance-2-mini',
  dureeDefaut: 5,
  libelleCout: 'prix affiché près du bouton « Générer »',
  classer: classerPage,
  rolesAlerte: ROLES_ALERTE,

  /** Champ du prompt (l'éditeur caché qui sert de gabarit, aria-hidden, n'est pas un textbox). */
  prompt: (page) => page.getByRole('textbox', { name: CHAMP }).filter({ visible: true }).first(),
  champ: (page) => DREAMINA.prompt(page),
  connexion: (page) => page.getByRole('button', { name: CONNEXION }).or(page.getByRole('link', { name: CONNEXION })),
  envoi: (page) => page.getByRole('button', { name: ENVOI }).filter({ visible: true }).last(),
  /** Liste des modèles (combobox « Modèle »), sinon le déclencheur qui en affiche un. */
  listeModeles: (page) => page.getByRole('combobox', { name: LISTE_MODELES })
    .or(page.getByRole('combobox', { name: TOUS_MODELES })).or(page.getByRole('button', { name: TOUS_MODELES }).and(page.locator('[aria-haspopup]'))),
  formatResolution: (page) => page.getByRole('button', { name: FORMAT_RES }),
  /** Mode des références : le combobox de la barre qui en affiche un (il n'a pas d'aria-label). */
  modeReferences: (page) => page.getByRole('combobox').filter({ hasText: MODES_REFERENCES }),
  boutonDuree: (page) => page.getByRole('button', { name: DUREE }),

  /** Onglet « Vidéo » de la barre (bascule aria-pressed ; jamais le lien « Vidéo » du menu). */
  async preparer (page) {
    // 30/10 (1er essai réel) : la liste des 10 modèles descend jusqu'à ~1 100 px ; dans la vue de 860 px,
    // atteindre « Seedance 1.0 rapide » faisait défiler la page, ce qui REFERMAIT la liste. Vue plus haute.
    const vue = page.viewportSize()
    if (vue && vue.height < 1250) await page.setViewportSize({ width: vue.width, height: 1250 }).catch(() => {})
    const refus = await premier(page.getByRole('button', { name: REFUSER_COOKIES }))
    if (refus) { await cliquer(page, refus, { timeout: 5000 }); await pause(PAUSE_COURTE_MS) }
    const onglets = page.getByRole('button', { name: MODE_VIDEO }).and(page.locator('[aria-pressed]'))
    const actif = onglets.and(page.locator('[aria-pressed="true"]')).filter({ visible: true })
    if (await nombre(actif)) return
    // 30/10 (nouvelle page d'accueil) : onglets « Générer » / « Vidéo IA » sans état pressé ; la barre déjà
    // en mode vidéo (« Décris ta vidéo… ») suffit
    // repère sûr : la liste « Modèle » affiche un modèle VIDÉO (le texte du champ change selon le mode des références)
    const barreVideo = async () => nombre(page.getByRole('combobox', { name: LISTE_MODELES }).filter({ hasText: TOUS_MODELES }).filter({ visible: true }))
    if (!await nombre(onglets) && await barreVideo()) return
    const b = await premier(onglets) || await premier(page.getByRole('button', { name: MODE_VIDEO }))
    if (!b) throw new Echec('page', 'onglet « Vidéo » introuvable sur Dreamina : la page a peut-être changé')
    await cliquer(page, b, { timeout: 10000 })
    await pause(PAUSE_COURTE_MS)
    if (!await nombre(actif) && !await barreVideo()) throw new Echec('page', 'onglet « Vidéo » pas pris sur Dreamina')
  },

  /** Modèle demandé, APRÈS le mode vidéo ; rend l'id LU ensuite sur la liste ('' : pas lisible). */
  async choisirModele (page, modele, outils = {}) {
    const fiche = MODELES[modele.id]
    if (!fiche) throw new Echec('page', `modèle ${modele.id} inconnu du pilote Dreamina`)
    if (typeof outils.preparer === 'function') await outils.preparer(page)
    // Mode des références d'abord : en « Références de tout type », les Seedance 1.x sont grisés
    const ref = REFERENCES[fiche.references || 'tout']
    await enHaut(page)
    await reglerPanneau(page, { nom: ref.libelle, site: SITE, declencheur: DREAMINA.modeReferences, affiche: (t) => ref.affiche.test(t), options: [ref.re] })
    await enHaut(page)
    await reglerPanneau(page, { nom: fiche.libelle, site: SITE, declencheur: DREAMINA.listeModeles, affiche: (t) => fiche.re.test(t), options: [fiche.re] })
    const liste = await premier(DREAMINA.listeModeles(page))
    return liste ? modeleLu(await nomDe(liste)) : ''
  },

  /** Format et résolution (une bulle), puis durée (champ numérique de « Durée totale »). */
  async regler (page, job) {
    await enHaut(page)
    await reglerPanneau(page, {
      nom: `${job.format} · ${job.resolution}`,
      site: SITE,
      declencheur: DREAMINA.formatResolution,
      affiche: (t) => valeurDans(job.format).test(t) && valeurDans(job.resolution).test(t),
      options: [optionSeule(job.format), optionSeule(job.resolution)]
    })
    await reglerPanneau(page, {
      nom: `${job.duree} s`,
      site: SITE,
      declencheur: DREAMINA.boutonDuree,
      affiche: (t) => new RegExp(`^\\s*${job.duree}\\s*s\\s*$`, 'i').test(t),
      valeur: job.duree
    })
  },

  /**
   * Prix affiché près du bouton d'envoi : feuilles numériques des boîtes qui l'entourent (4 niveaux
   * au plus), hors du bouton ; le nombre BARRÉ (text-decoration) est le prix catalogue, l'autre le
   * coût débité (relevé : « 0 » et « 55 » barré). Un seul nombre : sans remise. Rend {debite,
   * liste}, null si rien n'est affiché, NaN si plusieurs prix différents.
   */
  async coutAnnonce (page) {
    const lu = await page.evaluate((envoi) => {
      const re = new RegExp(envoi, 'i')
      const vu = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
      const bouton = [...document.querySelectorAll('button')].filter((b) => re.test(b.getAttribute('aria-label') || b.innerText || '') && vu(b)).pop()
      if (!bouton) return null
      const barre = (el, haut) => {
        for (let e = el; e && e !== haut; e = e.parentElement) if (getComputedStyle(e).textDecorationLine.includes('line-through')) return true
        return false
      }
      for (let c = bouton.parentElement, n = 0; c && n < 4; c = c.parentElement, n++) {
        const nombres = [...c.querySelectorAll('*')].filter((e) => !e.children.length && !bouton.contains(e) && vu(e) && /^\s*\d{1,5}\s*$/.test(e.textContent || ''))
        if (!nombres.length) continue
        const debites = [...new Set(nombres.filter((e) => !barre(e, c)).map((e) => Number(e.textContent)))]
        const listes = [...new Set(nombres.filter((e) => barre(e, c)).map((e) => Number(e.textContent)))]
        if (debites.length !== 1 || listes.length > 1) return 'plusieurs'
        return { debite: debites[0], liste: listes.length ? listes[0] : debites[0] }
      }
      return null
    }, ENVOI.source).catch(() => null)
    return lu === 'plusieurs' ? Number.NaN : lu
  },

  /** Solde affiché en haut de page (« 216 ») : pas de libellé ni de rôle relevés, donc le préfixe
   * de classe `credit-amount-text` (secours) ; null si illisible. */
  credits: (page) => page.evaluate(() => {
    for (const el of document.querySelectorAll('[class*="credit-amount-text"]')) {
      if (!el.getClientRects().length) continue
      const m = /^\s*(\d[\d\s  ]{0,8})\s*$/.exec(el.innerText || '')
      if (m) return Number(m[1].replace(/\D/g, ''))
    }
    return null
  }).catch(() => null),

  /** Pas d'état « crédits insuffisants » relevé sur le bouton : seul « inactif » compte. */
  envoiBloque: (loc) => loc.evaluate((el) => ({ info: false, inactif: el.disabled || el.getAttribute('aria-disabled') === 'true' }))
    .catch(() => ({ info: false, inactif: false })),

  /** « Confirmer » de la fenêtre « Avant de continuer » (attestation de droits, à chaque vidéo). */
  confirmerEnvoi: (page) => premier(page.getByRole('dialog', { name: AVANT }).getByRole('button', { name: CONFIRMER })),
  /** Croix « Fermer » de la fenêtre des filigranes (test/dreamina.test.mjs la vérifie). */
  croixFiligrane: (page) => page.getByRole('dialog', { name: FILIGRANE }).getByRole('button', { name: FERMER }),

  videos: videosDe,
  /** Vidéos, `carte` vrai pour celles d'une génération suivie et finie (lireFiches). */
  videosInfos: async (page, job) => (await lireFiches(page, job)).videos,
  suiviGeneration: async (page, job) => {
    const { enCours, fini } = await lireFiches(page, job)
    return { enCours, fini }
  },

  /**
   * Télécharge la vidéo `index` : amenée dans la vue, survolée, clic sur ↓, fenêtre des
   * filigranes fermée par sa croix ; le fichier part avec ce clic (relevé). Pas de bouton, ou pas
   * de fichier : en secours, la source de la balise vidéo (lien signé, cookies du contexte).
   */
  async telecharger (page, index) {
    const video = page.locator('video').nth(index)
    await defilerVers(page, video)
    await survoler(page, video)
    await pause(PAUSE_COURTE_MS)
    const src = await video.evaluate((v) => v.currentSrc || v.src || '').catch(() => '')
    const bouton = await boutonTelecharger(page, video)
    if (bouton) {
      const attente = page.waitForEvent('download', { timeout: TEST ? 3000 : 60000 })
      attente.catch(() => {})
      await cliquer(page, bouton, { timeout: 10000 })
      await fermerFiligrane(page)
      const dl = await attente.catch(() => null)
      if (dl) return dl
    }
    note(`Dreamina : ${bouton ? 'clic sur ↓ sans fichier' : 'bouton ↓ introuvable'}, vidéo reprise depuis sa source`)
    return depuisSource(page, src)
  }
}
