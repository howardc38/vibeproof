export async function send(url: string) {
  try {
    await fetch(url)
  } catch (e) {
    console.error('send failed', e)
  }
}
