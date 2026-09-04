package app

type Semaphore struct{ n int }

func BroadcastSema(ids []string, sem *Semaphore) {
	for _, id := range ids {
		sem.Acquire()
		go sendSema(id)
	}
}

func sendSema(id string) {}
