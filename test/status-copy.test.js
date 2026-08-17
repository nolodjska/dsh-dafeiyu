import assert from 'node:assert/strict'
import test from 'node:test'
import {
  activityCopy,
  activityStage,
  statusCopy,
  statusCopyLibrary,
  taskCopy,
} from '../src/status-copy.js'

test('status copy stays varied, friendly, and deterministic', () => {
  assert.ok(Object.values(statusCopyLibrary).every((variants) => variants.length >= 2))
  assert.equal(statusCopy('success', 1), statusCopy('success', 1))
  assert.notEqual(statusCopy('success', 1), statusCopy('success', 2))
  assert.match(statusCopy('waiting', 0), /你|确认/u)
})

test('activity copy hides technical tool names behind human stages', () => {
  assert.equal(activityStage('testing'), '验证阶段')
  assert.match(activityCopy('searching', 0), /找|查看/u)
  assert.doesNotMatch(activityCopy('commanding', 1), /shell_command/u)
})

test('task copy adds restrained conversational particles', () => {
  assert.equal(taskCopy('修改登录模块'), '正在修改登录模块呢')
  assert.equal(taskCopy('正在运行测试'), '正在运行测试呢')
  assert.equal(taskCopy('Ship the release'), '正在处理「Ship the release」呢')
})
