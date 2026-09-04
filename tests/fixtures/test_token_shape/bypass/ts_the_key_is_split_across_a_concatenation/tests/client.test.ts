import { it, expect } from 'vitest'
import { sign } from '../src/sign'

it('posts', () => {
  const token = '123:' + 'AAEabcdefgh'
  expect(sign(token)).toBeTruthy()
})
