import axios from 'axios'

export async function send(url: string, body: string) {
  await axios.post(url, body)
}
