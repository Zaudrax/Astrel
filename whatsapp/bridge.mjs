// Pont WhatsApp d'Astrel (plan P1) : processus ENFANT du cœur, qui dialogue en
// lignes JSON sur stdin/stdout. Aucune socket (une socket du même utilisateur
// permettrait à Claude de forger un « oui »). Journal sur stderr.
//
//   node bridge.mjs --auth DIR             marche normale (compte déjà relié)
//   node bridge.mjs --auth DIR --link      appairage par QR (affiché sur stderr)
//   node bridge.mjs --auth DIR --link --phone 33612345678   code à 8 caractères
//   node bridge.mjs --auth DIR --link --qr-json   (plan P5, HUD) : le QR part sur stdout,
//               {t:"status", state:"qr", n, qr}, jamais sur stderr (journalisé par le cœur)
//   --media DIR : notes vocales téléchargées là (option A2), lues puis effacées par le cœur
//
// Pont → cœur : {t:"msg", id, ts, chat, from_me, text, forwarded, quoted, media}
//               {t:"status", state, ...}
// Cœur → pont : {t:"send", text}, {t:"typing"}, {t:"quit"}
//               {t:"logout"} (plan P5, hors appairage) : délie l'appareil côté téléphone,
//               répond {t:"status", state:"unlinked", ok} puis sortie 0
//               {t:"send_file", ref, path, name, mime, caption} (17/10 : copie préparée par
//               le cœur dans --media, effacée après l'essai ; réponse status file_sent|file_failed)
//               (appairage seulement : {t:"confirm", ok})
// Le pont n'écrit QU'À SON PROPRE NUMÉRO : le cœur ne donne jamais de destinataire.
//
// Codes de sortie : 0 arrêt demandé, 1 coupure (le cœur relance avec attente),
// 3 session perdue ou jamais reliée (le cœur NE relance PAS), 4 appairage refusé.

import fs from 'node:fs'
import path from 'node:path'
import readline from 'node:readline'
import makeWASocket, { Browsers, DisconnectReason, downloadMediaMessage, useMultiFileAuthState } from 'baileys'
import pino from 'pino'
import { classify, outgoing, remember, bareJid, SEEN_MAX, FILE_MAX_BYTES, documentContent, isStagedFile,
  qrStatus } from './filter.mjs'

const args = process.argv.slice(2)
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined }
const AUTH = opt('--auth')
const LINK = args.includes('--link')
const QR_JSON = LINK && args.includes('--qr-json')
const PHONE = (opt('--phone') || '').replace(/\D/g, '')
const MEDIA = opt('--media')
const MAX_VOICE_S = 300 // note vocale plus longue : non téléchargée
if (!AUTH) {
  process.stderr.write('usage : node bridge.mjs --auth DOSSIER [--link [--phone NUMERO]]\n')
  process.exit(2)
}
const SELF_FILE = path.join(AUTH, 'self.json')
const MEMO_FILE = path.join(AUTH, 'bridge-memo.json')

const logger = pino({ level: process.env.ASTREL_WA_LOG || 'warn' }, pino.destination(2))
const emit = (obj) => process.stdout.write(JSON.stringify(obj) + '\n')
const note = (text) => process.stderr.write(`[pont whatsapp] ${text}\n`)

function readJson (file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')) } catch { return fallback }
}
function writeJson (file, data) {
  const tmp = `${file}.tmp`
  fs.writeFileSync(tmp, JSON.stringify(data), { mode: 0o600 })
  fs.renameSync(tmp, file)
}

// Identifiants déjà vus (doublons) et envoyés (échos), gardés d'une relance à l'autre.
const memo = readJson(MEMO_FILE, {})
const seen = new Set(Array.isArray(memo.seen) ? memo.seen.slice(-SEEN_MAX) : [])
const sent = new Set(Array.isArray(memo.sent) ? memo.sent.slice(-SEEN_MAX) : [])
let memoTimer = null
function saveMemo () {
  clearTimeout(memoTimer)
  memoTimer = setTimeout(() => {
    try { writeJson(MEMO_FILE, { seen: [...seen], sent: [...sent] }) } catch (e) { note(`mémo non écrit : ${e}`) }
  }, 300)
}

let self = readJson(SELF_FILE, null)
let sock = null
let open = false
let quitting = false
let qrShown = 0
let pendingSelf = null // appairage : compte détecté, en attente de la confirmation de l'utilisateur
let pairingAsked = false
const counts = {}
let countsTimer = null
let stale = 0
let staleTimer = null

let exiting = false
function exit (code) {
  if (exiting) return // un seul code de sortie : le premier motif l'emporte
  exiting = true
  quitting = true
  clearTimeout(memoTimer)
  try { writeJson(MEMO_FILE, { seen: [...seen], sent: [...sent] }) } catch {}
  setTimeout(() => process.exit(code), 50)
}

function count (reason) {
  // Tiers, groupes, statuts : un compteur, jamais le contenu.
  counts[reason] = (counts[reason] || 0) + 1
  clearTimeout(countsTimer)
  countsTimer = setTimeout(() => emit({ t: 'status', state: 'ignored', counts: { ...counts } }), 5000)
}

function onUpsert ({ messages, type }) {
  if (LINK || !self) return
  const now = Date.now() / 1000
  for (const m of messages || []) {
    const r = classify(m, type, { self, now, seen, sent })
    if (!r.ok) {
      if (r.reason === 'stale') {
        // Reçu pendant qu'Astrel était éteint : ignoré, UNE note groupée.
        stale += 1
        remember(seen, String(m?.key?.id || ''))
        clearTimeout(staleTimer)
        staleTimer = setTimeout(() => { emit({ t: 'status', state: 'stale', count: stale }); stale = 0 }, 3000)
      } else if (r.reason !== 'echo') {
        count(r.reason)
      }
      continue
    }
    remember(seen, r.msg.id)
    saveMemo()
    withVoice(m, r.msg).then(emit, () => emit(r.msg))
  }
}

// Option A2 : SA note vocale (pas une transférée) est téléchargée pour être transcrite
// sur le PC. Rien d'autre n'est téléchargé (images, documents : pas encore pris en charge).
async function withVoice (m, msg) {
  const media = msg.media
  if (!MEDIA || media?.kind !== 'audio' || !media.ptt || msg.forwarded) return msg
  if ((media.seconds || 0) > MAX_VOICE_S) return { ...msg, media: { ...media, error: 'trop longue' } }
  try {
    const buf = await downloadMediaMessage(m, 'buffer', {}, { logger, reuploadRequest: sock.updateMediaMessage })
    fs.mkdirSync(MEDIA, { recursive: true, mode: 0o700 })
    const file = path.join(MEDIA, `${msg.id.replace(/[^\w-]/g, '')}.ogg`)
    fs.writeFileSync(file, buf, { mode: 0o600 })
    return { ...msg, media: { ...media, path: file } }
  } catch (e) {
    note(`note vocale non téléchargée : ${e?.message || e}`)
    return { ...msg, media: { ...media, error: 'téléchargement impossible' } }
  }
}

async function send (text) {
  const body = outgoing(text)
  if (!body) return
  if (!sock || !open || !self?.pn) {
    emit({ t: 'status', state: 'send_failed', reason: 'pas connecté' })
    return
  }
  try {
    const r = await sock.sendMessage(bareJid(self.pn), { text: body })
    if (r?.key?.id) { remember(sent, r.key.id); saveMemo() }
    emit({ t: 'status', state: 'sent', id: r?.key?.id || '' })
  } catch (e) {
    emit({ t: 'status', state: 'send_failed', reason: String(e?.message || e).slice(0, 200) })
  }
}

// 17/10 : un fichier pour l'utilisateur, à SON numéro seulement. Seule une copie préparée
// par le cœur dans le dossier média part (jamais un chemin quelconque), puis elle est effacée.
async function sendFile (cmd) {
  const ref = String(cmd.ref || '')
  const fail = (reason) => emit({ t: 'status', state: 'file_failed', ref, reason: String(reason).slice(0, 200) })
  let staged = ''
  try {
    if (!MEDIA) return fail('pas de dossier média')
    const real = fs.realpathSync(String(cmd.path || ''))
    if (!isStagedFile(real, fs.realpathSync(MEDIA))) return fail('fichier hors du dossier d\'envoi')
    staged = real
    const st = fs.lstatSync(real)
    if (!st.isFile() || st.size <= 0 || st.size > FILE_MAX_BYTES) return fail('fichier vide, trop gros ou spécial')
    const content = documentContent({ name: cmd.name, mime: cmd.mime, caption: cmd.caption })
    if (!content) return fail('type de fichier non permis')
    if (!sock || !open || !self?.pn) return fail('pas connecté')
    const r = await sock.sendMessage(bareJid(self.pn), { document: fs.readFileSync(real), ...content })
    if (r?.key?.id) { remember(sent, r.key.id); saveMemo() }
    emit({ t: 'status', state: 'file_sent', ref, id: r?.key?.id || '' })
  } catch (e) {
    fail(e?.message || e)
  } finally {
    if (staged) { try { fs.unlinkSync(staged) } catch {} }
  }
}

let queue = Promise.resolve()
function onCommand (line) {
  let cmd
  try { cmd = JSON.parse(line) } catch { return }
  if (!cmd || typeof cmd !== 'object') return
  if (cmd.t === 'send' && typeof cmd.text === 'string') {
    queue = queue.then(() => send(cmd.text)) // un envoi à la fois, jamais de rafale parallèle
  } else if (cmd.t === 'send_file' && typeof cmd.path === 'string') {
    queue = queue.then(() => sendFile(cmd))
  } else if (cmd.t === 'typing') {
    if (sock && open && self?.pn) sock.sendPresenceUpdate('composing', bareJid(self.pn)).catch(() => {})
  } else if (cmd.t === 'quit') {
    queue.then(() => { try { sock?.end(undefined) } catch {} ; exit(0) })
  } else if (cmd.t === 'confirm' && LINK) {
    confirmLink(Boolean(cmd.ok))
  } else if (cmd.t === 'logout' && !LINK) {
    queue = queue.then(logout)
  }
}

// Plan P5 : « Délier » depuis le HUD. L'appareil est retiré côté téléphone (le cœur
// efface ensuite les identifiants). Attend la connexion 15 s au plus ; sans elle,
// `ok: false` et le cœur dit de retirer « Astrel » des appareils connectés à la main.
async function logout () {
  quitting = true // la fermeture qui suit est voulue : ni « logged_out » ni code 3
  for (let i = 0; i < 150 && !(sock && open); i++) await new Promise((resolve) => setTimeout(resolve, 100))
  let ok = false
  try {
    if (sock && open) { await sock.logout(); ok = true }
  } catch (e) {
    note(`déconnexion refusée : ${e?.message || e}`)
  }
  emit({ t: 'status', state: 'unlinked', ok })
  exit(0)
}

async function confirmLink (ok) {
  if (!pendingSelf) return
  if (ok) {
    writeJson(SELF_FILE, pendingSelf)
    emit({ t: 'status', state: 'saved' })
    try { sock?.end(undefined) } catch {}
    exit(0)
    return
  }
  try { await sock?.logout() } catch {}
  emit({ t: 'status', state: 'refused' })
  exit(4)
}

// 18/10 : stdin fermé PENDANT l'appairage (cœur tué, terminal fermé) : personne ne
// confirmera plus. Un compte déjà scanné est délié côté téléphone (sinon « Astrel »
// resterait dans ses appareils connectés), puis sortie. Pas de `quitting` avant la fin
// de l'attente : la reconnexion qui suit le scan (restartRequired) doit pouvoir se faire.
let linkCreds = null
async function abandonLink () {
  if (exiting || quitting) return // réponse déjà donnée (confirm) : sa sortie suit
  const scanned = Boolean(pendingSelf || linkCreds?.me)
  if (scanned) {
    for (let i = 0; i < 100 && !(sock && open) && !exiting; i++) await new Promise((resolve) => setTimeout(resolve, 100))
  }
  if (exiting) return
  quitting = true // la fermeture qui suit est voulue
  let unlinked = false
  if (scanned && sock && open) {
    try { await sock.logout(); unlinked = true } catch (e) { note(`déconnexion refusée : ${e?.message || e}`) }
  }
  try { sock?.end(undefined) } catch {}
  note(`stdin fermé pendant l'appairage : ${unlinked ? 'appareil délié' : 'rien de relié'}, arrêt`)
  exit(unlinked ? 4 : 0)
}

async function connect () {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH)
  linkCreds = state.creds
  // `registered` reste faux après un appairage « appareil lié » (QR ou code) : c'est `me`
  // (le compte relié) qui dit qu'une session existe (vérifié le 17/10, sortie 3 en boucle).
  if (!LINK && !state.creds.registered && !state.creds.me) {
    emit({ t: 'status', state: 'need_link' })
    exit(3)
    return
  }
  sock = makeWASocket({
    auth: state,
    logger,
    browser: Browsers.ubuntu('Astrel'),
    markOnlineOnConnect: false, // le téléphone garde ses notifications
    syncFullHistory: false,
    shouldSyncHistoryMessage: () => false,
    generateHighQualityLinkPreview: false,
    getMessage: async () => undefined
  })
  sock.ev.on('creds.update', saveCreds)
  sock.ev.on('messages.upsert', onUpsert)
  sock.ev.on('connection.update', async (u) => {
    if (u.qr && LINK) {
      if (PHONE && !pairingAsked) {
        pairingAsked = true
        try {
          const code = await sock.requestPairingCode(PHONE)
          emit({ t: 'status', state: 'code', code })
        } catch (e) {
          emit({ t: 'status', state: 'error', reason: `code d'appairage refusé : ${e?.message || e}` })
        }
      } else if (!PHONE && QR_JSON) {
        qrShown += 1
        emit(qrStatus(u.qr, qrShown)) // HUD (plan P5) : stdout seulement, rien sur stderr
      } else if (!PHONE) {
        qrShown += 1
        const { default: qrcode } = await import('qrcode-terminal')
        process.stderr.write('\n')
        qrcode.generate(u.qr, { small: true }, (q) => process.stderr.write(q + '\n'))
        emit({ t: 'status', state: 'qr', n: qrShown })
      }
    } else if (u.qr && !LINK) {
      emit({ t: 'status', state: 'need_link' })
      exit(3)
      return
    }
    if (u.connection === 'open') {
      open = true
      const me = sock.user || {}
      const pn = bareJid(me.id || '')
      const lid = bareJid(me.lid || state.creds.me?.lid || '')
      if (LINK) {
        pendingSelf = { pn, lid, name: me.name || me.notify || '', linked_at: Math.floor(Date.now() / 1000) }
        emit({ t: 'status', state: 'linked', pn, lid, name: pendingSelf.name })
        return
      }
      if (self && pn && bareJid(self.pn) === pn && lid && !self.lid) {
        // LID du MÊME compte connu après coup : complété, jamais remplacé.
        self = { ...self, lid }
        try { writeJson(SELF_FILE, self) } catch {}
      }
      if (self && pn && bareJid(self.pn) !== pn) {
        emit({ t: 'status', state: 'error', reason: 'le compte relié ne correspond plus au numéro confirmé' })
        exit(3)
        return
      }
      emit({ t: 'status', state: 'open', pn, lid })
    } else if (u.connection === 'close') {
      open = false
      if (quitting) return
      const code = u.lastDisconnect?.error?.output?.statusCode
      if (code === DisconnectReason.loggedOut) {
        emit({ t: 'status', state: 'logged_out' })
        exit(3)
      } else if (code === DisconnectReason.restartRequired) {
        // Normal juste après le scan du QR : on se reconnecte aussitôt.
        connect().catch((e) => { emit({ t: 'status', state: 'error', reason: String(e) }); exit(1) })
      } else {
        emit({ t: 'status', state: 'closed', code: code ?? null })
        exit(1)
      }
    }
  })
}

fs.mkdirSync(AUTH, { recursive: true, mode: 0o700 })
// Lecteur disparu (cœur tué) : écrire sur stdout lèverait EPIPE et tuerait le pont avant
// qu'il ait délié l'appareil. Rien à faire de ces erreurs.
process.stdout.on('error', () => {})
readline.createInterface({ input: process.stdin }).on('line', onCommand).on('close', () => {
  if (LINK) { abandonLink().catch(() => exit(0)); return }
  try { sock?.end(undefined) } catch {}
  exit(0)
})
process.on('unhandledRejection', (e) => note(`erreur non gérée : ${e?.message || e}`))
connect().catch((e) => { emit({ t: 'status', state: 'error', reason: String(e?.message || e) }); exit(1) })
