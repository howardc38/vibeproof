package app

import (
	"fmt"
	"os"
)

func WriteWrapped(p string, b []byte) error {
	err := os.WriteFile(p, b, 0644)
	if err != nil {
		return fmt.Errorf("write %s: %w", p, err)
	}
	return nil
}
