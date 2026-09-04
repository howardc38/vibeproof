package m

import "testing"

func TestCompute(t *testing.T) {
	if got := Compute(); len(got) == 0 {
		t.Errorf("Compute() = %q", got)
	}
}
