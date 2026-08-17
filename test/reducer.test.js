import assert from 'node:assert/strict'
import test from 'node:test'
import { CompanionReducer, toolActivity } from '../src/companion-reducer.js'
import { CompanionMessageKind, CompanionState } from '../src/protocol.js'

const session = { header: { id: 'session-main' } }

function event(type, data = {}, seq = 0) {
  return { type, data, seq, time: Date.now() }
}

test('turn and tool events produce stable companion states', () => {
  const reducer = new CompanionReducer()
  assert.equal(reducer.handle(session, event('turn/start', { turn: 1 }, 1))[0].state, CompanionState.THINKING)

  const working = reducer.handle(session, event('tool/call', {
    turn: 1,
    step: 1,
    callId: 'call-1',
    name: 'shell_command',
  }, 2))[0]
  assert.equal(working.state, CompanionState.WORKING)
  assert.equal(working.activity, 'commanding')

  const afterResult = reducer.handle(session, event('tool/result', {
    turn: 1,
    step: 1,
    message: { toolCallId: 'call-1' },
  }, 3))[0]
  // 本轮用过工具后保持工作状态（避免思考 ↔ 坐下/起身横跳），回合结束才退出
  assert.equal(afterResult.state, CompanionState.WORKING)
  assert.equal(afterResult.activity, 'using-tool')

  const complete = reducer.handle(session, event('turn/end', {
    turn: 1,
    reason: { kind: 'completed' },
  }, 4))[0]
  assert.equal(complete.kind, CompanionMessageKind.PULSE)
  assert.equal(complete.state, CompanionState.SUCCESS)
  assert.equal(complete.resumeState, CompanionState.IDLE)
})

test('ask_user_question emits question asked/answered messages', () => {
  const reducer = new CompanionReducer()
  reducer.handle(session, event('turn/start', { turn: 1 }, 1))

  const asked = reducer.handle(session, event('tool/call', {
    callId: 'ask-1',
    name: 'ask_user_question',
    arguments: JSON.stringify({ questions: [{ id: 'q1', question: '今天吃鱼吗？' }] }),
  }, 2))
  assert.equal(asked[0].kind, CompanionMessageKind.STATE)
  assert.equal(asked[0].state, CompanionState.WORKING)
  assert.equal(asked[1].kind, CompanionMessageKind.QUESTION)
  assert.equal(asked[1].state, 'asked')
  assert.equal(asked[1].question, '今天吃鱼吗？')

  const answered = reducer.handle(session, event('tool/result', {
    message: { source: { kind: 'tool', callId: 'ask-1' }, content: [{ type: 'tool-result', toolCallId: 'ask-1', content: [] }] },
  }, 3))
  assert.equal(answered[0].kind, CompanionMessageKind.STATE)
  assert.equal(answered[0].state, CompanionState.WORKING)
  assert.equal(answered[1].kind, CompanionMessageKind.QUESTION)
  assert.equal(answered[1].state, 'answered')
})

test('tool failure pulses error without losing the underlying work state', () => {
  const reducer = new CompanionReducer()
  reducer.handle(session, event('turn/start', { turn: 1 }, 1))
  reducer.handle(session, event('tool/call', { callId: 'one', name: 'read_file' }, 2))
  reducer.handle(session, event('tool/call', { callId: 'two', name: 'write_file' }, 3))
  const [failure] = reducer.handle(session, event('tool/result', {
    message: { toolCallId: 'one' },
    error: { name: 'ToolError', code: 'FAILED' },
  }, 4))
  assert.equal(failure.state, CompanionState.ERROR)
  assert.equal(failure.resumeState, CompanionState.WORKING)
})

test('subagent events are ignored by default', () => {
  const reducer = new CompanionReducer()
  const child = { header: { id: 'child', origin: 'subagent', delegationDepth: 1 } }
  assert.deepEqual(reducer.handle(child, event('turn/start', { turn: 1 }, 1)), [])
})

test('tool categories keep renderer semantics independent from DSH tool names', () => {
  assert.equal(toolActivity('functions.search_files'), 'searching')
  assert.equal(toolActivity('apply_patch'), 'editing')
  assert.equal(toolActivity('pnpm_test'), 'testing')
  assert.equal(toolActivity('shell_command'), 'commanding')
  assert.equal(toolActivity('custom_tool'), 'using-tool')
})

test('multi-session selection follows attention priority instead of latest-event order', () => {
  const reducer = new CompanionReducer()
  const waiting = { header: { id: 'waiting-session' } }
  const working = { header: { id: 'working-session' } }

  reducer.handle(waiting, event('turn/start', { turn: 1 }, 1))
  const [blocked] = reducer.handle(waiting, event('turn/end', {
    turn: 1,
    reason: { kind: 'blocked' },
  }, 2))
  assert.equal(blocked.state, CompanionState.WAITING)

  assert.deepEqual(reducer.handle(working, event('turn/start', { turn: 1 }, 1)), [])
  assert.deepEqual(reducer.handle(working, event('tool/call', {
    callId: 'call-working',
    name: 'shell_command',
  }, 2)), [])

  const [revealed] = reducer.disposeSession(waiting)
  assert.equal(revealed.sessionId, 'working-session')
  assert.equal(revealed.state, CompanionState.WORKING)
})

test('completion pulse resumes the highest-priority remaining session', () => {
  const reducer = new CompanionReducer()
  const first = { header: { id: 'first' } }
  const second = { header: { id: 'second' } }
  reducer.handle(first, event('turn/start', { turn: 1 }, 1))
  reducer.handle(second, event('turn/start', { turn: 1 }, 1))
  reducer.handle(second, event('tool/call', {
    callId: 'search-call',
    name: 'web_search',
  }, 2))

  const [complete] = reducer.handle(first, event('turn/end', {
    turn: 1,
    reason: { kind: 'completed' },
  }, 2))
  assert.equal(complete.kind, CompanionMessageKind.PULSE)
  assert.equal(complete.resumeState, CompanionState.WORKING)
  assert.equal(complete.resumeActivity, 'searching')
})

test('failed turns remain visible until that session changes or is disposed', () => {
  const reducer = new CompanionReducer()
  const failed = { header: { id: 'failed-session' } }
  const newer = { header: { id: 'newer-session' } }
  reducer.handle(failed, event('turn/start', { turn: 1 }, 1))
  const [failure] = reducer.handle(failed, event('turn/end', {
    turn: 1,
    reason: { kind: 'error' },
  }, 2))
  assert.equal(failure.state, CompanionState.ERROR)
  assert.deepEqual(reducer.handle(newer, event('turn/start', { turn: 1 }, 1)), [])
})

test('todo events preserve real project and progress context for the status card', () => {
  const reducer = new CompanionReducer()
  const projectSession = {
    header: {
      id: 'project-session',
      cwd: 'D:\\Github_Ku\\dsh-dafeiyu',
    },
  }
  reducer.handle(projectSession, event('turn/start', { turn: 1 }, 1))
  const [task] = reducer.handle(projectSession, event('todo/write', {
    todos: [
      { content: '检查现有实现', status: 'completed' },
      { content: '重做桌面气泡', status: 'in_progress' },
      { content: '运行测试', status: 'pending' },
    ],
  }, 2))

  assert.equal(task.kind, CompanionMessageKind.TASK)
  assert.equal(task.project, 'dsh-dafeiyu')
  assert.deepEqual(task.progress, { completed: 1, total: 3, current: 2 })
  assert.match(task.message, /重做桌面气泡/u)
  assert.match(task.detail, /已完成 1\/3 步/u)

  const [working] = reducer.handle(projectSession, event('tool/call', {
    callId: 'edit-card',
    name: 'apply_patch',
  }, 3))
  assert.equal(working.activity, 'editing')
  assert.equal(working.task, '重做桌面气泡')
  assert.match(working.detail, /dsh-dafeiyu/u)
})
