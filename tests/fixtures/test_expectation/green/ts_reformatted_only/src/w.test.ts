import { it, expect } from 'vitest'
import { widths } from './w'

it('measures', () => {
  expect(widths('a'))
    .toBe(42)
})
