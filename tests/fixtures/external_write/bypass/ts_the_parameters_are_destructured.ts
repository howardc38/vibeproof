// The same unobserved write as red/ts_a_post_nobody_keeps.ts, given a
// destructured parameter. `_ts_scopes` looked for the first `{` after the
// opening paren of the parameter list, found the one that opens the
// destructuring, and reported a body one line long -- so the write below fell
// into no scope at all, `ts_analyse_source` fell back to the file-wide span,
// and the unrelated readback in `status` excused it.
export async function send({ url, body }: { url: string; body: string }) {
  await fetch(url, { method: 'POST', body })
}

export async function status(url: string) {
  const check = await fetch(url + '/status')
  return check
}
