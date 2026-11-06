// Worker d'images lancé pour de vrai, Chrome SANS FENÊTRE, sur une FAUSSE page Gemini
// servie en local (plan P14, lot 1) : `cd imagegen && node --test`. Aucun site réel.
// Tous les essais qui lancent Chrome sont dans CE fichier (exécutés l'un après l'autre) : un
// seul Chrome sans fenêtre à la fois ; test/noyau.test.mjs n'en lance aucun.
import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { MOTEURS, classer } from '../moteurs.mjs'

// Rythme de test AVANT de charger humain.mjs (lu à l'import) : les séances lancées dans ce
// processus (faux site, plus bas) n'attendent pas 2 à 5 s par geste et ignorent la nuit.
process.env.ASTREL_IMAGEGEN_RYTHME = 'test'
const { Echec, seance, verifierBase } = await import('../noyau.mjs')
const { compteur, verifierJob } = await import('../worker.mjs')
const { dossierProjets } = await import('../pieces.mjs')
const PROJETS = dossierProjets() // 05/11 : le dossier des projets de la machine, jamais écrit en dur

const DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const PAGE = fs.readFileSync(path.join(DIR, 'test', 'fausse-gemini.html'))
// P40 A3 : fausses pages ChatGPT et Bing, servies sous /chatgpt et /bing
const PAGES = {
  '/chatgpt': fs.readFileSync(path.join(DIR, 'test', 'fausse-chatgpt.html')),
  '/bing': fs.readFileSync(path.join(DIR, 'test', 'fausse-bing.html'))
}
// PNG 1×1 valide
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')
// Relevés envoyés par les fausses pages (POST /releve) : pièces reçues, choix des cookies
const releves = []

let serveur
let base
before(async () => {
  serveur = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x')
    if (req.method === 'POST' && url.pathname === '/releve') {
      let corps = ''
      req.on('data', (d) => { corps += d })
      req.on('end', () => { try { releves.push(JSON.parse(corps)) } catch {} res.end('ok') })
      return
    }
    if (url.pathname === '/image.png') { res.writeHead(200, { 'content-type': 'image/png' }); res.end(PNG); return }
    if (url.pathname === '/ancienne.png') { res.writeHead(200, { 'content-type': 'image/png' }); res.end(Buffer.concat([PNG, Buffer.from('ancienne')])); return }
    if (url.pathname === '/pas-une-image.png') { res.writeHead(200, { 'content-type': 'image/png' }); res.end('<html>non</html>'); return }
    // Images « générées » de la fausse Bing : un PNG différent par variante (octets en queue)
    const oig = /^\/th\/id\/OIG1\.(\w+)$/.exec(url.pathname)
    if (oig) {
      // Taille réelle (sans paramètres) redirigée : vers un autre hôte (refusé) ou vers v3 ici (permis)
      if (oig[1] === 'redir' && !url.search) { res.writeHead(302, { location: `${etrangerBase}th/id/OIG1.v1` }); res.end(); return }
      if (oig[1] === 'refusee' && !url.search) { res.writeHead(403); res.end('interdit'); return }
      if (oig[1] === 'saute' && !url.search) { res.writeHead(302, { location: '/th/id/OIG1.v3' }); res.end(); return }
      if (oig[1] === 'casse') { res.writeHead(200, { 'content-type': 'image/png' }); res.end('<html>non</html>'); return }
      res.writeHead(200, { 'content-type': 'image/png' })
      res.end(Buffer.concat([PNG, Buffer.from(oig[1])]))
      return
    }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    res.end(PAGES[url.pathname] || PAGE)
  })
  await new Promise((resolve) => serveur.listen(0, '127.0.0.1', resolve))
  base = `http://127.0.0.1:${serveur.address().port}/`
  // Hôte ÉTRANGER (127.0.0.2) : compte chaque requête reçue et ses cookies
  etranger = http.createServer((req, res) => {
    etrangerRecues.push({ url: req.url, cookie: req.headers.cookie ?? null })
    res.writeHead(200, { 'content-type': 'image/png' })
    res.end(Buffer.concat([PNG, Buffer.from('etranger')]))
  })
  await new Promise((resolve) => etranger.listen(0, '127.0.0.2', resolve))
  etrangerBase = `http://127.0.0.2:${etranger.address().port}/`
})
let etranger
let etrangerBase = ''
const etrangerRecues = []
const racines = []
after(() => {
  serveur.close()
  etranger?.close()
  for (const r of racines) fs.rmSync(r, { recursive: true, force: true })
})

function dossiers (alias = 'essai') {
  const racine = fs.mkdtempSync(path.join(os.tmpdir(), 'imagegen-test-'))
  racines.push(racine)
  return { racine, profil: path.join(racine, 'profils', alias), sortie: path.join(racine, 'sortie'), debug: path.join(racine, 'debug') }
}

// `surLigne(ligne, proc)` : appelé à chaque ligne JSON du worker (coupures en cours de route).
function lancer (job, { env = {}, surLigne = null } = {}) {
  const proc = spawn(process.execPath, ['worker.mjs'], {
    cwd: DIR, env: { ...process.env, ASTREL_IMAGEGEN_RYTHME: 'test', ...env }, stdio: ['pipe', 'pipe', 'pipe']
  })
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
    const garde = setTimeout(() => proc.kill('SIGKILL'), 40000)
    proc.on('exit', (code, signal) => {
      clearTimeout(garde)
      const lignes = out.split('\n').filter(Boolean).map((l) => JSON.parse(l))
      resolve({ code, signal, lignes, err, fin: lignes.at(-1) || {}, sortiLe: Date.now() })
    })
  })
}

// Processus Chrome lancés sur un profil de test (repérés par leur --user-data-dir), par PID.
function chromesDe (racine, { principal = false } = {}) {
  return execFileSync('ps', ['-eo', 'pid=,args='], { encoding: 'utf8' }).split('\n')
    .filter((l) => /chrome/.test(l) && l.includes(`--user-data-dir=${racine}`) && (!principal || !/--type=/.test(l)))
    .map((l) => Number(l.trim().split(/\s+/)[0]))
}

const job = (d, mode, extra = {}) => ({
  moteur: 'gemini', profil: d.profil, sortie: d.sortie, debug: d.debug, prompt: 'un caméléon sur une branche', url: `${base}?mode=${mode}`, ...extra
})

// --------------------------------------------------------------- sans navigateur

test('classer : quota avec heure de reprise, refus, délai, demain', () => {
  assert.deepEqual(classer("You've reached your limit for creating images. Try again after 3:00 PM.").reprise, '15:00')
  assert.equal(classer("You've reached your limit for creating images.").code, 'quota')
  assert.equal(classer('Vous avez atteint votre limite de création d\'images. Réessayez demain.').reprise, 'demain')
  assert.equal(classer('Limite de génération atteinte, réessayez dans 3 heures').reprise, '+3h')
  assert.equal(classer('Tu as atteint la limite de création à 9h30').reprise, '09:30')
  // « réessayez plus tard » seul : refus, pas quota (relecture du 21/10)
  assert.equal(classer('Je ne peux pas créer cette image. Réessayez plus tard.').code, 'refus')
  assert.equal(classer('Something went wrong. Try again later.').code, 'refus')
  assert.equal(classer('Too many requests on your free credits, try again later.').code, 'quota')
  assert.equal(classer('Réessayez demain : la limite du jour est atteinte.').code, 'quota')
  const r = classer("I can't create images like that.")
  assert.equal(r.code, 'refus')
  assert.match(r.message, /can't create/)
})

test('verifierJob : entrée refusée proprement', () => {
  const ok = { moteur: 'gemini', profil: '/tmp/p', sortie: '/tmp/s', prompt: '  un   chat  ' }
  assert.equal(verifierJob(ok).prompt, 'un chat')
  assert.equal(verifierJob(ok).plafond, 20)
  assert.equal(verifierJob(ok).fenetre, 'jour')
  assert.equal(verifierJob({ ...ok, plafond: 999 }).plafond, 100)
  assert.equal(verifierJob({ ...ok, fenetre: '24h' }).fenetre, '24h')
  assert.equal(verifierJob({ ...ok, fenetre: 'semaine' }).fenetre, 'jour')
  const code = (j) => { try { verifierJob(j); return 'ok' } catch (e) { return e.code } }
  assert.equal(code({ ...ok, moteur: 'midjourney' }), 'entree')
  assert.equal(code({ ...ok, profil: 'relatif' }), 'entree')
  assert.equal(code({ ...ok, prompt: '   ' }), 'entree')
  assert.equal(code({ ...ok, prompt: 'x'.repeat(4001) }), 'entree')
  assert.equal(code({ ...ok, fichiers: [path.join(PROJETS, 'absente-p40-a3.png')] }), 'fichiers') // lot A3 : filtre (pieces.mjs)
  assert.equal(code({ ...ok, url: 'javascript:alert(1)' }), 'entree')
})

test('contrat P40 étape 7 : service, modele, type, affichage ; fenetre = fenêtre du QUOTA', () => {
  // Entrée exacte de astrel/images_ia.py (_traiter)
  const py = {
    moteur: 'gemini', service: 'gemini', modele: 'defaut', type: 'image', affichage: 'masquee',
    profil: '/tmp/p', prompt: 'un chat', fichiers: [], sortie: '/tmp/s', debug: '/tmp/d', plafond: 20, fenetre: 'jour', delai_s: 120
  }
  const j = verifierJob(py)
  assert.equal(j.service, 'gemini')
  assert.equal(j.modele.id, 'defaut')
  assert.equal(j.type, 'image')
  assert.equal(j.affichage, 'masquee')
  assert.equal(j.fenetre, 'jour')
  assert.equal(j.url, 'https://gemini.google.com/app') // adresse du catalogue config/ia-web.json
  assert.equal(verifierJob({ ...py, moteur: undefined }).service, 'gemini') // `service` seul suffit
  assert.equal(verifierJob({ ...py, service: undefined }).service, 'gemini') // ancien `moteur` seul aussi
  assert.equal(verifierJob({ ...py, modele: '', type: undefined, affichage: undefined }).modele.id, 'defaut')
  assert.equal(verifierJob({ ...py, affichage: 'visible' }).affichage, 'visible')
  const code = (j) => { try { verifierJob(j); return 'ok' } catch (e) { return `${e.code} ${e.message}` } }
  assert.match(code({ ...py, type: 'video' }), /^entree .*type image/)
  assert.match(code({ ...py, affichage: 'cachee' }), /^entree affichage/)
  assert.match(code({ ...py, modele: 'imagen-9' }), /^entree modèle inconnu/)
  assert.match(code({ ...py, service: 'chatgpt' }), /^entree service et moteur/)
  assert.match(code({ ...py, service: 'flow', moteur: 'flow' }), /^entree service inconnu/)
  assert.match(code({ ...py, service: 'constructor', moteur: 'constructor' }), /^entree service inconnu/)
  assert.match(code({ ...py, debug: 'relatif' }), /^entree debug/)
})

test('affichage imposé par le site (vidéo, plan P41) : visible même si le job dit masquee', () => {
  const sites = { essai: { ...MOTEURS.gemini, nom: 'essai', type: 'video', affichage: 'visible' } }
  const j = verifierBase({ service: 'essai', type: 'video', affichage: 'masquee', profil: '/tmp/p', sortie: '/tmp/s', prompt: 'x' },
    { sites, type: 'video', catalogue: {} })
  assert.equal(j.affichage, 'visible')
})

test('plafond du jour atteint : refus sans ouvrir Chrome', async () => {
  const d = dossiers()
  fs.mkdirSync(path.dirname(d.profil), { recursive: true })
  const now = new Date()
  const jour = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  fs.writeFileSync(`${d.profil}.compteur.json`, JSON.stringify({ jour, n: 2, dernier: 0 }))
  const r = await lancer(job(d, 'ok', { plafond: 2 }))
  assert.equal(r.code, 1)
  assert.equal(r.fin.code, 'plafond')
  assert.ok(!r.lignes.some((l) => l.etape === 'ouverture'))
})

test('fenêtre de 24 h (ChatGPT, lot 5) : remise à zéro 24 h après la première image', () => {
  const d = dossiers()
  fs.mkdirSync(path.dirname(d.profil), { recursive: true })
  const hier = Date.now() - 23 * 3600000
  fs.writeFileSync(`${d.profil}.compteur.json`, JSON.stringify({ jour: '2000-01-01', n: 3, debut: hier }))
  // Ancien compteur v1 (P14, Gemini seul) : lu comme service gemini
  assert.equal(compteur(d.profil, 'gemini', '24h').etat.n, 3) // fenêtre encore ouverte, même un autre jour
  assert.equal(compteur(d.profil, 'gemini', 'jour').etat.n, 0)
  fs.writeFileSync(`${d.profil}.compteur.json`, JSON.stringify({ n: 3, debut: Date.now() - 25 * 3600000 }))
  const c = compteur(d.profil, 'gemini', '24h')
  assert.equal(c.etat.n, 0)
  c.ajouter()
  const lu = JSON.parse(fs.readFileSync(`${d.profil}.compteur.json`, 'utf8'))
  assert.equal(lu.v, 2)
  assert.ok(Date.now() - lu.services.gemini.debut < 5000)
})

test('compte occupé : un job à la fois (verrou d\'un process vivant)', async () => {
  const d = dossiers()
  fs.mkdirSync(path.dirname(d.profil), { recursive: true })
  fs.writeFileSync(`${d.profil}.lock`, String(process.pid))
  const r = await lancer(job(d, 'ok'))
  assert.equal(r.fin.code, 'occupe')
  fs.writeFileSync(`${d.profil}.lock`, '999999999') // process mort : verrou repris
  const r2 = await lancer(job(d, 'refus'))
  assert.notEqual(r2.fin.code, 'occupe')
  assert.ok(!fs.existsSync(`${d.profil}.lock`), 'verrou rendu à la fin')
})

// --------------------------------------------------------------- Chrome sans fenêtre, fausse page

test('image générée et téléchargée : étapes, fichier, compteur', async () => {
  const d = dossiers()
  const r = await lancer(job(d, 'ok'))
  assert.equal(r.code, 0, r.err)
  assert.deepEqual(r.lignes.filter((l) => l.t === 'etape').map((l) => l.etape),
    ['ouverture', 'saisie', 'generation', 'telechargement'])
  assert.ok(r.lignes.some((l) => l.t === 'envoye'))
  assert.equal(r.fin.t, 'fini')
  assert.match(r.fin.nom, /^\d{8}-\d{6}-gemini-un-cameleon-sur-une-branche\.png$/)
  assert.deepEqual(fs.readFileSync(r.fin.fichier), PNG)
  assert.equal(path.dirname(r.fin.fichier), d.sortie)
  const cpt = JSON.parse(fs.readFileSync(`${d.profil}.compteur.json`, 'utf8'))
  assert.equal(cpt.v, 2)
  assert.equal(cpt.services.gemini.n, 1)
  assert.deepEqual(fs.readdirSync(d.sortie), [r.fin.nom], 'aucun fichier temporaire laissé')
})

test('quota : code quota, message de l\'IA et heure de reprise', async () => {
  const r = await lancer(job(dossiers(), 'quota'))
  assert.equal(r.code, 1)
  assert.equal(r.fin.code, 'quota')
  assert.equal(r.fin.reprise, '15:00')
  assert.match(r.fin.message, /reached your limit/)
})

test('refus de contenu : texte de l\'IA rendu tel quel', async () => {
  const r = await lancer(job(dossiers(), 'refus'))
  assert.equal(r.fin.code, 'refus', JSON.stringify(r.fin) + r.err)
  assert.match(r.fin.message, /can't create images like that/)
})

test('captcha : arrêt net avec capture, rien n\'est tapé', async () => {
  const d = dossiers()
  const r = await lancer(job(d, 'captcha'))
  assert.equal(r.fin.code, 'captcha')
  assert.ok(!r.lignes.some((l) => l.t === 'envoye'))
  assert.ok(r.fin.capture && fs.existsSync(r.fin.capture))
})

test('prompt « qui n\'est pas un robot » : pas de faux captcha (bulle, champ, barre latérale)', async () => {
  const d = dossiers()
  const r = await lancer(job(d, 'ok', { prompt: "un chat qui n'est pas un robot, not a robot" }))
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  assert.equal(r.fin.t, 'fini')
})

test('« Réessayez plus tard » sans mot de limite : refus, pas quota', async () => {
  const r = await lancer(job(dossiers(), 'plustard'))
  assert.equal(r.fin.code, 'refus')
  assert.match(r.fin.message, /Réessayez plus tard/)
})

test('compte déconnecté : session, page changée : page', async () => {
  const s = await lancer(job(dossiers(), 'login'))
  assert.equal(s.fin.code, 'session')
  const p = await lancer(job(dossiers(), 'change'))
  assert.equal(p.fin.code, 'page')
  assert.match(p.fin.message, /champ du prompt introuvable/)
})

test('fichier téléchargé qui n\'est pas une image : refusé', async () => {
  const d = dossiers()
  const r = await lancer(job(d, 'pasimage'))
  assert.equal(r.fin.code, 'page')
  assert.deepEqual(fs.readdirSync(d.sortie), [])
})

test('délai dépassé : code delai, pas de relance', async () => {
  const r = await lancer(job(dossiers(), 'lent', { delai_s: 5 }))
  assert.equal(r.fin.code, 'delai')
  assert.equal(r.lignes.filter((l) => l.t === 'envoye').length, 1)
})

// --------------------------------------------------------------- P40 lot A2 : contrat, écart, modèle

test('fini : modele_utilise annoncé ; 2e génération du compte sur le même site avant 30 s : rythme (en test : pas d\'attente sur place), Chrome pas ouvert', async () => {
  const d = dossiers()
  const r = await lancer(job(d, 'ok', { service: 'gemini', modele: 'defaut', type: 'image', affichage: 'masquee', fenetre: 'jour' }))
  assert.equal(r.code, 0, r.err)
  assert.equal(r.fin.modele_utilise, 'defaut')
  assert.ok(!('credits_restants' in r.fin), 'Gemini n\'affiche pas de crédits')
  const cpt = JSON.parse(fs.readFileSync(`${d.profil}.compteur.json`, 'utf8'))
  assert.ok(Date.now() - cpt.services.gemini.dernier_envoi < 60000)
  const r2 = await lancer(job(d, 'ok'))
  assert.equal(r2.code, 1)
  assert.equal(r2.fin.code, 'rythme', JSON.stringify(r2.fin))
  assert.match(r2.fin.reprise, /^\d{2}:\d{2}$/)
  assert.ok(!r2.lignes.some((l) => l.etape === 'ouverture'))
  assert.ok(!fs.existsSync(`${d.profil}.lock`), 'verrou rendu')
})

test('30/10 : écart compté PAR SITE : un envoi Flow à l\'instant ne retarde pas Gemini', async () => {
  const d = dossiers()
  fs.mkdirSync(path.dirname(d.profil), { recursive: true })
  const t = Date.now() - 5000
  fs.writeFileSync(`${d.profil}.compteur.json`, JSON.stringify({
    v: 2, dernier: Date.now() - 120000, dernier_envoi: t, services: { flow: { jour: '2000-01-01', n: 20, debut: 1, dernier_envoi: t } }
  }))
  const r = await lancer(job(d, 'ok'))
  assert.equal(r.code, 0, JSON.stringify(r.fin))
  const lu = JSON.parse(fs.readFileSync(`${d.profil}.compteur.json`, 'utf8'))
  assert.equal(lu.services.flow.n, 20, 'compteur de l\'autre service intact')
})

// Faux site branché sur le noyau SANS le modifier (comme le fera un site vidéo, plan P41) :
// mêmes rôles que Gemini sur la fausse page, plus un sélecteur de modèle.
function seanceModele (d, choisirModele) {
  const catalogue = { gemini: { type: 'image', url: '', plafond: 20, fenetre: 'jour', modeles: [{ id: 'defaut', libelle: 'défaut', cout: 1 }, { id: 'rapide', libelle: 'Rapide', cout: 2 }] } }
  // `null` : site SANS sélecteur de modèle (Gemini en a un depuis le lot A3 : retiré ici)
  const site = { ...MOTEURS.gemini, choisirModele: choisirModele || undefined }
  const j = verifierBase({ service: 'gemini', modele: 'rapide', profil: d.profil, sortie: d.sortie, debug: d.debug, prompt: 'un chat', url: `${base}?mode=ok` },
    { sites: { gemini: site }, type: 'image', catalogue })
  const envois = []
  const lignes = []
  // Lignes émises gardées ici : stdout appartient au lanceur de tests
  return seance(j, async ({ envoye, modele }) => { envoye(); envoye(); envois.push(modele); return { nom: 'x' } },
    { emettre: (l) => lignes.push(l) })
    .then((res) => ({ res, envois, lignes }), (e) => ({ e, envois, lignes }))
}

test('modèle demandé : sélectionné par le site, compté en unités ; jamais substitué', async () => {
  // Site sans sélecteur de modèle : échec page, rien d'envoyé, rien de compté
  const d1 = dossiers()
  const sans = await seanceModele(d1, null)
  assert.ok(sans.e instanceof Echec, String(sans.e))
  assert.equal(sans.e.code, 'page')
  assert.match(sans.e.message, /rapide introuvable/)
  assert.deepEqual(sans.envois, [])
  // Le site prend un AUTRE modèle : échec page, jamais de substitution silencieuse
  const d2 = dossiers()
  const autre = await seanceModele(d2, async () => 'defaut')
  assert.equal(autre.e?.code, 'page')
  assert.match(autre.e.message, /la page a pris defaut/)
  assert.deepEqual(autre.envois, [])
  // Le site sélectionne le bon : modele_utilise, 2 unités comptées, un seul envoi
  const d3 = dossiers()
  const ok = await seanceModele(d3, async (page, modele) => modele.id)
  assert.equal(ok.e, undefined, String(ok.e))
  assert.equal(ok.res.modele_utilise, 'rapide')
  assert.deepEqual(ok.envois, ['rapide'])
  assert.equal(ok.lignes.filter((l) => l.t === 'envoye').length, 1)
  assert.deepEqual(sans.lignes.map((l) => l.t), ['etape'], 'sans modèle : ouverture seule, rien d\'envoyé')
  const cpt = JSON.parse(fs.readFileSync(`${d3.profil}.compteur.json`, 'utf8'))
  assert.equal(cpt.services.gemini.n, 2)
})

test('gestes humains dans un vrai Chrome : souris en courbe, clic hors du centre, molette', async () => {
  const { cliquer, defiler, taper } = await import('../humain.mjs')
  const d = dossiers()
  const j = verifierBase({ service: 'gemini', profil: d.profil, sortie: d.sortie, prompt: 'un chat', url: `${base}?mode=ok` },
    { sites: MOTEURS, type: 'image' })
  const lignes = []
  const res = await seance(j, async ({ page, champ }) => {
    await cliquer(page, champ)
    await taper(page, 'abc def')
    await defiler(page, 300)
    return { releve: await page.evaluate(() => window.souris), texte: await champ.innerText() }
  }, { emettre: (l) => lignes.push(l) })
  const { releve, texte } = res
  assert.equal(texte, 'abc def', 'le clic à la souris a bien donné le focus au champ')
  assert.ok(releve.mouvements >= 4, `mouvements : ${releve.mouvements}`)
  assert.equal(releve.clics.length, 1)
  const c = releve.clics[0]
  assert.equal(c.cible, 'champ')
  assert.ok(c.x !== c.cx || c.y !== c.cy, 'pas au centre exact')
  assert.ok(releve.molette >= 2, `crans de molette : ${releve.molette}`)
})

// --------------------------------------------------------------- P40 lot A2, corrections 1

// Mode visible (`viewport: null`, noyau.optionsLancement) : page.viewportSize() est nul. Rejoué
// SANS fenêtre (headless, mêmes options sinon) : un bouton sous la ligne de flottaison doit être
// amené dans la vue à la molette puis vraiment cliqué (relecture, fiche 15, C1).
test('mode visible (viewport nul) : élément sous la ligne de flottaison amené dans la vue et cliqué', async () => {
  const { chromium } = await import('playwright-core')
  const { optionsLancement } = await import('../noyau.mjs')
  const { cliquer } = await import('../humain.mjs')
  const d = dossiers()
  const ctx = await chromium.launchPersistentContext(d.profil, { ...optionsLancement({ affichage: 'visible' }), headless: true })
  try {
    const page = ctx.pages()[0] || await ctx.newPage()
    assert.equal(page.viewportSize(), null, 'même situation que le mode visible')
    await page.setContent(`<div style="height:3000px">haut</div><button id="bas" style="width:200px;height:60px">bas</button>
      <script>window.clics = 0; window.molette = 0
      document.getElementById('bas').addEventListener('click', () => { window.clics++ })
      addEventListener('wheel', () => { window.molette++ }, true)</script>`)
    await cliquer(page, page.locator('#bas'), { timeout: 3000 })
    const r = await page.evaluate(() => ({ clics: window.clics, molette: window.molette, y: document.getElementById('bas').getBoundingClientRect().y, h: innerHeight }))
    assert.equal(r.clics, 1, `clic reçu (bouton à y=${r.y}, vue ${r.h})`)
    assert.ok(r.molette >= 2, `défilé à la molette : ${r.molette} crans`)
  } finally {
    await ctx.close()
  }
})

// --------------------------------------------------------------- P40 lot A2, corrections 2

// Mode visible (fiche 17) : le clic partait PENDANT le défilement doux (mouse.wheel rend la main
// avant la fin) et manquait sa cible. Rejoué à coup sûr sans fenêtre par une page qui ANIME
// elle-même la molette (comme les sites à défilement doux) : sans attendreStable, 6 sur 6 ratés.
// Puis un bouton dans un conteneur défilant (rogné : dans la vue mais pas atteignable), placé
// AU-DESSUS de la souris de départ (y de 172 à 430) pour que la molette défile la page.
async function visibleSansFenetre (contenu) {
  const { chromium } = await import('playwright-core')
  const { optionsLancement } = await import('../noyau.mjs')
  const { cliquer } = await import('../humain.mjs')
  const d = dossiers()
  const ctx = await chromium.launchPersistentContext(d.profil, { ...optionsLancement({ affichage: 'visible' }), headless: true })
  try {
    const page = ctx.pages()[0] || await ctx.newPage()
    assert.equal(page.viewportSize(), null, 'même situation que le mode visible')
    await page.setContent(contenu)
    await cliquer(page, page.locator('#cible'), { timeout: 3000 })
    await page.waitForTimeout(300)
    return await page.evaluate(() => ({ clics: window.clics, recus: window.recus, h: innerHeight }))
  } finally {
    await ctx.close()
  }
}

const COMPTE_CLICS = `<script>window.clics = 0; window.recus = []
  document.getElementById('cible').addEventListener('click', () => { window.clics++ })
  addEventListener('mousedown', (e) => { window.recus.push(e.target.id || e.target.tagName) }, true)</script>`

test('mode visible, défilement doux : le clic attend que le bouton soit immobile', async () => {
  const r = await visibleSansFenetre(`<body style="margin:0"><div style="height:3000px">haut</div>
    <button id="cible" style="width:200px;height:60px">bas</button><div style="height:1500px"></div>${COMPTE_CLICS}
    <script>let but = scrollY, ici = scrollY, anime = false
    const pas = () => { ici += (but - ici) * 0.12; if (Math.abs(but - ici) < 0.5) { ici = but; anime = false } else requestAnimationFrame(pas); scrollTo(0, ici) }
    addEventListener('wheel', (e) => { e.preventDefault(); but = Math.max(0, Math.min(document.documentElement.scrollHeight - innerHeight, but + e.deltaY)); if (!anime) { anime = true; requestAnimationFrame(pas) } }, { passive: false })
    addEventListener('scroll', () => { if (!anime) { but = ici = scrollY } })</script>`)
  assert.equal(r.clics, 1, `clic reçu sur le bouton (appuis reçus par : ${r.recus.join(', ')})`)
})

test('mode visible, bouton dans un conteneur défilant : amené dans la vue et cliqué', async () => {
  const r = await visibleSansFenetre(`<body style="margin:0"><div style="height:120px;overflow:auto">
    <div style="height:2500px"></div><button id="cible" style="width:200px;height:40px">dans le cadre</button>
    <div style="height:800px"></div></div><div style="height:3000px"></div>${COMPTE_CLICS}`)
  assert.equal(r.clics, 1, `clic reçu sur le bouton (appuis reçus par : ${r.recus.join(', ')})`)
})

// Coupures en pleine génération (fiche 14, D1 et D2) : le worker doit sortir aussitôt, sans
// attendre le délai, verrou rendu, aucun Chrome laissé. delai_s 60 : un « delai » serait à 60 s.
async function coupure (agir) {
  const d = dossiers()
  let coupeLe = 0
  const r = await lancer(job(d, 'lent', { delai_s: 60 }), {
    surLigne: (l, proc) => {
      if (l.etape !== 'generation' || coupeLe) return
      coupeLe = -1
      setTimeout(() => { coupeLe = Date.now(); agir(proc, d) }, 500)
    }
  })
  for (let i = 0; i < 30 && chromesDe(d.racine).length; i++) await new Promise((resolve) => setTimeout(resolve, 100))
  const restes = chromesDe(d.racine)
  for (const pid of restes) { try { process.kill(pid, 'SIGKILL') } catch {} } // nettoyage, par PID
  return { r, d, duree: coupeLe > 0 ? r.sortiLe - coupeLe : Infinity, restes }
}

test('worker_sigterm : SIGTERM en pleine génération, sortie en moins de 3 s, échec interne, verrou rendu', async () => {
  const { r, d, duree, restes } = await coupure((proc) => proc.kill('SIGTERM'))
  assert.ok(duree < 3000, `sorti ${duree} ms après SIGTERM : ${JSON.stringify(r.fin)}`)
  assert.equal(r.fin.code, 'interne', JSON.stringify(r.fin))
  assert.match(r.fin.message, /navigateur fermé/)
  assert.ok(!fs.existsSync(`${d.profil}.lock`), 'verrou rendu')
  assert.deepEqual(restes, [], 'aucun Chrome laissé')
})

test('chrome_mort : Chrome tué pendant l\'attente, échec interne tout de suite (pas delai)', async () => {
  const { r, d, duree, restes } = await coupure((proc, dd) => {
    const pids = chromesDe(dd.racine, { principal: true })
    assert.ok(pids.length, 'Chrome du profil trouvé')
    for (const pid of pids) process.kill(pid, 'SIGKILL')
  })
  assert.ok(duree < 3000, `sorti ${duree} ms après la mort de Chrome : ${JSON.stringify(r.fin)}`)
  assert.equal(r.code, 1)
  assert.equal(r.fin.code, 'interne', JSON.stringify(r.fin))
  assert.ok(!fs.existsSync(`${d.profil}.lock`), 'verrou rendu')
  assert.deepEqual(restes, [], 'aucun Chrome laissé')
})

test('verrou_sigint : pas de .lock après SIGINT en pleine génération', async () => {
  const { r, d, duree, restes } = await coupure((proc) => proc.kill('SIGINT'))
  assert.ok(duree < 3000, `sorti ${duree} ms après SIGINT (code ${r.code}, signal ${r.signal})`)
  assert.ok(!fs.existsSync(`${d.profil}.lock`), 'verrou rendu à la sortie')
  assert.deepEqual(restes, [], 'aucun Chrome laissé')
})

// --------------------------------------------------------------- P40 lot A3 : sites, modèle, pièces jointes

// Catalogue de TEST (le vrai n'a que « defaut » : modèles non relevés) : deux modèles nommés
// comme sur la fausse page Gemini, un menu reconnu par les libellés seuls (pas de motif).
function catalogueTest () {
  const f = path.join(dossiers().racine, 'ia-web.json')
  const modeles = [{ id: 'defaut', libelle: 'défaut', cout: 1 }, { id: 'modele-a', libelle: 'Modèle A', cout: 1 }, { id: 'modele-b', libelle: 'Modèle B', cout: 1 }]
  fs.writeFileSync(f, JSON.stringify({ services: {
    gemini: { type: 'image', fournisseur: 'google', modeles, plafond: 20, fenetre: 'jour' },
    chatgpt: { type: 'image', fournisseur: 'openai', modeles: [{ id: 'defaut', cout: 1 }], plafond: 3, fenetre: '24h' },
    bing: { type: 'image', fournisseur: 'aucun', modeles: [{ id: 'defaut', cout: 1 }], plafond: 15, fenetre: 'jour' }
  } }))
  return f
}

/** Images de test dans un dossier permis par ASTREL_IMAGEGEN_RACINES (rythme de test seulement). */
function piecesTest (n) {
  const r = dossiers().racine
  const dir = path.join(r, 'pj')
  fs.mkdirSync(dir)
  const chemins = []
  for (let i = 1; i <= n; i++) {
    const f = path.join(dir, `piece-${i}.png`)
    fs.writeFileSync(f, Buffer.concat([PNG, Buffer.from(`piece ${i}`)]))
    chemins.push(f)
  }
  return { dir, chemins, env: { ASTREL_IMAGEGEN_RACINES: dir } }
}

test('A3 verifierJob : profil anonyme réservé à Bing, pièces jointes filtrées dès l\'entrée', () => {
  const base1 = { profil: '/tmp/p/essai', sortie: '/tmp/s', prompt: 'un chat' }
  const code = (j) => { try { verifierJob(j); return 'ok' } catch (e) { return `${e.code} ${e.message}` } }
  assert.equal(code({ ...base1, service: 'chatgpt' }), 'ok')
  assert.match(code({ ...base1, service: 'bing' }), /^entree .*sans compte : profil « anonyme »/)
  assert.equal(code({ ...base1, service: 'bing', profil: '/tmp/p/anonyme' }), 'ok')
  assert.match(code({ ...base1, service: 'gemini', profil: '/tmp/p/anonyme' }), /^entree .*réservé aux services sans compte/)
  assert.match(code({ ...base1, service: 'bing', profil: '/tmp/p/anonyme', fichiers: [path.join(PROJETS, 'x.png')] }), /^fichiers .*ne prend pas de pièce jointe/)
  assert.match(code({ ...base1, service: 'gemini', fichiers: Array(7).fill(path.join(PROJETS, 'x.png')) }), /^fichiers 6 pièces jointes au plus/)
  assert.match(code({ ...base1, service: 'gemini', fichiers: ['/etc/hostname'] }), /^fichiers .*hors des pièces jointes/)
})

test('A3 Gemini : modèle du catalogue choisi dans le menu, annoncé en modele_utilise', async () => {
  const cat = catalogueTest()
  const r = await lancer(job(dossiers(), 'ok', { service: 'gemini', modele: 'modele-b' }), { env: { ASTREL_IA_WEB_CATALOGUE: cat } })
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  assert.equal(r.fin.modele_utilise, 'modele-b')
  // Déjà affiché : rien à cliquer, même résultat
  const r2 = await lancer(job(dossiers(), 'ok', { service: 'gemini', modele: 'modele-a' }), { env: { ASTREL_IA_WEB_CATALOGUE: cat } })
  assert.equal(r2.fin.modele_utilise, 'modele-a', JSON.stringify(r2.fin))
})

test('A3 Gemini : modèle qui ne prend pas ou page muette = échec page, rien d\'envoyé', async () => {
  const cat = catalogueTest()
  const fige = await lancer(job(dossiers(), 'ok&modele=fige', { service: 'gemini', modele: 'modele-b' }), { env: { ASTREL_IA_WEB_CATALOGUE: cat } })
  assert.equal(fige.fin.code, 'page', JSON.stringify(fige.fin))
  assert.match(fige.fin.message, /la page a pris modele-a/)
  assert.ok(!fige.lignes.some((l) => l.t === 'envoye'))
  const muet = await lancer(job(dossiers(), 'ok&modele=muet', { service: 'gemini', modele: 'modele-b' }), { env: { ASTREL_IA_WEB_CATALOGUE: cat } })
  assert.equal(muet.fin.code, 'page', JSON.stringify(muet.fin))
  assert.ok(!muet.lignes.some((l) => l.t === 'envoye'))
})

test('A3 Gemini : deux pièces jointes déposées par le sélecteur de fichiers (menu « Upload files »)', async () => {
  const pj = piecesTest(2)
  const avant = releves.length
  const r = await lancer(job(dossiers(), 'ok', { service: 'gemini', fichiers: pj.chemins }), { env: pj.env })
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  const recu = releves.slice(avant).filter((x) => x.site === 'gemini').at(-1)
  assert.deepEqual(recu?.joints.map((j) => [j.name, j.type, j.size]),
    [['piece-1.png', 'image/png', PNG.length + 7], ['piece-2.png', 'image/png', PNG.length + 7]])
})

test('Gemini 30/10 : libellés du vrai site (« Importation et outils », « Importer des fichiers. Documents… »)', async () => {
  const pj = piecesTest(1)
  const avant = releves.length
  const r = await lancer(job(dossiers(), 'ok&ui=2026', { service: 'gemini', fichiers: pj.chemins }), { env: pj.env })
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  const recu = releves.slice(avant).filter((x) => x.site === 'gemini').at(-1)
  assert.deepEqual(recu?.joints.map((j) => j.name), ['piece-1.png'])
})

test('Gemini 30/10 : attestation « Creating content from images and files » acceptée (Agree), puis envoi', async () => {
  const pj = piecesTest(1)
  const avant = releves.length
  const r = await lancer(job(dossiers(), 'ok&ui=2026&attest=1', { service: 'gemini', fichiers: pj.chemins }), { env: pj.env })
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  const recu = releves.slice(avant).filter((x) => x.site === 'gemini').at(-1)
  assert.deepEqual([recu?.joints.map((j) => j.name), recu?.attestation], [['piece-1.png'], true])
  assert.match(r.err, /attestation .* acceptée/)
})

test('Gemini 30/10 : fenêtre de nouveautés (« Let Gemini personalize using memory ») refusée, jamais acceptée', async () => {
  const pj = piecesTest(1)
  const avant = releves.length
  const r = await lancer(job(dossiers(), 'ok&ui=2026&attest=1&popup=memoire', { service: 'gemini', fichiers: pj.chemins }), { env: pj.env })
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  assert.match(r.err, /fenêtre « Let Gemini personalize using memory » refusée \(« No thanks »\)/)
  assert.ok(!releves.slice(avant).some((x) => x.interdit), 'Allow jamais cliqué')
})

test('A3 Gemini : pièce sans aperçu = échec page AVANT l\'envoi ; pièce disparue en file = fichiers', async () => {
  const pj = piecesTest(1)
  const r = await lancer(job(dossiers(), 'ok&pj=sansapercu', { service: 'gemini', fichiers: pj.chemins }), { env: pj.env })
  assert.equal(r.fin.code, 'page', JSON.stringify(r.fin))
  assert.match(r.fin.message, /sans aperçu/)
  assert.ok(!r.lignes.some((l) => l.t === 'envoye'))
  // Hors des dossiers permis (variable absente) : refus sans Chrome
  const r2 = await lancer(job(dossiers(), 'ok', { service: 'gemini', fichiers: pj.chemins }))
  assert.equal(r2.fin.code, 'fichiers', JSON.stringify(r2.fin))
  assert.ok(!r2.lignes.some((l) => l.etape === 'ouverture'))
})

test('A3 ChatGPT : image téléchargée, pièces une par une (sélecteur d\'un seul fichier)', async () => {
  const pj = piecesTest(2)
  const avant = releves.length
  const d = dossiers()
  const r = await lancer(job(d, 'ok', { service: 'chatgpt', moteur: undefined, url: `${base}chatgpt?mode=ok`, fichiers: pj.chemins }), { env: pj.env })
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  assert.match(r.fin.nom, /^\d{8}-\d{6}-chatgpt-un-cameleon-sur-une-branche\.png$/)
  assert.equal(r.fin.modele_utilise, 'defaut')
  assert.deepEqual(r.fin.fichiers, [{ fichier: r.fin.fichier, nom: r.fin.nom }])
  const recu = releves.slice(avant).filter((x) => x.site === 'chatgpt').at(-1)
  assert.deepEqual(recu?.joints.map((j) => j.name), ['piece-1.png', 'piece-2.png'])
  const cpt = JSON.parse(fs.readFileSync(`${d.profil}.compteur.json`, 'utf8'))
  assert.equal(cpt.services.chatgpt.n, 1)
})

test('A3 ChatGPT : quota du plan gratuit (+23h), déconnecté = session, ancienne image jamais reprise', async () => {
  const chat = (mode) => job(dossiers(), mode, { service: 'chatgpt', moteur: undefined, url: `${base}chatgpt?mode=${mode}` })
  const q = await lancer(chat('quota'))
  assert.equal(q.fin.code, 'quota', JSON.stringify(q.fin))
  assert.equal(q.fin.reprise, '+23h')
  const s = await lancer(chat('login'))
  assert.equal(s.fin.code, 'session', JSON.stringify(s.fin))
  const h = await lancer(chat('historique'))
  assert.equal(h.code, 0, JSON.stringify(h.fin) + h.err)
  assert.deepEqual(fs.readFileSync(h.fin.fichier), PNG, 'la NOUVELLE image, pas celle de la conversation reprise')
})

const bing = (mode, extra = {}) => {
  const d = dossiers('anonyme')
  return { d, j: job(d, mode, { service: 'bing', moteur: undefined, url: `${base}bing?mode=${mode}`, ...extra }) }
}

test('A3 Bing sans compte : 4 variantes, noms UNIQUES dans la même seconde, cookies refusés', async () => {
  const avant = releves.length
  const { d, j } = bing('ok')
  j.url += '&cookies=1'
  const r = await lancer(j)
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  assert.deepEqual(r.lignes.filter((l) => l.t === 'etape').map((l) => l.etape), ['ouverture', 'saisie', 'generation', 'telechargement'])
  assert.equal(r.fin.fichiers.length, 4)
  assert.equal(r.fin.fichiers[0].fichier, r.fin.fichier, 'principale en tête')
  const noms = r.fin.fichiers.map((f) => f.nom)
  assert.equal(new Set(noms).size, 4, `noms uniques : ${noms.join(', ')}`)
  for (const n of noms) assert.match(n, /^\d{8}-\d{6}-bing-un-cameleon-sur-une-branche(-[234])?\.png$/)
  assert.deepEqual(fs.readdirSync(d.sortie).sort(), [...noms].sort(), 'rien d\'écrasé, aucun temporaire')
  const contenus = r.fin.fichiers.map((f) => fs.readFileSync(f.fichier).subarray(PNG.length).toString())
  assert.deepEqual(contenus, ['v1', 'v2', 'v3', 'v4'], 'chaque variante garde ses octets')
  assert.equal(releves.slice(avant).filter((x) => x.site === 'bing').at(-1)?.cookies, 'refuse', 'bandeau : refusé, jamais accepté')
  const cpt = JSON.parse(fs.readFileSync(`${d.profil}.compteur.json`, 'utf8'))
  assert.equal(cpt.services.bing.n, 1, 'une génération = une unité')
})

test('A3 Bing : grille de 2, anciennes créations ignorées, variante illisible sautée', async () => {
  const deux = await lancer(bing('deux').j)
  assert.equal(deux.code, 0, JSON.stringify(deux.fin) + deux.err)
  assert.equal(deux.fin.fichiers.length, 2)
  const rec = await lancer(bing('recentes').j)
  assert.equal(rec.code, 0, JSON.stringify(rec.fin) + rec.err)
  const contenus = rec.fin.fichiers.map((f) => fs.readFileSync(f.fichier).subarray(PNG.length).toString())
  assert.deepEqual(contenus, ['v1', 'v2', 'v3', 'v4'])
  const casse = await lancer(bing('casse').j)
  assert.equal(casse.code, 0, JSON.stringify(casse.fin) + casse.err)
  assert.deepEqual(casse.fin.fichiers.map((f) => fs.readFileSync(f.fichier).subarray(PNG.length).toString()), ['v2'])
})

test('A3 Bing : prompt bloqué = refus, compte exigé = session, défi = captcha', async () => {
  const refus = await lancer(bing('refus').j)
  assert.equal(refus.fin.code, 'refus', JSON.stringify(refus.fin))
  assert.match(refus.fin.message, /prompt has been blocked/)
  const login = await lancer(bing('login').j)
  assert.equal(login.fin.code, 'session', JSON.stringify(login.fin))
  assert.ok(!login.lignes.some((l) => l.t === 'envoye'), 'rien envoyé quand Bing exige un compte')
  const defi = await lancer(bing('defi').j)
  assert.equal(defi.fin.code, 'captcha', JSON.stringify(defi.fin))
})

// --------------------------------------------------------------- P40 A3, retour du testeur (fiche 2)

test('bing_redirection_autre_hote : 302 vers un autre hôte = échec page, ZÉRO requête reçue par cet hôte', async () => {
  const avant = etrangerRecues.length
  const r = await lancer(bing('redirection').j)
  assert.equal(r.fin.code, 'page', JSON.stringify(r.fin) + r.err)
  assert.match(r.fin.message, /images de Bing illisibles/)
  assert.deepEqual(etrangerRecues.slice(avant), [], 'l\'hôte étranger n\'a jamais été contacté (ni page, ni worker)')
})

test('Bing : redirection vers le même hôte suivie à la main, image de la cible enregistrée', async () => {
  const r = await lancer(bing('saut').j)
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  assert.deepEqual(r.fin.fichiers.map((f) => fs.readFileSync(f.fichier).subarray(PNG.length).toString()), ['v3'])
})

test('ChatGPT : défi anti-robot après l\'envoi = captcha, arrêt', async () => {
  const r = await lancer(job(dossiers(), 'captcha', { service: 'chatgpt', moteur: undefined, url: `${base}chatgpt?mode=captcha` }))
  assert.equal(r.fin.code, 'captcha', JSON.stringify(r.fin))
  assert.ok(r.fin.capture && fs.existsSync(r.fin.capture))
})

// --------------------------------------------------------------- P40 A3, relecture (fiche 4)

test('Bing : même image en vignette et en aperçu (autre ?w=) comptée UNE fois, jamais téléchargée deux fois', async () => {
  const r = await lancer(bing('doublons').j)
  assert.equal(r.code, 0, JSON.stringify(r.fin) + r.err)
  assert.deepEqual(r.fin.fichiers.map((f) => fs.readFileSync(f.fichier).subarray(PNG.length).toString()), ['v1', 'v2', 'v3'])
})

test('Bing : image refusée (HTTP 403) notée au journal du worker, échec qui dit la vraie cause', async () => {
  const r = await lancer(bing('interdite').j)
  assert.equal(r.fin.code, 'page', JSON.stringify(r.fin))
  assert.match(r.fin.message, /HTTP 403/)
  assert.match(r.err, /bing : image 1 sautée : HTTP 403/)
  const redir = await lancer(bing('redirection').j)
  assert.match(redir.fin.message, /redirection vers un hôte non permis \(127\.0\.0\.2:/)
  assert.match(redir.err, /image 1 sautée : redirection vers un hôte non permis/)
})
