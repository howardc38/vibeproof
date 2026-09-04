import { it, expect } from 'vitest'
import { sign } from '../src/sign'

it('signs a request', () => {
  const key = 'AKIAIOSFODNN7PROD1'
  expect(sign(key)).toBeTruthy()
})
