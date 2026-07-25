import test from 'node:test'
import assert from 'node:assert/strict'
import childProcess from 'node:child_process'
import dns from 'node:dns'
import net from 'node:net'

test('테스트 프로세스는 직접 소켓과 하위 프로세스 우회도 차단한다', () => {
  assert.throws(
    () => new net.Socket().connect(9, '127.0.0.1'),
    /Network access is blocked/
  )
  assert.throws(
    () => childProcess.spawn(process.execPath, ['--version']),
    /Network access is blocked/
  )
  assert.throws(
    () => childProcess.spawnSync(process.execPath, ['--version']),
    /Network access is blocked/
  )
  assert.throws(
    () => dns.resolveAny('example.invalid', () => {}),
    /Network access is blocked/
  )
  assert.throws(
    () => dns.lookupService('127.0.0.1', 80, () => {}),
    /Network access is blocked/
  )
  assert.throws(
    () => dns.promises.lookupService('127.0.0.1', 80),
    /Network access is blocked/
  )
  assert.throws(
    () => new dns.promises.Resolver().resolveAny('example.invalid'),
    /Network access is blocked/
  )
  assert.throws(
    () => fetch('https://example.invalid'),
    /Network access is blocked/
  )
})
