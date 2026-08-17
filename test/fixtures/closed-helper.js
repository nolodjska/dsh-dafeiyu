import { createInterface } from 'node:readline'

process.stdout.write(`${JSON.stringify({ protocolVersion: 1, kind: 'ready' })}\n`)

createInterface({ input: process.stdin }).once('line', () => {
  process.stdout.write(`${JSON.stringify({ protocolVersion: 1, kind: 'closed', reason: 'user' })}\n`)
  setTimeout(() => process.exit(0), 20)
})
