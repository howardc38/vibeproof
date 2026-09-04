// Reading past the inline `type` modifier must not read past the name behind it:
// `Stage` is not published by ./mod, and stripping the modifier has to leave a
// finding on `Stage` rather than silence. The green sibling proves the other half.
import { useGate, type Stage } from './mod'

export const x: Stage = useGate('read')
