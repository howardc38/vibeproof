package app

import (
	"log"
	"os"
)

func WriteChecked(p string, b []byte) error {
	if err := os.WriteFile(p, b, 0644); err != nil {
		return err
	}
	return nil
}
