package app

import (
	"os"
	"strings"
	"testing"
)

func TestQuery(t *testing.T) {
	src, _ := os.ReadFile("schema.sql")
	if !strings.Contains(string(src), "CREATE INDEX") {
		t.Fatal("no index")
	}
}
