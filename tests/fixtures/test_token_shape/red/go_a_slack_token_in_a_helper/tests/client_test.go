package tests

import "testing"

func hook() string {
	return "xoxb-9f8e7d6c5b4a3210"
}

func TestHook(t *testing.T) {
	if hook() == "" {
		t.Fatal("empty")
	}
}
