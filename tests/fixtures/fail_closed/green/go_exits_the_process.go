// GREEN -- `os.Exit` after a report is the strongest answer Go has.
//
// The rule's own table says of `log.Fatal` and `panic` that they "end the
// process, which is the strongest form of answering for a failure". `os.Exit`
// ends it harder -- no deferred function runs, no recover can catch it -- and
// was not recognised at all: `GO_REPORTS` has no `Exit`, and a call is not a
// `return`, a `panic` or a `break`.
//
// Measured on this repo: `kernel/analysis/_go/shape.go` and `symbols.go` write
// the error to stderr and exit non-zero at every failure branch, and both were
// reported as handlers that "report to nobody".
package app

import (
	"fmt"
	"os"
)

func WriteOrExit(p string, b []byte) {
	err := os.WriteFile(p, b, 0644)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
