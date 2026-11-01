// Liste blanche du pont WhatsApp d'Astrel (plan P1, étapes 5 et 6). Fonctions
// PURES (aucun accès réseau ni disque) : testées par `node --test`.
//
// Seul passe un message que l'utilisateur s'écrit À LUI-MÊME (chat « message à
// soi-même »), reçu en direct (`notify`), récent, jamais vu, qui n'est pas un
// écho d'un envoi du pont ni une réponse marquée « 🔷 » (ou « 🤖 », l'ancienne
// marque). Tout le reste (tiers, groupes, statuts, chaînes) est ignoré SANS lire
// son contenu : seul un compteur par raison est tenu.

export const MAX_AGE_S = 180
// 19/10 (demande de l'utilisateur) : 🤖 remplacé par 🔷 ; l'ancienne marque reste ignorée
// en entrée (messages déjà envoyés). Même liste que `WHATSAPP_IGNORED_PREFIXES` (config.py).
export const BOT_PREFIX = '🔷'
export const BOT_PREFIXES = [BOT_PREFIX, '🤖']

/** Le texte porte-t-il la marque d'Astrel (nouvelle ou ancienne) ? */
export function isMarked (text) {
  const t = String(text ?? '').trimStart()
  return BOT_PREFIXES.some((p) => t.startsWith(p))
}
export const SEEN_MAX = 500

/** « 33612345678:12@s.whatsapp.net » → « 33612345678@s.whatsapp.net » (appareil retiré). */
export function bareJid (jid) {
  if (typeof jid !== 'string' || !jid.includes('@')) return ''
  const [user, server] = jid.split('@')
  return `${user.split(':')[0]}@${server}`.toLowerCase()
}

/** Les adresses de l'utilisateur (numéro et LID) sous forme nue. */
export function selfIds (self) {
  return new Set([self?.pn, self?.lid].map(bareJid).filter(Boolean))
}

/** Le message vient-il du chat « message à soi-même » ? (adresse ou adresse alternative.) */
export function isSelfChat (key, self) {
  const mine = selfIds(self)
  if (!mine.size || !key) return false
  const chat = bareJid(key.remoteJid)
  if (!chat || chat.endsWith('@g.us') || chat.endsWith('@broadcast') || chat.endsWith('@newsletter')) return false
  if (mine.has(chat)) return true
  // Adressage LID : le chat peut arriver en @lid avec le numéro en adresse alternative.
  const alt = bareJid(key.remoteJidAlt)
  return Boolean(alt) && mine.has(alt) && (chat.endsWith('@lid') || chat.endsWith('@s.whatsapp.net'))
}

/** Retire les enveloppes (éphémère, vue unique, document avec légende, envoi d'un autre appareil). */
export function unwrap (message) {
  let m = message
  for (let i = 0; i < 6 && m && typeof m === 'object'; i++) {
    const inner = m.ephemeralMessage?.message || m.viewOnceMessage?.message || m.viewOnceMessageV2?.message ||
      m.viewOnceMessageV2Extension?.message || m.documentWithCaptionMessage?.message ||
      m.deviceSentMessage?.message || m.editedMessage?.message
    if (!inner) break
    m = inner
  }
  return m || null
}

function quotedText (ctx) {
  const q = unwrap(ctx?.quotedMessage)
  if (!q) return ''
  return String(q.conversation || q.extendedTextMessage?.text || q.imageMessage?.caption ||
    q.documentMessage?.caption || q.videoMessage?.caption || '')
}

/** Texte, transfert, citation et pièce jointe d'un message déjà déballé. */
export function extract (message) {
  const m = unwrap(message)
  if (!m) return null
  if (m.protocolMessage || m.reactionMessage) {
    return null // suppression, modification, réaction : pas une demande
  }
  let text = ''
  let ctx = null
  let media = null
  if (typeof m.conversation === 'string' && m.conversation) {
    text = m.conversation
  } else if (m.extendedTextMessage) {
    text = String(m.extendedTextMessage.text || '')
    ctx = m.extendedTextMessage.contextInfo
  } else if (m.audioMessage) {
    ctx = m.audioMessage.contextInfo
    media = { kind: 'audio', mime: String(m.audioMessage.mimetype || ''), ptt: Boolean(m.audioMessage.ptt),
      seconds: Number(m.audioMessage.seconds || 0) }
  } else if (m.imageMessage) {
    text = String(m.imageMessage.caption || '')
    ctx = m.imageMessage.contextInfo
    media = { kind: 'image', mime: String(m.imageMessage.mimetype || ''), ptt: false }
  } else if (m.documentMessage) {
    text = String(m.documentMessage.caption || '')
    ctx = m.documentMessage.contextInfo
    media = { kind: 'document', mime: String(m.documentMessage.mimetype || ''), ptt: false,
      name: String(m.documentMessage.fileName || '') }
  } else if (m.videoMessage) {
    text = String(m.videoMessage.caption || '')
    ctx = m.videoMessage.contextInfo
    media = { kind: 'video', mime: String(m.videoMessage.mimetype || ''), ptt: false }
  } else {
    return null // sondage, contact, position, autocollant… : non pris en charge
  }
  const forwarded = Boolean(ctx?.isForwarded) || Number(ctx?.forwardingScore || 0) > 0
  return { text, forwarded, quoted: quotedText(ctx), media }
}

/** Secondes depuis l'époque d'un `messageTimestamp` (nombre, chaîne ou Long de protobuf). */
export function tsSeconds (value) {
  if (value == null) return 0
  if (typeof value === 'number') return value
  if (typeof value === 'object' && typeof value.toNumber === 'function') return value.toNumber()
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

/**
 * Juge un message de `messages.upsert`.
 * ctx : { self: {pn, lid}, now (s), seen: Set, sent: Set, maxAge }
 * → { ok: true, msg } (contrat pont→cœur) ou { ok: false, reason }.
 * Raisons : other (tiers, groupe : contenu jamais lu), not_me, echo, stale
 * (reçu hors ligne ou trop vieux), dup, empty, bot.
 */
export function classify (m, type, ctx) {
  const key = m?.key
  if (!key || !isSelfChat(key, ctx.self)) return { ok: false, reason: 'other' }
  if (!key.fromMe) return { ok: false, reason: 'not_me' }
  const id = String(key.id || '')
  if (!id) return { ok: false, reason: 'empty' }
  if (ctx.sent?.has(id)) return { ok: false, reason: 'echo' }
  if (ctx.seen?.has(id)) return { ok: false, reason: 'dup' }
  const body = extract(m.message)
  // Avant le type : Baileys émet NOS envois en `append` avant même que `sendMessage`
  // rende leur id (pas encore dans `sent`) ; sans ça, chaque réponse passait pour
  // un message reçu hors ligne.
  if (body && isMarked(body.text)) return { ok: false, reason: 'bot' }
  const ts = tsSeconds(m.messageTimestamp)
  // `append` : marqué hors ligne par le serveur (Astrel éteint, ou coupure réseau de
  // quelques secondes) ; accepté s'il est récent, l'âge tranche ci-dessous.
  if (type !== 'notify' && type !== 'append') return { ok: false, reason: 'stale' }
  if (!ts || ctx.now - ts > (ctx.maxAge ?? MAX_AGE_S)) return { ok: false, reason: 'stale' }
  if (!body || (!body.text.trim() && !body.media)) return { ok: false, reason: 'empty' }
  return {
    ok: true,
    msg: {
      t: 'msg',
      id,
      ts,
      chat: bareJid(key.remoteJid),
      from_me: true,
      text: body.text,
      forwarded: body.forwarded,
      quoted: body.quoted,
      media: body.media
    }
  }
}

/** Texte sortant : toujours marqué « 🔷 » (un message ainsi marqué n'est jamais relu). */
export function outgoing (text) {
  const t = String(text ?? '').trim()
  if (!t) return ''
  return t.startsWith(BOT_PREFIX) ? t : `${BOT_PREFIX} ${t}`
}

// Fichiers envoyés à l'utilisateur (17/10, outil MCP `whatsapp_send_file`) : le cœur vérifie
// le fichier et le COPIE dans le dossier média (`out-<ref>.<ext>`) ; le pont n'envoie
// que de là. Mêmes listes que `astrel/config.py` (WHATSAPP_FILE_TYPES, _MAX_BYTES).
export const FILE_MAX_BYTES = 25 * 1024 * 1024
export const FILE_MIMES = new Set([
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'image/png', 'image/jpeg', 'text/plain', 'text/markdown', 'text/csv'
])

/** Le fichier (chemin RÉSOLU) est-il une copie préparée par le cœur dans le dossier média ? */
export function isStagedFile (real, mediaDir) {
  if (typeof real !== 'string' || typeof mediaDir !== 'string' || !mediaDir) return false
  const cut = real.lastIndexOf('/')
  return real.slice(0, cut) === mediaDir.replace(/\/+$/, '') && /^out-[0-9a-f]+\.[a-z]+$/.test(real.slice(cut + 1))
}

/**
 * Contenu d'un document envoyé à soi (sans le fichier) : type de la liste fermée,
 * nom sans chemin, légende TOUJOURS marquée « 🔷 » — Baileys réémet notre envoi en
 * `append` avant de rendre son id : la marque le fait ignorer (`classify` → bot).
 * null si refusé.
 */
export function documentContent ({ name, mime, caption } = {}) {
  if (!FILE_MIMES.has(mime)) return null
  const fileName = String(name ?? '').replace(/[\u0000-\u001f\u007f/\\]/g, '_').trim().slice(0, 120)
  if (!fileName) return null
  return { mimetype: mime, fileName, caption: outgoing(caption) || `${BOT_PREFIX} ${fileName}` }
}

/** Garde les `max` derniers identifiants (ordre d'insertion). */
// Plan P5 : QR de l'appairage pour le HUD (`--qr-json`), en ligne JSON sur stdout
// seulement (jamais stderr, que le cœur journalise). Un QR anormal n'est pas relayé.
export const QR_MAX_CHARS = 2000
export function qrStatus (qr, n) {
  const status = { t: 'status', state: 'qr', n }
  if (typeof qr === 'string' && qr.length > 0 && qr.length <= QR_MAX_CHARS) status.qr = qr
  return status
}

export function remember (set, id, max = SEEN_MAX) {
  if (!id) return set
  set.delete(id)
  set.add(id)
  while (set.size > max) set.delete(set.values().next().value)
  return set
}
