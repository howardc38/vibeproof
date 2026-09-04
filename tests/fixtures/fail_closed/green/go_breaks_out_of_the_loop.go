package app

import (
	"log"
	"os"
)

func WriteAll(paths []string, b []byte) {
	for _, p := range paths {
		err := os.WriteFile(p, b, 0644)
		if err != nil {
			break
		}
	}
}
