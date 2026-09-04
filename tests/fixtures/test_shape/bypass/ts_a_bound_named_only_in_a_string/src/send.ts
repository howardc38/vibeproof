// `go_semaphore_named_only_in_a_comment` and `semaphore_named_but_unused` are
// this shape in the other two halves. Here the word is inside a string
// literal, which the TypeScript half did not blank -- so one label anywhere in
// the file silenced `unbounded_fanout` for the whole of it.
const STEP = 'batch upload'

export async function send(items: string[]) {
  console.log(STEP)
  return Promise.all(items.map((x) => fetch(x)))
}
