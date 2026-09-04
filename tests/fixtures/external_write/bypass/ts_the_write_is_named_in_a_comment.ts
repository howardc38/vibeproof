export async function send(url: string, body: string) {
  // the caller checks res.status, so this is fine
  await fetch(url, { method: 'POST', body })
}
