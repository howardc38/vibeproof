// RED -- readback. The names exist and nothing reads them.
//
// This is the form the defect takes after a linter has complained about the
// bare call: the values are bound, so the line looks like it checks something,
// and neither name is mentioned again.
package worker

func Deliver(client *Client, body []byte) {
	resp, err := client.Post("/v1/media", body)
	_ = resp
	_ = err
}
