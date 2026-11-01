// Pont WhatsApp lancé pour de vrai sur un FAUX Baileys (plan P5) : `cd whatsapp && node --test`.
// Vérifié : QR pour le HUD sur stdout seulement (`--qr-json`), mode terminal inchangé,
// commande `logout` hors appairage.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { qrStatus, QR_MAX_CHARS } from '../filter.mjs'

const DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

function run (args, env, { stdin = [], until, endStdin = false } = {}) {
  const auth = fs.mkdtempSync(path.join(os.tmpdir(), 'pont-test-'))
  const proc = spawn(process.execPath, ['--import', './testkit/register.mjs', 'bridge.mjs', '--auth', auth, ...args],
    { cwd: DIR, env: { ...process.env, ...env }, stdio: ['pipe', 'pipe', 'pipe'] })
  let out = ''
  let err = ''
  proc.stdout.on('data', (d) => { out += d })
  proc.stderr.on('data', (d) => { err += d })
  for (const line of stdin) proc.stdin.write(JSON.stringify(line) + '\n')
  return new Promise((resolve) => {
    const lines = () => out.split('\n').filter(Boolean).map((l) => { try { return JSON.parse(l) } catch { return {} } })
    const timer = setInterval(() => {
      // endStdin : on ferme stdin (cœur tué) au lieu de tuer le pont ; il doit sortir SEUL.
      if (until && until(lines())) { clearInterval(timer); if (endStdin) proc.stdin.end(); else proc.kill() }
    }, 20)
    const guard = setTimeout(() => proc.kill(), 8000)
    proc.on('exit', (code) => {
      clearInterval(timer)
      clearTimeout(guard)
      fs.rmSync(auth, { recursive: true, force: true })
      resolve({ code, lines: lines(), err })
    })
  })
}

test('qrStatus : QR joint seulement s\'il est plausible', () => {
  assert.deepEqual(qrStatus('2@abc', 1), { t: 'status', state: 'qr', n: 1, qr: '2@abc' })
  assert.deepEqual(qrStatus('', 2), { t: 'status', state: 'qr', n: 2 })
  assert.deepEqual(qrStatus(42, 3), { t: 'status', state: 'qr', n: 3 })
  assert.equal('qr' in qrStatus('x'.repeat(QR_MAX_CHARS + 1), 4), false)
})

test('--qr-json : le QR part sur stdout, rien sur stderr', async () => {
  const r = await run(['--link', '--qr-json'], { FAKE_WA_MODE: 'qr' },
    { until: (l) => l.filter((x) => x.state === 'qr').length >= 2 })
  const qrs = r.lines.filter((x) => x.state === 'qr')
  assert.deepEqual(qrs.map((x) => [x.n, x.qr]), [[1, '2@FAUXQRSECRETUN'], [2, '2@FAUXQRSECRETDEUX']])
  assert.equal(r.err.includes('FAUXQR'), false)
  assert.equal(r.err.trim(), '')
})

test('mode terminal inchangé : QR dessiné sur stderr, jamais en clair sur stdout', async () => {
  const r = await run(['--link'], { FAKE_WA_MODE: 'qr' }, { until: (l) => l.some((x) => x.state === 'qr') })
  const qr = r.lines.find((x) => x.state === 'qr')
  assert.deepEqual(qr, { t: 'status', state: 'qr', n: 1 })
  assert.ok(r.err.length > 50) // le QR en caractères
})

test('logout hors appairage : appareil délié côté téléphone, sortie 0', async () => {
  const r = await run([], { FAKE_WA_MODE: 'open', FAKE_WA_ME: '1' }, { stdin: [{ t: 'logout' }] })
  assert.equal(r.code, 0)
  assert.ok(r.lines.some((x) => x.t === 'fake' && x.did === 'logout'))
  assert.ok(r.lines.some((x) => x.state === 'unlinked' && x.ok === true))
  assert.equal(r.lines.some((x) => x.state === 'logged_out'), false) // voulu : pas « session perdue »
})

test('logout ignoré pendant un appairage', async () => {
  const r = await run(['--link', '--qr-json'], { FAKE_WA_MODE: 'qr' },
    { stdin: [{ t: 'logout' }], until: (l) => l.filter((x) => x.state === 'qr').length >= 2 })
  assert.equal(r.lines.some((x) => x.state === 'unlinked' || x.did === 'logout'), false)
})

test('appairage : stdin fermé avant le scan, le pont sort seul sans rien délier', async () => {
  const r = await run(['--link', '--qr-json'], { FAKE_WA_MODE: 'qr' },
    { until: (l) => l.some((x) => x.state === 'qr'), endStdin: true })
  assert.equal(r.code, 0) // sortie propre, pas tué par le garde-temps (code null)
  assert.equal(r.lines.some((x) => x.did === 'logout'), false)
})

test('appairage : stdin fermé après le scan, appareil délié côté téléphone puis sortie', async () => {
  const r = await run(['--link', '--qr-json'], { FAKE_WA_MODE: 'open' },
    { until: (l) => l.some((x) => x.state === 'linked'), endStdin: true })
  assert.equal(r.code, 4)
  assert.ok(r.lines.some((x) => x.t === 'fake' && x.did === 'logout'))
  assert.equal(r.lines.some((x) => x.state === 'saved'), false)
})
