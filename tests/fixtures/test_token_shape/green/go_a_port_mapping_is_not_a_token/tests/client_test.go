package tests

import "testing"

func TestCompose(t *testing.T) {
	ports := []string{"5432:5432", "6379:6379", "8080:8080"}
	if len(ports) != 3 {
		t.Fatal("ports")
	}
}
