// Worker de génération d'images (plan P14, lot 1 ; P40 lots A2 et A3) : processus JETABLE lancé
// par le cœur (astrel/images_ia.py), UNE génération par lancement, puis sortie. Pilote le site
// dans un Chrome RÉEL sur le profil PERSISTANT d'un compte (runtime/imagegen/profils/<alias>/),
// jamais celui du navigateur « playwright » d'Astrel. Ne lit ni mots de passe ni cookies :
// il ouvre le site, constate s'il est connecté, tape, attend, télécharge.
//
// Contrat d'entrée et de sortie, interface d'un site : en tête de noyau.mjs (plan P40 étape 7).
// Ici, seulement ce qui est propre aux IMAGES :
//   - `type` : `image` seulement (la vidéo a son worker, video.mjs, plan P41) ;
//   - services : gemini, chatgpt, bing (moteurs.mjs, un module par site dans sites/) ;
//   - plafond 1 à 100 images, délai 5 à 300 s, prompt de 4 000 caractères au plus ;
//   - `fichiers` : pièces jointes (P40 A3), 6 au plus, filtre du cœur appliqué ICI aussi
//     (pieces.mjs : runtime/attachments ou ~/Projets, hors secrets, 5 Mo, PNG/JPEG/WebP),
//     vérifiées à l'entrée (refus sans ouvrir Chrome) puis RELUES juste avant le dépôt ; un site
//     sans `joindre` (Bing) les refuse : échec `fichiers` ;
//   - profil : un site SANS compte (`sansCompte`, Bing) n'accepte que le profil anonyme
//     (alias réservé `anonyme`, ia_web.ALIAS_ANONYME) ; les autres le refusent ;
//   - sortie `fini` : {fichier, nom, octets, type (extension : png|jpg|webp, pas le type du
//     job), fichiers: [{fichier, nom}] (principale en tête ; Bing : jusqu'à 4 variantes, noms
//     UNIQUES), modele_utilise, credits_restants?}.
//
// Ce que le parcours lit d'un site d'images, en plus de l'interface du noyau :
//   prompt(texte) → texte à taper ; joindre?(page, pieces) ; preparer?(page) (bandeau à fermer) ;
//   envoyer?(page) (défaut : Entrée) ; parcours « conversation » (Gemini, ChatGPT) :
//   telecharger(page), enCours(page), reponse(page), images(page) ; parcours propre (Bing) :
//   avantEnvoi?(page), attendre(page, job, avant) → sources, recuperer(page, sources) → [{saveAs}].
//
// Rythme et gestes humains (humain.mjs) : pauses, frappe variable, souris en courbe, clic hors
// du centre, défilement à la molette, jamais la nuit, 10 min entre deux générations d'un
// compte, un seul essai, aucune relance.

import path from 'node:path'
import { MOTEURS, classer } from './moteurs.mjs'
import {
  Echec, SIGNATURES_IMAGE, defi, emit, enregistrer, nombre, seance, siFerme, siPrincipal, verifierBase, visible
} from './noyau.mjs'
import { ATTENTE_REFUS_MS, PAUSE_COURTE_MS, SONDE_MS, cliquer, dormir, pause, survoler, taper } from './humain.mjs'
import { lirePieces } from './pieces.mjs'

export { compteur, verrouiller } from './noyau.mjs'

const OCTETS_MAX = 40 * 1024 * 1024
const BORNES = { promptMax: 4000, plafondDefaut: 20, plafondMax: 100, delaiDefautS: 120, delaiMinS: 5, delaiMaxS: 300 }
// Profil des services sans compte (Bing) : même alias réservé que astrel/ia_web.ALIAS_ANONYME
export const ALIAS_ANONYME = 'anonyme'
export const IMAGES_MAX = 4

export function verifierJob (job) {
  const j = verifierBase(job, { sites: MOTEURS, type: 'image', bornes: BORNES })
  const alias = path.basename(j.profil)
  if (j.site.sansCompte && alias !== ALIAS_ANONYME) {
    throw new Echec('entree', `${j.site.libelle} se pilote sans compte : profil « ${ALIAS_ANONYME} » attendu`)
  }
  if (!j.site.sansCompte && alias === ALIAS_ANONYME) {
    throw new Echec('entree', `profil « ${ALIAS_ANONYME} » réservé aux services sans compte`)
  }
  if (j.fichiers.length) {
    if (typeof j.site.joindre !== 'function') throw new Echec('fichiers', `${j.site.libelle} ne prend pas de pièce jointe`)
    lirePieces(j.fichiers) // refus dès l'entrée, sans ouvrir Chrome ; relues avant le dépôt
  }
  return j
}

// --------------------------------------------------------------- parcours « conversation »

async function attendreResultat (page, job, avant) {
  const moteur = job.site
  const fin = Date.now() + job.delaiMs
  let stableDepuis = 0
  let dernier = ''
  while (Date.now() < fin) {
    siFerme(page) // SIGTERM ou Chrome tué : sortie tout de suite, pas au délai
    await defi(page, job)
    if (await nombre(moteur.telecharger(page)) > avant) return
    const enCours = await visible(moteur.enCours(page))
    const texte = enCours ? '' : await moteur.reponse(page).innerText({ timeout: 1000 }).catch(() => '')
    const images = await nombre(moteur.images(page))
    if (texte.trim() && !images) {
      if (texte !== dernier) stableDepuis = Date.now()
      dernier = texte
      const lu = classer(texte)
      const stable = Date.now() - stableDepuis
      // Quota : dès que le texte ne bouge plus ; refus : après ~8 s sans image (une image
      // peut suivre un premier texte « voici ton image »).
      if (lu.code === 'quota' && stable >= 2 * SONDE_MS) throw new Echec('quota', lu.message, { reprise: lu.reprise })
      if (stable >= ATTENTE_REFUS_MS) throw new Echec('refus', lu.message || 'pas d\'image rendue')
    } else {
      dernier = ''
    }
    await dormir(SONDE_MS)
  }
  throw new Echec('delai', `pas d'image au bout de ${Math.round(job.delaiMs / 1000)} s`)
}

async function telechargement (page, job) {
  const moteur = job.site
  const bouton = moteur.telecharger(page).last()
  // Le bouton peut n'apparaître qu'au survol de l'image : la souris y va d'abord, en courbe.
  await survoler(page, moteur.images(page).last())
  await pause(PAUSE_COURTE_MS)
  const [dl] = await Promise.all([
    page.waitForEvent('download', { timeout: 60000 }),
    cliquer(page, bouton, { timeout: 10000 })
  ]).catch((e) => { throw new Echec('page', `téléchargement impossible : ${String(e?.message || e).split('\n')[0].slice(0, 200)}`) })
  return dl
}

const enregistrerImage = (dl, job) => enregistrer(dl, job, {
  signatures: SIGNATURES_IMAGE, octetsMax: OCTETS_MAX, refus: 'le fichier téléchargé n\'est pas une image', vide: 'image'
})

/** Enregistre les téléchargements dans l'ordre (noms uniques, noyau.enregistrer) ; un fichier
 * refusé (pas une image) est sauté si un autre passe, sinon sa raison remonte. */
async function enregistrerTous (dls, job) {
  const res = []
  let erreur = null
  for (const dl of dls.slice(0, IMAGES_MAX)) {
    try {
      res.push(await enregistrerImage(dl, job))
    } catch (e) {
      if (!(e instanceof Echec)) throw e
      erreur = erreur || e
    }
  }
  if (!res.length) throw erreur || new Echec('page', 'aucune image téléchargée')
  return res
}

async function parcoursImage ({ page, champ, job, envoye }) {
  const site = job.site
  if (typeof site.preparer === 'function') await site.preparer(page)
  emit({ t: 'etape', etape: 'saisie' })
  if (job.fichiers.length) {
    // Relues ici : le job a pu attendre en file, le fichier changer entre-temps
    await site.joindre(page, lirePieces(job.fichiers))
    await pause(PAUSE_COURTE_MS)
  }
  await cliquer(page, champ, { timeout: 10000 })
  await pause(PAUSE_COURTE_MS)
  await taper(page, site.prompt(job.prompt))
  await pause()
  const propre = typeof site.attendre === 'function'
  const avant = propre
    ? (typeof site.avantEnvoi === 'function' ? await site.avantEnvoi(page) : [])
    : await nombre(site.telecharger(page))
  if (typeof site.envoyer === 'function') await site.envoyer(page)
  else await page.keyboard.press('Enter')
  envoye()
  emit({ t: 'etape', etape: 'generation' })
  let dls
  if (propre) {
    const sources = await site.attendre(page, job, avant)
    await pause(PAUSE_COURTE_MS)
    emit({ t: 'etape', etape: 'telechargement' })
    dls = await site.recuperer(page, sources)
  } else {
    await attendreResultat(page, job, avant)
    await pause(PAUSE_COURTE_MS)
    emit({ t: 'etape', etape: 'telechargement' })
    dls = [await telechargement(page, job)]
  }
  const res = await enregistrerTous(dls, job)
  await pause(PAUSE_COURTE_MS)
  // `fichiers` : toutes les images rendues, la principale en tête (Bing : jusqu'à 4 variantes).
  return { ...res[0], fichiers: res.map((r) => ({ fichier: r.fichier, nom: r.nom })) }
}

export function generer (job) {
  return seance(job, parcoursImage, { outils: { modeles: job.modeles, menu: job.menuModeles } })
}

siPrincipal(import.meta.url, verifierJob, generer)
