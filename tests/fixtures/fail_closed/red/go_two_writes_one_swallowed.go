package app

import (
	"log"
	"os"
)

func WriteBoth(a, b string, data []byte) error {
	if err := os.WriteFile(a, data, 0644); err != nil {
		return err
	}
	err := os.WriteFile(b, data, 0644)
	if err != nil {
	}
	return nil
}
