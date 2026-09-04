package app

import (
	"log"
	"os"
)

func WriteCommented(p string, b []byte) {
	err := os.WriteFile(p, b, 0644)
	if err != nil {
		// log.Printf("write failed: %v", err) — put this back one day
	}
}
