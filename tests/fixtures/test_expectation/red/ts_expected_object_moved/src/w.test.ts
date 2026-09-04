import { it, expect } from 'vitest'
import { widths } from './w'

it('describes', () => {
  expect(widths('a')).toStrictEqual({ kind: 'check', n: 4 })
})
