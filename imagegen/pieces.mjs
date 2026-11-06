// Pièces jointes d'une génération d'images (plan P40 lot A3) : filtre du cœur, appliqué AUSSI
// par le worker (il ne fait jamais confiance au chemin reçu), puis dépôt par setInputFiles.
//
// Règles (mêmes que astrel/videos_ia.image_depart et video.mjs refusImage, filtre de P40 A3) :
//   - 6 fichiers au plus, chemins ABSOLUS ;
//   - fichier réel sous runtime/attachments (dossier d'Astrel) ou le dossier des projets (dossierProjets) ;
//   - jamais un dossier privé d'Astrel (runtime hors pièces jointes et images IA, config,
//     hooks, vault, logs), jamais un profil de compte (runtime/imagegen/profils : cookies),
//     jamais un élément caché ni un nom de secret ;
//   - fichier ordinaire à un seul nom (pas de lien dur), ouvert sans suivre de lien, 5 Mo au plus ;
//   - image PNG, JPEG ou WebP reconnue dans ses OCTETS (pas d'après le nom).
// Les octets lus ici sont ceux qui seront déposés : jamais relus sur le disque entre-temps.
//
// Test SEULEMENT (ASTREL_IMAGEGEN_RYTHME=test, retiré par le cœur) : ASTREL_IMAGEGEN_RACINES
// ajoute des dossiers permis (fausses images sous /tmp).

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { Echec, SIGNATURES_IMAGE } from './noyau.mjs'
import { TEST } from './humain.mjs'

const ICI = path.dirname(fileURLToPath(import.meta.url))
const RACINE_ASTREL = path.resolve(ICI, '..')

export const PIECES_MAX = 6
export const PIECE_MAX_OCTETS = 5 * 1024 * 1024
const MIMES = { png: 'image/png', jpg: 'image/jpeg', webp: 'image/webp' }
// Comme astrel/medias._SECRET_NAME (et video.mjs) : jamais pris là.
const NOM_SECRET = /(?:^|[^a-z0-9])(?:secrets?|credentials?|passwords?|passwd|mots?[-_ ]de[-_ ]passe|api[-_ ]?keys?|private[-_ ]?keys?|id_(?:rsa|dsa|ecdsa|ed25519)|whatsapp-auth|whatsapp-media|hud-token|hud-session-key|hud-profile)(?:[^a-z0-9]|$)/i
const PRIVES = ['runtime', 'config', 'hooks', 'vault', 'logs']

const sous = (p, r) => p !== r && p.startsWith(r.endsWith(path.sep) ? r : r + path.sep)
const reelOu = (p) => { try { return fs.realpathSync(p) } catch { return path.resolve(p) } }

/** Dossier des projets (05/11, plus écrit en dur) : celui du cœur (`ASTREL_WORKDIR` = config.WORKDIR, posé par
 * ia_web.env_worker), sinon ~/Projets (même défaut que config.WORKDIR). */
export function dossierProjets (env = process.env) {
  const v = String(env.ASTREL_WORKDIR || '').trim()
  if (!v) return path.join(os.homedir(), 'Projets')
  return v === '~' || v.startsWith('~/') || v.startsWith('~\\') ? path.join(os.homedir(), v.slice(1)) : path.resolve(v)
}

/** Dossiers d'où une pièce jointe peut venir (chemins réels). */
export function racinesPieces () {
  const r = [path.join(RACINE_ASTREL, 'runtime', 'attachments'), dossierProjets()]
  if (TEST && process.env.ASTREL_IMAGEGEN_RACINES) {
    r.push(...process.env.ASTREL_IMAGEGEN_RACINES.split(':').filter((x) => path.isAbsolute(x)))
  }
  return r.map(reelOu)
}

/** Raison de refuser un chemin RÉEL, '' s'il est permis. */
export function refusPiece (reel, racines = racinesPieces()) {
  if (!racines.some((r) => sous(reel, r))) return 'hors des pièces jointes et du dossier des projets'
  const astrel = reelOu(RACINE_ASTREL)
  const pj = reelOu(path.join(astrel, 'runtime', 'attachments'))
  const imagesIa = reelOu(path.join(astrel, 'runtime', 'imagegen'))
  const profils = path.join(imagesIa, 'profils')
  if (reel === profils || sous(reel, profils)) return "dossier privé d'Astrel"
  if (PRIVES.some((d) => sous(reel, path.join(astrel, d))) && !sous(reel, pj) && !sous(reel, imagesIa)) {
    return "dossier privé d'Astrel (jetons, réglages, mémoire, journaux)"
  }
  const parties = reel.split(path.sep).filter(Boolean)
  if (parties.some((p) => p.startsWith('.'))) return 'fichier ou dossier caché'
  if (parties.some((p) => NOM_SECRET.test(p))) return 'le nom ressemble à un secret'
  return ''
}

// Filtre UNIQUE : video.mjs (images de début et de fin, plan P41) le reprend depuis P40 A4.

/**
 * Vérifie une pièce jointe et la LIT. Rend {chemin (réel), name, mimeType, buffer} : la forme
 * attendue par setInputFiles / FileChooser.setFiles. Échec `fichiers` sinon.
 */
export function lirePiece (brut, { racines = racinesPieces(), rang = 1, quoi = '' } = {}) {
  // `quoi` (P40 A4) : nom de la pièce dans le message (video.mjs : « image de début »)
  const refus = (raison) => new Echec('fichiers', `${quoi || `pièce jointe ${rang}`} refusée : ${raison}`)
  const texte = typeof brut === 'string' ? brut.trim() : ''
  if (!texte || !path.isAbsolute(texte) || texte.includes('\0')) throw refus('chemin absolu attendu')
  let reel
  try { reel = fs.realpathSync(texte) } catch { throw refus('fichier introuvable') }
  const raison = refusPiece(reel, racines)
  if (raison) throw refus(raison)
  // Fichier ordinaire vérifié AVANT l'ouverture : un tube nommé bloquerait le worker. O_NONBLOCK :
  // remplacé par un tube entre les deux, l'ouverture ne bloque pas et le fstat le refuse.
  let avant
  try { avant = fs.lstatSync(reel) } catch { throw refus('fichier introuvable') }
  if (!avant.isFile()) throw refus('pas un fichier ordinaire')
  let fd
  try { fd = fs.openSync(reel, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK) } catch { throw refus('fichier illisible') }
  try {
    const st = fs.fstatSync(fd)
    if (!st.isFile() || st.nlink > 1 || st.ino !== avant.ino || st.dev !== avant.dev) throw refus('pas un fichier ordinaire')
    if (st.size > PIECE_MAX_OCTETS) throw refus('image trop lourde (5 Mo au plus)')
    let ouvert = ''
    try { ouvert = fs.readlinkSync(`/proc/self/fd/${fd}`) } catch {}
    if (ouvert && ouvert !== reel) throw refus('fichier remplacé pendant la vérification')
    const buffer = Buffer.alloc(st.size)
    let lu = 0
    while (lu < st.size) {
      const n = fs.readSync(fd, buffer, lu, st.size - lu, lu)
      if (!n) break
      lu += n
    }
    const sig = SIGNATURES_IMAGE.find(([, ok]) => ok(buffer.subarray(0, 16)))
    if (lu !== st.size || !sig) throw refus('pas une image PNG, JPEG ou WebP')
    const base = path.basename(reel).replace(/[^\w.-]+/g, '_').slice(-80)
    return { chemin: reel, name: base || `image-${rang}.${sig[0]}`, mimeType: MIMES[sig[0]], buffer }
  } finally { fs.closeSync(fd) }
}

/** Toutes les pièces d'un job (liste de chemins), vérifiées et lues ; 6 au plus. */
export function lirePieces (liste, options = {}) {
  if (!Array.isArray(liste)) throw new Echec('fichiers', 'pièces jointes : liste de chemins attendue')
  if (liste.length > PIECES_MAX) throw new Echec('fichiers', `${PIECES_MAX} pièces jointes au plus`)
  const pieces = liste.map((brut, i) => lirePiece(brut, { ...options, rang: i + 1 }))
  if (new Set(pieces.map((p) => p.chemin)).size !== pieces.length) throw new Echec('fichiers', 'la même pièce jointe deux fois')
  return pieces
}
