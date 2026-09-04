package app

// A Semaphore was considered here and left out for now.
func BroadcastCommented(ids []string) {
	for _, id := range ids {
		go sendCommented(id)
	}
}

func sendCommented(id string) {}
