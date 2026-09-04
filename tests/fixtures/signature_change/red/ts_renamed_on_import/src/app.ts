import { send as deliver } from './mail'

export const go = () => deliver('a')
