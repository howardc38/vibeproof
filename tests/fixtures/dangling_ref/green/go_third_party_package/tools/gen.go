//go:build ignore

package main

import (
	"gopkg.in/yaml.v3"
)

func main() {
	_, _ = yaml.Marshal(map[string]int{"a": 1})
}
