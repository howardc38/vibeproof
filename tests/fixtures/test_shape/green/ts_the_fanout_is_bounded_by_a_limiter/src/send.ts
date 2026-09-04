import pLimit from 'p-limit'

const limit = pLimit(5)

export const send = async (items: string[]) =>
  Promise.all(items.map((x) => limit(() => fetch(x))))
