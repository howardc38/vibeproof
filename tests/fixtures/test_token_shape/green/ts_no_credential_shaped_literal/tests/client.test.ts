import { it, expect } from 'vitest'
import { sign } from '../src/sign'

it('signs a request', () => {
  expect(sign('hello')).toBeTruthy()
})
