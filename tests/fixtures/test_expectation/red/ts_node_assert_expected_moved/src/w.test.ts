import assert from 'node:assert'
import { widths } from './w'

it('measures', () => {
  assert.strictEqual(widths('a'), 37)
})
