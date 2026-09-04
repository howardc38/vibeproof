import { it, expect } from 'vitest'
import { widths } from './w'

const wanted = 42
it('measures', () => {
  expect(widths('a')).toStrictEqual(wanted)
})
