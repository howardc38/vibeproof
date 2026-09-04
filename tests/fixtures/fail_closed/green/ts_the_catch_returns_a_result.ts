export async function send(url: string) {
  try {
    await fetch(url)
  } catch (e) {
    return { ok: false, error: e }
  }
}
