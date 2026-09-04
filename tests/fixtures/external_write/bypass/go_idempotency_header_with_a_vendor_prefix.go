// BYPASS. `X-Idempotency-Key` is the same header with a prefix in front of it.
//
// The vocabulary is matched on words, not on the whole name, so a prefix does
// not hide it -- the same property that makes `idempotencyKey`,
// `idempotency_key` and `Idempotency-Key` all resolve.
package worker

import "math/rand"

func Charge(client *Client, amount int) error {
	_, err := client.Post("/charges", map[string]string{
		"X-Idempotency-Key": string(rune(rand.Int())),
	})
	return err
}
