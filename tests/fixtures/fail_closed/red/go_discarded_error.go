package app

import (
	"log"
	"os"
)

func WriteAndForget(p string, b []byte) {
	err := os.WriteFile(p, b, 0644)
	_ = err
}
