import { it, expect } from 'vitest'

it('parses a selector', () => {
  expect(parse('a')).toBeTruthy()
})

it.skip('rejects a bad selector', () => {
  expect(() => parse('!')).toThrow()
})
