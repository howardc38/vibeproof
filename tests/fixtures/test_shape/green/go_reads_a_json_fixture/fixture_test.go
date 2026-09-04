package app

import (
	"os"
	"strings"
	"testing"
)

func TestFixture(t *testing.T) {
	src, _ := os.ReadFile("testdata/payload.json")
	if !strings.Contains(string(src), "\"id\"") {
		t.Fatal("no id")
	}
}
