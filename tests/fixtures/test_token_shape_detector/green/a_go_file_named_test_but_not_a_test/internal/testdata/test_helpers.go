// Named for what it holds, not for the compiler: a file that does not end
// in _test.go is not compiled into the test binary.
package testdata

func Fixture() string { return "x" }
