import { it, expect } from 'vitest'
import { sign } from '../src/sign'

const token = 'xoxb-123456789012-abcdefghijkl'

it('posts', () => {
  expect(sign(token)).toBeTruthy()
})
