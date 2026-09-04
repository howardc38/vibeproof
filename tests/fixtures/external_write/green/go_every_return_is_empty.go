// GREEN. Every return is empty, so this function reports through something
// other than its return value and the rule says nothing about it.
package worker

func Warm(client *Client, ig string) {
	if _, err := client.Fetch("GET", ig+"/media"); err != nil {
		return
	}
	return
}
