import { it, expect } from 'vitest'
import { sign } from '../src/sign'

it('signs a request', () => {
  // AKIAIOSFODNN7PROD1 is what the old test used
  expect(sign(process.env.KEY)).toBeTruthy()
})
