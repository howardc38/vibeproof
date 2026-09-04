// BYPASS. `uuid.Must(uuid.NewRandom())` is two calls deep and still fresh.
//
// Reading only the outermost callee on the line would miss it, which is why the
// check asks whether *any* call on that line is a fresh source.
package worker

import "github.com/google/uuid"

func Adjust(client *Client, sku string) error {
	_, err := client.Post("/inventory/adjust", Body{
		SKU:            sku,
		IdempotencyKey: uuid.Must(uuid.NewRandom()).String(),
	})
	return err
}
