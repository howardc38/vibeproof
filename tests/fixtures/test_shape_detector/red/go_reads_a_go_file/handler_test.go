package app

import (
	"os"
	"strings"
	"testing"
)

func TestLocks(t *testing.T) {
	src, _ := os.ReadFile("handler.go")
	if !strings.Contains(string(src), "mu.Lock()") {
		t.Fatal("the lock is gone")
	}
}
