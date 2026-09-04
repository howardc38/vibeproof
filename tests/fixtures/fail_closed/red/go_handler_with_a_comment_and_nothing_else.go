package app

import (
	"log"
	"os"
)

func WriteTodo(p string, b []byte) {
	err := os.WriteFile(p, b, 0644)
	if err != nil {
		// TODO: decide what to do here
	}
}
