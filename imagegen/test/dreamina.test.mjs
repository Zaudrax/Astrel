// Pilote vidéo DREAMINA (plan P41 lot B5, 29/10/2026) : `cd imagegen && node --test
// --test-concurrency=1` (un seul Chrome à la fois). Deux parties, Chrome SANS FENÊTRE :
// 1. les PAGES ENREGISTRÉES du relevé (/home/user/Projets/Videos-IA/releves/dreamina/), ouvertes
//    en file:// avec TOUT le réseau coupé : les sélecteurs du pilote trouvent les vrais éléments ;
// 2. le worker lancé pour de vrai sur la FAUSSE page test/fausse-dreamina.html, servie en local.
// Aucun accès à dreamina.capcut.com, aucun compte, aucune génération.
import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

process.env.ASTREL_IMAGEGEN_RYTHME = 'test'
const { Echec, CHROME, lireCatalogue } = await import('../noyau.mjs')
const { verifierJob } = await import('../video.mjs')
const { DREAMINA, MODELES, classerPage, modeleLu } = await import('../sites/dreamina.mjs')
const { nomDe } = await import('../sites/commun.mjs')
const { chromium } = await import('playwright-core')

const DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const PAGE = fs.readFileSync(path.join(DIR, 'test', 'fausse-dreamina.html'))
const RELEVE = '/home/user/Projets/Videos-IA/releves/dreamina'
const RACINE_TESTS = fs.mkdtempSync(path.join(os.tmpdir(), 'dreamina-test-'))
const PROMPT = 'un chat roux qui saute sur un canapé dans un salon lumineux'

/** mp4 minimal : ftyp isom + moov/mvhd de `secondes` (durée relue par video.dureeMp4). */
function mp4 (secondes) {
  const boite = (type, ...corps) => {
    const c = Buffer.concat(corps)
    const h = Buffer.alloc(8)
    h.writeUInt32BE(8 + c.length)
    h.write(type, 4, 'latin1')
    return Buffer.concat([h, c])
  }
  const mvhd = Buffer.alloc(100)
  mvhd.writeUInt32BE(1000, 12)
  mvhd.writeUInt32BE(secondes * 1000, 16)
  return Buffer.concat([boite('ftyp', Buffer.from('isom', 'latin1'), Buffer.from([0, 0, 2, 0]), Buffer.from('isommp41', 'latin1')),
    boite('moov', boite('mvhd', mvhd)), boite('mdat', Buffer.alloc(64))])
}

let serveur
let base
const signaux = new Map() // cle → {envoi: [], confirme: [], interdit: [], ferme: []}
const vide = () => ({ envoi: [], confirme: [], interdit: [], ferme: [] })
const signauxDe = (d) => signaux.get(path.basename(d.racine)) || vide()
before(async () => {
  serveur = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x')
    const nom = url.pathname.slice(1)
    if (['envoi', 'confirme', 'interdit', 'ferme'].includes(nom) && req.method === 'POST') {
      let b = ''
      req.on('data', (x) => { b += x })
      req.on('end', () => {
        const r = JSON.parse(b)
        const s = signaux.get(r.cle) || vide()
        s[nom].push(r)
        signaux.set(r.cle, s)
        res.end('ok')
      })
      return
    }
    const clips = { '/clip.mp4': 5, '/ancien.mp4': 3, '/attente.mp4': 1, '/feed.mp4': 2 }
    if (clips[url.pathname]) {
      res.writeHead(200, { 'content-type': 'video/mp4', ...(url.search ? {} : { 'content-disposition': 'attachment' }) })
      res.end(mp4(clips[url.pathname]))
      return
    }
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

function lancer (job) {
  const proc = spawn(process.execPath, ['video.mjs'], { cwd: DIR, env: { ...process.env, ASTREL_IMAGEGEN_RYTHME: 'test' }, stdio: ['pipe', 'pipe', 'pipe'] })
  let out = ''
  let err = ''
  proc.stdout.on('data', (x) => { out += x })
  proc.stderr.on('data', (x) => { err += x })
  proc.stdin.end(JSON.stringify(job) + '\n')
  return new Promise((resolve) => {
    const garde = setTimeout(() => proc.kill('SIGKILL'), 60000)
    proc.on('exit', (code) => {
      clearTimeout(garde)
      const lignes = out.split('\n').filter(Boolean).map((l) => JSON.parse(l))
      resolve({ code, lignes, err, fin: lignes.at(-1) || {} })
    })
  })
}

const chromesDe = (racine) => execFileSync('ps', ['-eo', 'pid=,args='], { encoding: 'utf8' }).split('\n')
  .filter((l) => /chrome/.test(l) && l.includes(`--user-data-dir=${racine}`))

const job = (d, mode, extra = {}) => ({
  service: 'dreamina', type: 'video', profil: d.profil, sortie: d.sortie, debug: d.debug, prompt: PROMPT,
  url: `${base}ai-tool/home?type=video&mode=${mode}&cle=${path.basename(d.racine)}`, ...extra
})
const compteurDe = (d) => JSON.parse(fs.readFileSync(`${d.profil}.compteur.json`, 'utf8'))

async function reussit (d, j) {
  const r = await lancer(j)
  assert.equal(r.code, 0, `${r.err}\n${JSON.stringify(r.fin)}`)
  assert.equal(r.fin.t, 'fini')
  assert.deepEqual(signauxDe(d).interdit, [], 'aucun geste interdit (filigranes, abonnement, menu, régénérer)')
  assert.deepEqual(chromesDe(d.racine), [], 'aucun Chrome laissé')
  return r
}

async function echoue (d, j, code) {
  const r = await lancer(j)
  assert.equal(r.code, 1, `${r.err}\n${JSON.stringify(r.fin)}`)
  assert.equal(r.fin.t, 'echec')
  assert.equal(r.fin.code, code, JSON.stringify(r.fin))
  assert.ok(!fs.existsSync(`${d.profil}.lock`), 'verrou rendu')
  assert.deepEqual(chromesDe(d.racine), [], 'aucun Chrome laissé')
  return r
}

// --------------------------------------------------------------- sans navigateur

test('catalogue : Dreamina vidéo, 10 modèles relevés, 120 par jour ; job par défaut Mini 5 s 16:9 720p', () => {
  const c = lireCatalogue().dreamina
  assert.deepEqual([c.type, c.plafond, c.fenetre, c.modeles[0].id, c.modeles[0].cout, c.modeles.length], ['video', 120, 'jour', 'seedance-2-mini', 55, 10])
  // Mêmes ids et mêmes prix au catalogue (Python) et dans le pilote : le prix lu doit être exact
  const brut = JSON.parse(fs.readFileSync(path.join(DIR, '..', 'config', 'ia-web.json'), 'utf8')).services.dreamina.modeles
  assert.deepEqual(brut.map((m) => m.id).sort(), Object.keys(MODELES).sort())
  for (const m of brut) {
    assert.deepEqual(Object.fromEntries(Object.entries(m.durees).map(([d, x]) => [d, x])), Object.fromEntries(Object.entries(MODELES[m.id].cout).map(([d, x]) => [d, x])), m.id)
    assert.deepEqual(Object.keys(m.durees).map(Number), MODELES[m.id].durees, m.id)
    assert.equal(m.cout, MODELES[m.id].cout[5], m.id)
  }
  const d = dossiers()
  const j = verifierJob(job(d, 'ok'))
  assert.deepEqual([j.modele.id, j.modele.cout, j.duree, j.format, j.resolution, j.coutExact, j.affichage],
    ['seedance-2-mini', 55, 5, '16:9', '720p', 55, 'masquee'])
  assert.equal(verifierJob(job(d, 'ok', { modele: 'defaut' })).modele.id, 'seedance-2-mini')
  assert.equal(verifierJob(job(d, 'ok', { format: '9:16' })).format, '9:16')
})

test('verifierJob Dreamina : durée, format, modèle non proposé, images refusés proprement', () => {
  const d = dossiers()
  const code = (j) => { try { verifierJob(j); return 'ok' } catch (e) { assert.ok(e instanceof Echec, String(e)); return e.code } }
  assert.equal(code(job(d, 'ok', { duree: 8 })), 'entree', 'Mini : 5, 10 ou 15 s (coûts relevés)')
  assert.equal(code(job(d, 'ok', { modele: 'seedance-25-brouillon', duree: 10 })), 'entree', 'brouillon : 5 s seul relevé')
  assert.equal(code(job(d, 'ok', { format: '2:1' })), 'entree')
  assert.equal(code(job(d, 'ok', { modele: 'seedance-9' })), 'entree', 'pas au catalogue')
  const r = (m, duree) => { const j = verifierJob(job(d, 'ok', { modele: m, duree })); return [j.resolution, j.coutExact] }
  assert.deepEqual(r('seedance-25-brouillon', 5), ['480p', 75])
  assert.deepEqual(r('seedance-1', 10), ['1080p', 250])
  assert.deepEqual(r('happyhorse-11', 15), ['720p', 405])
  assert.equal(code(job(d, 'ok', { image_debut: '/tmp/x.png' })), 'entree', 'pas d\'image de départ sur Dreamina')
  assert.equal(code(job(d, 'ok', { fichiers: ['/tmp/x.png'] })), 'entree')
})

test('modèles du menu : chacun reconnu par son seul motif, le brouillon jamais pris pour 2.5', () => {
  assert.deepEqual(Object.keys(MODELES), ['seedance-2-mini', 'seedance-2-rapide', 'seedance-2', 'seedance-25-brouillon', 'seedance-25',
    'seedance-15-pro', 'seedance-1-rapide', 'seedance-1', 'happyhorse-11', 'wan-3'])
  const nb = (s) => s.replace(/ /g, ' ')
  assert.equal(modeleLu(nb('Brouillon Dreamina Seedance 2.5 Crée des brouillons en 480p')), 'seedance-25-brouillon')
  assert.equal(modeleLu(nb('Dreamina Seedance 2.5 Des vidéos plus réalistes')), 'seedance-25')
  assert.equal(modeleLu(nb('Dreamina Seedance 1.5 Pro Génère des vidéos avec un son naturel synchronisé.')), 'seedance-15-pro')
  assert.equal(modeleLu(nb('Dreamina Seedance 1.0 Des performances de premier ordre')), 'seedance-1')
  assert.equal(modeleLu(nb('Dreamina Seedance 1.0 rapide Performances dignes d\'un film')), 'seedance-1-rapide')
  assert.equal(modeleLu(nb('HappyHorse 1.1 Mouvement fluide')), 'happyhorse-11')
  assert.equal(modeleLu(nb('Wan 3.0')), 'wan-3')
  assert.equal(modeleLu(nb('Dreamina Seedance 2.0 Mini Plus rentable')), 'seedance-2-mini')
  assert.equal(modeleLu(nb('Dreamina Seedance 2.0 rapide Génération rapide')), 'seedance-2-rapide')
  assert.equal(modeleLu(nb('Dreamina Seedance 2.0 Extrêmement polyvalent')), 'seedance-2')
  assert.equal(modeleLu('Seedance 2.0 Mini'), '', 'carte « Nouveautés » : pas le menu')
})

test('textes : rien de reconnu avant l\'envoi ; après, message d\'état ou fin sans vidéo = page (lent)', () => {
  assert.equal(classerPage('Crédits insuffisants', { apres: false }), null, 'aucun message relevé : jamais deviné')
  assert.equal(classerPage('x', { apres: true }), null)
  const a = classerPage('x', { apres: true, alertes: 'Une erreur est survenue.' })
  assert.deepEqual([a.code, a.lent], ['page', true])
  assert.match(a.message, /à relever/)
  assert.equal(classerPage('', { apres: true, finSansVideo: true }).code, 'page')
})

// --------------------------------------------------------------- pages ENREGISTRÉES (file://, réseau coupé)

const pagesReelles = fs.existsSync(path.join(RELEVE, 'dreamina-accueil-video.html'))

test('pages enregistrées : les sélecteurs du pilote trouvent les vrais éléments', { skip: !pagesReelles && 'relevé Dreamina absent' }, async () => {
  const navigateur = await chromium.launch({ executablePath: CHROME, headless: true })
  const sorties = []
  try {
    const ctx = await navigateur.newContext({ viewport: { width: 1366, height: 860 } })
    // Rien ne sort : seules les pages locales se chargent (CDN de Dreamina, polices, mesures : coupés)
    await ctx.route('**/*', (r) => r.request().url().startsWith('file:') ? r.continue() : (sorties.push(r.request().url()), r.abort()))
    const page = await ctx.newPage()
    const ouvrir = (f) => page.goto(`file://${RELEVE}/${f}`, { waitUntil: 'domcontentloaded' })

    await ouvrir('dreamina-accueil-video.html')
    assert.equal(await DREAMINA.prompt(page).getAttribute('aria-label'), 'Invite', 'champ du prompt (tiptap)')
    assert.equal(await DREAMINA.champ(page).isVisible(), true)
    assert.equal(await DREAMINA.connexion(page).first().isVisible().catch(() => false), false, 'connecté')
    const liste = DREAMINA.listeModeles(page).filter({ visible: true })
    assert.equal(await liste.count(), 1)
    assert.equal(modeleLu(await nomDe(liste.first())), 'seedance-2-mini', 'modèle affiché par la liste')
    assert.equal(await DREAMINA.envoi(page).getAttribute('aria-label'), 'Générer', 'bouton d\'envoi')
    assert.deepEqual(await DREAMINA.envoiBloque(DREAMINA.envoi(page)), { info: false, inactif: true }, 'inactif tant que le prompt est vide')
    assert.match(await nomDe(DREAMINA.formatResolution(page).filter({ visible: true }).first()), /16:9.*720P/)
    assert.equal(await nomDe(DREAMINA.boutonDuree(page).filter({ visible: true }).first()), '5s')
    assert.deepEqual(await DREAMINA.coutAnnonce(page), { debite: 0, liste: 55 }, '« 0 » débité, « 55 » barré')
    assert.equal(await DREAMINA.credits(page), 216)
    await DREAMINA.preparer(page) // onglet « Vidéo » déjà actif : rien à cliquer
    assert.equal(await DREAMINA.confirmerEnvoi(page), null)

    await ouvrir('dreamina-avant-de-continuer.html')
    const ok = await DREAMINA.confirmerEnvoi(page)
    assert.ok(ok, 'fenêtre « Avant de continuer » : bouton « Confirmer »')
    assert.equal((await ok.innerText()).trim(), 'Confirmer')
    assert.equal(await page.getByRole('dialog', { name: /avant de continuer/i }).getByRole('button', { name: /^annuler$/i }).count(), 1)

    await ouvrir('dreamina-attente.html')
    const jobAttente = { prompt: 'Un chat roux qui saute sur un canapé dans un salon lumineux' }
    assert.deepEqual(await DREAMINA.suiviGeneration(page, jobAttente), { enCours: true, fini: false }, 'badge « 7% » de la fiche du prompt')
    const infos = await DREAMINA.videosInfos(page, jobAttente)
    assert.ok(infos.some((v) => /record-loading-animation/.test(v.src) && v.visible), 'animation d\'attente présente')
    assert.ok(infos.every((v) => v.carte === false), 'aucune vidéo prise pendant la génération (animation comprise)')
    assert.equal(await DREAMINA.prompt(page).isVisible(), true, 'un seul champ visible sur la page de génération')

    await ouvrir('dreamina-resultat.html')
    assert.ok((await DREAMINA.videos(page)).some((s) => /^https:\/\/v16-cc\.capcut\.com\//.test(s)), 'vidéo rendue (CDN signé)')
    assert.equal(await page.locator('[class*="controls-tr"]').count() > 0, true, 'groupe d\'icônes en haut à droite (secours du bouton ↓)')
    assert.equal(await DREAMINA.credits(page), 256)

    await ouvrir('dreamina-filigrane.html')
    const croix = DREAMINA.croixFiligrane(page)
    assert.equal(await croix.count(), 1, 'croix « Fermer » de « Supprime les filigranes IA »')
    assert.equal(await croix.isVisible(), true)
    assert.equal(await page.getByRole('dialog', { name: /filigrane/i }).getByRole('switch').getAttribute('aria-checked'), 'false')
    await ctx.close()
  } finally {
    await navigateur.close()
  }
  assert.ok(sorties.every((u) => !u.startsWith('file:')))
})

// --------------------------------------------------------------- worker sur la fausse page

test('Mini 5 s 720p : réglages lus, UN envoi, « Confirmer » cliqué, vidéo enregistrée, filigranes jamais touchés', async () => {
  const d = dossiers()
  const r = await reussit(d, job(d, 'ok'))
  const f = r.fin
  assert.ok(f.fichier.startsWith(d.sortie + path.sep) && f.fichier.endsWith('.mp4'), f.fichier)
  assert.deepEqual([f.type, f.modele_utilise, f.duree, f.duree_reelle, f.format, f.resolution, f.cout_annonce, f.credits_restants],
    ['mp4', 'seedance-2-mini', 5, 5, '16:9', '720p', 25, 216])
  assert.deepEqual(r.lignes.filter((l) => l.t === 'etape').map((l) => l.etape), ['ouverture', 'saisie', 'generation', 'telechargement'])
  assert.equal(r.lignes.filter((l) => l.t === 'envoye').length, 1)
  const s = signauxDe(d)
  assert.equal(s.envoi.length, 1, 'UN clic d\'envoi')
  assert.equal(s.confirme.length, 1, '« Avant de continuer » confirmée une fois')
  assert.equal(s.ferme.length, 1, 'fenêtre des filigranes fermée par sa croix')
  assert.doesNotMatch(r.err, /reprise depuis sa source/, 'fichier venu du bouton ↓')
  assert.deepEqual([s.envoi[0].modele, s.envoi[0].format, s.envoi[0].res, s.envoi[0].duree, s.envoi[0].video, s.envoi[0].prompt],
    ['Dreamina Seedance 2.0 Mini', '16:9', '720P', 5, true, PROMPT])
  assert.equal(compteurDe(d).services.dreamina.n, 55, 'prix catalogue compté (remise non comptée)')
})

test('réglages de départ autres (Image, 9:16 · 1080P, 10s, « rapide ») : tous remis, format demandé pris', async () => {
  const d = dossiers()
  const r = await reussit(d, job(d, 'reglages', { format: '1:1' }))
  const [e] = signauxDe(d).envoi
  assert.deepEqual([e.modele, e.ref, e.format, e.res, e.duree, e.video], ['Dreamina Seedance 2.0 Mini', 'Références de tout type', '1:1', '720P', 5, true])
  assert.equal(r.fin.cout_annonce, 25)
})

test('Seedance 1.0 rapide 10 s : « Première et dernière image » réglé AVANT le modèle (grisé sinon), 720p, 50', async () => {
  const d = dossiers()
  const r = await reussit(d, job(d, 'ok', { modele: 'seedance-1-rapide', duree: 10 }))
  const [e] = signauxDe(d).envoi
  assert.deepEqual([e.modele, e.ref, e.res, e.duree, e.liste], ['Dreamina Seedance 1.0 rapide', 'Première et dernière image', '720P', 10, 50])
  assert.deepEqual([r.fin.modele_utilise, r.fin.resolution, r.fin.cout_annonce], ['seedance-1-rapide', '720p', 20])
})

test('départ en « Première et dernière image » (1.0 rapide, 1080P) : Mini remis en « Références de tout type »', async () => {
  const d = dossiers()
  await reussit(d, job(d, 'reglages_1x'))
  const [e] = signauxDe(d).envoi
  assert.deepEqual([e.modele, e.ref, e.res, e.duree, e.liste], ['Dreamina Seedance 2.0 Mini', 'Références de tout type', '720P', 5, 55])
})

test('résolution du modèle : brouillon 2.5 en 480p (75), Seedance 1.0 en 1080p (125)', async () => {
  const d1 = dossiers()
  const r1 = await reussit(d1, job(d1, 'ok', { modele: 'seedance-25-brouillon' }))
  assert.deepEqual([signauxDe(d1).envoi[0].res, signauxDe(d1).envoi[0].liste, r1.fin.resolution], ['480P', 75, '480p'])
  const d2 = dossiers()
  const r2 = await reussit(d2, job(d2, 'ok', { modele: 'seedance-1', plafond: 1000 }))
  assert.deepEqual([signauxDe(d2).envoi[0].res, signauxDe(d2).envoi[0].ref, signauxDe(d2).envoi[0].liste, r2.fin.resolution],
    ['1080P', 'Première et dernière image', 125, '1080p'])
})

test('plus cher que 120 (limite au solde) : le solde du site décide, rien envoyé s\'il ne couvre pas', async () => {
  const d1 = dossiers()
  const r1 = await echoue(d1, job(d1, 'ok', { modele: 'happyhorse-11', duree: 10, plafond: 1000 }), 'credits')
  assert.match(r1.fin.message, /216 restants, 240 demandés/)
  assert.deepEqual(signauxDe(d1).envoi, [])
  const d2 = dossiers()
  const r2 = await reussit(d2, job(d2, 'riche', { modele: 'happyhorse-11', duree: 10, plafond: 1000 }))
  assert.deepEqual([signauxDe(d2).envoi[0].modele, signauxDe(d2).envoi[0].liste, r2.fin.cout_annonce], ['HappyHorse 1.1', 270, 240])
  assert.equal(compteurDe(d2).services.dreamina.n, 270)
})

test('utilisation gratuite (« 0 », « 55 » barré) : coût débité 0, compteur à 55', async () => {
  const d = dossiers()
  const r = await reussit(d, job(d, 'gratuit'))
  assert.equal(r.fin.cout_annonce, 0)
  assert.equal(compteurDe(d).services.dreamina.n, 55)
})

test('solde plus bas que le coût : credits AVANT l\'envoi, rien envoyé ni compté', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'credits'), 'credits')
  assert.match(r.fin.message, /20 restants, 25 demandés/)
  assert.deepEqual(signauxDe(d).envoi, [])
  assert.ok(!r.lignes.some((l) => l.t === 'envoye'))
  assert.ok(r.fin.capture && fs.existsSync(r.fin.capture))
})

test('prix catalogue différent du relevé : page, rien envoyé', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'cout'), 'page')
  assert.match(r.fin.message, /coût annoncé 80 crédits, attendu 55/)
  assert.deepEqual(signauxDe(d).envoi, [])
})

test('pas connecté : session ; éditeur disparu : page avec capture', async () => {
  const d1 = dossiers()
  await echoue(d1, job(d1, 'session'), 'session')
  const d2 = dossiers()
  const r = await echoue(d2, job(d2, 'change'), 'page')
  assert.ok(r.fin.capture && fs.existsSync(r.fin.capture))
})

test('message d\'état sans génération après l\'envoi : page (à relever), un seul envoi, capture', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'alerte'), 'page')
  assert.match(r.fin.message, /message de Dreamina sans vidéo.*Une erreur est survenue/)
  assert.equal(signauxDe(d).envoi.length, 1)
  assert.equal(r.lignes.filter((l) => l.t === 'envoye').length, 1)
  assert.ok(r.fin.capture && fs.existsSync(r.fin.capture))
})

test('progression puis plus rien (refus probable) : page, jamais un second envoi', async () => {
  const d = dossiers()
  const r = await echoue(d, job(d, 'sans_video'), 'page')
  assert.match(r.fin.message, /terminée sans vidéo/)
  assert.equal(signauxDe(d).envoi.length, 1)
})

test('génération qui n\'en finit pas : delai, un seul envoi', async () => {
  const d = dossiers()
  await echoue(d, job(d, 'lent', { delai_s: 5 }), 'delai')
  assert.equal(signauxDe(d).envoi.length, 1)
})

test('génération d\'avant du MÊME prompt et fil d\'inspiration chargé en route : seule la nouvelle vidéo est prise', async () => {
  const d = dossiers()
  const r = await reussit(d, job(d, 'ancien'))
  assert.equal(r.fin.duree_reelle, 5, 'pas l\'ancienne (3 s) ni celle du fil (2 s)')
})

test('↓ sans fichier : vidéo reprise depuis la source de sa balise ; ↓ nommé « Télécharger » ; sans « Avant de continuer »', async () => {
  const d1 = dossiers()
  const r1 = await reussit(d1, job(d1, 'dl_rate'))
  assert.match(r1.err, /reprise depuis sa source/)
  assert.equal(r1.fin.duree_reelle, 5)
  const d2 = dossiers()
  await reussit(d2, job(d2, 'nom_dl'))
  const d3 = dossiers()
  await reussit(d3, job(d3, 'sans_confirmation'))
  assert.deepEqual(signauxDe(d3).confirme, [])
  assert.equal(signauxDe(d3).envoi.length, 1)
})
