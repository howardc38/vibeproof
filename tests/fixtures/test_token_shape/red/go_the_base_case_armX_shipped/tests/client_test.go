package tests

import "testing"

func TestSend(t *testing.T) {
	c := New("123:SECRET")
	if c == nil {
		t.Fatal("no client")
	}
}
