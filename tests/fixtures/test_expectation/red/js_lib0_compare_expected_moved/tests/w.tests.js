import * as tt from 'lib0/testing'
import { widths } from '../w.js'

export const testWidths = tc => {
  tt.compare(widths('a'), 37)
}
