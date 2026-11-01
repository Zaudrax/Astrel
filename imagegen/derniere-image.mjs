// Dernière image RÉELLE d'un clip vidéo (plan P41 lot B1, 28/10/2026) : pour ENCHAÎNER des clips
// (Flow, mode Frames : la dernière image d'un clip devient l'image de début du suivant).
//
// « Réelle » : la dernière image DÉCODÉE du flux vidéo, pas une image clé proche de la fin ni
// une vignette. Deux moyens, dans cet ordre :
//   1. ffmpeg (ASTREL_FFMPEG, sinon `ffmpeg` du PATH) : `-sseof -3` puis `-update 1`, chaque
//      image décodée écrase la précédente dans le même PNG ; reste la dernière ;
//   2. sinon PyAV (bibliothèques de ffmpeg) par le Python d'Astrel (`.venv/bin/python`, ou
//      ASTREL_PYTHON) : tout le flux est décodé, la dernière image gardée. Cette machine n'a
//      pas de binaire ffmpeg (28/10) : c'est ce moyen-là qui sert ici. ⚠️ PyAV 17 et Pillow
//      viennent du site UTILISATEUR (~/.local), pas de la venv ni de requirements.txt.
// Le PNG est écrit à côté du clip (`<clip>-derniere.png`, ou `sortie`), d'abord sous un nom
// temporaire, vérifié par ses octets (signature PNG), puis renommé.
//
// Ligne de commande : node derniere-image.mjs <clip.mp4|webm> [<sortie.png>]
//   → une ligne JSON {fichier, via} ou {erreur}, code 0 ou 1.

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ICI = path.dirname(fileURLToPath(import.meta.url))
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
const DELAI_MS = 120000

// Décodage complet, dernière image gardée (PyAV + Pillow, venv d'Astrel). Arguments : clip, png.
const PYAV = `
import sys, av
clip, sortie = sys.argv[1], sys.argv[2]
derniere = None
with av.open(clip) as c:
    flux = c.streams.video[0]
    flux.thread_type = "AUTO"
    for image in c.decode(flux):
        derniere = image
if derniere is None:
    sys.exit("aucune image vidéo décodée")
derniere.to_image().save(sortie, format="PNG")
print(f"{derniere.width}x{derniere.height}")
`

function trouverFfmpeg () {
  const env = process.env.ASTREL_FFMPEG
  if (env) return path.isAbsolute(env) && fs.existsSync(env) ? env : null
  for (const d of String(process.env.PATH || '').split(':')) {
    if (!d) continue
    const f = path.join(d, 'ffmpeg')
    try { fs.accessSync(f, fs.constants.X_OK); return f } catch {}
  }
  return null
}

function trouverPython () {
  const env = process.env.ASTREL_PYTHON
  if (env) return path.isAbsolute(env) && fs.existsSync(env) ? env : null
  const venv = path.join(ICI, '..', '.venv', 'bin', 'python')
  return fs.existsSync(venv) ? venv : null
}

const estPng = (f) => {
  try {
    const b = Buffer.alloc(8)
    const fd = fs.openSync(f, 'r')
    const n = fs.readSync(fd, b, 0, 8, 0)
    fs.closeSync(fd)
    return n === 8 && b.equals(PNG) && fs.statSync(f).size > 8
  } catch { return false }
}

const motif = (r) => String(r.error?.message || r.stderr || '').trim().split('\n').at(-1)?.slice(0, 200) || `code ${r.status}`

/**
 * Extrait la dernière image décodée de `clip` en PNG. Rend {fichier, via: 'ffmpeg'|'pyav'}.
 * `moyen` : 'auto' (ffmpeg s'il existe, sinon PyAV), 'ffmpeg' ou 'pyav' (tests). Lève une
 * Error lisible si rien ne marche ; aucun fichier partiel n'est laissé.
 */
export async function derniereImage (clip, { sortie = '', moyen = 'auto' } = {}) {
  const source = path.resolve(String(clip || ''))
  let st
  try { st = fs.statSync(source) } catch { throw new Error(`clip introuvable : ${path.basename(source)}`) }
  if (!st.isFile() || !st.size) throw new Error('clip vide ou pas un fichier')
  const cible = sortie ? path.resolve(sortie) : source.replace(/\.[a-z0-9]{2,5}$/i, '') + '-derniere.png'
  if (cible === source) throw new Error('sortie identique au clip')
  const tmp = path.join(path.dirname(cible), `.derniere-${process.pid}-${Date.now()}.png`)
  const essais = []
  const ffmpeg = moyen === 'pyav' ? null : trouverFfmpeg()
  const python = moyen === 'ffmpeg' ? null : trouverPython()
  try {
    if (ffmpeg) {
      const r = spawnSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-sseof', '-3', '-i', source,
        '-an', '-update', '1', '-f', 'image2', '-c:v', 'png', tmp], { encoding: 'utf8', timeout: DELAI_MS })
      if (r.status === 0 && estPng(tmp)) { fs.renameSync(tmp, cible); return { fichier: cible, via: 'ffmpeg' } }
      essais.push(`ffmpeg : ${motif(r)}`)
    } else if (moyen === 'ffmpeg') essais.push('ffmpeg absent')
    if (python) {
      // `-P` (pas le dossier courant dans le chemin des modules), PAS `-I` : PyAV et Pillow sont
      // dans le site utilisateur de l'utilisateur (~/.local), que `-I` masque (mesuré le 28/10).
      const r = spawnSync(python, ['-P', '-c', PYAV, source, tmp], { encoding: 'utf8', timeout: DELAI_MS, cwd: path.dirname(cible) })
      if (r.status === 0 && estPng(tmp)) { fs.renameSync(tmp, cible); return { fichier: cible, via: 'pyav' } }
      essais.push(`pyav : ${motif(r)}`)
    } else if (moyen !== 'ffmpeg') essais.push('ni ffmpeg ni python de la venv (PyAV)')
  } finally {
    try { fs.unlinkSync(tmp) } catch {}
  }
  throw new Error(`dernière image impossible à extraire (${essais.join(' ; ')})`)
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const [clip, sortie] = process.argv.slice(2)
  derniereImage(clip, { sortie }).then(
    (r) => { process.stdout.write(JSON.stringify(r) + '\n'); process.exit(0) },
    (e) => { process.stdout.write(JSON.stringify({ erreur: String(e?.message || e) }) + '\n'); process.exit(1) }
  )
}
