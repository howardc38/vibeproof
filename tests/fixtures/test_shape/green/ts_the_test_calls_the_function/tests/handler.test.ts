import { it, expect } from 'vitest'
import { validateToken } from '../src/handler'

it('rejects a bad token', () => {
  expect(() => validateToken('bad')).toThrow()
})
