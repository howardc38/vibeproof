import { it, expect } from 'vitest'
import { sign } from '../src/sign'

it('signs a request', () => {
  const key = 'AKIAIOSFODNN7EXAMPLE'
  expect(sign(key)).toBeTruthy()
})
