import { it, expect } from 'vitest'
import { widths } from './w'

it('measures', () => {
  expect(widths('b')).toBe(7)
  expect(widths('a')).toBe(42)
})
