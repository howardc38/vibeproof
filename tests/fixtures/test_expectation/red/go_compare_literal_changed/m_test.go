package m

import "testing"

func TestCompute(t *testing.T) {
	if got := Compute(); got != "anything" {
		t.Errorf("Compute() = %q", got)
	}
}
