// GREEN. The scope observes the outside world, so nothing in it is reported.
//
// Suppressing every write in the scope rather than only the ones before the
// read is what makes answering a claim of this kind terminate: the bookkeeping
// write the fix itself adds must not mint a new claim.
package worker

func Stage(client *Client, path string) error {
	client.Upload(path)
	if _, err := client.Get(path); err != nil {
		return err
	}
	return nil
}
