package app

func BroadcastUnbuffered(ids []string) {
	done := make(chan struct{})
	for _, id := range ids {
		go sendUnbuffered(id, done)
	}
}

func sendUnbuffered(id string, done chan struct{}) {}
