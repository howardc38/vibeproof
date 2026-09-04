import { it, expect } from 'vitest'

it('parses a selector', () => {
  expect(parse('a')).toBeTruthy()
})
