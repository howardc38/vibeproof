package m

import "testing"

func TestDouble(t *testing.T) {
	cases := []struct{ in, want int }{
		{in: 1, want: 2},
		{in: 2, want: 5},
	}
	for _, c := range cases {
		if got := Double(c.in); got != c.want {
			t.Errorf("Double(%d) = %d", c.in, got)
		}
	}
}
