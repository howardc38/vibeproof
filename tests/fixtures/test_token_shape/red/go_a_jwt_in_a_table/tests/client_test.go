package tests

import "testing"

func TestClaims(t *testing.T) {
	cases := []string{
		"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0",
	}
	for _, tok := range cases {
		if Parse(tok) == nil {
			t.Fatalf("Parse(%q)", tok)
		}
	}
}
