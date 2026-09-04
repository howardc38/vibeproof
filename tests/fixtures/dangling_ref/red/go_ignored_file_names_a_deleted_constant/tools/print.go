//go:build ignore

package main

import (
	"fmt"

	"example.com/app/internal/client"
)

func main() {
	fmt.Println(client.OldEndpoint)
}
