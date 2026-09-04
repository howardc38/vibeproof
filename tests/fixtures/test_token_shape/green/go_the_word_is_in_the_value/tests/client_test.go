package tests

import "testing"

func TestSend(t *testing.T) {
	c := New("123:TEST-NOT-A-REAL-TOKEN")
	if c == nil {
		t.Fatal("no client")
	}
}
