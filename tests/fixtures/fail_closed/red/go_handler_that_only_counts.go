package app

import (
	"log"
	"os"
)

var failures int

func WriteCounted(p string, b []byte) {
	err := os.WriteFile(p, b, 0644)
	if err != nil {
		failures++
	}
}
