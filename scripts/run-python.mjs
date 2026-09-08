import { spawnSync } from 'node:child_process'

const [python, prefix] = process.platform === 'win32'
  ? ['py', ['-3']]
  : ['python3', []]
const result = spawnSync(python, [...prefix, ...process.argv.slice(2)], { stdio: 'inherit' })

if (result.error) {
  console.error(`Unable to start ${python}: ${result.error.message}`)
  process.exit(1)
}
process.exit(result.status ?? 1)
