package tests

import "testing"

func TestName(t *testing.T) {
	if Name() != "the daily digest" {
		t.Fatal("name")
	}
}
