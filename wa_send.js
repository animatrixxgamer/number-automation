#!/usr/bin/env node
/**
 * Send one message from an already-paired WhatsApp session.
 *
 *   node wa_send.js 15551234567 "hello"
 *   node wa_send.js --session work 15551234567 "hello"
 *
 * Uses the linked session in wa-sessions/<label> (default: main). If nothing is
 * paired yet, run `node wa_login.js` first.
 */

import { pathToFileURL } from 'node:url'
import {
  DEFAULT_SESSION,
  createSocket,
  isPaired,
  jidFor,
  normalizePhone,
  sessionDir,
  takeSession,
} from './wa_login.js'

export async function sendOnce(digits, text, session = DEFAULT_SESSION) {
  const dir = sessionDir(session)

  // Check before createSocket, which would otherwise create the directory.
  if (!(await isPaired(dir))) {
    throw new Error(`No paired session in ${dir}. Run: node wa_login.js --session ${session}`)
  }

  const sock = await createSocket(dir)
  const opened = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), 60_000)
    sock.ev.on('connection.update', ({ connection }) => {
      if (connection === 'open' || connection === 'close') {
        clearTimeout(timer)
        resolve(connection === 'open')
      }
    })
  })
  if (!opened) {
    await sock.end(undefined)
    throw new Error('Could not open the WhatsApp connection (is the phone online?).')
  }

  try {
    await sock.sendMessage(jidFor(digits), { text })
  } finally {
    await sock.end(undefined)
  }
}

const isMain = process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url
if (isMain) {
  const { session, rest } = takeSession(process.argv.slice(2))
  const [number, ...words] = rest
  const text = words.join(' ')
  if (!number || !text) {
    console.error('usage: node wa_send.js [--session <label>] <number with country code> <message>')
    process.exit(1)
  }
  try {
    const digits = normalizePhone(number)
    await sendOnce(digits, text, session)
    console.log(`Sent to +${digits}.`)
  } catch (err) {
    console.error('Failed:', err?.message || err)
    process.exit(1)
  }
}
