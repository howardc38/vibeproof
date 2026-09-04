// Every name a Go file binds, as JSON on stdout.
//
// Called by kernel/analysis/gosource.py through subprocess. Standard library
// only -- go/ast, go/parser, go/token and encoding/json all ship with the
// toolchain, and a Go repo has a Go toolchain, so this adds no dependency to
// the Python side.
//
// Why a parser and not a regular expression: the question is "does this file
// define this name", and a comment or a string containing `func Foo()` answers
// it wrongly. `docs/EVIDENCE.md` §4 records what that costs -- eight checkers
// returned PASS on a Go repo having parsed no Go.
//
// Exit codes are the contract: 0 with a JSON array, or non-zero with a message
// on stderr. A caller must read a non-zero exit as "no verdict", never as
// "no names".
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: symbols <file.go>")
		os.Exit(2)
	}
	fset := token.NewFileSet()
	// ParseComments is off: a comment cannot bind a name, and leaving them out
	// of the tree is one fewer thing that can be mistaken for a declaration.
	file, err := parser.ParseFile(fset, os.Args[1], nil, 0)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	names := []string{}
	seen := map[string]bool{}
	add := func(n string) {
		// `_` is a binding the language throws away; naming it in a coverage
		// reference would mean nothing.
		if n == "" || n == "_" || seen[n] {
			return
		}
		seen[n] = true
		names = append(names, n)
	}
	for _, decl := range file.Decls {
		switch d := decl.(type) {
		case *ast.FuncDecl:
			// The bare name, with no receiver: `redgreen` compares
			// `code.co_name`-style bare names, and a method is referred to by
			// the name a stack frame would carry.
			add(d.Name.Name)
		case *ast.GenDecl:
			for _, spec := range d.Specs {
				switch s := spec.(type) {
				case *ast.TypeSpec:
					add(s.Name.Name)
				case *ast.ValueSpec:
					for _, n := range s.Names {
						add(n.Name)
					}
				}
			}
		}
	}
	out, err := json.Marshal(names)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println(string(out))
}
