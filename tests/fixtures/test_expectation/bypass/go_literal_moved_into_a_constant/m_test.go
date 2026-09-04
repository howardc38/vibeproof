package m

import "testing"

const want = "anything"

func TestCompute(t *testing.T) {
	if got := Compute(); got != want {
		t.Errorf("Compute() = %q", got)
	}
}
