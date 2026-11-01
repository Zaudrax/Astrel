// Noyau commun et gestes humains (plan P40 lot A2), SANS navigateur : aucun Chrome n'est lancé
// ici (ceux de test/worker.test.mjs passent un par un). `cd imagegen && node --test`.
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { ECART_GENERATION_MS, autorise, courbe, pointCible } from '../humain.mjs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { CATALOGUE, compteur, lireCatalogue, nomFichier, optionsLancement, verrouiller } from '../noyau.mjs'

const racines = []
after(() => { for (const r of racines) fs.rmSync(r, { recursive: true, force: true }) })
const tmp = () => { const r = fs.mkdtempSync(path.join(os.tmpdir(), 'noyau-test-')); racines.push(r); return r }
const a = (h, m = 0) => new Date(2026, 9, 27, h, m, 0, 0).getTime()

// --------------------------------------------------------------- horaires

test('jamais la nuit : 0 h à 7 h refusé, reprise à 07:00', () => {
  const nuit = { debut: 0, fin: 7 }
  for (const h of [0, 3, 6]) {
    const r = autorise({ maintenant: a(h, 30), nuit })
    assert.equal(r?.code, 'nuit', `à ${h} h 30`)
    assert.equal(r.reprise, '07:00')
  }
  assert.equal(autorise({ maintenant: a(7, 0), nuit }), null)
  assert.equal(autorise({ maintenant: a(23, 59), nuit }), null)
  assert.equal(autorise({ maintenant: a(3), nuit: null }), null) // rythme de test : nuit ignorée
})

test('30 s au moins entre deux générations d\'un compte sur un même site (30/10), reste en ms', () => {
  assert.equal(ECART_GENERATION_MS, 30000)
  const r = autorise({ maintenant: a(14, 0) + 10000, dernierEnvoi: a(14, 0), nuit: null })
  assert.equal(r.code, 'rythme')
  assert.equal(r.reste, 20000)
  assert.equal(r.reprise, '14:01')
  assert.match(r.message, /30 s au moins .* sur un même site : encore 20 s/)
  assert.equal(autorise({ maintenant: a(14, 0) + 30000, dernierEnvoi: a(14, 0), nuit: null }), null)
  assert.equal(autorise({ maintenant: a(14, 0), dernierEnvoi: 0, nuit: null }), null)
  // La nuit passe avant l'écart
  assert.equal(autorise({ maintenant: a(2), dernierEnvoi: a(2) - 1000, nuit: { debut: 0, fin: 7 } }).code, 'nuit')
})

// --------------------------------------------------------------- souris

test('clic : jamais au centre exact, toujours dans l\'élément, loin du bord', () => {
  const box = { x: 100, y: 50, width: 200, height: 40 }
  for (let i = 0; i < 2000; i++) {
    const p = pointCible(box)
    assert.ok(p.x >= box.x + 0.15 * box.width && p.x <= box.x + 0.85 * box.width, `x ${p.x}`)
    assert.ok(p.y >= box.y + 0.15 * box.height && p.y <= box.y + 0.85 * box.height, `y ${p.y}`)
    assert.ok(Math.abs(p.x - 200) >= 0.05 * box.width - 1e-9, 'décalé du centre en x')
    assert.ok(Math.abs(p.y - 70) >= 0.05 * box.height - 1e-9, 'décalé du centre en y')
  }
})

test('souris : courbe qui finit pile sur la cible et s\'écarte de la ligne droite', () => {
  const de = { x: 10, y: 10 }
  const vers = { x: 610, y: 410 }
  let ecartMax = 0
  for (let k = 0; k < 50; k++) {
    const pts = courbe(de, vers, 30)
    assert.equal(pts.length, 30)
    assert.deepEqual(pts.at(-1), vers)
    // Distance de chaque point à la droite (de, vers)
    const dx = vers.x - de.x
    const dy = vers.y - de.y
    const long = Math.hypot(dx, dy)
    const d = Math.max(...pts.map((p) => Math.abs(dy * (p.x - de.x) - dx * (p.y - de.y)) / long))
    ecartMax = Math.max(ecartMax, d)
    assert.ok(d > 5, `courbe trop droite (${d.toFixed(1)} px)`)
  }
  assert.ok(ecartMax > 40)
  // Vitesse variable : pas courts au départ, plus longs au milieu
  const pts = courbe({ x: 0, y: 0 }, { x: 1000, y: 0 }, 40, () => 0.5)
  const pas = pts.map((p, i) => Math.hypot(p.x - (i ? pts[i - 1].x : 0), p.y - (i ? pts[i - 1].y : 0)))
  assert.ok(pas[0] < pas[20] / 3, `départ ${pas[0].toFixed(1)}, milieu ${pas[20].toFixed(1)}`)
})

// --------------------------------------------------------------- Chrome réel, sans « stealth »

test('lancement : Chrome réel, bac à sable, sans --enable-automation ; visible = fenêtre et UA natifs', () => {
  const m = optionsLancement({ affichage: 'masquee', chrome: '/bin/chrome', ua: 'UA-essai' })
  assert.equal(m.headless, true)
  assert.equal(m.executablePath, '/bin/chrome')
  assert.equal(m.chromiumSandbox, true)
  assert.deepEqual(m.ignoreDefaultArgs, ['--enable-automation', '--password-store=basic', '--use-mock-keychain'])
  assert.deepEqual(m.viewport, { width: 1366, height: 860 })
  assert.equal(m.userAgent, 'UA-essai')
  const v = optionsLancement({ affichage: 'visible', chrome: '/bin/chrome', ua: 'UA-essai' })
  assert.equal(v.headless, false)
  assert.equal(v.viewport, null, 'taille de fenêtre native')
  assert.ok(!('userAgent' in v), 'User-Agent natif')
  assert.deepEqual(v.ignoreDefaultArgs, ['--enable-automation', '--password-store=basic', '--use-mock-keychain'])
  // Rien qui masque l'automatisation ou cache la fenêtre
  for (const o of [m, v]) {
    const args = JSON.stringify(o.args || [])
    assert.ok(!/AutomationControlled|window-position|start-minimized|stealth/i.test(args), args)
  }
  assert.equal(optionsLancement({ affichage: 'n importe quoi', chrome: '/bin/chrome', ua: '' }).headless, true)
})

// --------------------------------------------------------------- compteur du compte

test('compteur v2 : par service en unités, écart daté PAR SITE (30/10), ancien fichier v1 repris', () => {
  const profil = path.join(tmp(), 'amw')
  let t = a(14)
  const horloge = () => t
  const g = compteur(profil, 'gemini', 'jour', horloge)
  g.ajouter()
  t += 1000
  const f = compteur(profil, 'flow', '24h', horloge)
  assert.equal(f.etat.n, 0, 'services séparés sur un même compte Google')
  f.ajouter(20) // 20 crédits d'une vidéo
  const lu = JSON.parse(fs.readFileSync(`${profil}.compteur.json`, 'utf8'))
  assert.equal(lu.v, 2)
  assert.equal(lu.services.gemini.n, 1)
  assert.equal(lu.services.flow.n, 20)
  assert.equal(lu.dernier_envoi, t, 'dernier envoi, tous services (journal)')
  assert.equal(compteur(profil, 'gemini', 'jour', horloge).dernierEnvoi, t - 1000, 'écart de Gemini : son propre envoi, pas celui de Flow')
  assert.equal(lu.services.flow.dernier_envoi, t)
  assert.equal((fs.statSync(`${profil}.compteur.json`).mode & 0o777), 0o600)
  // fin() : dernière ouverture seulement, l'envoi reste daté
  t += 5000
  compteur(profil, 'gemini', 'jour', horloge).fin()
  const lu2 = JSON.parse(fs.readFileSync(`${profil}.compteur.json`, 'utf8'))
  assert.equal(lu2.dernier, t)
  assert.equal(lu2.dernier_envoi, t - 5000)
  // Lendemain : Gemini (jour) remis à zéro, Flow (24 h depuis la 1re unité) aussi après 24 h
  t = a(14) + 86400000 + 2000
  assert.equal(compteur(profil, 'gemini', 'jour', horloge).etat.n, 0)
  assert.equal(compteur(profil, 'flow', '24h', horloge).etat.n, 0)
  // v1 (P14) : Gemini seul ; l'écart part de la dernière activité s'il y a eu une image
  const v1 = path.join(tmp(), 'v1')
  fs.writeFileSync(`${v1}.compteur.json`, JSON.stringify({ jour: '2026-10-27', n: 4, debut: a(9), dernier: a(13) }))
  t = a(13, 5)
  const c = compteur(v1, 'gemini', 'jour', horloge)
  assert.equal(c.etat.n, 4)
  assert.equal(c.dernierEnvoi, a(13))
  assert.equal(compteur(v1, 'chatgpt', '24h', horloge).etat.n, 0)
  // Illisible : repart de zéro
  fs.writeFileSync(`${v1}.compteur.json`, '{pas du json')
  assert.equal(compteur(v1, 'gemini', 'jour', horloge).etat.n, 0)
})

// --------------------------------------------------------------- catalogue et nom de fichier

test('catalogue config/ia-web.json lu comme ia_web.py ; illisible = vide', () => {
  const cat = lireCatalogue(CATALOGUE)
  assert.equal(cat.gemini.type, 'image')
  assert.equal(cat.gemini.url, 'https://gemini.google.com/app')
  assert.equal(cat.gemini.modeles[0].id, 'defaut')
  assert.equal(cat.chatgpt.fenetre, '24h')
  const f = path.join(tmp(), 'cat.json')
  fs.writeFileSync(f, JSON.stringify({ services: {
    flow: { type: 'video', url: 'http://pas-https', plafond: 50, fenetre: '24h', modeles: [{ id: 'lite', cout: 10 }, { id: 'Mauvais Id', cout: 1 }, { id: 'fast', cout: 99999 }] },
    'Bad Name': { type: 'image' },
    autre: { type: 'son' }
  } }))
  const c = lireCatalogue(f)
  assert.deepEqual(Object.keys(c), ['flow'])
  assert.equal(c.flow.url, '', 'https seulement')
  assert.deepEqual(c.flow.modeles.map((m) => [m.id, m.cout]), [['lite', 10], ['fast', 1000]])
  fs.writeFileSync(f, 'illisible')
  assert.deepEqual(lireCatalogue(f), {})
})

test('nom de fichier : date, service, prompt sans accents', () => {
  const d = new Date(2026, 9, 27, 9, 5, 7)
  assert.equal(nomFichier('gemini', 'Génère une image : un Caméléon !', 'png', d), '20261027-090507-gemini-un-cameleon.png')
  assert.equal(nomFichier('flow', '!!!', 'mp4', d), '20261027-090507-flow-media.mp4')
})

// --------------------------------------------------------------- verrou du compte (P40 A2, corrections 1)

test('verrou : .lock vide ou illisible (autre worker entre open et write) = occupe, jamais supprimé', () => {
  const profil = path.join(tmp(), 'vide')
  for (const contenu of ['', 'pas-un-pid']) {
    fs.writeFileSync(`${profil}.lock`, contenu)
    assert.throws(() => verrouiller(profil), (e) => e.code === 'occupe', `contenu ${JSON.stringify(contenu)}`)
    assert.equal(fs.readFileSync(`${profil}.lock`, 'utf8'), contenu, 'verrou de l\'autre laissé tel quel')
  }
  // pid mort lisible : toujours repris
  fs.writeFileSync(`${profil}.lock`, '999999999')
  const liberer = verrouiller(profil)
  assert.equal(fs.readFileSync(`${profil}.lock`, 'utf8'), String(process.pid))
  liberer()
  assert.ok(!fs.existsSync(`${profil}.lock`))
})

test('verrou rendu à la sortie du process (garde de 185 s : process.exit) ; jamais celui d\'un autre', () => {
  const noyau = path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), 'noyau.mjs')
  const profil = path.join(tmp(), 'sortie')
  const script = (fin) => `import { verrouiller } from ${JSON.stringify(noyau)}
    const liberer = verrouiller(${JSON.stringify(profil)}); ${fin}`
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', script('process.exit(1)')], { encoding: 'utf8', timeout: 20000 })
  assert.equal(r.status, 1, r.stderr)
  assert.ok(!fs.existsSync(`${profil}.lock`), 'verrou rendu par process.exit')
  // Rendu puis repris par un autre avant la sortie : le verrou de l'autre reste
  const r2 = spawnSync(process.execPath, ['--input-type=module', '-e',
    script(`liberer(); (await import('node:fs')).default.writeFileSync(${JSON.stringify(`${profil}.lock`)}, '1'); process.exit(0)`)],
  { encoding: 'utf8', timeout: 20000 })
  assert.equal(r2.status, 0, r2.stderr)
  assert.equal(fs.readFileSync(`${profil}.lock`, 'utf8'), '1', 'verrou de l\'autre intact')
})
