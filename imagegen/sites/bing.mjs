// Bing Image Creator SANS compte (plan P40 lot A3 ; décision du 21/10 : 4 variantes
// par demande). Profil ANONYME persistant (alias réservé « anonyme », ia_web.ALIAS_ANONYME) :
// aucun compte Microsoft, rien à reconnecter. Si Bing exige un compte (bouton « Join & Create »,
// page login.live.com), c'est un échec `session` : on ne se connecte jamais à la place de l'utilisateur.
//
// ⚠️ Aucun sélecteur n'a été essayé sur le vrai site (consigne : aucun essai réel avant le pilote
// avec l'utilisateur) ; quota sans compte NON établi (plan P40). Sélecteurs par RÔLE et libellé
// accessible (FR + EN), plus deux repères stables : le nom du champ de formulaire (`q`) et le
// chemin des images générées par Bing (`/th/id/OIG…`). Page changée = échec `page` avec capture.
//
// Parcours propre (hooks lus par worker.mjs) : `preparer` (bandeau de cookies : REFUSER, jamais
// accepter à la place de l'utilisateur), `envoyer` (clic sur « Create »), `attendre` (grille de 1 à 4
// images), `recuperer` (chaque image en taille réelle, par le contexte du navigateur : mêmes
// cookies, aucune autre adresse que Bing ou la page elle-même). Pas de pièce jointe (pas de
// `joindre`), pas de choix du modèle (pas de `choisirModele` : seul `defaut`).

import fs from 'node:fs'
import { Echec, defi, nombre, note, siFerme, visible } from '../noyau.mjs'
import { PAUSE_COURTE_MS, SONDE_MS, TEST, cliquer, dormir, pause } from '../humain.mjs'
import { classer, premier } from './commun.mjs'

const CHAMP = /describe what you.d like to create|décrivez ce que vous (souhaitez|voulez) créer|describe the image|décri(vez|s) l.image|create an image|créer une image/i
const CREER = /^\s*(create|créer|generate|générer)\s*$/i
// Bouton ou lien qui demande un compte Microsoft pour créer (le « Sign in » de l'en-tête de Bing,
// toujours là pour un visiteur, n'en est PAS un)
const CONNEXION = /join (&|and) create|rejoindre et créer|sign in to create|connectez-vous pour créer|se connecter pour créer/i
const REFUSER_COOKIES = /^\s*(reject|reject all|refuser|tout refuser|decline|continuer sans accepter)\s*$/i
// Message d'erreur de la page de résultats (contenu bloqué, avertissement, limite)
const ERREUR = '#girer, [role="alert"]'
// Images générées : chemin des vignettes de Bing (th.bing.com/th/id/OIG…)
const IMAGE_GENEREE = /\/th\/id\/OIG/i
const HOTES_IMAGES = /(^|\.)bing\.(com|net)$/i
export const VARIANTES_MAX = 4
// Grille complète (4) ou nombre d'images inchangé depuis ce temps : fini
const STABLE_MS = TEST ? 400 : 6000
const IMAGE_OCTETS_MAX = 40 * 1024 * 1024

/** Adresses des images générées visibles, dans l'ordre de la page. */
const generees = (page) => page.evaluate((motif) => {
  const re = new RegExp(motif, 'i')
  return [...document.images].filter((i) => {
    const r = i.getBoundingClientRect()
    return re.test(i.currentSrc || i.src) && r.width >= 32 && r.height >= 32
  }).map((i) => i.currentSrc || i.src)
}, IMAGE_GENEREE.source).catch(() => [])

/** Adresse de l'image en taille réelle (vignette sans ses paramètres de taille), ou '' si elle
 * n'est ni chez Bing ni sur l'hôte de la page. */
export function tailleReelle (src, hotePage = '') {
  let u
  try { u = new URL(src) } catch { return '' }
  if (!/^https?:$/.test(u.protocol) || !IMAGE_GENEREE.test(u.pathname)) return ''
  if (!HOTES_IMAGES.test(u.hostname) && u.hostname !== hotePage) return ''
  if (u.protocol !== 'https:' && u.hostname !== hotePage) return ''
  u.search = ''
  u.hash = ''
  return u.toString()
}

/** Adresse finale (après redirection) chez Bing en https, ou sur l'hôte de la page. */
export function hotePermis (adresse, hotePage = '') {
  let u
  try { u = new URL(adresse) } catch { return false }
  if (u.hostname === hotePage && /^https?:$/.test(u.protocol)) return true
  return u.protocol === 'https:' && HOTES_IMAGES.test(u.hostname)
}

export const REDIRECTIONS_MAX = 3

/** GET de `url` par le contexte du navigateur, redirections suivies À LA MAIN (3 au plus, th.bing.com
 * → tse*.mm.bing.net) : l'hôte de chaque saut est contrôlé AVANT la requête, donc un hôte non permis
 * n'est jamais contacté et ne reçoit aucun cookie du profil (relevé du testeur A3, fiche 2 : avec
 * le suivi automatique, l'hôte étranger recevait la requête avant le refus). Rend {rep} (réponse
 * 2xx) ou {raison} (hôte refusé, trop de sauts, statut HTTP, erreur réseau) : la raison est notée
 * par `recuperer` (relecture A3, fiche 4 : plus d'image sautée en silence). */
export async function lireSansFuite (page, url, hotePage = '') {
  let adresse = url
  for (let saut = 0; saut <= REDIRECTIONS_MAX; saut++) {
    if (!hotePermis(adresse, hotePage)) {
      const hote = (() => { try { return new URL(adresse).host } catch { return '?' } })()
      return { raison: saut ? `redirection vers un hôte non permis (${hote})` : `hôte non permis (${hote})` }
    }
    let rep
    try {
      rep = await page.request.get(adresse, { timeout: 30000, maxRedirects: 0 })
    } catch (e) {
      return { raison: `erreur réseau : ${String(e?.message || e).split('\n')[0].slice(0, 120)}` }
    }
    const statut = rep.status()
    if (statut >= 300 && statut < 400) {
      const vers = rep.headers().location
      await rep.dispose().catch(() => {})
      if (!vers) return { raison: `redirection HTTP ${statut} sans adresse` }
      try { adresse = new URL(vers, adresse).toString() } catch { return { raison: 'redirection vers une adresse illisible' } }
      continue
    }
    if (!rep.ok()) { await rep.dispose().catch(() => {}); return { raison: `HTTP ${statut}` } }
    return { rep }
  }
  return { raison: `plus de ${REDIRECTIONS_MAX} redirections` }
}

export const BING = {
  nom: 'bing',
  libelle: 'Bing Image Creator',
  type: 'image',
  url: 'https://www.bing.com/images/create', // repli : l'adresse du catalogue config/ia-web.json prime
  sansCompte: true,
  hotesConnexion: /(^|\.)(login\.live\.com|login\.microsoftonline\.com|account\.microsoft\.com)$/i,
  prompt: (texte) => texte, // générateur d'images : la demande telle quelle
  champ: (page) => page.getByRole('textbox', { name: CHAMP })
    .or(page.locator('textarea[name="q"]')).filter({ visible: true }).first(),
  connexion: (page) => page.getByRole('link', { name: CONNEXION }).or(page.getByRole('button', { name: CONNEXION })),

  /** Bandeau de cookies : REFUSER s'il est là (jamais accepter à la place de l'utilisateur). */
  async preparer (page) {
    const b = await premier(page.getByRole('button', { name: REFUSER_COOKIES }).or(page.getByRole('link', { name: REFUSER_COOKIES })))
    if (b) { await cliquer(page, b, { timeout: 5000 }); await pause(PAUSE_COURTE_MS) }
  },

  /** Images déjà là avant l'envoi (créations récentes du profil) : jamais prises pour le résultat. */
  avantEnvoi: (page) => generees(page),

  /** Envoi par le bouton « Create » ; « Join & Create » (compte exigé) : session. */
  async envoyer (page) {
    if (await visible(BING.connexion(page).first())) throw new Echec('session', 'Bing demande un compte Microsoft pour créer : impossible sans compte')
    const b = await premier(page.getByRole('button', { name: CREER }).or(page.getByRole('link', { name: CREER })))
    if (!b) throw new Echec('page', 'bouton « Create » introuvable : la page a peut-être changé')
    await cliquer(page, b, { timeout: 10000 })
  },

  /**
   * Attend la grille : 4 images NOUVELLES, ou un nombre inchangé depuis STABLE_MS. Message
   * d'erreur affiché (contenu bloqué, limite) : `refus` ou `quota` (moteurs.classer). Page de
   * connexion : `session`. Rend les adresses des images (4 au plus), dans l'ordre de la page.
   */
  async attendre (page, job, avant = []) {
    const hotePage = (() => { try { return new URL(page.url()).hostname } catch { return '' } })()
    // Une image = son adresse en TAILLE RÉELLE (sans ?w=…) : la même vue en vignette et en aperçu ne
    // compte qu'une fois (relecture A3, fiche 4 : sinon 4 atteint trop tôt avec un doublon).
    const cle = (s) => tailleReelle(s, hotePage) || s
    const deja = new Set(avant.map(cle))
    const fin = Date.now() + job.delaiMs
    let vues = []
    let depuis = 0
    while (Date.now() < fin) {
      siFerme(page)
      await defi(page, job)
      const hote = (() => { try { return new URL(page.url()).hostname } catch { return '' } })()
      if (BING.hotesConnexion.test(hote) || await visible(BING.connexion(page).first())) {
        throw new Echec('session', 'Bing demande un compte Microsoft pour créer : impossible sans compte')
      }
      const parCle = new Map()
      for (const s of await generees(page)) {
        const k = cle(s)
        if (!deja.has(k) && !parCle.has(k)) parCle.set(k, s)
      }
      const nouvelles = [...parCle.values()]
      if (nouvelles.length >= VARIANTES_MAX) return nouvelles.slice(0, VARIANTES_MAX)
      if (nouvelles.length && nouvelles.length === vues.length) {
        if (Date.now() - depuis >= STABLE_MS) return nouvelles
      } else {
        vues = nouvelles
        depuis = Date.now()
      }
      if (!nouvelles.length) {
        const err = page.locator(ERREUR).filter({ visible: true })
        if (await nombre(err)) {
          const texte = (await err.first().innerText({ timeout: 1000 }).catch(() => '')).trim()
          if (texte) {
            const lu = classer(texte)
            if (lu.code === 'quota') throw new Echec('quota', lu.message, { reprise: lu.reprise })
            throw new Echec('refus', lu.message || 'Bing n\'a rendu aucune image')
          }
        }
      }
      await dormir(SONDE_MS)
    }
    throw new Echec('delai', `pas d'image au bout de ${Math.round(job.delaiMs / 1000)} s`)
  },

  /** Chaque image en taille réelle, lue par le contexte du navigateur (mêmes cookies). Rend des
   * « téléchargements » ({saveAs}) pour noyau.enregistrer, qui vérifie les octets. */
  async recuperer (page, sources) {
    const hotePage = (() => { try { return new URL(page.url()).hostname } catch { return '' } })()
    const sorties = []
    const raisons = []
    const vues = new Set()
    const sauter = (n, raison) => {
      raisons.push(raison)
      note(`bing : image ${n} sautée : ${raison}`)
    }
    let n = 0
    for (const src of sources) {
      if (n >= VARIANTES_MAX) break
      const url = tailleReelle(src, hotePage)
      if (url && vues.has(url)) continue // même image en deux tailles : une seule fois
      n++
      if (!url) { sauter(n, 'adresse hors de Bing ou pas une image générée'); continue }
      vues.add(url)
      await pause(PAUSE_COURTE_MS)
      const lu = await lireSansFuite(page, url, hotePage)
      if (!lu.rep) { sauter(n, lu.raison); continue }
      const taille = Number(lu.rep.headers()['content-length'] || 0)
      if (taille > IMAGE_OCTETS_MAX) { await lu.rep.dispose().catch(() => {}); sauter(n, `trop lourde (${taille} octets annoncés)`); continue }
      const corps = await lu.rep.body().catch(() => null)
      if (!corps || !corps.length) { sauter(n, 'réponse vide ou illisible'); continue }
      if (corps.length > IMAGE_OCTETS_MAX) { sauter(n, `trop lourde (${corps.length} octets)`); continue }
      sorties.push({ saveAs: async (fichier) => { fs.writeFileSync(fichier, corps, { mode: 0o600 }) } })
    }
    if (!sorties.length) {
      const causes = [...new Set(raisons)].join(' ; ') || 'aucune image'
      throw new Echec('page', `images de Bing illisibles : ${causes}`.slice(0, 400))
    }
    return sorties
  }
}
