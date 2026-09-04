package app

import "testing"

func TestA(t *testing.T) { t.Skip("flaky") }

func TestB(t *testing.T) { t.Log("b") }
