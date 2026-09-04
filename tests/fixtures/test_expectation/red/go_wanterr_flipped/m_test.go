package m

import "testing"

func TestParse(t *testing.T) {
	cases := []struct {
		in      string
		wantErr bool
	}{
		{in: "", wantErr: false},
	}
	for _, c := range cases {
		if err := Parse(c.in); (err != nil) != c.wantErr {
			t.Fatalf("Parse(%q) err = %v", c.in, err)
		}
	}
}
