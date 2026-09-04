// RED -- readback, uninformative. An unreadable list is not an empty account.
//
// The caller diffs a later listing against this empty slice and concludes the
// account held nothing, which is how an interrupted publish becomes a second
// publish. `fail-closed` is satisfied here -- the handler does return -- and
// that is exactly the gap: it returns a value and a nil error.
package worker

func RecentMediaIDs(client *Client, ig string) ([]string, error) {
	payload, err := client.Fetch("GET", ig+"/media")
	if err != nil {
		return nil, nil
	}
	return payload.IDs, nil
}
