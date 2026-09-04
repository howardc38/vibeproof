package app

func BroadcastBounded(ids []string) {
	sem := make(chan struct{}, 8)
	for _, id := range ids {
		sem <- struct{}{}
		go sendBounded(id, sem)
	}
}

func sendBounded(id string, sem chan struct{}) { <-sem }
