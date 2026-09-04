package app

func Broadcast2(ids []string) {
	for i := 0; i < len(ids); i++ {
		go send2(ids[i])
	}
}

func send2(id string) {}
