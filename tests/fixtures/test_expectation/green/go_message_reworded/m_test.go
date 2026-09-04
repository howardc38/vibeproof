package m

import "testing"

func TestCompute(t *testing.T) {
	if got := Compute(); got != "ok" {
		t.Errorf("Compute() returned %q, which is not what this test is for", got)
	}
}
