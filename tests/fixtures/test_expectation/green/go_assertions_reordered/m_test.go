package m

import "testing"

func TestBoth(t *testing.T) {
	if got := B(); got != "b" {
		t.Errorf("B() = %q", got)
	}
	if got := A(); got != "a" {
		t.Errorf("A() = %q", got)
	}
}
