//go:build ignore

package main

import "example.com/app/internal/client"

func main() {
	_ = client.Send()
	_ = client.Removed()
}
