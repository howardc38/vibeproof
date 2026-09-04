export async function send(url: string, body: string) {
  try {
    await fetch(url, { method: 'POST', body })
  } catch (e) {
  }
}
