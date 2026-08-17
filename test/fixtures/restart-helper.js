import { appendFileSync, existsSync, writeFileSync } from 'node:fs'
import { createInterface } from 'node:readline'

const markerPath = process.argv[2]
const eventLog = process.argv[3]
const firstLaunch = !existsSync(markerPath)
if (firstLaunch) writeFileSync(markerPath, 'crashed-once', 'utf8')

process.stdout.write(`${JSON.stringify({ protocolVersion: 1, kind: 'ready' })}\n`)

createInterface({ input: process.stdin }).on('line', (line) => {
  if (!line.trim()) return
  if (firstLaunch) process.exit(17)
  appendFileSync(eventLog, `${line}\n`, 'utf8')
  const message = JSON.parse(line)
  if (message.kind === 'shutdown') process.exit(0)
})
