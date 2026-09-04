package app

func Broadcast(ids []string) {
	for _, id := range ids {
		go send(id)
	}
}

func send(id string) {}
