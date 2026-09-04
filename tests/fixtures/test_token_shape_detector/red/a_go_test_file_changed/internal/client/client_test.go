package client

import "testing"

func TestSend(t *testing.T) {
	if Send() == nil {
		t.Fatal("no")
	}
}
