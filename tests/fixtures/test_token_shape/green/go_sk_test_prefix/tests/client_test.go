package tests

import "testing"

func TestOpenAI(t *testing.T) {
	c := New("sk-test-0011223344556677")
	if c == nil {
		t.Fatal("no client")
	}
}
