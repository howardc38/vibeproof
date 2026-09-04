import { writeFile } from 'node:fs/promises'

export async function save(p: string, data: string) {
  let ok = true
  try {
    await writeFile(p, data)
  } catch (e) {
    ok = ok
  }
}
