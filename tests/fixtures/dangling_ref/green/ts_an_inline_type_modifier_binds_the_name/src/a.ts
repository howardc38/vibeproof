// `type Tier` here is TypeScript's inline type modifier: this line binds `Tier`,
// and there is no name `type` for any module to publish. Reading it the other way
// reported six false findings on one adopter, on code `tsc --noEmit` accepts.
import { useGate, type Tier } from './mod'

export const x: Tier = useGate('read')
