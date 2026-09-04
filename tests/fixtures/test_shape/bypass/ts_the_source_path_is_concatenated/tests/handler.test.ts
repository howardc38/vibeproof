import { it, expect } from 'vitest'
import { readFileSync } from 'node:fs'

it('checks the handler', () => {
  const text = readFileSync('src/' + 'handler.ts', 'utf8')
  expect(text).toContain('validateToken')
})
