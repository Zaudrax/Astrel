// Faux Baileys pour `node --test` (plan P5) : aucun réseau, aucun vrai compte.
// Chargé à la place de « baileys » par testkit/hooks.mjs. Comportement réglé par
// l'environnement : FAKE_WA_MODE = qr (deux QR de suite) | open (connexion ouverte),
// FAKE_WA_ME = 1 : identifiants déjà présents (compte relié).

const LOGGED_OUT = 401
const RESTART = 515

export const DisconnectReason = { loggedOut: LOGGED_OUT, restartRequired: RESTART }
export const Browsers = { ubuntu: (name) => ['Ubuntu', name, '22.04'] }
export async function downloadMediaMessage () { return Buffer.from('') }

export async function useMultiFileAuthState () {
  const me = process.env.FAKE_WA_ME ? { id: '33612345678:1@s.whatsapp.net' } : undefined
  return { state: { creds: { registered: false, me } }, saveCreds: () => {} }
}

export default function makeWASocket () {
  const handlers = {}
  const fire = (name, value) => { for (const fn of handlers[name] || []) fn(value) }
  const sock = {
    user: null,
    ev: { on: (name, fn) => { (handlers[name] ||= []).push(fn) } },
    async logout () {
      process.stdout.write(JSON.stringify({ t: 'fake', did: 'logout' }) + '\n')
      fire('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode: LOGGED_OUT } } } })
    },
    end () {},
    async requestPairingCode () { return 'ABCD1234' },
    async sendMessage () { return { key: { id: 'X' } } },
    async sendPresenceUpdate () {},
    updateMediaMessage: async () => {}
  }
  setTimeout(() => {
    if (process.env.FAKE_WA_MODE === 'qr') {
      fire('connection.update', { qr: '2@FAUXQRSECRETUN' })
      setTimeout(() => fire('connection.update', { qr: '2@FAUXQRSECRETDEUX' }), 30)
    } else {
      sock.user = { id: '33612345678:1@s.whatsapp.net', lid: '1@lid' }
      fire('connection.update', { connection: 'open' })
    }
  }, 20)
  return sock
}
