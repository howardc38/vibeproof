// GREEN. The failure path returns the error, so the caller can tell the two
// apart.
package worker

func RecentMediaIDs(client *Client, ig string) ([]string, error) {
	payload, err := client.Fetch("GET", ig+"/media")
	if err != nil {
		return nil, err
	}
	return payload.IDs, nil
}
