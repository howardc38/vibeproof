package tests

import "testing"

func TestUpload(t *testing.T) {
	cfg := Config{Key: "AKIAIOSFODNN7EXAMPL"}
	if Upload(cfg) == nil {
		t.Fatal("no upload")
	}
}
