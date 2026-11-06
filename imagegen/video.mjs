// Worker de génération de VIDÉOS (plan P41 lot B1, 28/10/2026) : processus JETABLE lancé par le
// cœur (astrel/videos_ia.py), UNE vidéo par lancement, puis sortie. Même socle que les images
// (noyau.mjs : contrat, verrou et compteur du compte, Chrome réel sur le profil persistant,
// défi anti-robot, session, enregistrement par les octets ; humain.mjs : rythme et gestes).
// Sites : Google Flow (sites/flow.mjs, lot B1) et Dreamina (sites/dreamina.mjs, lot B5, 29/10),
// choisis par `job.service`. Aucun essai sur les vrais sites avant le pilote avec l'utilisateur : tout
// est éprouvé sur test/fausse-flow.html et test/fausse-dreamina.html.
//
// Contrat : en tête de noyau.mjs, plus ce qui est propre à la VIDÉO :
//   entrée  `type` video ; `modele` id du catalogue (vide ou `defaut` = modèle par défaut du
//           site, rendu sous son id : Flow `lite`, Dreamina `seedance-2-mini`) ; `duree` en s
//           (Flow Veo : 8 seul ; Omni : 4, 6, 8, 10 ; défaut 8 ; Dreamina : 5, 10, 15) ; `format` parmi
//           ceux du site (16:9 par défaut ; Flow : 9:16 aussi) ; résolution 720p imposée, sauf
//           celle du modèle (`definition` de sa fiche : Dreamina brouillon 480p, Seedance 1.0 1080p) ;
//           `image_debut`, `image_fin` facultatives (Flow seul : mode Frames, cases Start et End), chemins
//           vérifiés comme les pièces jointes des images (astrel/videos_ia.image_depart) et
//           REVÉRIFIÉS au moment de les déposer ; `fichiers` (ancien nom, videos_ia.py au lot
//           B2) = [image_debut] ou [image_debut, image_fin], jamais avec les deux champs ;
//           `derniere_image` (booléen) : extraire aussi la dernière image réelle du clip, pour
//           enchaîner le clip suivant ; `delai_s` 5 à 3 600 s (défaut 900, attente longue,
//           sondes lentes) ; `affichage` choisi par le JOB (`masquee` par défaut, `visible` sur
//           demande : correction de consigne du 28/10, aucune fenêtre ne doit surgir).
//   sortie  {t:"fini", fichier (chaîne), nom, octets, type (mp4|webm), duree (demandée),
//           duree_reelle (lue dans le mp4, sinon null), format, resolution, cout_annonce,
//           images: {debut, fin} (noms), fichiers: [{fichier, nom}], derniere_image?,
//           modele_utilise (id du catalogue), credits_restants?}
//   échecs  ceux du noyau, plus `activite` (« unusual activity », rien débité : arrêt, le cœur
//           met le compte au repos 6 h), `credits` (crédits insuffisants, bouton d'envoi orange),
//           `pointe` (créneau de pointe, `reprise` « HH:MM » LOCALE si la page la donne,
//           fuseau écrit converti),
//           `quota`, `refus`, `fichiers` (image refusée).
//
// Règles d'envoi (plan P41, relecture B2 fiche 3) : UN SEUL clic d'envoi par essai, jamais de
// second envoi automatique ; AVANT ce clic : défi, activité inhabituelle, crédits, créneau de
// pointe, puis le COÛT ANNONCÉ (prix catalogue du site : Flow « Generating will use N credits »,
// Dreamina le prix barré près du bouton d'envoi) doit être exactement celui attendu pour le
// modèle, la durée et x1 en 720p (sinon un réglage a été mal lu : refus, rien n'est envoyé) ; le
// solde affiché doit couvrir le coût DÉBITÉ (remise comprise). `envoye` n'est émis qu'une fois,
// et seulement si le clic a atteint le bouton ou fait réagir la page (clicRecu) : un clic tombé à
// côté ne compte rien. Une confirmation demandée par le site (Flow « Generate », Dreamina « Avant
// de continuer » : décision de l'utilisateur, confirmée à CHAQUE vidéo) est la MÊME demande.
//
// ---------------------------------------------------------------- interface d'un site VIDÉO
// En plus de celle du noyau (nom, url, hotesConnexion, champ, connexion, choisirModele, credits) :
//   modeles {id: {libelle, re, durees: [s], cout: {s: crédits relevés}}}, formats [défaut d'abord],
//   modeleDefaut (id), dureeDefaut (s), libelleCout (texte des messages), rolesAlerte (sélecteur)
//   preparer(page)             une fois par page : mode vidéo (Flow : projet, Video, Frames)
//   regler(page, job)          format, résolution, durée, nombre de vidéos
//   deposerImage?(page, quelle, image)   absent : images de début et de fin refusées à l'entrée
//   prompt(page)               champ du prompt
//   classer(texte, {apres, prompt, alertes, maintenant, finSansVideo}) → {code, message, reprise,
//                              lent?} | null ; `lent` : cru seulement s'il reste REFUS_STABLE_MS
//   coutAnnonce(page, texte) → {debite, liste} | null (absent) | NaN (plusieurs)
//   envoi(page), envoiBloque(bouton) → {info, inactif}, confirmerEnvoi(page) → bouton | null
//   videos(page) → [src], videosInfos(page, job) → [{src, visible, duree, carte?}] (carte faux :
//                              vidéo hors de la génération suivie, jamais prise)
//   suiviGeneration?(page, job) → {enCours, fini} : génération visible en cours (aucun message
//                              cru alors) ou finie sans vidéo
//   telecharger(page, index, job) → téléchargement Playwright ou {saveAs(chemin)}

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  Echec, capture, captcha, defi, emit, enregistrer, lireCatalogue, MODELE_DEFAUT, seance, siFerme, siPrincipal, verifierBase
} from './noyau.mjs'
import { PAUSE_COURTE_MS, TEST, cliquer, dormir, hasard, pause, taper } from './humain.mjs'
import { FLOW } from './sites/flow.mjs'
import { DREAMINA } from './sites/dreamina.mjs'
import { ROLES_ALERTE } from './sites/commun.mjs'
import { derniereImage } from './derniere-image.mjs'
import { PIECE_MAX_OCTETS, dossierProjets, lirePiece, refusPiece } from './pieces.mjs'

const ICI = path.dirname(fileURLToPath(import.meta.url))
const RACINE_ASTREL = path.resolve(ICI, '..')

export const SITES = { flow: FLOW, dreamina: DREAMINA }
export const RESOLUTION = '720p'
const OCTETS_MAX = 1024 * 1024 * 1024
export const IMAGE_MAX = PIECE_MAX_OCTETS // comme astrel/videos_ia.IMAGE_MAX (filtre de pieces.mjs)
// Attente longue (15 min par défaut, 30 min et plus permis : relecture B2, fiche 3)
export const BORNES = { promptMax: 2000, plafondDefaut: 50, plafondMax: 1000, delaiDefautS: 900, delaiMinS: 5, delaiMaxS: 3600 }
// Sondes LENTES pendant la génération (2 à 10 min) : une lecture de la page toutes les 4 à 7 s
const SONDE_VIDEO_MS = TEST ? [60, 90] : [4000, 7000]
const REFUS_STABLE_MS = TEST ? 600 : 10000

// --------------------------------------------------------------- signatures

// Mêmes marques que astrel/medias.sniff (« video/mp4 ») : le cœur revérifie le fichier.
const MARQUES_MP4 = new Set(['isom', 'iso2', 'iso4', 'iso5', 'iso6', 'mp41', 'mp42', 'avc1', 'dash', 'M4V ', 'MSNV', '3gp4', '3gp5'])
export const SIGNATURES_VIDEO = [
  ['mp4', (b) => b.slice(4, 8).toString('latin1') === 'ftyp' && MARQUES_MP4.has(b.slice(8, 12).toString('latin1'))],
  ['webm', (b) => b[0] === 0x1a && b[1] === 0x45 && b[2] === 0xdf && b[3] === 0xa3] // EBML ; « webm » vérifié après
]

/** Durée (s) d'un mp4 lue dans `moov/mvhd`, null si illisible (webm, fichier tronqué). */
export function dureeMp4 (fichier) {
  let fd
  try {
    fd = fs.openSync(fichier, 'r')
    const taille = fs.fstatSync(fd).size
    const lire = (pos, n) => { const b = Buffer.alloc(n); return fs.readSync(fd, b, 0, n, pos) === n ? b : null }
    const boite = (pos, fin) => {
      const h = lire(pos, 16)
      if (!h || pos + 8 > fin) return null
      let t = h.readUInt32BE(0)
      let entete = 8
      if (t === 1) { t = Number(h.readBigUInt64BE(8)); entete = 16 } else if (t === 0) t = fin - pos
      if (t < entete || pos + t > fin) return null
      return { type: h.slice(4, 8).toString('latin1'), debut: pos + entete, fin: pos + t }
    }
    const chercher = (type, debut, fin) => {
      for (let pos = debut, n = 0; pos < fin && n < 1000; n++) {
        const b = boite(pos, fin)
        if (!b) return null
        if (b.type === type) return b
        pos = b.fin
      }
      return null
    }
    const moov = chercher('moov', 0, taille)
    const mvhd = moov && chercher('mvhd', moov.debut, moov.fin)
    if (!mvhd) return null
    const v = lire(mvhd.debut, 32)
    if (!v) return null
    const [echelle, duree] = v[0] === 1
      ? [v.readUInt32BE(20), Number(v.readBigUInt64BE(24))]
      : [v.readUInt32BE(12), v.readUInt32BE(16)]
    return echelle > 0 ? Math.round((duree / echelle) * 100) / 100 : null
  } catch { return null } finally { if (fd !== undefined) fs.closeSync(fd) }
}

// --------------------------------------------------------------- images de début et de fin

// P40 A4 (28/10) : plus de copie du filtre ici, il est repris de pieces.mjs (même règles que
// astrel/ia_web.pieces et videos_ia.image_depart) ; seules les racines de test restent propres à
// la vidéo (ASTREL_VIDEO_RACINES).
const reelOu = (p) => { try { return fs.realpathSync(p) } catch { return path.resolve(p) } }

/** Dossiers d'où une image peut venir : pièces jointes d'Astrel et dossier des projets (pieces.dossierProjets,
 * comme videos_ia.image_depart). En test SEULEMENT (ASTREL_IMAGEGEN_RYTHME=test, retiré par le cœur),
 * ASTREL_VIDEO_RACINES ajoute des dossiers (fausses images sous /tmp). */
export function racinesImages () {
  const r = [path.join(RACINE_ASTREL, 'runtime', 'attachments'), dossierProjets()]
  if (TEST && process.env.ASTREL_VIDEO_RACINES) r.push(...process.env.ASTREL_VIDEO_RACINES.split(':').filter((x) => path.isAbsolute(x)))
  return r.map(reelOu)
}

/** Raison de refuser une image (chemin RÉEL), '' si elle est permise (pieces.refusPiece). */
export function refusImage (reel, racines = racinesImages()) {
  return refusPiece(reel, racines)
}

/**
 * Vérifie une image et la LIT (pieces.lirePiece : chemin absolu, fichier réel permis, ouvert sans
 * suivre de lien, fichier ordinaire à un seul nom, 5 Mo au plus, PNG/JPEG/WebP lu dans les OCTETS,
 * fichier ouvert = fichier vérifié). Rend {chemin, nom, mime, octets} ; les octets sont ce qui sera
 * déposé (jamais relu entre-temps). Échec `fichiers` sinon.
 */
export function lireImage (brut, { quelle = 'image', racines = racinesImages() } = {}) {
  const p = lirePiece(brut, { racines, quoi: quelle })
  return { chemin: p.chemin, nom: p.name, mime: p.mimeType, octets: p.buffer }
}

// --------------------------------------------------------------- entrée

export function verifierJob (brut, { catalogue = lireCatalogue() } = {}) {
  const j = verifierBase(brut, { sites: SITES, type: 'video', bornes: BORNES, catalogue })
  const site = j.site
  // Modèle : id du catalogue ; `defaut` = modèle par défaut du site (Flow : Veo 3.1 Lite, relevé
  // B0 ; Dreamina : Seedance 2.0 Mini, relevé B5), rendu sous son id
  let modele = j.modele
  if (modele.id === MODELE_DEFAUT) {
    const id = site.modeleDefaut
    const m = (catalogue[j.service]?.modeles || []).find((x) => x.id === id)
    modele = { id, libelle: m?.libelle || site.modeles[id].libelle, cout: m?.cout || site.modeles[id].cout[site.dureeDefaut] }
  }
  const fiche = site.modeles[modele.id]
  if (!fiche) throw new Echec('entree', `modèle ${modele.id} pas pris en charge par le pilote ${j.service}`)
  const duree = brut.duree == null || brut.duree === '' ? site.dureeDefaut : Number(brut.duree)
  if (!fiche.durees.includes(duree)) throw new Echec('entree', `durée ${brut.duree} s impossible pour ${modele.id} (${fiche.durees.join(', ')} s)`)
  const format = brut.format == null || brut.format === '' ? site.formats[0] : String(brut.format)
  if (!site.formats.includes(format)) throw new Echec('entree', `format : ${site.formats.join(' ou ')} attendu`)
  // Coût attendu en 720p, x1 : relevé par durée (Flow B0, Dreamina B5) ; durée non relevée (Omni
  // 6 s) : au plus le coût du catalogue. Compté tel quel dans le compteur du compte.
  const coutExact = fiche.cout[duree] ?? null
  modele = { ...modele, cout: coutExact ?? modele.cout }
  // Images : champs propres, ou `fichiers` (contrat de videos_ia.py au lot B2), jamais les deux
  const anciens = j.fichiers
  if (anciens.length > 2) throw new Echec('fichiers', 'deux images au plus (début et fin)')
  if (anciens.length && (brut.image_debut || brut.image_fin)) throw new Echec('entree', 'fichiers et image_debut/image_fin en même temps')
  const chemins = { debut: brut.image_debut ?? anciens[0] ?? '', fin: brut.image_fin ?? anciens[1] ?? '' }
  if ((chemins.debut || chemins.fin) && typeof site.deposerImage !== 'function') {
    throw new Echec('entree', `images de début et de fin pas prises en charge par le pilote ${j.service}`)
  }
  const images = {}
  for (const quelle of ['debut', 'fin']) {
    if (chemins[quelle] === '' || chemins[quelle] == null) continue
    images[quelle] = lireImage(chemins[quelle], { quelle: `image de ${quelle === 'debut' ? 'début' : 'fin'}` }).chemin
  }
  if (brut.derniere_image != null && typeof brut.derniere_image !== 'boolean') throw new Echec('entree', 'derniere_image : vrai ou faux')
  return { ...j, modele, duree, format, resolution: fiche.definition || RESOLUTION, coutExact, images, derniereImage: brut.derniere_image === true }
}

// --------------------------------------------------------------- lecture de la page

/** Texte RENDU de la page, sans le champ du prompt ni le prompt lui-même (un prompt « high
 * demand » ne doit jamais passer pour le créneau de pointe). */
export async function textePage (page, prompt) {
  let t = await page.evaluate(() => {
    let texte = document.body?.innerText || ''
    for (const el of document.querySelectorAll('textarea, [contenteditable="true"], input')) {
      const v = (el.value || el.innerText || '').trim()
      if (v) texte = texte.split(v).join(' ')
    }
    return texte
  }).catch(() => '')
  const p = String(prompt || '').trim()
  if (p) t = t.split(p).join(' ')
  return t
}

/** Texte RENDU des seuls éléments de rôle alert, status ou dialog (`roles`, ROLES_ALERTE du
 * site), affichés, sans les champs ni le prompt : seul endroit où un quota, des crédits ou un
 * créneau de pointe sont crus après l'envoi (site.classer). */
export async function texteAlertes (page, prompt, roles = ROLES_ALERTE) {
  let t = await page.evaluate((roles) => {
    const morceaux = []
    for (const el of document.querySelectorAll(roles)) {
      // innerText d'un élément non affiché = son textContent (modèle caché) : écarté
      if (!el.getClientRects().length || (typeof el.checkVisibility === 'function' && !el.checkVisibility({ visibilityProperty: true }))) continue
      let texte = el.innerText || ''
      for (const c of el.querySelectorAll('textarea, [contenteditable="true"], input')) {
        const v = (c.value || c.innerText || '').trim()
        if (v) texte = texte.split(v).join(' ')
      }
      morceaux.push(texte)
    }
    return morceaux.join('\n')
  }, roles).catch(() => '')
  const p = String(prompt || '').trim()
  if (p) t = t.split(p).join(' ')
  return t
}

const lignesDe = (t) => String(t || '').split('\n').map((l) => l.replace(/\s+/g, ' ').trim()).filter(Boolean)

async function echecPage (page, job, code, message, extra = {}) {
  const fichier = await capture(page, job.debug, code)
  return new Echec(code, message, { ...extra, ...(fichier ? { capture: fichier } : {}) })
}

function verifierSession (page, job) {
  let hote = ''
  try { hote = new URL(page.url()).hostname } catch {}
  if (job.site.hotesConnexion.test(hote)) throw new Echec('session', 'compte déconnecté pendant la génération : à reconnecter')
}

/** État de la page qui interdit l'envoi (avant le clic) : activité inhabituelle, crédits,
 * limite, créneau de pointe. */
async function bloqueAvant (page, job) {
  const lu = job.site.classer(await textePage(page, job.prompt), { apres: false })
  if (lu) throw await echecPage(page, job, lu.code, lu.message, lu.reprise ? { reprise: lu.reprise } : {})
}

// --------------------------------------------------------------- parcours d'une vidéo

const prepares = new WeakSet()

/** Un réglage introuvable peut venir d'un défi apparu APRÈS le contrôle d'arrivée (il couvre la
 * page) : c'est alors un `captcha`, jamais une « page changée ». */
async function sansDefi (page, etape) {
  try {
    return await etape()
  } catch (e) {
    if (e instanceof Echec && e.code === 'page' && await captcha(page)) {
      throw new Echec('captcha', 'défi anti-robot affiché : arrêt, jamais contourné')
    }
    throw e
  }
}

/** Mode vidéo du site (Flow : projet ouvert, annonces fermées, Video, Frames) : une seule fois
 * par page. Aussi donné au noyau (`outils.preparer`) : le menu des modèles vidéo de Flow n'existe
 * qu'en mode Video. */
async function preparer (page, site) {
  if (prepares.has(page)) return
  await sansDefi(page, () => site.preparer(page))
  prepares.add(page)
}

/** Coût annoncé : le prix catalogue (`liste`, barré s'il y a une remise) doit être celui relevé
 * pour le modèle et la durée ; le solde affiché doit couvrir le coût DÉBITÉ. Rend ce dernier. */
async function verifierCout (page, job) {
  const { site } = job
  const lu = await site.coutAnnonce(page, await textePage(page, job.prompt), job)
  if (lu === null) throw await echecPage(page, job, 'page', `coût annoncé (${site.libelleCout}) introuvable : rien n'est envoyé`)
  if (Number.isNaN(lu) || !(lu.debite <= lu.liste)) throw await echecPage(page, job, 'page', 'plusieurs coûts annoncés sur la page : rien n\'est envoyé')
  const attendu = job.coutExact
  if (attendu !== null ? lu.liste !== attendu : lu.liste > job.modele.cout) {
    throw await echecPage(page, job, 'page',
      `coût annoncé ${lu.liste} crédits, attendu ${attendu ?? `au plus ${job.modele.cout}`} (${job.modele.id}, ${job.duree} s, ${job.resolution}, x1) : réglage mal lu, rien n'est envoyé`)
  }
  const solde = await site.credits(page)
  if (Number.isFinite(solde) && solde < lu.debite) throw await echecPage(page, job, 'credits', `crédits insuffisants : ${solde} restants, ${lu.debite} demandés`)
  return lu.debite
}

/** Une vidéo nouvelle est-elle le RÉSULTAT ? Adresse absente avant l'envoi, affichée (une
 * animation d'attente masquée reste souvent dans la page), et, si le navigateur a lu sa durée,
 * au moins la moitié de la durée demandée (2 s au plus) : une boucle d'attente d'une seconde
 * n'est pas prise pour le clip. Durée inconnue (métadonnées pas chargées) : acceptée. */
export function estResultat (v, avantVideos, duree) {
  if (!v?.src || avantVideos.has(v.src) || !v.visible || v.carte === false) return false
  return !(Number.isFinite(v.duree) && v.duree < Math.min(2, duree / 2))
}

/** Attend la vidéo nouvelle (estResultat). Pendant l'attente, seules les lignes NOUVELLES de la
 * page sont classées (le pied de page d'avant ne compte jamais) ; quota, crédits et pointe ne
 * sont crus que dans un élément alert, status ou dialog, jamais dans un écho du prompt
 * (site.classer : sinon le cœur change de compte, deuxième génération). Génération visible en
 * cours (site.suiviGeneration) : aucun message n'est cru. */
async function attendreVideo (page, job, { avantVideos, avantLignes }) {
  const { site } = job
  const fin = Date.now() + job.delaiMs
  let vu = null
  let vuDepuis = 0
  let candidat = '' // vidéo retenue à la lecture précédente : prise si elle l'est encore à celle-ci
  while (Date.now() < fin) {
    siFerme(page)
    await defi(page, job)
    verifierSession(page, job)
    const videos = await site.videosInfos(page, job)
    const index = videos.findIndex((v) => estResultat(v, avantVideos, job.duree))
    // Deux lectures de suite : entre les deux, le navigateur a le temps de lire la durée d'une
    // animation d'attente (inconnue à son apparition)
    if (index >= 0 && videos[index].src === candidat) return index
    candidat = index >= 0 ? videos[index].src : ''
    if (candidat) { await dormir(hasard(SONDE_VIDEO_MS[0], SONDE_VIDEO_MS[1])); continue } // une vidéo est là : aucun message n'arrête
    const suivi = typeof site.suiviGeneration === 'function' ? await site.suiviGeneration(page, job) : null
    if (suivi?.enCours) { vu = null; await dormir(hasard(SONDE_VIDEO_MS[0], SONDE_VIDEO_MS[1])); continue }
    const nouvelles = lignesDe(await textePage(page, job.prompt)).filter((l) => !avantLignes.has(l))
    const alertes = lignesDe(await texteAlertes(page, job.prompt, site.rolesAlerte)).filter((l) => !avantLignes.has(l))
    const lu = site.classer(nouvelles.join('\n'), { apres: true, prompt: job.prompt, alertes: alertes.join('\n'), finSansVideo: !!suivi?.fini })
    if (lu) {
      if (!vu || vu.code !== lu.code) { vu = lu; vuDepuis = Date.now() }
      const stable = Date.now() - vuDepuis
      // Refus (et message `lent`) : laissé stable un moment (un premier message peut précéder la
      // vidéo) ; le reste dès la deuxième lecture identique.
      if (stable >= (lu.code === 'refus' || lu.lent ? REFUS_STABLE_MS : 1)) {
        throw await echecPage(page, job, lu.code, lu.message, lu.reprise ? { reprise: lu.reprise } : {})
      }
    } else {
      vu = null
    }
    await dormir(hasard(SONDE_VIDEO_MS[0], SONDE_VIDEO_MS[1]))
  }
  throw new Echec('delai', `pas de vidéo au bout de ${Math.round(job.delaiMs / 1000)} s`)
}

async function telecharger (page, job, index) {
  emit({ t: 'etape', etape: 'telechargement' })
  let dl
  try {
    dl = await job.site.telecharger(page, index, job)
  } catch (e) {
    if (e instanceof Echec) throw e
    throw new Echec('page', `téléchargement impossible : ${String(e?.message || e).split('\n')[0].slice(0, 200)}`)
  }
  let res
  try {
    res = await enregistrer(dl, job, { signatures: SIGNATURES_VIDEO, octetsMax: OCTETS_MAX, refus: 'le fichier téléchargé n\'est pas une vidéo mp4 ou webm', vide: 'video' })
  } catch (e) {
    // Téléchargement raté côté site (404, annulé : « saveAs: canceled ») : la page, pas le worker
    if (e instanceof Echec) throw e
    throw new Echec('page', `téléchargement impossible : ${String(e?.message || e).split('\n')[0].slice(0, 200)}`)
  }
  if (res.type === 'webm') {
    // EBML commun à webm et matroska : le type de document « webm » doit suivre (astrel/medias.sniff)
    const tete = Buffer.alloc(64)
    const fd = fs.openSync(res.fichier, 'r')
    fs.readSync(fd, tete, 0, 64, 0)
    fs.closeSync(fd)
    if (!tete.includes('webm')) {
      try { fs.unlinkSync(res.fichier) } catch {}
      throw new Echec('page', 'le fichier téléchargé est un matroska, pas un webm : refusé')
    }
  }
  return res
}

/**
 * Le clic d'envoi a-t-il abouti ? Oui si le bouton a reçu l'appui (marque), sinon si la page
 * a réagi dans les secondes qui suivent (bouton remplacé par la page entre-temps) : confirmation
 * demandée, vidéo ou ligne nouvelle. Rend la preuve, ou '' (rien : le clic est tombé à côté).
 * Dans le doute la page a réagi, donc on compte : sur-compter ne relance jamais rien.
 */
async function clicRecu (page, job, marque, { avantVideos, avantLignes }) {
  const fin = Date.now() + (TEST ? 500 : 4000)
  do {
    if (marque && await marque.evaluate((m) => m.vu).catch(() => false)) return 'bouton'
    if (await job.site.confirmerEnvoi(page).catch(() => null)) return 'confirmation'
    if ((await job.site.videos(page)).some((s) => s && !avantVideos.has(s))) return 'video'
    if (lignesDe(await textePage(page, job.prompt)).some((l) => !avantLignes.has(l))) return 'page'
    await dormir(TEST ? 40 : 300)
  } while (Date.now() < fin)
  return ''
}

export async function parcoursVideo ({ page, job, envoye }) {
  const { site } = job
  emit({ t: 'etape', etape: 'saisie' })
  await preparer(page, site)
  await bloqueAvant(page, job)
  await sansDefi(page, () => site.regler(page, job))
  // Images REVÉRIFIÉES ici (le job a pu attendre des heures en file) et déposées depuis ces octets
  const deposees = {}
  for (const quelle of ['debut', 'fin']) {
    if (!job.images[quelle]) continue
    const img = lireImage(job.images[quelle], { quelle: `image de ${quelle === 'debut' ? 'début' : 'fin'}` })
    await sansDefi(page, () => site.deposerImage(page, quelle, img))
    deposees[quelle] = img.nom
    await pause(PAUSE_COURTE_MS)
  }
  const champ = site.prompt(page)
  await cliquer(page, champ, { timeout: 10000 })
  await pause(PAUSE_COURTE_MS)
  await taper(page, job.prompt)
  await pause()

  // ---- avant l'UNIQUE clic d'envoi
  await defi(page, job)
  await bloqueAvant(page, job)
  const cout = await verifierCout(page, job)
  const bouton = site.envoi(page)
  if (!await bouton.isVisible().catch(() => false)) throw await echecPage(page, job, 'page', 'bouton d\'envoi introuvable : la page a peut-être changé')
  const etat = await site.envoiBloque(bouton)
  if (etat.info) throw await echecPage(page, job, 'credits', 'bouton d\'envoi orange (icône d\'info) : crédits insuffisants')
  if (etat.inactif) throw await echecPage(page, job, 'page', 'bouton d\'envoi inactif : un réglage manque peut-être')
  const avantVideos = new Set(await site.videos(page))
  const avantLignes = new Set(lignesDe(await textePage(page, job.prompt)))

  // Le clic doit ATTEINDRE le bouton (relecture B1 fiche 9) : un clic tombé sur un calque ne
  // compte aucun crédit. Marque hors de window (rien de visible pour la page).
  const marque = await page.evaluateHandle(() => ({ vu: false })).catch(() => null)
  if (marque) {
    await bouton.evaluate((el, m) => {
      const vu = () => { m.vu = true }
      for (const type of ['pointerdown', 'mousedown', 'click']) el.addEventListener(type, vu, { capture: true, once: true })
    }, marque).catch(() => {})
  }
  await cliquer(page, bouton, { timeout: 10000 })
  const recu = await clicRecu(page, job, marque, { avantVideos, avantLignes })
  if (marque) await marque.dispose().catch(() => {})
  if (!recu) throw await echecPage(page, job, 'page', 'clic d\'envoi sans effet (bouton non atteint, rien de changé sur la page) : rien n\'est compté ni renvoyé')
  envoye() // un seul envoi par essai : aucun autre clic d'envoi dans ce parcours
  emit({ t: 'etape', etape: 'generation' })
  // Confirmation du site (Flow « Confirm before generating », Always par défaut ; Dreamina « Avant
  // de continuer », à chaque vidéo) : la MÊME demande, confirmée une fois
  const fin = Date.now() + (TEST ? 400 : 5000)
  while (Date.now() < fin) {
    const ok = await site.confirmerEnvoi(page)
    if (ok) { await pause(PAUSE_COURTE_MS); await cliquer(page, ok, { timeout: 5000 }); break }
    await dormir(TEST ? 40 : 400)
  }

  const index = await attendreVideo(page, job, { avantVideos, avantLignes })
  await pause(PAUSE_COURTE_MS)
  const res = await telecharger(page, job, index)
  await pause(PAUSE_COURTE_MS)
  return {
    ...res,
    duree: job.duree,
    duree_reelle: res.type === 'mp4' ? dureeMp4(res.fichier) : null,
    format: job.format,
    resolution: job.resolution,
    cout_annonce: cout,
    images: deposees
  }
}

export async function generer (job) {
  const res = await seance(job, parcoursVideo, { outils: { preparer: (page) => preparer(page, job.site) } })
  const sortie = { ...res, fichier: String(res.fichier), fichiers: [{ fichier: String(res.fichier), nom: res.nom }] }
  if (job.derniereImage) {
    // Pour enchaîner le clip suivant (image de début = dernière image réelle de celui-ci) ; un
    // échec ici ne perd pas la vidéo, déjà enregistrée.
    try {
      sortie.derniere_image = (await derniereImage(sortie.fichier)).fichier
    } catch (e) {
      sortie.derniere_image_erreur = String(e?.message || e).split('\n')[0].slice(0, 200)
    }
  }
  return sortie
}

siPrincipal(import.meta.url, verifierJob, generer)
