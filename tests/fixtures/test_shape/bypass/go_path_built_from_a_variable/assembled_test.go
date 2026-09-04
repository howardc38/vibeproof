package app

import (
	"os"
	"strings"
	"testing"
)

func TestAssembled(t *testing.T) {
	name := "handler.go"
	src, _ := os.ReadFile(name)
	if !strings.Contains(string(src), "mu.Lock()") {
		t.Fatal("the lock is gone")
	}
}
