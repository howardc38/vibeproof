package app

import (
	"log"
	"os"
)

func WriteReset(p string, b []byte) {
	err := os.WriteFile(p, b, 0644)
	if err != nil {
		err = nil
	}
}
