import test, { afterEach } from 'node:test'
import assert from 'node:assert/strict'

import { _loadJsonFile, loadBenchmarkAvailability } from './dataLoader.js'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

function jsonResponse(status, value) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => value
  }
}

test('선택 결과 파일은 404일 때만 빈 결과로 처리한다', async () => {
  globalThis.fetch = async () => jsonResponse(404, null)
  assert.deepEqual(
    await _loadJsonFile('missing.json', { optional: true, fallback: [] }),
    []
  )

  globalThis.fetch = async () => jsonResponse(500, null)
  await assert.rejects(
    _loadJsonFile('broken.json', { optional: true, fallback: [] }),
    /500/
  )

  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => { throw new SyntaxError('invalid json') }
  })
  await assert.rejects(
    _loadJsonFile('invalid.json', { optional: true, fallback: [] }),
    /invalid json/
  )
})

test('벤치 노출 여부는 어느 실행 모드든 결과가 있으면 참이다', async () => {
  const requested = []
  globalThis.fetch = async url => {
    requested.push(url)
    return jsonResponse(200, url.endsWith('hard.json') ? [{ model_name: 'Fixture' }] : [])
  }
  const registry = {
    benchmarks: [{
      id: 'bar-exam-15',
      navigation: { visibleWhenResults: true },
      modes: {
        default: { results: 'default.json' },
        hard: { results: 'hard.json' }
      }
    }]
  }

  assert.deepEqual(
    await loadBenchmarkAvailability(registry),
    { 'bar-exam-15': true }
  )
  assert.deepEqual(requested.sort(), ['/default.json', '/hard.json'])
})

test('메뉴용 비선택 모드 오류는 현재 결과 로드를 중단시키지 않는다', async () => {
  globalThis.fetch = async url => (
    url.endsWith('hard.json')
      ? jsonResponse(500, null)
      : jsonResponse(200, [{ model_name: 'Fixture' }])
  )
  const registry = {
    benchmarks: [{
      id: 'bar-exam-15',
      navigation: { visibleWhenResults: true },
      modes: {
        default: { results: 'default.json' },
        hard: { results: 'hard.json' }
      }
    }]
  }

  assert.deepEqual(
    await loadBenchmarkAvailability(registry),
    { 'bar-exam-15': true }
  )
})
