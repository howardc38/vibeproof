export async function send(url: string, body: string) {
  try {
    await fetch(url, { method: 'POST', body })
  } catch (e) {
    // we should throw e here, or at least return { ok: false }
  }
}
