import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createRequire } from 'node:module'
import test from 'node:test'
import { apply } from '../src/index.js'

test('package metadata exposes the DSH web client bundle', () => {
  const require = createRequire(import.meta.url)
  const metadata = require('dsh-dafeiyu/package.json')
  assert.equal(metadata.exports['./client'], './lib/client.js')
  assert.equal(metadata.dsh.client.platform, 'web')
})

async function waitFor(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
  throw new Error('timed out waiting for plugin integration condition')
}

test('plugin forwards DSH-shaped session events and owns helper shutdown', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'dsh-dafeiyu-plugin-'))
  const eventLog = join(directory, 'events.jsonl')
  const listeners = new Map()
  let dispose
  const ctx = {
    logger: { debug() {}, info() {}, warn() {}, error() {} },
    on(name, callback) {
      listeners.set(name, callback)
    },
    effect(setup) {
      dispose = setup()
    },
  }

  apply(ctx, { helper: { headless: true, eventLog } })
  const session = { header: { id: 'phase0-real-shape' } }
  listeners.get('session/event')(session, { type: 'turn/start', seq: 1, data: { turn: 1 } })
  listeners.get('session/event')(session, {
    type: 'tool/call',
    seq: 2,
    data: { callId: 'call-1', name: 'web_search' },
  })
  listeners.get('session/event')(session, {
    type: 'turn/end',
    seq: 3,
    data: { turn: 1, reason: { kind: 'completed' } },
  })
  dispose()

  await waitFor(async () => {
    try {
      return (await readFile(eventLog, 'utf8')).includes('"kind": "shutdown"')
    } catch {
      return false
    }
  })

  const messages = (await readFile(eventLog, 'utf8')).trim().split(/\r?\n/).map(JSON.parse)
  assert.deepEqual(messages.map((message) => message.kind), [
    'hello',
    'state',
    'state',
    'state',
    'pulse',
    'shutdown',
  ])
  assert.deepEqual(messages.map((message) => message.state).filter(Boolean), [
    'IDLE',
    'IDLE',
    'THINKING',
    'WORKING',
    'SUCCESS',
  ])
  await rm(directory, { recursive: true, force: true })
})
