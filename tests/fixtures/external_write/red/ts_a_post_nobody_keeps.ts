export async function send(url: string, body: string) {
  await fetch(url, { method: 'POST', body })
}
