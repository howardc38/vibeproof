export const both = async () =>
  Promise.all([fetch('/a'), fetch('/b')])
