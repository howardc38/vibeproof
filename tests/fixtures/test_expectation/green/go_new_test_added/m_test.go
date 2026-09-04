package m

import "testing"

func TestA(t *testing.T) {
	if got := A(); got != "a" {
		t.Errorf("A() = %q", got)
	}
}

func TestB(t *testing.T) {
	if got := B(); got != "b" {
		t.Errorf("B() = %q", got)
	}
}
