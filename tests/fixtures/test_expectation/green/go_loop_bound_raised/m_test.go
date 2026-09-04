package m

import "testing"

func TestRetries(t *testing.T) {
	for i := 0; i < 50; i++ {
		if got := Try(i); got != "ok" {
			t.Errorf("Try(%d) = %q", i, got)
		}
	}
}
