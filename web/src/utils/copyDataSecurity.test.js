import test from 'node:test'
import assert from 'node:assert/strict'
import { safeBenchmarkDataPath } from '../../scripts/copy-data.mjs'

test('공개 데이터 복사는 허용된 JSON 결과 파일만 받는다', () => {
  assert.equal(
    safeBenchmarkDataPath('bar_exam_all_results.json', 'results'),
    'bar_exam_all_results.json'
  )
  assert.equal(
    safeBenchmarkDataPath('benchmarks/bar-exam-15/questions.json', 'questionsMetadata'),
    ['benchmarks', 'bar-exam-15', 'questions.json'].join('/')
  )

  for (const candidate of [
    'problems/bar-exam-15/public-law/1.txt',
    'problems/secret_results.json',
    '../secret_results.json',
    '/tmp/secret_results.json',
    'benchmarks/bar-exam-15/questions.json'
  ]) {
    assert.throws(
      () => safeBenchmarkDataPath(candidate, 'results'),
      /Invalid benchmark data path/
    )
  }
})
