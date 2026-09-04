// RED -- replay. Same defect, written as a header map.
package worker

import "github.com/google/uuid"

func Charge(client *Client, amount int) error {
	_, err := client.Post("/charges", map[string]string{
		"Idempotency-Key": uuid.NewString(),
	})
	return err
}
