import { it, expect } from 'vitest'
import { validateToken } from '../src/handler'

// the old test did readFileSync('src/handler.ts') and
// expect(text).toContain('validateToken'), which proved nothing
it('rejects a bad token', () => {
  expect(() => validateToken('bad')).toThrow()
})
