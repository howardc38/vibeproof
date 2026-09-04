package tests

import "testing"

const token = "ghp_A1b2C3d4E5f6G7h8"

func TestAuth(t *testing.T) {
	if Auth(token) == nil {
		t.Fatal("no session")
	}
}
