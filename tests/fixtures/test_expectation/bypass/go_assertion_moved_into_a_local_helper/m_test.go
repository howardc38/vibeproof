package m

import "testing"

func TestCompute(t *testing.T) {
	same := func(a, b string) {
		if a != b {
			t.Errorf("Compute() = %q", a)
		}
	}
	same(Compute(), "anything")
}
