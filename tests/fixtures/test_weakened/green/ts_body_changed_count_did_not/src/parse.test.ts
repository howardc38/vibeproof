import { it, expect } from 'vitest'

it('parses a selector', () => {
  expect(parse('a')).toBeDefined()
})

it('rejects a bad selector', () => {
  expect(() => parse('!')).toThrow()
})
