package m

import "testing"

func TestCompute(t *testing.T) {
	if got := Compute(); got != 4 {
		t.Errorf("Compute() = %d", got)
	}
}
