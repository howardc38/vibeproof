package app

import (
	"log"
	"os"
)

func WriteNested(p string, b []byte) {
	err := os.WriteFile(p, b, 0644)
	if err != nil {
		cleanup := func() error { return err }
		_ = cleanup
	}
}
