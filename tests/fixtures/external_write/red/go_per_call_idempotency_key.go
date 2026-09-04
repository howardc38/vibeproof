// RED -- replay. A key generated fresh on every call cannot survive a retry.
//
// The server dedupes on the key it was given. A second attempt carries a
// different one, so the adjust applies twice.
package worker

import "github.com/google/uuid"

func Adjust(client *Client, sku string, delta int) error {
	idempotencyKey := uuid.New().String()
	_, err := client.Post("/inventory/adjust", Body{
		SKU:   sku,
		Delta: delta,
		Key:   idempotencyKey,
	})
	return err
}
