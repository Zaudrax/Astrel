// Worker VIDÉO (plan P41 lot B1) lancé pour de vrai sur une FAUSSE page Flow servie en local
// (test/fausse-flow.html) : `cd imagegen && node --test --test-concurrency=1` (un seul Chrome à
// la fois : ce fichier et worker.test.mjs en lancent chacun). Chrome SANS FENÊTRE, sauf UN cas
// visible lancé sous Xvfb, WAYLAND_DISPLAY retiré (aucune fenêtre sur l'écran de l'utilisateur).
// Aucun site réel, aucune génération.
import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

process.env.ASTREL_IMAGEGEN_RYTHME = 'test'
const RACINE_TESTS = fs.mkdtempSync(path.join(os.tmpdir(), 'video-test-'))
process.env.ASTREL_VIDEO_RACINES = RACINE_TESTS // images de test permises (mode test seulement)
const { Echec } = await import('../noyau.mjs')
const { verifierJob, lireImage, refusImage, racinesImages, dureeMp4, estResultat, SIGNATURES_VIDEO, IMAGE_MAX } = await import('../video.mjs')
const { classerPage, lireCout, reprise } = await import('../sites/flow.mjs')
const { derniereImage } = await import('../derniere-image.mjs')

const DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const ASTREL = path.dirname(DIR)
const SCRIPT = process.env.VIDEO_TEST_SCRIPT || 'video.mjs' // contre-épreuves : copie sans une garde
const PAGE = fs.readFileSync(path.join(DIR, 'test', 'fausse-flow.html'))
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')
const PYTHON = path.join(ASTREL, '.venv', 'bin', 'python')
const FFMPEG_PW = path.join(os.homedir(), '.cache', 'ms-playwright', 'ffmpeg-1011', 'ffmpeg-linux')

/** mp4 minimal : ftyp isom + moov/mvhd (version 0 ou 1) de `secondes`. */
function mp4 (secondes = 8, version = 0) {
  const boite = (type, ...corps) => {
    const c = Buffer.concat(corps)
    const h = Buffer.alloc(8)
    h.writeUInt32BE(8 + c.length)
    h.write(type, 4, 'latin1')
    return Buffer.concat([h, c])
  }
  const ftyp = boite('ftyp', Buffer.from('isom', 'latin1'), Buffer.from([0, 0, 2, 0]), Buffer.from('isommp41', 'latin1'))
  let mvhd
  if (version === 1) {
    mvhd = Buffer.alloc(112)
    mvhd[0] = 1
    mvhd.writeUInt32BE(600, 20)
    mvhd.writeBigUInt64BE(BigInt(secondes * 600), 24)
  } else {
    mvhd = Buffer.alloc(100)
    mvhd.writeUInt32BE(1000, 12)
    mvhd.writeUInt32BE(secondes * 1000, 16)
  }
  return Buffer.concat([ftyp, boite('moov', boite('mvhd', mvhd)), boite('mdat', Buffer.alloc(64))])
}
const EBML = (doc) => Buffer.concat([Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0x9f, 0x42, 0x86, 0x81, 0x01, 0x42, 0xf7, 0x81, 0x01, 0x42, 0xf2, 0x81, 0x04, 0x42, 0xf3, 0x81, 0x08, 0x42, 0x82, 0x80 | doc.length]), Buffer.from(doc, 'latin1'), Buffer.alloc(64)])

let serveur
let base
const CHARGEMENT = { webm: null } // animation d'attente de 1,25 s (PyAV), servie au mode chargement
const envois = new Map() // cle → relevés des clics d'envoi
const signaux = new Map() // cle → signaux de la nouvelle interface (&ui=2026) : « interdit:… », attestation, taille téléchargée
before(async () => {
  serveur = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x')
    const fichier = (type, corps) => { res.writeHead(200, { 'content-type': type, 'content-disposition': 'attachment' }); res.end(corps) }
    if (url.pathname === '/envoi') {
      let b = ''
      req.on('data', (d) => { b += d })
      req.on('end', () => { const r = JSON.parse(b); envois.set(r.cle, [...(envois.get(r.cle) || []), r]); res.end('ok') })
      return
    }
    if (url.pathname === '/signal') {
      let b = ''
      req.on('data', (d) => { b += d })
      req.on('end', () => { const r = JSON.parse(b); signaux.set(r.cle, [...(signaux.get(r.cle) || []), r.quoi]); res.end('ok') })
      return
    }
    if (url.pathname === '/vignette.png') { res.writeHead(200, { 'content-type': 'image/png' }); res.end(PNG); return }
    if (url.pathname === '/apercu.mp4') { res.writeHead(200, { 'content-type': 'video/mp4' }); res.end(mp4(8)); return }
    if (url.pathname.startsWith('/recaptcha/')) { res.writeHead(200, { 'content-type': 'text/html' }); res.end('<html><body>cadre</body></html>'); return }
    if (url.pathname === '/clip.mp4') return fichier('video/mp4', mp4(8))
    if (url.pathname === '/clip.webm') return fichier('video/webm', EBML('webm'))
    if (url.pathname === '/clip.mkv') return fichier('video/x-matroska', EBML('matroska'))
    if (url.pathname === '/pas-video.mp4') return fichier('video/mp4', '<html>non</html>')
    if (url.pathname === '/absent.mp4') { res.writeHead(404, { 'content-type': 'text/plain' }); res.end('absent'); return }
    if (url.pathname === '/chargement.webm' && CHARGEMENT.webm) { res.writeHead(200, { 'content-type': 'video/webm' }); res.end(CHARGEMENT.webm); return }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    res.end(PAGE)
  })
  await new Promise((resolve) => serveur.listen(0, '127.0.0.1', resolve))
  base = `http://127.0.0.1:${serveur.address().port}/`
})
after(() => {
  serveur.close()
  fs.rmSync(RACINE_TESTS, { recursive: true, force: true })
})

let n = 0
function dossiers () {
  const racine = path.join(RACINE_TESTS, `essai${++n}`)
  fs.mkdirSync(racine)
  return { racine, profil: path.join(racine, 'profils', 'essai'), sortie: path.join(racine, 'sortie'), debug: path.join(racine, 'debug') }
}
function image (d, nom = 'debut.png', octets = PNG) {
  const f = path.join(d.racine, nom)
  fs.mkdirSync(path.dirname(f), { recursive: true })
  fs.writeFileSync(f, octets)
  return f
}

function lancer (job, { env = {}, surLigne = null, sansEnv = [] } = {}) {
  const e = { ...process.env, ASTREL_IMAGEGEN_RYTHME: 'test', ASTREL_VIDEO_RACINES: RACINE_TESTS, ...env }
  for (const k of sansEnv) delete e[k]
  const proc = spawn(process.execPath, [SCRIPT], { cwd: DIR, env: e, stdio: ['pipe', 'pipe', 'pipe'] })
  let out = ''
  let err = ''
  let lu = 0
  proc.stdout.on('data', (d) => {
    out += d
    if (!surLigne) return
    const lignes = out.split('\n')
    for (const l of lignes.slice(lu, -1)) if (l) surLigne(JSON.parse(l), proc)
    lu = lignes.length - 1
  })
  proc.stderr.on('data', (d) => { err += d })
  proc.stdin.end(JSON.stringify(job) + '\n')
  return new Promise((resolve) => {
    const garde = setTimeout(() => proc.kill('SIGKILL'), 60000)
    proc.on('exit', (code, signal) => {
      clearTimeout(garde)
      const lignes = out.split('\n').filter(Boolean).map((l) => JSON.parse(l))
      resolve({ code, signal, lignes, err, fin: lignes.at(-1) || {} })
    })
  })
}

function chromesDe (racine) {
  return execFileSync('ps', ['-eo', 'pid=,args='], { encoding: 'utf8' }).split('\n')
    .filter((l) => /chrome/.test(l) && l.includes(`--user-data-dir=${racine}`))
}

const job = (d, mode, extra = {}) => ({
  service: 'flow', type: 'video', profil: d.profil, sortie: d.sortie, debug: d.debug,
  prompt: 'un caméléon marche sur une branche', url: `${base}?mode=${mode}&cle=${path.basename(d.racine)}`, ...extra
})
const envoisDe = (d) => envois.get(path.basename(d.racine)) || []
/** Job sur la NOUVELLE interface de la fausse page (&ui=2026) ; `params` : réglages de la page. */
const job2026 = (d, params = '', extra = {}) =>
  job(d, 'ok', { url: `${base}?ui=2026&cle=${path.basename(d.racine)}${params ? `&${params}` : ''}`, ...extra })
const signauxDe = (d) => signaux.get(path.basename(d.racine)) || []
const compteurDe = (d) => JSON.parse(fs.readFileSync(`${d.profil}.compteur.json`, 'utf8'))

/** Échec attendu : code, aucun Chrome laissé, verrou rendu ; rend le résultat. */
async function echoue (d, j, code, opts) {
  const r = await lancer(j, opts)
  assert.equal(r.code, 1, `${r.err}\n${JSON.stringify(r.fin)}`)
  assert.equal(r.fin.t, 'echec')
  assert.equal(r.fin.code, code, JSON.stringify(r.fin))
  assert.ok(!fs.existsSync(`${d.profil}.lock`), 'verrou rendu')
  assert.deepEqual(chromesDe(d.racine), [], 'aucun Chrome laissé')
  return r
}

// --------------------------------------------------------------- sans navigateur

test('verifierJob : defaut = Veo 3.1 Lite (id du catalogue), 8 s, 16:9, 720p, attente longue', () => {
  const d = dossiers()
  const j = verifierJob(job(d, 'ok'))
  assert.deepEqual([j.modele.id, j.modele.cout, j.duree, j.format, j.resolution, j.coutExact], ['lite', 10, 8, '16:9', '720p', 10])
  assert.equal(j.affichage, 'masquee', 'Flow n\'impose plus la fenêtre visible (correction du 28/10)')
  assert.equal(verifierJob(job(d, 'ok', { affichage: 'visible' })).affichage, 'visible')
  assert.equal(j.delaiMs, 900000, 'défaut 15 min')
  assert.equal(verifierJob(job(d, 'ok', { delai_s: 1800 })).delaiMs, 1800000, '30 min permises (B2 envoie jusqu\'à 1 800 s)')
  assert.equal(verifierJob(job(d, 'ok', { delai_s: 3600 })).delaiMs, 3600000)
  assert.equal(verifierJob(job(d, 'ok', { modele: 'fast' })).modele.cout, 20)
  const omni = verifierJob(job(d, 'ok', { modele: 'omni-flash', duree: 10, format: '9:16' }))
  assert.deepEqual([omni.modele.cout, omni.duree, omni.format], [15, 10, '9:16'])
  const omni6 = verifierJob(job(d, 'ok', { modele: 'omni-flash', duree: 6 }))
  assert.deepEqual([omni6.coutExact, omni6.modele.cout], [null, 12], 'Omni 6 s non relevé : au plus le coût du catalogue')
})

test('verifierJob : entrées refusées proprement', () => {
  const d = dossiers()
  const code = (j) => { try { verifierJob(j); return 'ok' } catch (e) { assert.ok(e instanceof Echec, String(e)); return e.code } }
  assert.equal(code(job(d, 'ok', { duree: 4 })), 'entree', 'Veo : 8 s seulement')
  assert.equal(code(job(d, 'ok', { modele: 'omni-flash', duree: 5 })), 'entree')
  assert.equal(code(job(d, 'ok', { format: '4:3' })), 'entree')
  assert.equal(code(job(d, 'ok', { modele: 'sora' })), 'entree')
  assert.equal(code(job(d, 'ok', { type: 'image' })), 'entree')
  assert.equal(code(job(d, 'ok', { derniere_image: 'oui' })), 'entree')
  assert.equal(code(job(d, 'ok', { prompt: 'x'.repeat(2001) })), 'entree')
  const a = image(d, 'a.png')
  assert.equal(code(job(d, 'ok', { fichiers: [a, a, a] })), 'fichiers')
  assert.equal(code(job(d, 'ok', { fichiers: [a], image_fin: a })), 'entree', 'jamais les deux formes')
})

test('images : début et fin vérifiées comme les pièces jointes (videos_ia.image_depart)', () => {
  const d = dossiers()
  const a = image(d, 'debut.png')
  const b = image(d, 'fin.png')
  const j = verifierJob(job(d, 'ok', { image_debut: a, image_fin: b }))
  assert.deepEqual(j.images, { debut: a, fin: b })
  assert.deepEqual(verifierJob(job(d, 'ok', { fichiers: [a, b] })).images, { debut: a, fin: b }, 'ancien champ fichiers de videos_ia.py')
  assert.deepEqual(verifierJob(job(d, 'ok', { fichiers: [a] })).images, { debut: a })
  const lu = lireImage(a)
  assert.equal(lu.mime, 'image/png')
  assert.ok(lu.octets.equals(PNG))
  const refus = (chemin) => { try { lireImage(chemin); return '' } catch (e) { assert.equal(e.code, 'fichiers'); return e.message } }
  assert.match(refus('relatif.png'), /chemin absolu/)
  assert.match(refus(`${a}\0x`), /chemin absolu/)
  assert.match(refus(path.join(d.racine, 'absente.png')), /introuvable/)
  assert.match(refus(image(d, 'texte.png', Buffer.from('<html>pas une image</html>'))), /PNG, JPEG ou WebP/)
  assert.match(refus(image(d, '.cache/cachee.png')), /caché/)
  assert.match(refus(image(d, 'mes-secrets.png')), /secret/)
  const lourde = Buffer.concat([PNG, Buffer.alloc(IMAGE_MAX)])
  assert.match(refus(image(d, 'lourde.png', lourde)), /trop lourde/)
  const dur = path.join(d.racine, 'dur.png')
  fs.linkSync(image(d, 'origine.png'), dur)
  assert.match(refus(dur), /pas un fichier ordinaire/, 'lien dur refusé')
  const dehors = fs.mkdtempSync(path.join(os.tmpdir(), 'video-dehors-'))
  try {
    fs.writeFileSync(path.join(dehors, 'x.png'), PNG)
    assert.match(refus(path.join(dehors, 'x.png')), /hors des pièces jointes/)
    fs.symlinkSync(path.join(dehors, 'x.png'), path.join(d.racine, 'lien.png'))
    assert.match(refus(path.join(d.racine, 'lien.png')), /hors des pièces jointes/, 'lien symbolique vers dehors')
  } finally { fs.rmSync(dehors, { recursive: true, force: true }) }
  // Dossiers privés d'Astrel sous le dossier des projets (05/11 : celui qui contient Astrel en tient lieu)
  const projets = fs.realpathSync(path.dirname(ASTREL))
  const r = [projets]
  assert.match(refusImage(path.join(ASTREL, 'config', 'x.png'), r), /privé/)
  assert.match(refusImage(path.join(ASTREL, 'runtime', 'imagegen', 'profils', 'a', 'x.png'), r), /privé/)
  assert.equal(refusImage(path.join(ASTREL, 'runtime', 'attachments', 'x.png'), r), '')
  assert.equal(refusImage(path.join(projets, 'Videos-IA', 'clip-derniere.png'), r), '', 'dernière image d\'un clip : réutilisable')
})

test('racines de test ignorées hors mode test (le cœur retire ASTREL_IMAGEGEN_RYTHME)', () => {
  assert.ok(racinesImages().includes(fs.realpathSync(RACINE_TESTS)))
  // 05/11 : dossier des projets donné par le cœur (ASTREL_WORKDIR, ia_web.env_worker), jamais écrit en dur
  const projets = fs.realpathSync(path.dirname(ASTREL))
  const env = { ...process.env, ASTREL_VIDEO_RACINES: RACINE_TESTS, ASTREL_WORKDIR: projets }
  delete env.ASTREL_IMAGEGEN_RYTHME
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', 'const v = await import("./video.mjs"); console.log(JSON.stringify(v.racinesImages()))'], { cwd: DIR, env, encoding: 'utf8' })
  const lues = JSON.parse(r.stdout)
  assert.ok(!lues.includes(fs.realpathSync(RACINE_TESTS)), r.stdout)
  assert.ok(lues.includes(projets), r.stdout)
})

test('textes de Flow : activité, crédits, pointe (avant envoi), refus (après), reprise HH:MM locale', () => {
  assert.equal(classerPage('We noticed some unusual activity. Please try again later.').code, 'activite')
  assert.equal(classerPage('You don\'t have enough credits').code, 'credits')
  const p = classerPage('We\'re experiencing high demand right now. Please try again after 5:00 PM.')
  assert.deepEqual([p.code, p.reprise], ['pointe', '17:00'])
  assert.equal(classerPage('Flow est en forte demande, réessayez à 17h30').reprise, '17:30')
  const t0 = new Date(2026, 9, 28, 15, 10, 30).getTime() // arrondi à la minute suivante
  assert.equal(classerPage('High demand: try again in 2 hours', { maintenant: t0 }).reprise, '17:11')
  assert.equal(classerPage('High demand right now', { maintenant: t0 }).reprise, '', 'jamais devinée')
  assert.equal(classerPage('We couldn\'t generate your video'), null, 'refus seulement après l\'envoi')
  assert.equal(classerPage('We couldn\'t generate your video', { apres: true }).code, 'refus')
  assert.equal(classerPage('This site is protected by reCAPTCHA. Privacy Policy Terms'), null)
  assert.equal(reprise('rien'), '')
})

test('textes FIXES de Flow (relecture B1) : ne bloquent pas l\'envoi ; les vrais messages, si', () => {
  for (const t of ['Upgrade for higher generation limits', 'Peak time pricing', 'Acheter plus de crédits', 'Tarif heures de pointe', 'Try again at 5 PM']) {
    assert.equal(classerPage(t), null, t)
  }
  assert.equal(classerPage('You\'ve reached your daily limit').code, 'quota')
  assert.equal(classerPage('Daily generation limit reached').code, 'quota')
  assert.equal(classerPage('Quota exceeded for this account').code, 'quota') // relevé B1 : formulations à prévoir
  assert.equal(classerPage('Rate limit exceeded, try again in 10 minutes').code, 'quota')
  assert.equal(classerPage('Vous n\'avez plus de crédits').code, 'credits')
  assert.equal(classerPage('Peak hours: please try again later').code, 'pointe')
  assert.equal(classerPage('Flow est en heures de pointe').code, 'pointe')
})

test('APRÈS l\'envoi : quota, crédits, pointe lus seulement dans alert/status/dialog, jamais dans un écho du prompt', () => {
  const prompt = 'un panneau "not enough credits", daily limit reached, high demand ; un caméléon'
  const echo = 'Generating: un panneau ”NOT enough   credits”, Daily limit reached, high demand…'
  assert.equal(classerPage(echo, { apres: true, prompt, alertes: echo }), null, 'écho retouché du prompt')
  assert.equal(classerPage('Flow is experiencing high demand', { apres: true, prompt }), null, 'hors rôle d\'alerte')
  assert.equal(classerPage('You have no credits left', { apres: true, prompt }), null)
  const vrai = classerPage('x', { apres: true, prompt, alertes: 'Flow is at capacity. Try again after 5:00 PM.' })
  assert.deepEqual([vrai.code, vrai.reprise], ['pointe', '17:00'])
  assert.equal(classerPage('x', { apres: true, prompt, alertes: 'You\'ve reached your daily limit' }).code, 'quota')
  // activité et refus : toute la page nouvelle, écho du prompt exclu aussi
  assert.equal(classerPage('We noticed unusual activity', { apres: true, prompt }).code, 'activite')
  assert.equal(classerPage('Prompt : unusual activity', { apres: true, prompt: 'une unusual activity' }), null)
  assert.equal(classerPage('We couldn\'t generate your video', { apres: true, prompt }).code, 'refus')
})

test('reprise : mot entier, heure sans minutes, fuseau CONVERTI en heure locale, inconnu = vide', () => {
  const t0 = Date.UTC(2026, 9, 28, 14, 10) // 28/10 : Paris en UTC+1, Los Angeles encore en UTC-7
  const local = (ms) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
  const loc = (h, m) => local(new Date(2026, 9, 28, h, m).getTime())
  assert.equal(reprise('Try again at 5 PM', t0), loc(17, 0))
  assert.equal(reprise('try again at 5 p.m.', t0), loc(17, 0))
  assert.equal(reprise('at 12 AM', t0), '00:00')
  assert.equal(reprise('réessayez à 17h', t0), '17:00')
  assert.equal(reprise('format 16:9 high demand', t0), '', 'pas « at 16:9 » dans « format »')
  assert.equal(reprise('Note that 5 PM slots are busy', t0), '', 'pas « at 5 PM » dans « that »')
  assert.equal(reprise('try again at 5', t0), '', 'heure nue : pas une heure')
  assert.equal(reprise('after 5:00 PM PT', t0), local(Date.UTC(2026, 9, 29, 0, 0)), '17 h PDT = 0 h UTC le 29')
  assert.equal(reprise('after 5 PM Pacific Time', t0), local(Date.UTC(2026, 9, 29, 0, 0)))
  assert.equal(reprise('after 9:30 AM ET', t0 - 3 * 3600000), local(Date.UTC(2026, 9, 28, 13, 30)))
  assert.equal(reprise('after 17:00 UTC', t0), local(Date.UTC(2026, 9, 28, 17, 0)))
  assert.equal(reprise('after 17:00 UTC+2', t0), local(Date.UTC(2026, 9, 28, 15, 0)))
  assert.equal(reprise('after 5:00 PM XYZ', t0), '', 'fuseau inconnu : jamais lu comme local')
  assert.equal(reprise('after 5:00 PM. Please wait', t0), loc(17, 0), 'un mot ordinaire n\'est pas un fuseau')
})

test('vidéo de RÉSULTAT : nouvelle, affichée, pas une boucle d\'attente plus courte que la moitié du clip', () => {
  const avant = new Set(['/ancienne.mp4'])
  assert.equal(estResultat({ src: '/clip.mp4', visible: true, duree: null }, avant, 8), true, 'durée inconnue : acceptée')
  assert.equal(estResultat({ src: '/clip.mp4', visible: true, duree: 8 }, avant, 8), true)
  assert.equal(estResultat({ src: '/clip.mp4', visible: true, duree: 4 }, avant, 4), true)
  assert.equal(estResultat({ src: '/attente.webm', visible: true, duree: 1.2 }, avant, 8), false)
  assert.equal(estResultat({ src: '/clip.mp4', visible: false, duree: null }, avant, 8), false, 'masquée')
  assert.equal(estResultat({ src: '/ancienne.mp4', visible: true, duree: 8 }, avant, 8), false)
  assert.equal(estResultat({ src: '', visible: true, duree: 8 }, avant, 8), false)
})

test('image : tube nommé (FIFO) refusé SANS l\'ouvrir (le worker ne bloque pas)', () => {
  const d = dossiers()
  const f = path.join(d.racine, 'tube.png')
  execFileSync('mkfifo', [f])
  const r = spawnSync(process.execPath, ['--input-type=module', '-e',
    `const v = await import('./video.mjs'); try { v.lireImage(${JSON.stringify(f)}); console.log('lu') } catch (e) { console.log(e.code, e.message) }`],
  { cwd: DIR, env: { ...process.env }, encoding: 'utf8', timeout: 5000 })
  assert.equal(r.signal, null, 'bloqué sur le tube')
  assert.match(r.stdout, /^fichiers .*pas un fichier ordinaire/, r.stdout + r.stderr)
})

test('coût annoncé : lu, absent (null), plusieurs différents (NaN)', () => {
  assert.equal(lireCout('Generating will use 10 credits'), 10)
  assert.equal(lireCout('La génération va utiliser 12 crédits'), 12)
  assert.equal(lireCout('Generating will use 10 credits … Generating will use 10 credits'), 10)
  assert.equal(lireCout('50 credits'), null)
  assert.ok(Number.isNaN(lireCout('Generating will use 10 credits / Generating will use 40 credits')))
})

test('fichier vidéo : signatures mp4/webm, durée lue dans mvhd (v0 et v1), tronqué = null', () => {
  const sig = (b) => SIGNATURES_VIDEO.find(([, ok]) => ok(b.subarray(0, 16)))?.[0]
  assert.equal(sig(mp4()), 'mp4')
  assert.equal(sig(EBML('webm')), 'webm')
  assert.equal(sig(Buffer.from('<html>non</html>')), undefined)
  const qt = mp4(); qt.write('qt  ', 8, 'latin1')
  assert.equal(sig(qt), undefined, 'QuickTime refusé comme par medias.sniff')
  const d = dossiers()
  const f = (nom, b) => { const p = path.join(d.racine, nom); fs.writeFileSync(p, b); return p }
  assert.equal(dureeMp4(f('a.mp4', mp4(8))), 8)
  assert.equal(dureeMp4(f('b.mp4', mp4(10, 1))), 10)
  assert.equal(dureeMp4(f('c.mp4', mp4(8).subarray(0, 40))), null)
  assert.equal(dureeMp4(f('d.webm', EBML('webm'))), null)
})

test('quality (100 crédits) : plafond de 50, refusé sans ouvrir Chrome', async () => {
  const d = dossiers()
  const r = await lancer(job(d, 'ok', { modele: 'quality' }))
  assert.equal(r.fin.code, 'plafond', JSON.stringify(r.fin))
  assert.ok(!r.lignes.some((l) => l.etape === 'ouverture'))
})

// --------------------------------------------------------------- dernière image d'un clip

const avecPython = fs.existsSync(PYTHON) && spawnSync(PYTHON, ['-P', '-c', 'import av, PIL'], { timeout: 20000 }).status === 0

/** Clip de 30 images rouges dont la DERNIÈRE est bleue (h264 avec images B, ou vp8). */
function clip (fichier, codec) {
  const r = spawnSync(PYTHON, ['-P', '-c', `
import sys, av
from PIL import Image
c = av.open(sys.argv[1], 'w')
s = c.add_stream(sys.argv[2], rate=24)
s.width, s.height, s.pix_fmt = 64, 48, 'yuv420p'
for i in range(30):
    f = av.VideoFrame.from_image(Image.new('RGB', (64, 48), (0, 0, 255) if i == 29 else (255, 0, 0)))
    for p in s.encode(f): c.mux(p)
for p in s.encode(): c.mux(p)
c.close()
`, fichier, codec], { encoding: 'utf8', timeout: 60000 })
  assert.equal(r.status, 0, r.stderr)
  return fichier
}
const couleur = (png) => JSON.parse(spawnSync(PYTHON, ['-P', '-c', 'import sys; from PIL import Image; print(list(Image.open(sys.argv[1]).convert("RGB").getpixel((32, 24))))', png], { encoding: 'utf8' }).stdout)

test('dernière image : PyAV sur un mp4 h264, c\'est la dernière image décodée (bleue)', { skip: !avecPython && 'venv d\'Astrel sans PyAV' }, async () => {
  const d = dossiers()
  const c = clip(path.join(d.racine, 'clip.mp4'), 'libx264')
  const r = await derniereImage(c, { moyen: 'pyav' })
  assert.deepEqual(r, { fichier: path.join(d.racine, 'clip-derniere.png'), via: 'pyav' })
  const [rouge, , bleu] = couleur(r.fichier)
  assert.ok(bleu > 200 && rouge < 60, `pixel ${couleur(r.fichier)}`)
  assert.deepEqual(fs.readdirSync(d.racine).filter((f) => f.startsWith('.')), [], 'aucun fichier temporaire laissé')
  // Réutilisable comme image de début du clip suivant (chaînage)
  assert.equal(lireImage(r.fichier).mime, 'image/png')
})

test('dernière image : ffmpeg (celui de Playwright, vp8) sur un webm', { skip: (!avecPython || !fs.existsSync(FFMPEG_PW)) && 'PyAV ou ffmpeg de Playwright absent' }, async () => {
  const d = dossiers()
  const c = clip(path.join(d.racine, 'clip.webm'), 'libvpx')
  const avant = process.env.ASTREL_FFMPEG
  process.env.ASTREL_FFMPEG = FFMPEG_PW
  try {
    const r = await derniereImage(c, { moyen: 'ffmpeg', sortie: path.join(d.racine, 'fin.png') })
    assert.equal(r.via, 'ffmpeg')
    const [rouge, , bleu] = couleur(r.fichier)
    assert.ok(bleu > 200 && rouge < 60, `pixel ${couleur(r.fichier)}`)
  } finally {
    if (avant === undefined) delete process.env.ASTREL_FFMPEG
    else process.env.ASTREL_FFMPEG = avant
  }
})

test('dernière image : clip absent ou illisible = erreur claire, rien laissé ; ligne de commande', { skip: !avecPython && 'venv d\'Astrel sans PyAV' }, async () => {
  const d = dossiers()
  await assert.rejects(derniereImage(path.join(d.racine, 'absent.mp4')), /introuvable/)
  const faux = path.join(d.racine, 'faux.mp4')
  fs.writeFileSync(faux, mp4(8))
  await assert.rejects(derniereImage(faux, { moyen: 'pyav' }), /impossible à extraire/)
  assert.deepEqual(fs.readdirSync(d.racine).sort(), ['faux.mp4'])
  const c = clip(path.join(d.racine, 'cli.mp4'), 'mpeg4')
  const r = spawnSync(process.execPath, ['derniere-image.mjs', c], { cwd: DIR, encoding: 'utf8', env: { ...process.env, PATH: '/usr/bin:/bin' }, timeout: 60000 })
  assert.equal(r.status, 0, r.stdout + r.stderr)
  assert.equal(JSON.parse(r.stdout).fichier, path.join(d.racine, 'cli-derniere.png'))
})

// --------------------------------------------------------------- worker sur la fausse page (Chrome sans fenêtre)

test('Frames : Veo 3.1 Lite 8 s 9:16 avec images de début et de fin ; un seul envoi ; vidéo enregistrée', async () => {
  const d = dossiers()
  const a = image(d, 'debut.png')
  const b = image(d, 'fin.png')
  const r = await lancer(job(d, 'ok', { image_debut: a, image_fin: b, format: '9:16', derniere_image: true }))
  assert.equal(r.code, 0, `${r.err}\n${JSON.stringify(r.fin)}`)
  const f = r.fin
  assert.equal(f.t, 'fini')
  assert.equal(typeof f.fichier, 'string')
  assert.ok(f.fichier.startsWith(d.sortie + path.sep) && f.fichier.endsWith('.mp4'), f.fichier)
  assert.ok(fs.statSync(f.fichier).size > 0)
  assert.deepEqual([f.type, f.modele_utilise, f.duree, f.duree_reelle, f.format, f.resolution, f.cout_annonce, f.credits_restants],
    ['mp4', 'lite', 8, 8, '9:16', '720p', 10, 50])
  assert.deepEqual(f.images, { debut: 'debut.png', fin: 'fin.png' })
  assert.deepEqual(f.fichiers, [{ fichier: f.fichier, nom: f.nom }])
  assert.match(f.derniere_image_erreur || '', /impossible/, 'le faux mp4 n\'a pas d\'image : erreur notée, vidéo gardée')
  assert.deepEqual(r.lignes.filter((l) => l.t === 'etape').map((l) => l.etape), ['ouverture', 'saisie', 'generation', 'telechargement'])
  assert.equal(r.lignes.filter((l) => l.t === 'envoye').length, 1)
  const e = envoisDe(d)
  assert.equal(e.length, 1, 'UN clic d\'envoi')
  assert.deepEqual([e[0].modele, e[0].format, e[0].nombre, e[0].start, e[0].end, e[0].video, e[0].frames, e[0].annonce],
    ['Veo 3.1 - Lite', '9:16', 'x1', 'debut.png', 'fin.png', true, true, false])
  assert.equal(e[0].prompt, 'un caméléon marche sur une branche')
  assert.equal(compteurDe(d).services.flow.n, 10)
  assert.deepEqual(chromesDe(d.racine), [])
})

test('Omni 1.1 Flash 10 s : 720p et durée réglés dans les menus, coût 15 vérifié', async () => {
  const d = dossiers()
  const r = await lancer(job(d, 'ok', { modele: 'omni-flash', duree: 10 }))
  assert.equal(r.code, 0, `${r.err}\n${JSON.stringify(r.fin)}`)
  assert.equal(r.fin.modele_utilise, 'omni-flash')
  const [e] = envoisDe(d)
  assert.deepEqual([e.modele, e.res, e.duree, e.cout], ['Omni 1.1 Flash', '720p', '10s', 15])
  assert.equal(compteurDe(d).services.flow.n, 15)
})

test('crédits insuffisants (bouton orange) : arrêt AVANT l\'envoi, rien compté', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'credits'), 'credits')
  assert.deepEqual(envoisDe(d), [])
  assert.ok(!r.lignes.some((l) => l.t === 'envoye'))
  assert.ok(r.fin.capture && fs.existsSync(r.fin.capture))
})

test('solde affiché plus bas que le coût, bouton PAS orange : credits avant l\'envoi, rien envoyé', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'solde_bas'), 'credits')
  assert.match(r.fin.message, /5 restants, 10 demandés/)
  assert.deepEqual(envoisDe(d), [])
})

test('coût annoncé différent de l\'attendu : refus, rien envoyé', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'cout'), 'page')
  assert.match(r.fin.message, /coût annoncé 15 crédits, attendu 10/)
  assert.deepEqual(envoisDe(d), [])
})

test('créneau de pointe affiché AVANT l\'envoi : code pointe, reprise HH:MM, aucun envoi', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'pointe'), 'pointe')
  assert.equal(r.fin.reprise, '17:00')
  assert.deepEqual(envoisDe(d), [])
  assert.ok(!r.lignes.some((l) => l.t === 'envoye'))
})

test('créneau de pointe APRÈS l\'envoi : pointe, un seul envoi (le cœur rend les crédits)', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'pointe_apres'), 'pointe')
  assert.equal(r.fin.reprise, '17:00')
  assert.equal(envoisDe(d).length, 1)
  assert.equal(r.lignes.filter((l) => l.t === 'envoye').length, 1)
})

test('activité inhabituelle avant ou après l\'envoi : activite, jamais de second envoi', async () => {
  const d1 = dossiers()
  await echoue(d1, job(d1, 'activite_avant'), 'activite')
  assert.deepEqual(envoisDe(d1), [])
  const d2 = dossiers()
  const r = await echoue(d2, job(d2, 'activite'), 'activite')
  assert.equal(envoisDe(d2).length, 1)
  assert.match(r.fin.message, /unusual activity/)
})

test('écho RETOUCHÉ du prompt (mots-pièges) dans l\'état de génération : aucun faux quota/crédits/pointe, la vidéo arrive', async () => {
  const d = dossiers()
  const prompt = 'un panneau "not enough credits", daily limit reached, high demand, try again after 5 PM ; un caméléon sur une branche'
  const r = await lancer(job(d, 'echo', { prompt }))
  assert.equal(r.code, 0, `${r.err}\n${JSON.stringify(r.fin)}`)
  assert.equal(r.fin.t, 'fini')
  assert.equal(envoisDe(d).length, 1)
  assert.equal(r.lignes.filter((l) => l.t === 'envoye').length, 1)
})

test('« high demand » hors de tout rôle d\'alerte après l\'envoi : pas de pointe (pas de changement de compte)', async () => {
  const d = dossiers()
  const r = await lancer(job(d, 'pointe_carte'))
  assert.equal(r.code, 0, `${r.err}\n${JSON.stringify(r.fin)}`)
  assert.equal(envoisDe(d).length, 1)
})

test('clic d\'envoi tombé sur un calque : page, AUCUN envoye compté, aucun second clic', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'clic_rate'), 'page')
  assert.match(r.fin.message, /clic d.envoi sans effet/)
  assert.ok(!r.lignes.some((l) => l.t === 'envoye'), 'rien compté')
  assert.deepEqual(envoisDe(d), [], 'le bouton n\'a rien reçu')
  assert.ok(!fs.existsSync(`${d.profil}.compteur.json`) || !(compteurDe(d).services?.flow?.n), 'compteur intact')
})

test('téléchargement en 404 : code page (pas interne), rien dans la sortie', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'dl404'), 'page')
  assert.match(r.fin.message, /téléchargement impossible/)
  assert.deepEqual(fs.existsSync(d.sortie) ? fs.readdirSync(d.sortie) : [], [])
})

test('animation d\'attente (boucle de 1,25 s, affichée) avant le clip : pas prise pour le résultat', { skip: !avecPython && 'venv d\'Astrel sans PyAV' }, async () => {
  const d = dossiers()
  CHARGEMENT.webm = fs.readFileSync(clip(path.join(d.racine, 'attente.webm'), 'libvpx'))
  const r = await lancer(job(d, 'chargement'))
  assert.equal(r.code, 0, `${r.err}\n${JSON.stringify(r.fin)}`)
  assert.deepEqual([r.fin.type, r.fin.duree_reelle], ['mp4', 8], 'le clip, pas l\'animation (webm)')
  assert.equal(envoisDe(d).length, 1)
})

test('refus de contenu après l\'envoi : refus, texte du site', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'refus'), 'refus')
  assert.match(r.fin.message, /couldn.t generate/)
  assert.equal(envoisDe(d).length, 1)
})

test('défi reCAPTCHA VISIBLE : captcha, capture, rien tapé ni envoyé ; le badge invisible seul ne compte pas', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'captcha'), 'captcha')
  assert.ok(r.fin.capture && fs.existsSync(r.fin.capture))
  assert.deepEqual(envoisDe(d), [])
})

test('défi apparu APRÈS l\'arrivée, par-dessus la barre : captcha (pas « page changée »), rien envoyé', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'captcha_tard'), 'captcha')
  assert.ok(r.fin.capture && fs.existsSync(r.fin.capture))
  assert.deepEqual(envoisDe(d), [])
})

test('session perdue, page changée (capture), bouton d\'envoi inactif', async () => {
  const d1 = dossiers()
  await echoue(d1, job(d1, 'session'), 'session')
  const d2 = dossiers()
  const r = await echoue(d2, job(d2, 'change'), 'page')
  assert.ok(r.fin.capture && fs.existsSync(r.fin.capture), 'capture de débogage')
  const d3 = dossiers()
  const r3 = await echoue(d3, job(d3, 'fermee'), 'page')
  assert.match(r3.fin.message, /inactif/)
  assert.deepEqual(envoisDe(d3), [])
})

test('délai dépassé : delai, un seul envoi, aucune relance', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'lent', { delai_s: 5 }), 'delai')
  assert.equal(envoisDe(d).length, 1)
  assert.ok(r.fin.capture)
})

test('confirmation « Generate » et recadrage d\'image : la même demande, un seul envoi', async () => {
  const d1 = dossiers()
  const r1 = await lancer(job(d1, 'confirmer'))
  assert.equal(r1.code, 0, `${r1.err}\n${JSON.stringify(r1.fin)}`)
  assert.equal(envoisDe(d1).length, 1)
  const d2 = dossiers()
  const r2 = await lancer(job(d2, 'recadrage', { image_debut: image(d2, 'a.png') }))
  assert.equal(r2.code, 0, `${r2.err}\n${JSON.stringify(r2.fin)}`)
  assert.equal(envoisDe(d2)[0].start, 'a.png')
})

test('téléchargement : webm accepté, matroska et non-vidéo refusés sans fichier laissé', async () => {
  const d1 = dossiers()
  const r1 = await lancer(job(d1, 'webm'))
  assert.equal(r1.code, 0, `${r1.err}\n${JSON.stringify(r1.fin)}`)
  assert.deepEqual([r1.fin.type, r1.fin.duree_reelle], ['webm', null])
  assert.ok(r1.fin.fichier.endsWith('.webm'))
  for (const mode of ['matroska', 'pasvideo']) {
    const d = dossiers()
    await echoue(d, job(d, mode), 'page')
    assert.deepEqual(fs.existsSync(d.sortie) ? fs.readdirSync(d.sortie) : [], [], `${mode} : rien dans la sortie`)
  }
})

test('image REVÉRIFIÉE au dépôt : remplacée après l\'entrée, refusée, rien envoyé', async () => {
  const d = dossiers()
  const a = image(d, 'debut.png')
  const r = await echoue(d, job(d, 'ok', { image_debut: a }), 'fichiers', {
    surLigne: (l) => { if (l.etape === 'ouverture') fs.writeFileSync(a, '<html>plus une image</html>') }
  })
  assert.match(r.fin.message, /image de début refusée/)
  assert.deepEqual(envoisDe(d), [])
})

// --------------------------------------------------------------- NOUVELLE interface de Flow (&ui=2026, relevé du 30/10)

/** Signaux d'une séance sur la nouvelle interface : aucun geste interdit, la liste des autres. */
function sansGesteInterdit (d) {
  const s = signauxDe(d)
  assert.deepEqual(s.filter((x) => x.startsWith('interdit:')), [], 'geste interdit (bandeau, Download project, roue Settings, Uploads…)')
  return s
}

test('NOUVELLE interface : Lite 9:16 x1, début ET fin (la case End reçoit l\'image de FIN), attestation, 720p ; ni bandeau ni pièges', async () => {
  const d = dossiers()
  const a = image(d, 'debut.png')
  const b = image(d, 'fin.png')
  // Compte neuf : projet en Image (Nano Banana), x2 ; modèle vidéo de départ Quality (le menu doit servir)
  const r = await lancer(job2026(d, 'depart=quality', { image_debut: a, image_fin: b, format: '9:16' }))
  assert.equal(r.code, 0, `${r.err}\n${JSON.stringify(r.fin)}`)
  const f = r.fin
  assert.deepEqual([f.t, f.type, f.modele_utilise, f.duree, f.duree_reelle, f.format, f.resolution, f.cout_annonce],
    ['fini', 'mp4', 'lite', 8, 8, '9:16', '720p', 10], 'coût déduit des réglages lus (Flow ne l\'affiche plus)')
  assert.equal(f.credits_restants, undefined, 'aucun solde affiché')
  assert.ok(f.fichier.startsWith(d.sortie + path.sep) && fs.statSync(f.fichier).size > 0, f.fichier)
  assert.deepEqual(f.images, { debut: 'debut.png', fin: 'fin.png' })
  assert.equal(r.lignes.filter((l) => l.t === 'envoye').length, 1)
  const e = envoisDe(d)
  assert.equal(e.length, 1, 'UN clic sur « Start generation »')
  assert.deepEqual([e[0].type, e[0].sousmode, e[0].modele, e[0].format, e[0].nombre, e[0].duree, e[0].reglages],
    ['video', 'frames', 'Veo 3.1 - Lite', '9:16', 'x1', '8s', 'Video · 720p · 8s crop_9_16 x1'])
  assert.equal(e[0].start, 'debut.png')
  assert.equal(e[0].end, 'fin.png', 'End = image de fin seule (l\'image de début restait choisie dans la bibliothèque)')
  assert.equal(e[0].prompt, 'un caméléon marche sur une branche')
  const s = sansGesteInterdit(d)
  assert.deepEqual(s.filter((x) => x === 'attestation'), ['attestation'], '« I agree » une fois (1er dépôt du compte)')
  assert.deepEqual(s.filter((x) => x.startsWith('telechargement:')), ['telechargement:720p'], '« 720p Original size », jamais GIF ni agrandi')
  assert.equal(compteurDe(d).services.flow.n, 10)
  assert.deepEqual(chromesDe(d.racine), [])
})

test('NOUVELLE interface : x2 resté (radio x1 sans effet) ou durée lue fausse : échec page AVANT l\'envoi', async () => {
  const d1 = dossiers()
  const r1 = await echoue(d1, job2026(d1, 'mode=x2_bloque&video=1'), 'page')
  assert.match(r1.fin.message, /réglages lus « Video · 720p · 8s crop_16_9 x2 ».*rien n'est envoyé/)
  assert.deepEqual(envoisDe(d1), [])
  assert.ok(!r1.lignes.some((l) => l.t === 'envoye'))
  sansGesteInterdit(d1)
  const d2 = dossiers()
  const r2 = await echoue(d2, job2026(d2, 'duree=4s'), 'page')
  assert.match(r2.fin.message, /réglages lus « Video · 720p · 4s crop_16_9 x1 ».*rien n'est envoyé/)
  assert.deepEqual(envoisDe(d2), [])
  sansGesteInterdit(d2)
})

test('NOUVELLE interface : panneau refermé par le choix du modèle : modèle RELU ; pas pris = page, rien envoyé', async () => {
  const d1 = dossiers()
  const r1 = await lancer(job2026(d1, 'depart=fast&modele_ferme=1&attest=0', { image_debut: image(d1, 'seule.png') }))
  assert.equal(r1.code, 0, `${r1.err}\n${JSON.stringify(r1.fin)}`)
  assert.deepEqual([envoisDe(d1)[0].modele, envoisDe(d1)[0].start, envoisDe(d1)[0].end], ['Veo 3.1 - Lite', 'seule.png', ''])
  assert.ok(!signauxDe(d1).includes('attestation'), 'attestation déjà acceptée sur ce compte : aucune fenêtre')
  sansGesteInterdit(d1)
  // Le modèle ne change pas (Quality, 100 crédits) : jamais envoyé en comptant 10
  const d2 = dossiers()
  const r2 = await echoue(d2, job2026(d2, 'depart=quality&modele_refuse=1'), 'page')
  assert.match(r2.fin.message, /modèle lite introuvable \(la page a pris quality\)/)
  assert.deepEqual(envoisDe(d2), [])
})

// --------------------------------------------------------------- mode visible, sous Xvfb

function xvfb () {
  return new Promise((resolve) => {
    let proc
    try { proc = spawn('Xvfb', ['-displayfd', '1', '-screen', '0', '1366x900x24', '-nolisten', 'tcp'], { stdio: ['ignore', 'pipe', 'ignore'] }) } catch { resolve(null); return }
    let out = ''
    proc.on('error', () => resolve(null))
    proc.stdout.on('data', (b) => { out += b; if (/\d+\n/.test(out)) resolve({ proc, display: `:${out.trim()}` }) })
    setTimeout(() => resolve(null), 5000)
  })
}

test('mode VISIBLE demandé par le job : fenêtre ordinaire (pas --headless), sous Xvfb sans WAYLAND_DISPLAY', async (t) => {
  const x = await xvfb()
  if (!x) { t.skip('Xvfb absent'); return }
  try {
    const d = dossiers()
    let lignesChrome = []
    const r = await lancer(job(d, 'ok', { affichage: 'visible' }), {
      env: { DISPLAY: x.display },
      sansEnv: ['WAYLAND_DISPLAY', 'XDG_SESSION_TYPE'],
      surLigne: (l) => { if (l.etape === 'generation') lignesChrome = chromesDe(d.racine).filter((c) => !/--type=/.test(c)) }
    })
    assert.equal(r.code, 0, `${r.err}\n${JSON.stringify(r.fin)}`)
    assert.equal(lignesChrome.length, 1, 'un Chrome principal')
    assert.ok(!/--headless/.test(lignesChrome[0]), 'fenêtre ordinaire')
    assert.ok(!/--ozone-platform=wayland/.test(lignesChrome[0]), 'pas sur l\'écran Wayland de l\'utilisateur')
    assert.equal(envoisDe(d).length, 1)
  } finally {
    x.proc.kill('SIGTERM') // par PID : le Xvfb de ce test seulement
  }
})
