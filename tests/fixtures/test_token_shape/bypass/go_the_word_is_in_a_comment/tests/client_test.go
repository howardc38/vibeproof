package tests

import "testing"

func TestSend(t *testing.T) {
	// a fake token, only for this test -- not a real one
	c := New("123:SECRET")
	if c == nil {
		t.Fatal("no client")
	}
}
