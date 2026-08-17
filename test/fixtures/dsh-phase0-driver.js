/** Test-only DSH plugin used by the real-host acceptance check. */
export const name = 'dsh-dafeiyu-phase0-driver'
export const inject = ['sessions']

export function apply(ctx) {
  const timer = setTimeout(() => {
    const session = ctx.sessions.create('dsh-dafeiyu-acceptance', { meta: { cwd: process.cwd() } })
    session.append('turn/start', { turn: 1 })
    session.append('step/start', { turn: 1, step: 1 })
    session.append('tool/call', {
      turn: 1,
      step: 1,
      callId: 'acceptance-call',
      name: 'web_search',
      arguments: '{}',
    })
    session.append('step/end', { turn: 1, step: 1 })
    session.append('turn/end', { turn: 1, reason: { kind: 'completed' } })
  }, 2_000)
  ctx.effect(() => () => clearTimeout(timer))
}
