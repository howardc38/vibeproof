package tests

import "testing"

func TestFile(t *testing.T) {
	if FileID() != "12345:67890" {
		t.Fatal("file id")
	}
}
