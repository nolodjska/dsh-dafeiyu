import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { HelperProcess } from '../src/helper-process.js'
import { CompanionMessageKind, CompanionState, createMessage } from '../src/protocol.js'

const fixture = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'restart-helper.js')
const closedFixture = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'closed-helper.js')

async function waitFor(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
  throw new Error('timed out waiting for helper restart')
}

test('unexpected helper exit restarts and replays the latest state snapshot', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'dsh-dafeiyu-restart-'))
  const marker = join(directory, 'crashed-once')
  const eventLog = join(directory, 'events.jsonl')
  const logger = { debug() {}, info() {}, warn() {}, error() {} }
  const bridge = new HelperProcess({
    command: process.execPath,
    args: [fixture, marker, eventLog],
    headless: false,
    heartbeatMs: 0,
    restartDelayMs: 50,
  }, logger)
  bridge.start()
  bridge.send(createMessage(CompanionMessageKind.STATE, {
    state: CompanionState.WORKING,
    activity: 'testing',
    message: 'replay me',
  }))

  await waitFor(async () => {
    try {
      return (await readFile(eventLog, 'utf8')).includes('replay me')
    } catch {
      return false
    }
  })
  bridge.stop('restart-test-complete')
  await waitFor(async () => {
    try {
      return (await readFile(eventLog, 'utf8')).includes('"kind":"shutdown"')
    } catch {
      return false
    }
  })

  const messages = (await readFile(eventLog, 'utf8')).trim().split(/\r?\n/).map(JSON.parse)
  assert.equal(messages.some((message) => message.state === CompanionState.WORKING), true)
  assert.equal(messages.at(-1).kind, CompanionMessageKind.SHUTDOWN)
  await rm(directory, { recursive: true, force: true })
})

test('explicit user close suppresses automatic restart until the next DSH boot', async () => {
  const logger = { debug() {}, info() {}, warn() {}, error() {} }
  const bridge = new HelperProcess({
    command: process.execPath,
    args: [closedFixture],
    headless: false,
    heartbeatMs: 0,
    restartDelayMs: 25,
  }, logger)
  bridge.start()
  bridge.send(createMessage(CompanionMessageKind.STATE, {
    state: CompanionState.IDLE,
    message: 'close me',
  }))
  await waitFor(() => bridge.restartSuppressed === true)
  await waitFor(() => bridge.child === undefined)
  await new Promise((resolve) => setTimeout(resolve, 100))
  assert.equal(bridge.child, undefined)
  bridge.stop('closed-test-complete')
})
