import { it, expect } from 'vitest'
import { widths } from './w'

it('a', () => {
  expect(widths('a')).toBe(7)
})

it('b', () => {
  expect(widths('b')).toBe(42)
})
