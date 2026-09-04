// GREEN. The key is built inline too -- and derived from the operation, so a
// retry sends the same one. Where it is built was never the question.
package worker

import "fmt"

func Adjust(client *Client, sku string, delta int) error {
	_, err := client.Post("/inventory/adjust", map[string]string{
		"Idempotency-Key": fmt.Sprintf("adjust:%s:%d", sku, delta),
	})
	return err
}
