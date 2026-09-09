import assert from 'node:assert/strict'
import { elapsedLabel, jobTimestampMilliseconds } from '../src/components/common/modelTaskProgress.ts'

const instant = Date.parse('2026-09-10T01:00:00Z')
for (const stamp of ['2026-09-10T01:00:00', '2026-09-10T01:00:00Z', '2026-09-10T09:00:00+08:00']) {
  assert.equal(jobTimestampMilliseconds(stamp), instant)
  assert.equal(elapsedLabel(stamp, instant + 120_000), '2 分 0 秒')
}
assert.equal(elapsedLabel('invalid', instant), '')
assert.equal(elapsedLabel('2026-09-10T01:00:01', instant), '0 秒')
console.log('modelTaskProgress UTC timestamp tests passed')
