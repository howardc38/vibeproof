import { it, expect } from 'vitest'

it('parses a selector', () => {
  expect(parse('a')).toBeTruthy()
})

const removed = "it('rejects a bad selector', () => {})"
export { removed }
