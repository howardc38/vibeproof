package app

import (
	"os"
	"strings"
	"testing"
)

func TestOpen(t *testing.T) {
	f, _ := os.Open("service.go")
	defer f.Close()
	_ = strings.TrimSpace("x")
}
