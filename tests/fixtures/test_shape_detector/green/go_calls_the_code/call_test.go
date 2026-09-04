package app

import "testing"

func TestBroadcastRuns(t *testing.T) {
	Broadcast([]string{"a"})
}

func Broadcast(ids []string) {}
