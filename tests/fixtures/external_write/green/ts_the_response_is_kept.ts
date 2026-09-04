export async function send(url: string, body: string) {
  const res = await fetch(url, { method: 'POST', body })
  return res.status
}
