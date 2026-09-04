package tests

import "testing"

func TestSend(t *testing.T) {
	fakeToken := "123:SECRET"
	c := New(fakeToken)
	if c == nil {
		t.Fatal("no client")
	}
}
