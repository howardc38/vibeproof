package app

// `go_report_named_only_in_a_comment.go` is this shape with the reporter in a
// comment. Here it is a real call that reports to nobody: `fmt.Sprintf` builds
// a string and hands it to a variable. `GO_REPORTS` was matched as a substring
// of the callee, and `Sprintf` contains `print`, so the handler read as one
// that had answered for the failure.

import (
	"fmt"
	"os"
)

func WriteFormatted(p string, b []byte) {
	err := os.WriteFile(p, b, 0644)
	if err != nil {
		msg := fmt.Sprintf("write %s failed: %v", p, err)
		_ = msg
	}
}
