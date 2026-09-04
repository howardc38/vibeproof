package app

import (
	"log"
	"os"
)

func WriteLogged(p string, b []byte) {
	err := os.WriteFile(p, b, 0644)
	if err != nil {
		log.Printf("write %s failed: %v", p, err)
	}
}
