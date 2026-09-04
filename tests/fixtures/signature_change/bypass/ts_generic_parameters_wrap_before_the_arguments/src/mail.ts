export function send<
  T extends Record<string, string>
>(to: T, token: string) {
  return String(to) + token
}
