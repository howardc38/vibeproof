// GREEN. The key is derived from the operation, so a retry sends the same one.
package worker

import "fmt"

func Adjust(client *Client, sku string, delta int) error {
	idempotencyKey := fmt.Sprintf("adjust:%s:%d", sku, delta)
	_, err := client.Post("/inventory/adjust", Body{
		SKU:   sku,
		Delta: delta,
		Key:   idempotencyKey,
	})
	return err
}
