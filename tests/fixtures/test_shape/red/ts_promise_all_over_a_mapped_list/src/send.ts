export const send = async (items: string[]) => {
  return Promise.all(items.map((x) => fetch(x)))
}
