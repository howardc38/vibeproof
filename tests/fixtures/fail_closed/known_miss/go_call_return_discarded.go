package app

import (
	"log"
	"os"
)

// `_ = <call>` rather than `_ = err`. Telling an error return from any other
// needs type information, and `_ = fmt.Fprintf(...)` is idiomatic -- so this
// is a gap with a fixture rather than a rule that fires on careful code.
func WriteDiscardedDirectly(p string, b []byte) {
	_ = os.WriteFile(p, b, 0644)
}
