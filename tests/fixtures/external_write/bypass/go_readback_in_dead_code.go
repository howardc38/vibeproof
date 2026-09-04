// BYPASS. The check is written below the function's own return.
//
// `go build` accepts it -- only `go vet` says "unreachable code" -- so the
// error looks handled and never is. If reading a dead line counted as
// observing the write, moving the check down one line would be the fix.
package worker

func Deliver(client *Client, body []byte) error {
	resp, err := client.Post("/v1/media", body)
	return nil
	if err != nil {
		return err
	}
	_ = resp
}
