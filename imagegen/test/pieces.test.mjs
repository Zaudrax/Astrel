// Lot A3 du plan P40, SANS navigateur : filtre des pièces jointes (pieces.mjs), noms de fichiers
// uniques (noyau.poserSansEcraser), motifs relevés du catalogue, sites (moteurs.mjs). Aucun Chrome
// ici (ceux de test/worker.test.mjs passent un par un). `cd imagegen && node --test`.
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { PIECES_MAX, dossierProjets, lirePiece, lirePieces, refusPiece } from '../pieces.mjs'
import { lireCatalogue, poserSansEcraser } from '../noyau.mjs'
import { MOTEURS, classer } from '../moteurs.mjs'
import { hotePermis, tailleReelle } from '../sites/bing.mjs'
import { motifModele } from '../sites/commun.mjs'

const ASTREL = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')
const JPG = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 0x10, 0x4a, 0x46, 0x49, 0x46, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0])

const racines = []
after(() => { for (const r of racines) fs.rmSync(r, { recursive: true, force: true }) })
function bac () {
  const r = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pieces-test-')))
  racines.push(r)
  return r
}
const code = (f) => { try { f(); return 'ok' } catch (e) { return `${e.code} ${e.message}` } }

// --------------------------------------------------------------- filtre des pièces jointes

test('pièce permise : octets lus, type reconnu dans les octets, nom nettoyé', () => {
  const r = bac()
  fs.writeFileSync(path.join(r, 'mon chat.png'), PNG)
  fs.writeFileSync(path.join(r, 'photo.bin'), JPG) // extension trompeuse : le type vient des octets
  const [a, b] = lirePieces([path.join(r, 'mon chat.png'), path.join(r, 'photo.bin')], { racines: [r] })
  assert.equal(a.name, 'mon_chat.png')
  assert.equal(a.mimeType, 'image/png')
  assert.deepEqual(a.buffer, PNG)
  assert.equal(a.chemin, path.join(r, 'mon chat.png'))
  assert.equal(b.mimeType, 'image/jpeg')
})

test('pièce refusée : hors des racines, relative, introuvable, pas une image, trop lourde', () => {
  const r = bac()
  const ailleurs = bac()
  fs.writeFileSync(path.join(ailleurs, 'a.png'), PNG)
  fs.writeFileSync(path.join(r, 'texte.png'), 'pas une image')
  fs.writeFileSync(path.join(r, 'lourde.png'), Buffer.concat([PNG, Buffer.alloc(5 * 1024 * 1024)]))
  const o = { racines: [r] }
  assert.match(code(() => lirePiece(path.join(ailleurs, 'a.png'), o)), /^fichiers .*hors des pièces jointes/)
  assert.match(code(() => lirePiece('relatif.png', o)), /^fichiers .*chemin absolu/)
  assert.match(code(() => lirePiece(path.join(r, 'absente.png'), o)), /^fichiers .*introuvable/)
  assert.match(code(() => lirePiece(path.join(r, 'texte.png'), o)), /^fichiers .*pas une image/)
  assert.match(code(() => lirePiece(path.join(r, 'lourde.png'), o)), /^fichiers .*5 Mo/)
  assert.match(code(() => lirePiece(42, o)), /^fichiers .*chemin absolu/)
  assert.match(code(() => lirePiece(`${r}/a\0.png`, o)), /^fichiers .*chemin absolu/)
})

test('pièce refusée : lien vers l\'extérieur, lien dur, tube nommé, dossier ou nom caché, nom de secret', () => {
  const r = bac()
  const ailleurs = bac()
  fs.writeFileSync(path.join(ailleurs, 'dehors.png'), PNG)
  fs.symlinkSync(path.join(ailleurs, 'dehors.png'), path.join(r, 'lien.png'))
  fs.writeFileSync(path.join(r, 'a.png'), PNG)
  fs.linkSync(path.join(r, 'a.png'), path.join(r, 'dur.png'))
  execFileSync('mkfifo', [path.join(r, 'tube.png')])
  fs.mkdirSync(path.join(r, '.cache'))
  fs.writeFileSync(path.join(r, '.cache', 'b.png'), PNG)
  fs.writeFileSync(path.join(r, 'api_key.png'), PNG)
  fs.writeFileSync(path.join(r, 'whatsapp-auth.png'), PNG)
  const o = { racines: [r] }
  assert.match(code(() => lirePiece(path.join(r, 'lien.png'), o)), /hors des pièces jointes/, 'lien résolu vers son vrai chemin')
  assert.match(code(() => lirePiece(path.join(r, 'dur.png'), o)), /pas un fichier ordinaire/)
  const t0 = Date.now()
  assert.match(code(() => lirePiece(path.join(r, 'tube.png'), o)), /pas un fichier ordinaire/)
  assert.ok(Date.now() - t0 < 1000, 'un tube nommé ne bloque pas')
  assert.match(code(() => lirePiece(path.join(r, '.cache', 'b.png'), o)), /caché/)
  assert.match(code(() => lirePiece(path.join(r, 'api_key.png'), o)), /secret/)
  assert.match(code(() => lirePiece(path.join(r, 'whatsapp-auth.png'), o)), /secret/)
})

test('dossiers privés d\'Astrel refusés AVANT toute ouverture ; pièces jointes et images IA permises', () => {
  // 05/11 : le dossier qui contient Astrel tient lieu de dossier des projets (installation dans ~/Projets),
  // aucun chemin de la machine écrit ici
  const projets = fs.realpathSync(path.dirname(ASTREL))
  const racinesJ = [projets]
  for (const d of ['config/ia-web.json', 'runtime/imagegen/profils/amw/Default/Cookies', 'runtime/session.json', 'hooks/x.png', 'vault/x.png', 'logs/x.png']) {
    assert.match(refusPiece(path.join(ASTREL, d), racinesJ), /dossier privé/, d)
  }
  assert.equal(refusPiece(path.join(ASTREL, 'runtime', 'attachments', 'photo.png'), racinesJ), '')
  assert.equal(refusPiece(path.join(projets, 'Images-IA', 'x.png'), racinesJ), '')
  assert.match(refusPiece('/etc/passwd', racinesJ), /hors des pièces jointes et du dossier des projets/)
  assert.match(refusPiece(projets, racinesJ), /hors des pièces jointes/, 'la racine elle-même n\'est pas un fichier permis')
})

test('dossier des projets : celui du cœur (ASTREL_WORKDIR), sinon ~/Projets ; « ~ » développé (05/11)', () => {
  assert.equal(dossierProjets({ ASTREL_WORKDIR: path.join(os.tmpdir(), 'mes projets') }), path.join(os.tmpdir(), 'mes projets'))
  assert.equal(dossierProjets({}), path.join(os.homedir(), 'Projets'))
  assert.equal(dossierProjets({ ASTREL_WORKDIR: '  ' }), path.join(os.homedir(), 'Projets'))
  assert.equal(dossierProjets({ ASTREL_WORKDIR: '~/Travail' }), path.join(os.homedir(), 'Travail'))
  assert.ok(path.isAbsolute(dossierProjets({ ASTREL_WORKDIR: 'relatif' })), 'toujours absolu')
})

test('six pièces au plus, jamais deux fois la même, liste attendue', () => {
  const r = bac()
  const chemins = []
  for (let i = 0; i < 7; i++) { fs.writeFileSync(path.join(r, `p${i}.png`), PNG); chemins.push(path.join(r, `p${i}.png`)) }
  assert.equal(PIECES_MAX, 6)
  assert.equal(lirePieces(chemins.slice(0, 6), { racines: [r] }).length, 6)
  assert.match(code(() => lirePieces(chemins, { racines: [r] })), /^fichiers 6 pièces jointes au plus/)
  assert.match(code(() => lirePieces([chemins[0], chemins[0]], { racines: [r] })), /deux fois/)
  assert.match(code(() => lirePieces('un-seul.png', { racines: [r] })), /^fichiers .*liste/)
  // Rang de la pièce fautive dans le message
  fs.writeFileSync(path.join(r, 'mauvaise.png'), 'x')
  assert.match(code(() => lirePieces([chemins[0], path.join(r, 'mauvaise.png')], { racines: [r] })), /pièce jointe 2 refusée/)
})

// --------------------------------------------------------------- noms uniques (Bing : 4 variantes)

test('enregistrement : jamais un fichier écrasé, « -2 », « -3 »… dans la même seconde', () => {
  const r = bac()
  const noms = []
  for (let i = 1; i <= 4; i++) {
    const tmp = path.join(r, '.telechargement')
    fs.writeFileSync(tmp, `variante ${i}`)
    const { fichier, nom } = poserSansEcraser(tmp, r, '20261028-120000-bing-un-chat.png')
    noms.push(nom)
    assert.equal(fs.readFileSync(fichier, 'utf8'), `variante ${i}`)
    assert.ok(!fs.existsSync(tmp), 'temporaire retiré')
    assert.equal(fs.statSync(fichier).nlink, 1, 'un seul nom : montrable par medias.py')
  }
  assert.deepEqual(noms, ['20261028-120000-bing-un-chat.png', '20261028-120000-bing-un-chat-2.png',
    '20261028-120000-bing-un-chat-3.png', '20261028-120000-bing-un-chat-4.png'])
  for (let i = 1; i <= 4; i++) assert.equal(fs.readFileSync(path.join(r, noms[i - 1]), 'utf8'), `variante ${i}`, 'aucune variante écrasée')
})

// --------------------------------------------------------------- catalogue : motifs relevés

test('catalogue : motif par modèle et menu_modeles gardés s\'ils sont valides, jamais inventés', () => {
  const f = path.join(bac(), 'ia-web.json')
  fs.writeFileSync(f, JSON.stringify({ services: { gemini: { type: 'image', menu_modeles: 'mod[eè]le', modeles: [
    { id: 'defaut' }, { id: 'a', libelle: 'Modèle A', motif: '^\\s*mod[eè]le a' }, { id: 'b', libelle: 'B', motif: '(' }, { id: 'c', motif: 'x'.repeat(201) }
  ] }, chatgpt: { type: 'image', menu_modeles: '[' } } }))
  const c = lireCatalogue(f)
  assert.equal(c.gemini.menu_modeles, 'mod[eè]le')
  assert.equal(c.gemini.modeles[1].motif, '^\\s*mod[eè]le a')
  assert.ok(!('motif' in c.gemini.modeles[2]), 'expression invalide écartée')
  assert.ok(!('motif' in c.gemini.modeles[3]), 'trop longue écartée')
  assert.ok(!('menu_modeles' in c.chatgpt))
  // Sans motif : le libellé EXACT du catalogue, pris tel quel (caractères spéciaux échappés)
  assert.ok(motifModele({ id: 'x', libelle: '2.5 Flash (aperçu)' }).test('Gemini 2.5 Flash (aperçu)'))
  assert.ok(!motifModele({ id: 'x', libelle: '2.5 Flash' }).test('2x5 Flash'))
})

test('vrai catalogue : modèles des sites d\'images NON relevés, seul « defaut » (rien d\'inventé)', () => {
  const c = lireCatalogue()
  for (const s of ['gemini', 'chatgpt', 'bing']) {
    assert.deepEqual(c[s].modeles.map((m) => m.id), ['defaut'], s)
    assert.ok(!c[s].menu_modeles, s)
  }
  const brut = JSON.parse(fs.readFileSync(path.join(ASTREL, 'config', 'ia-web.json'), 'utf8'))
  for (const s of ['gemini', 'chatgpt', 'bing']) assert.equal(brut.services[s].modeles_releves, false, s)
})

// --------------------------------------------------------------- sites

test('sites : un module par site, Bing sans compte, sans pièce jointe ni modèle', () => {
  assert.deepEqual(Object.keys(MOTEURS), ['gemini', 'chatgpt', 'bing'])
  for (const s of Object.values(MOTEURS)) {
    assert.equal(s.type, 'image')
    for (const f of ['champ', 'connexion', 'prompt']) assert.equal(typeof s[f], 'function', `${s.nom}.${f}`)
    assert.ok(s.hotesConnexion instanceof RegExp)
  }
  assert.equal(typeof MOTEURS.gemini.choisirModele, 'function')
  assert.equal(typeof MOTEURS.chatgpt.choisirModele, 'function')
  assert.equal(typeof MOTEURS.gemini.joindre, 'function')
  assert.equal(typeof MOTEURS.chatgpt.joindre, 'function')
  assert.equal(MOTEURS.bing.sansCompte, true)
  assert.equal(MOTEURS.bing.joindre, undefined)
  assert.equal(MOTEURS.bing.choisirModele, undefined)
  assert.equal(MOTEURS.bing.prompt('un chat'), 'un chat', 'générateur : demande telle quelle')
  assert.equal(MOTEURS.chatgpt.prompt('un chat'), 'Génère une image : un chat')
  assert.ok(MOTEURS.chatgpt.hotesConnexion.test('auth.openai.com'))
  assert.ok(MOTEURS.bing.hotesConnexion.test('login.live.com'))
  assert.ok(!MOTEURS.bing.hotesConnexion.test('www.bing.com'))
})

test('ChatGPT : limite du plan gratuit = quota, reprise « +23h »', () => {
  const r = classer("You've hit the free plan limit for image generation requests. You can create more images when the limit resets in 23 hours and 59 minutes.")
  assert.equal(r.code, 'quota')
  assert.equal(r.reprise, '+23h')
  assert.equal(classer("I can't create that image because the request violates our content policies.").code, 'refus')
})

test('Bing : image en taille réelle, chez Bing ou sur l\'hôte de la page seulement', () => {
  assert.equal(tailleReelle('https://th.bing.com/th/id/OIG1.abc?w=270&h=270&c=6&pid=ImgGn'), 'https://th.bing.com/th/id/OIG1.abc')
  assert.equal(tailleReelle('https://tse1.mm.bing.net/th/id/OIG2.x?w=1'), 'https://tse1.mm.bing.net/th/id/OIG2.x')
  assert.equal(tailleReelle('https://evil.example/th/id/OIG1.abc?w=1'), '', 'autre hôte')
  assert.equal(tailleReelle('http://th.bing.com/th/id/OIG1.abc'), '', 'https seulement chez Bing')
  assert.equal(tailleReelle('https://th.bing.com/th/id/R.abc'), '', 'pas une image générée')
  assert.equal(tailleReelle('http://127.0.0.1:9/th/id/OIG1.v1?w=2', '127.0.0.1'), 'http://127.0.0.1:9/th/id/OIG1.v1')
  assert.equal(tailleReelle('javascript:alert(1)'), '')
  assert.equal(tailleReelle('pas une adresse'), '')
  // Après redirection : hôte final revérifié
  assert.ok(hotePermis('https://tse3.mm.bing.net/th/id/OIG1.abc'))
  assert.ok(!hotePermis('https://bing.com.evil.example/x'))
  assert.ok(!hotePermis('http://th.bing.com/x'))
  assert.ok(hotePermis('http://127.0.0.1:9/th/id/OIG1.v1', '127.0.0.1'))
  assert.ok(!hotePermis('http://127.0.0.2:9/x', '127.0.0.1'))
})
