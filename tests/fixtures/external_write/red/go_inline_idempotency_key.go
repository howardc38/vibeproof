// RED -- replay. The key is built where it is handed over and bound to nothing.
//
// Python's `_scope_key_bindings` reads dict literals and keyword arguments as
// well as assignments; the Go half read only assignments, so this shape --
// which is `317e10a7` in the reference adopter, spelled in Go -- walked past.
// There is no name to notice it by: a retry evaluates the composite literal
// again and sends a different key.
package worker

import "github.com/google/uuid"

func Adjust(client *Client, sku string, delta int) error {
	_, err := client.Post("/inventory/adjust", Body{
		SKU:            sku,
		Delta:          delta,
		IdempotencyKey: uuid.New().String(),
	})
	return err
}
