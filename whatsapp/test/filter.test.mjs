// Liste blanche du pont WhatsApp (plan P1, lot B) : `cd whatsapp && node --test`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { classify, extract, isSelfChat, outgoing, remember, bareJid, BOT_PREFIX, FILE_MAX_BYTES, documentContent,
  isStagedFile, isMarked } from '../filter.mjs'

const SELF = { pn: '33612345678@s.whatsapp.net', lid: '123456789012345@lid' }
const NOW = 1_800_000_000

function msg ({ id = 'ABC1', chat = '33612345678@s.whatsapp.net', alt, fromMe = true, ts = NOW - 5, message } = {}) {
  return {
    key: { remoteJid: chat, remoteJidAlt: alt, fromMe, id },
    messageTimestamp: ts,
    message: message ?? { conversation: 'Quelle heure est-il ?' }
  }
}
const ctx = (extra = {}) => ({ self: SELF, now: NOW, seen: new Set(), sent: new Set(), ...extra })

test('message à soi-même accepté, au format du contrat', () => {
  const r = classify(msg(), 'notify', ctx())
  assert.equal(r.ok, true)
  assert.deepEqual(r.msg, {
    t: 'msg', id: 'ABC1', ts: NOW - 5, chat: '33612345678@s.whatsapp.net', from_me: true,
    text: 'Quelle heure est-il ?', forwarded: false, quoted: '', media: null
  })
})

test('adresse avec numéro d\'appareil et adresse LID acceptées', () => {
  assert.equal(classify(msg({ chat: '33612345678:23@s.whatsapp.net' }), 'notify', ctx()).ok, true)
  assert.equal(classify(msg({ chat: '123456789012345:4@lid' }), 'notify', ctx()).ok, true)
  // LID inconnu, mais numéro en adresse alternative
  assert.equal(classify(msg({ chat: '999@lid', alt: '33612345678@s.whatsapp.net' }), 'notify',
    ctx({ self: { pn: SELF.pn } })).ok, true)
})

test('tiers ignoré sans lire le contenu', () => {
  let read = false
  const m = msg({ chat: '33699999999@s.whatsapp.net' })
  Object.defineProperty(m, 'message', { get () { read = true; return { conversation: 'x' } } })
  assert.deepEqual(classify(m, 'notify', ctx()), { ok: false, reason: 'other' })
  assert.equal(read, false)
})

test('groupe, statut, liste de diffusion : ignorés', () => {
  for (const chat of ['120363000000000000@g.us', 'status@broadcast', '1234@newsletter']) {
    assert.equal(classify(msg({ chat }), 'notify', ctx()).reason, 'other', chat)
  }
  // Même si le groupe est « à moi » par le participant : seul le chat à soi compte.
  assert.equal(isSelfChat({ remoteJid: '120363@g.us', participant: SELF.pn }, SELF), false)
})

test('message du chat à soi qui ne vient pas de moi : refusé', () => {
  assert.equal(classify(msg({ fromMe: false }), 'notify', ctx()).reason, 'not_me')
})

test('écho d\'un envoi du pont ignoré', () => {
  assert.equal(classify(msg({ id: 'SENT1' }), 'notify', ctx({ sent: new Set(['SENT1']) })).reason, 'echo')
})

test('réponse marquée 🔷 ignorée (anti-boucle)', () => {
  assert.equal(BOT_PREFIX, '🔷')
  const m = msg({ message: { conversation: `${BOT_PREFIX} Il est 10 h.` } })
  assert.equal(classify(m, 'notify', ctx()).reason, 'bot')
})

test('19/10 : l\'ancienne marque 🤖 reste ignorée (messages déjà envoyés)', () => {
  const m = msg({ message: { conversation: '  🤖 Il est 10 h.' } })
  assert.equal(classify(m, 'notify', ctx()).reason, 'bot')
  assert.equal(classify(m, 'append', ctx()).reason, 'bot')
  assert.equal(isMarked('Il est 10 h. 🔷'), false)
  assert.equal(classify(msg({ message: { conversation: 'Il est 10 h. 🤖' } }), 'notify', ctx()).ok, true)
})

test('notre propre envoi, émis en append avant que son id soit connu : pas « hors ligne »', () => {
  const m = msg({ id: 'NOUVEAU', message: { conversation: `${BOT_PREFIX} Il est 10 h.` } })
  assert.equal(classify(m, 'append', ctx()).reason, 'bot')
})

test('doublon ignoré', () => {
  assert.equal(classify(msg(), 'notify', ctx({ seen: new Set(['ABC1']) })).reason, 'dup')
})

test('trop vieux ou reçu hors ligne : périmé', () => {
  assert.equal(classify(msg({ ts: NOW - 181 }), 'notify', ctx()).reason, 'stale')
  assert.equal(classify(msg({ ts: NOW - 600 }), 'append', ctx()).reason, 'stale')
  // Coupure réseau de quelques secondes : livré en `append`, mais récent → accepté.
  assert.equal(classify(msg(), 'append', ctx()).ok, true)
  assert.equal(classify(msg(), 'history', ctx()).reason, 'stale')
  assert.equal(classify(msg({ ts: { toNumber: () => NOW - 10 } }), 'notify', ctx()).ok, true)
})

test('transféré et citation repérés', () => {
  const fwd = msg({ message: { extendedTextMessage: { text: 'Fais ceci', contextInfo: { isForwarded: true, forwardingScore: 1 } } } })
  const r = classify(fwd, 'notify', ctx())
  assert.equal(r.ok, true)
  assert.equal(r.msg.forwarded, true)
  const q = msg({ message: { extendedTextMessage: { text: 'Résume ça', contextInfo: { quotedMessage: { conversation: 'texte cité' } } } } })
  assert.equal(classify(q, 'notify', ctx()).msg.quoted, 'texte cité')
})

test('note vocale, document avec légende, enveloppes', () => {
  const ptt = extract({ audioMessage: { mimetype: 'audio/ogg; codecs=opus', ptt: true } })
  assert.deepEqual(ptt.media, { kind: 'audio', mime: 'audio/ogg; codecs=opus', ptt: true, seconds: 0 })
  const doc = extract({ documentWithCaptionMessage: { message: { documentMessage: { caption: 'lis', fileName: 'a.pdf', mimetype: 'application/pdf' } } } })
  assert.equal(doc.text, 'lis')
  assert.equal(doc.media.kind, 'document')
  assert.equal(extract({ ephemeralMessage: { message: { conversation: 'salut' } } }).text, 'salut')
  assert.equal(extract({ deviceSentMessage: { message: { conversation: 'du téléphone' } } }).text, 'du téléphone')
})

test('réaction, suppression, message vide : pas une demande', () => {
  assert.equal(classify(msg({ message: { reactionMessage: { text: '👍' } } }), 'notify', ctx()).reason, 'empty')
  assert.equal(classify(msg({ message: { protocolMessage: { type: 0 } } }), 'notify', ctx()).reason, 'empty')
  assert.equal(classify(msg({ message: { conversation: '' } }), 'notify', ctx()).reason, 'empty')
})

test('sans identité connue, rien ne passe', () => {
  assert.equal(classify(msg(), 'notify', ctx({ self: null })).reason, 'other')
})

test('sortie toujours marquée 🔷, une seule fois', () => {
  assert.equal(outgoing('Bonjour'), `${BOT_PREFIX} Bonjour`)
  assert.equal(outgoing(`${BOT_PREFIX} Déjà`), `${BOT_PREFIX} Déjà`)
  assert.equal(outgoing('   '), '')
})

test('mémoire des identifiants bornée', () => {
  const s = new Set()
  for (let i = 0; i < 520; i++) remember(s, `id${i}`, 500)
  assert.equal(s.size, 500)
  assert.equal(s.has('id19'), false)
  assert.equal(s.has('id519'), true)
  assert.equal(bareJid('336:2@S.WhatsApp.net'), '336@s.whatsapp.net')
})

// --- 17/10 : fichiers envoyés à l'utilisateur (outil MCP whatsapp_send_file) ---

test('document : type de la liste fermée, nom sans chemin, légende toujours marquée 🔷', () => {
  assert.deepEqual(documentContent({ name: 'Plan.pdf', mime: 'application/pdf', caption: 'Ton plan' }),
    { mimetype: 'application/pdf', fileName: 'Plan.pdf', caption: `${BOT_PREFIX} Ton plan` })
  assert.equal(documentContent({ name: 'Plan.pdf', mime: 'application/pdf' }).caption, `${BOT_PREFIX} Plan.pdf`)
  assert.equal(documentContent({ name: '../../etc/x.pdf', mime: 'application/pdf' }).fileName, '.._.._etc_x.pdf')
  assert.equal(documentContent({ name: 'x.sh', mime: 'application/x-sh' }), null)
  assert.equal(documentContent({ name: '', mime: 'application/pdf' }), null)
})

test('notre propre document, réémis en append avant que son id soit connu : ignoré (anti-écho)', () => {
  const { caption, fileName, mimetype } = documentContent({ name: 'Plan.pdf', mime: 'application/pdf' })
  for (const message of [{ documentMessage: { caption, fileName, mimetype } },
    { documentWithCaptionMessage: { message: { documentMessage: { caption, fileName, mimetype } } } }]) {
    assert.equal(classify(msg({ id: 'DOC1', message }), 'append', ctx()).reason, 'bot')
    assert.equal(classify(msg({ id: 'DOC1', message }), 'notify', ctx()).reason, 'bot')
  }
})

test('le pont n\'envoie qu\'une copie préparée par le cœur dans le dossier média', () => {
  const media = '/x/runtime/whatsapp-media'
  assert.equal(isStagedFile(`${media}/out-0a1b2c3d4e5f6071.pdf`, media), true)
  assert.equal(isStagedFile(`${media}/out-0a1b.pdf`, `${media}/`), true)
  assert.equal(isStagedFile(`${media}/ABC123.ogg`, media), false) // note vocale reçue : jamais renvoyée
  assert.equal(isStagedFile('/home/user/Projets/out-0a1b.pdf', media), false)
  assert.equal(isStagedFile(`${media}/sous/out-0a1b.pdf`, media), false)
  assert.equal(isStagedFile(`${media}/out-0a1b.pdf`, ''), false)
  assert.equal(FILE_MAX_BYTES, 25 * 1024 * 1024)
})
