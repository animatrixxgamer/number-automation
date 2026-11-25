#!/usr/bin/env node
/**
 * Pair YOUR OWN WhatsApp account as a linked device from this PC.
 *
 * WhatsApp has no OTP login for third-party clients. The only way in is to link
 * a device from a phone that is already signed in:
 *
 *   QR            scan the terminal code in WhatsApp -> Linked devices
 *   pairing code  WhatsApp shows an 8-character code; type it on the phone
 *                 (Linked devices -> Link with phone number)
 *
 * Each account gets its own directory under wa-sessions/, picked with
 * --session <label> (default: main). Pair one account per run, by hand.
 */

import path from 'node:path'
import { chmod, mkdir, readdir, readFile, rm } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import readline from 'node:readline/promises'
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  Browsers,
  fetchLatestBaileysVersion,
  jidNormalizedUser,
} from '@whiskeysockets/baileys'
import pino from 'pino'
import qrcode from 'qrcode-terminal'

export const DEFAULT_SESSION = 'main'
const SESSIONS_ROOT = path.resolve('wa-sessions')
const LABEL_RE = /^[A-Za-z0-9_-]{1,40}$/
const silentLogger = pino({ level: process.env.WA_LOG_LEVEL || 'silent' })
const MAX_RECONNECTS = 8

export function sessionDir(label = DEFAULT_SESSION) {
  if (!LABEL_RE.test(String(label))) {
    throw new Error("Session label must be 1-40 characters: letters, digits, '-' or '_'.")
  }
  return path.join(SESSIONS_ROOT, label)
}

export function normalizePhone(raw) {
  const digits = String(raw ?? '').replace(/\D/g, '')
  if (digits.length < 6 || digits.length > 15) {
    throw new Error('Use the full number with country code, digits only (e.g. 15551234567).')
  }
  return digits
}

export const jidFor = (digits) => `${digits}@s.whatsapp.net`

/** Pull `--session <label>` (or `--session=<label>`) out of argv. */
export function takeSession(argv, fallback = DEFAULT_SESSION) {
  const rest = []
  let session = fallback
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--session') {
      session = argv[i + 1]
      if (!session) throw new Error('--session needs a label, e.g. --session work')
      i += 1
    } else if (arg.startsWith('--session=')) {
      session = arg.slice('--session='.length)
    } else {
      rest.push(arg)
    }
  }
  return { session, rest }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/** Did this session directory finish pairing with the phone? */
export async function isPaired(dir) {
  try {
    const creds = JSON.parse(await readFile(path.join(dir, 'creds.json'), 'utf8'))
    return creds.registered === true
  } catch {
    return false
  }
}

/** Show the saved session labels and whether each is paired, without keys. */
export async function listSessions() {
  let entries = []
  try {
    entries = await readdir(SESSIONS_ROOT, { withFileTypes: true })
  } catch (err) {
    if (err.code !== 'ENOENT') throw err
  }
  const labels = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()

  if (!labels.length) {
    console.log(`No saved WhatsApp sessions in ${SESSIONS_ROOT}.`)
    console.log('Pair one with: node wa_login.js --session <label>')
    return
  }

  const width = Math.max(...labels.map((label) => label.length))
  console.log(`WhatsApp sessions in ${SESSIONS_ROOT}:`)
  for (const label of labels) {
    const status = (await isPaired(path.join(SESSIONS_ROOT, label)))
      ? 'paired'
      : 'not paired (pair again)'
    console.log(`  ${label.padEnd(width)}  ${status}`)
  }
  console.log(
    '\nReuse one with --session <label>, drop one with --remove <label>. Only ' +
      'labels and status are shown; credentials are never printed.',
  )
}

/** Pull `--remove <label>` (or `--remove=<label>`) out of argv. */
function takeRemove(argv) {
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--remove') {
      const label = argv[i + 1]
      if (!label) throw new Error('--remove needs a label, e.g. --remove work')
      return label
    }
    if (arg.startsWith('--remove=')) {
      const label = arg.slice('--remove='.length)
      if (!label) throw new Error('--remove needs a label, e.g. --remove work')
      return label
    }
  }
  return null
}

/** Delete one saved session directory, but only after an explicit confirmation. */
export async function removeSession(label) {
  const dir = sessionDir(label)
  let entries
  try {
    entries = await readdir(dir)
  } catch (err) {
    if (err.code !== 'ENOENT') throw err
    console.log(`No saved WhatsApp session named '${label}' in ${SESSIONS_ROOT}.`)
    console.log('See what is saved with: node wa_login.js --list')
    return
  }

  const status = (await isPaired(dir)) ? 'paired' : 'not paired'
  console.log(`Session '${label}' (${status}, ${entries.length} file(s)) will be deleted:`)
  console.log(`  ${dir}`)
  console.log(
    'This only forgets the link on this PC. The phone keeps the linked device ' +
      'until you remove it there: press "l" in the menu first, or use ' +
      'WhatsApp -> Linked devices on the phone.',
  )

  if (!(await askYes(`Delete '${label}'? type 'yes': `))) {
    console.log('Nothing deleted.')
    return
  }
  await rm(dir, { recursive: true, force: true })
  console.log(`Deleted session '${label}'. No credentials were printed.`)
}

/** Ask a yes/no question; end of input counts as "no" instead of hanging. */
async function askYes(question) {
  process.stdout.write(question)
  // terminal: false keeps readline from echoing piped input; the for-await loop
  // ends cleanly on EOF instead of leaving a promise pending.
  const rl = readline.createInterface({ input: process.stdin, terminal: false })
  try {
    for await (const line of rl) {
      return line.trim().toLowerCase() === 'yes'
    }
    return false
  } finally {
    rl.close()
    process.stdout.write('\n')
  }
}

/** Open a socket on an existing session directory (creating it if needed). */
export async function createSocket(dir) {
  await mkdir(dir, { recursive: true, mode: 0o700 })
  await chmod(dir, 0o700).catch(() => {})
  const { state, saveCreds } = await useMultiFileAuthState(dir)
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: undefined }))
  const sock = makeWASocket({
    version,
    auth: state,
    logger: silentLogger,
    browser: Browsers.ubuntu('Chrome'),
    syncFullHistory: false,
    markOnlineOnConnect: false,
  })
  sock.ev.on('creds.update', saveCreds)
  return sock
}

async function menu(sock, ask, rl, dir) {
  const me = sock.user ?? {}
  const myJid = me.id ? jidNormalizedUser(me.id) : '(unknown)'
  console.log(`\nLinked as ${me.name || me.verifiedName || 'you'} (${myJid})`)
  console.log(`Session: ${dir}; the next run stays linked.\n`)

  for (;;) {
    console.log('  [s] send a test message to yourself')
    console.log('  [n] send a message to one number')
    console.log('  [l] log out this PC (removes the device)')
    console.log('  [Enter] quit')
    const choice = (await ask('> ')).toLowerCase()

    if (!choice || choice === 'q' || choice === 'quit') {
      console.log('Bye. This PC stays linked; run the script again to use it.')
      rl.close()
      await sock.end(undefined)
      return
    }

    if (choice === 's') {
      try {
        await sock.sendMessage(myJid, { text: 'Test message from wa_login.js on my PC.' })
        console.log('Sent to your own chat.')
      } catch (err) {
        console.error('Send failed:', err.message)
      }
      continue
    }

    if (choice === 'n') {
      try {
        const digits = normalizePhone(await ask('Number with country code: '))
        const text = await ask('Message: ')
        if (!text) {
          console.log('Empty message, nothing sent.')
          continue
        }
        await sock.sendMessage(jidFor(digits), { text })
        console.log(`Sent to +${digits}.`)
      } catch (err) {
        console.error('Send failed:', err.message)
      }
      continue
    }

    if (choice === 'l') {
      if ((await ask("Log out this PC? type 'yes': ")).toLowerCase() !== 'yes') continue
      try {
        await sock.logout()
      } catch {
        /* the connection may already be gone */
      }
      await rm(dir, { recursive: true, force: true })
      console.log(`Logged out and removed ${dir}.`)
      rl.close()
      process.exit(0)
    }

    console.log('Unknown choice.')
  }
}

export async function login({ phone, mode, session = DEFAULT_SESSION } = {}) {
  const dir = sessionDir(session)
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
  const ask = async (question) => (await rl.question(question)).trim()

  const answer = mode ?? (await ask('Pair with [q]R code or [p]airing code (default q): '))
  const usePairing = String(answer || 'q').toLowerCase().startsWith('p')

  // The number is only needed to request a pairing code; QR needs none.
  let digits = ''
  if (usePairing) {
    try {
      digits = normalizePhone(phone || (await ask('Your WhatsApp number with country code: ')))
    } catch (err) {
      console.error(err.message)
      rl.close()
      process.exit(1)
    }
  }

  let attempt = 0

  const fatal = (err) => {
    console.error('Fatal:', err?.message || err)
    rl.close()
    process.exit(1)
  }

  const start = async () => {
    const sock = await createSocket(dir)
    let lastQr = ''
    let lastCodeAt = 0

    const showPairingCode = async () => {
      if (sock.authState.creds.registered) return
      const now = Date.now()
      if (now - lastCodeAt < 60_000) return // the code expires, so refresh it slowly
      lastCodeAt = now
      try {
        const code = await sock.requestPairingCode(digits)
        console.log(
          `\nPairing code: ${code}\n` +
            'On your phone: WhatsApp -> Linked devices -> Link with phone number -> type the code.\n',
        )
      } catch (err) {
        console.error('Could not request a pairing code:', err.message)
      }
    }

    sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
      if (qr && usePairing) {
        await showPairingCode()
      } else if (qr && qr !== lastQr) {
        lastQr = qr
        console.log('\nScan with WhatsApp -> Linked devices -> Link a device')
        qrcode.generate(qr, { small: true })
      }

      if (connection === 'open') {
        attempt = 0
        await menu(sock, ask, rl, dir)
        return
      }

      if (connection === 'close') {
        const status = lastDisconnect?.error?.output?.statusCode
        if (status === DisconnectReason.loggedOut) {
          await rm(dir, { recursive: true, force: true })
          console.error('This device was logged out. Run the script again to re-pair.')
          rl.close()
          process.exit(0)
        }
        attempt += 1
        if (attempt > MAX_RECONNECTS) {
          console.error(`Gave up after ${MAX_RECONNECTS} reconnect attempts.`)
          rl.close()
          process.exit(1)
        }
        const wait = Math.min(2 ** attempt * 1_000, 30_000)
        console.log(`Connection closed (${status ?? 'unknown'}). Reconnecting in ${wait / 1000}s...`)
        await sleep(wait)
        start().catch(fatal)
      }
    })
  }

  await start().catch(fatal)
}

const isMain = process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url
if (isMain) {
  try {
    const argv = process.argv.slice(2)
    if (argv.includes('--list') && (argv.includes('--remove') || argv.some((a) => a.startsWith('--remove=')))) {
      throw new Error('Use either --list or --remove, not both.')
    }
    const removeLabel = takeRemove(argv)
    if (removeLabel) {
      await removeSession(removeLabel)
    } else if (argv.includes('--list')) {
      await listSessions()
    } else {
      const { session, rest } = takeSession(argv)
      await login({ phone: rest[0], session })
    }
  } catch (err) {
    console.error('Fatal:', err?.message || err)
    process.exit(1)
  }
}
