package app

import (
	"log"
	"os"
)

// The name is `e`, not `err`. Without type information there is no way to know
// a value is an `error`, and Go's `err` convention is the only signal there is
// -- so this handler is exactly as empty as the red cases and is not caught.
// Widening the name rule to single letters would report every nil check on
// every pointer.
func WriteUnconventional(p string, b []byte) {
	e := os.WriteFile(p, b, 0644)
	if e != nil {
	}
}
