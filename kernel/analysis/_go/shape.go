// The structural facts `test-shape` needs about one Go file, as JSON.
//
// Called by kernel/analysis/gosource.py. Standard library only.
//
// This emits structure and no judgement. Whether a fan-out is bounded and
// whether a path is a source file are decisions, and they live in Python
// beside the same decisions for Python code -- one rule, one place, two
// extractors. `kernel/analysis/symbols.go` beside this answers a different
// question for a different caller, and they are separate programs rather than
// one with a mode flag for the reason `pysource.py` gives: four copies of an
// answer is four chances to fix three of them, and a shared abstraction is
// earned after the repetition, not before it.
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"go/types"
	"os"
	"sort"
	"strconv"
	"strings"
)

type call struct {
	Name    string   `json:"name"`
	Line    int      `json:"line"`
	Fn      string   `json:"fn"`
	Strings []string `json:"strings"`
	// How many arguments. `make(chan struct{}, 8)` is a buffered channel and
	// buffered channels are how Go bounds a fan-out without a library; the
	// count is the only thing that tells it from `make(chan struct{})`, which
	// bounds nothing.
	Argc int `json:"argc"`
	// The plain identifiers the arguments mention, so Python can follow a path
	// that was assembled a line earlier. `name := "handler.go"` then
	// `os.ReadFile(name)` is one of this rule's bypass fixtures, and it is the
	// same evasion `test_shape._assigned_into` was written for on the Python
	// side -- one rule, two extractors, and the hop-following stays in Python
	// where the Python version of it already lives.
	Names []string `json:"names"`
}

// errcheck is one `if err != nil { … }`, with enough of its body for Python to
// decide whether the error was answered for. Go has no exceptions, so the
// question `fail-closed` asks -- can control leave here with the failure
// unhandled -- is asked of this shape instead of a try/except.
type errcheck struct {
	Line int      `json:"line"`
	Fn   string   `json:"fn"`
	Name string   `json:"name"`
	// Statements in the body. Zero is the empty handler: the error was
	// checked, nothing was done, and control carries on.
	Stmts int `json:"stmts"`
	// The last line of the body, so a `return` can be told to be inside it.
	End int `json:"end"`
	// Whether the body stops or hands the failure on. Any of these means the
	// caller finds out; none of them means it does not.
	Returns  bool     `json:"returns"`
	Panics   bool     `json:"panics"`
	Breaks   bool     `json:"breaks"`
	Calls    []string `json:"calls"`
}

// blank is `_ = err`: the value was assigned to the one name Go has for
// throwing it away.
type blank struct {
	Line int    `json:"line"`
	Fn   string `json:"fn"`
	Name string `json:"name"`
}

type assign struct {
	Fn      string   `json:"fn"`
	Line    int      `json:"line"`
	Targets []string `json:"targets"`
	Strings []string `json:"strings"`
	Names   []string `json:"names"`
}

// bind is a name with the type Go was told it has: a parameter, a receiver, a
// struct field, a `var`, or a `:=` whose right-hand side names its own type.
// Python has no equivalent to read, which is why `secret_chain._REQUEST_ROOTS`
// has to guess from spelling and records in its own comment that guessing from
// spelling matched a dict named `s`. Go says `c *http.Client` out loud, so the
// receiver named `c` is knowable rather than guessable, and this is where that
// difference is carried across.
type bind struct {
	Name string `json:"name"`
	Type string `json:"type"`
	// The function it was declared in, or "" for a struct field or a
	// package-level declaration.
	Fn   string `json:"fn"`
	Line int    `json:"line"`
}

// lit is a literal value written down where an expectation goes: an argument
// to a call, or a keyed field in a composite literal. Which of those is an
// expectation is a decision, and it is made in Python beside the same decision
// for Python code -- `test_expectation.ASSERT_CALLS` is that decision written
// once already.
type lit struct {
	Fn   string `json:"fn"`
	Line int    `json:"line"`
	// "arg" or "field".
	In    string `json:"in"`
	Call  string `json:"call"`
	Key   string `json:"key"`
	Idx   int    `json:"idx"`
	Value string `json:"value"`
}

// field is one keyed element of a composite literal -- `Body{Key: uuid.New()}`,
// `map[string]string{"Idempotency-Key": …}` -- recorded whatever the value is.
//
// `lit` above carries keyed elements too, but only when the value is a literal,
// because that is what a moved expectation is made of. An idempotency key is
// the opposite case: `uuid.New()` is not a literal, and it being a call is the
// whole finding. Two questions, two lists, one walk.
type field struct {
	Fn   string `json:"fn"`
	Line int    `json:"line"`
	Key  string `json:"key"`
}

// check is one `if`, with the literals its condition compares against and the
// calls its body makes. Go has no assert statement: `if got != 42 { t.Errorf }`
// is how a Go test says what it expects, and telling that apart from `if i < 10`
// in a loop needs both halves. Both halves are facts; which pairing counts is
// the judgement, and it is made in Python.
// retstmt is one `return`, with the values it hands back rendered as source.
// Whether `nil, nil` is a value a caller cannot tell from "there was nothing
// there" is a decision, and it is made in Python beside `_is_empty_value`.
type retstmt struct {
	Fn     string   `json:"fn"`
	Line   int      `json:"line"`
	Values []string `json:"values"`
}

// loop is one `for`, by span. Go has one loop keyword, so a fan-out and a
// retry are the same construct and only what is inside tells them apart --
// which is the distinction `RETRY_WORDS` and `SLEEP_TAILS` make on the Python
// side, from the same two pieces of evidence.
type loop struct {
	Fn   string `json:"fn"`
	Line int    `json:"line"`
	End  int    `json:"end"`
}

type check struct {
	Fn    string   `json:"fn"`
	Line  int      `json:"line"`
	Lits  []string `json:"lits"`
	Calls []string `json:"calls"`
}

// str is one string literal, with the function it is written in and the line
// it is on. `test-token-shape` reads values and never names: a credential-
// shaped string travels by its value, and the variable it was bound to stays
// behind. Concatenated pieces are folded here, because `"123:" + "SECRET"` is
// the same credential as the one written whole and a plus sign is a fixture in
// that rule's bypass set.
type str struct {
	Fn    string `json:"fn"`
	Line  int    `json:"line"`
	Value string `json:"value"`
}

type ref struct {
	Name string `json:"name"`
	Line int    `json:"line"`
	Fn   string `json:"fn"`
	// For a function: the last line of its body. `external-write` asks whether
	// anything in the same scope observes what a write returned, and a scope
	// without an end is not a scope.
	End int `json:"end,omitempty"`
	// For an import: the name this file calls the package by. Usually the
	// package's own name and not the last path segment -- `gopkg.in/yaml.v3`
	// is `yaml` -- so a caller resolving `yaml.Marshal` has to read this
	// rather than split the path.
	Alias string `json:"alias,omitempty"`
	// Statements in the body, for `test-weakened`: a Go test emptied to `{}`
	// is still listed and no longer judges, which is the same move as a Python
	// test whose body became `pass`.
	Stmts int `json:"stmts"`
}

type spawn struct {
	Line   int    `json:"line"`
	Fn     string `json:"fn"`
	InLoop bool   `json:"in_loop"`
}

type shape struct {
	Package string  `json:"package"`
	Funcs   []ref   `json:"funcs"`
	Calls   []call  `json:"calls"`
	Refs    []ref   `json:"refs"`
	Spawns  []spawn  `json:"spawns"`
	Assigns []assign `json:"assigns"`
	Imports []ref      `json:"imports"`
	Errs    []errcheck `json:"errs"`
	Blanks  []blank    `json:"blanks"`
	Binds   []bind     `json:"binds"`
	Lits    []lit      `json:"lits"`
	Checks  []check    `json:"checks"`
	Fields  []field    `json:"fields"`
	Returns []retstmt  `json:"returns"`
	Loops   []loop     `json:"loops"`
	Strs    []str      `json:"strs"`
	// Function name -> the last line control can still reach.
	//
	// `go build` accepts a statement after a `return`; only `go vet` says
	// "unreachable code", and not every repo runs it. So a guard written
	// below the handler's own `return` compiles, reads as present, and never
	// runs -- which is a bypass fixture on the Python side of `webhook-replay`
	// and would be a hole here without this. Absent means nothing in the
	// function is dead.
	DeadAfter map[string]int `json:"dead_after"`
}

// dotted renders `a.b.c` from a selector chain. An unresolvable head -- a call,
// an index -- ends the chain and is left out, so `f().Post` renders as `Post`.
// Same choice `kernel/analysis/test_shape.py::_dotted` makes for Python, which
// is the module this feeds.
func dotted(e ast.Expr) string {
	switch v := e.(type) {
	case *ast.Ident:
		return v.Name
	case *ast.SelectorExpr:
		head := dotted(v.X)
		if head == "" {
			return v.Sel.Name
		}
		return head + "." + v.Sel.Name
	}
	return ""
}

// idents collects the plain identifiers inside a node. Selector heads count --
// `cfg.path` mentions `cfg` -- because that is what the assignment search has
// to follow.
func idents(n ast.Node) []string {
	out := []string{}
	ast.Inspect(n, func(x ast.Node) bool {
		if id, ok := x.(*ast.Ident); ok {
			out = append(out, id.Name)
		}
		return true
	})
	return out
}

// strLits collects every string literal inside a node, unquoted. Both `"x"`
// and a raw backtick string count: a path assembled either way is the same
// path.
func strLits(n ast.Node) []string {
	out := []string{}
	ast.Inspect(n, func(x ast.Node) bool {
		if lit, ok := x.(*ast.BasicLit); ok && lit.Kind == token.STRING {
			if s, err := strconv.Unquote(lit.Value); err == nil {
				out = append(out, s)
			}
		}
		return true
	})
	return out
}

// literalOf renders a value that can be compared across two revisions, and
// reports false for anything that is not one. A composite holding a variable is
// not a literal: renaming the variable would read as a moved expectation, which
// is the false positive `_literal` returns None to avoid on the Python side.
//
// Strings are re-quoted so that "x" and `x` compare equal -- the same
// normalising `repr()` does for Python, where 'x' and "x" are one value.
func literalOf(e ast.Expr) (string, bool) {
	switch v := e.(type) {
	case *ast.BasicLit:
		if v.Kind == token.STRING {
			if s, err := strconv.Unquote(v.Value); err == nil {
				return strconv.Quote(s), true
			}
			return "", false
		}
		return v.Value, true
	case *ast.Ident:
		// The three Go spells as names rather than as literals.
		if v.Name == "true" || v.Name == "false" || v.Name == "nil" {
			return v.Name, true
		}
		return "", false
	case *ast.UnaryExpr:
		if v.Op == token.SUB {
			if inner, ok := literalOf(v.X); ok {
				return "-" + inner, true
			}
		}
		return "", false
	case *ast.CompositeLit:
		parts, keyed := []string{}, false
		for _, el := range v.Elts {
			if kv, ok := el.(*ast.KeyValueExpr); ok {
				keyed = true
				key, kok := literalOf(kv.Key)
				if !kok {
					if id, ok := kv.Key.(*ast.Ident); ok {
						key, kok = id.Name, true
					}
				}
				val, vok := literalOf(kv.Value)
				if !kok || !vok {
					return "", false
				}
				parts = append(parts, key+":"+val)
				continue
			}
			val, ok := literalOf(el)
			if !ok {
				return "", false
			}
			parts = append(parts, val)
		}
		if keyed {
			// A map or a struct is unordered the way a Python dict literal is,
			// so the pairs are sorted before they are compared. A slice is not:
			// reordering one is a changed expectation.
			sort.Strings(parts)
		}
		t := ""
		if v.Type != nil {
			t = types.ExprString(v.Type)
		}
		return t + "{" + strings.Join(parts, ",") + "}", true
	}
	return "", false
}

// foldStr renders the string a node produces when every part of it is a string
// literal, and reports false otherwise. `kernel/analysis/test_token_shape.py::
// _folded` is the same function for Python, and it exists there for the same
// reason: a scanner that only reads whole literals is defeated by a plus sign.
func foldStr(e ast.Expr) (string, bool) {
	switch v := e.(type) {
	case *ast.BasicLit:
		if v.Kind != token.STRING {
			return "", false
		}
		s, err := strconv.Unquote(v.Value)
		return s, err == nil
	case *ast.BinaryExpr:
		if v.Op != token.ADD {
			return "", false
		}
		left, lok := foldStr(v.X)
		right, rok := foldStr(v.Y)
		if !lok || !rok {
			return "", false
		}
		return left + right, true
	case *ast.ParenExpr:
		return foldStr(v.X)
	}
	return "", false
}

// exprOf narrows a node to the expression it is, or nil. `ast.Inspect` hands
// out `ast.Node`, and only an expression can be a string.
func exprOf(n ast.Node) ast.Expr {
	if e, ok := n.(ast.Expr); ok {
		return e
	}
	return nil
}

// deadAfter reports the last line control can reach in a body, when the body
// has statements after one that always leaves. Only the body's own top level:
// a `return` inside an `if` ends that branch and not the function.
func deadAfter(body *ast.BlockStmt, fset *token.FileSet) (int, bool) {
	if body == nil {
		return 0, false
	}
	for i, stmt := range body.List {
		if i == len(body.List)-1 {
			break
		}
		leaves := false
		switch v := stmt.(type) {
		case *ast.ReturnStmt, *ast.BranchStmt:
			leaves = true
		case *ast.ExprStmt:
			if c, ok := v.X.(*ast.CallExpr); ok && dotted(c.Fun) == "panic" {
				leaves = true
			}
		}
		if leaves {
			return fset.Position(stmt.End()).Line, true
		}
	}
	return 0, false
}

func isComparison(op token.Token) bool {
	switch op {
	case token.EQL, token.NEQ, token.LSS, token.LEQ, token.GTR, token.GEQ:
		return true
	}
	return false
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: shape <file.go>")
		os.Exit(2)
	}
	fset := token.NewFileSet()
	// Comments are not parsed: a comment naming a semaphore is not a bound, and
	// `unbounded_fanout` records that a text search for one was satisfied by
	// exactly that. Leaving them out of the tree is how this cannot repeat.
	file, err := parser.ParseFile(fset, os.Args[1], nil, 0)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	line := func(p token.Pos) int { return fset.Position(p).Line }

	out := shape{Package: file.Name.Name, Funcs: []ref{}, Calls: []call{},
		Refs: []ref{}, Spawns: []spawn{}, Assigns: []assign{},
		Imports: []ref{}, Errs: []errcheck{}, Blanks: []blank{},
		Binds: []bind{}, Lits: []lit{}, Checks: []check{},
		Strs: []str{}, DeadAfter: map[string]int{},
		Returns: []retstmt{}, Loops: []loop{}, Fields: []field{}}

	// Every name whose type is written down. A field list covers parameters,
	// receivers, results and struct fields alike -- Go spells all four the
	// same way -- and an unnamed one (`func f(*http.Client)`) binds nothing,
	// so it is skipped rather than recorded under an empty name.
	fields := func(list *ast.FieldList, fn string) {
		if list == nil {
			return
		}
		for _, f := range list.List {
			t := types.ExprString(f.Type)
			if t == "" {
				continue
			}
			for _, n := range f.Names {
				out.Binds = append(out.Binds,
					bind{Name: n.Name, Type: t, Fn: fn, Line: line(n.Pos())})
			}
		}
	}
	for _, decl := range file.Decls {
		gen, ok := decl.(*ast.GenDecl)
		if !ok {
			continue
		}
		for _, spec := range gen.Specs {
			switch v := spec.(type) {
			case *ast.TypeSpec:
				if st, ok := v.Type.(*ast.StructType); ok {
					fields(st.Fields, "")
				}
			case *ast.ValueSpec:
				if v.Type == nil {
					continue
				}
				t := types.ExprString(v.Type)
				for _, n := range v.Names {
					out.Binds = append(out.Binds,
						bind{Name: n.Name, Type: t, Fn: "", Line: line(n.Pos())})
				}
			}
		}
	}

	// Imports, for `layer-boundary`. The path with its quotes removed and the
	// line it is on: a violation a reader cannot find is one they will not fix,
	// which is the reason the Python half takes line numbers from the tree
	// rather than from a set of names.
	for _, imp := range file.Imports {
		if path, err := strconv.Unquote(imp.Path.Value); err == nil {
			alias := ""
			if imp.Name != nil {
				alias = imp.Name.Name
			}
			out.Imports = append(out.Imports,
				ref{Name: path, Line: line(imp.Pos()), Alias: alias})
		}
	}

	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Body == nil {
			continue
		}
		name := fn.Name.Name
		out.Funcs = append(out.Funcs, ref{Name: name, Line: line(fn.Pos()),
			End: line(fn.Body.End()), Stmts: len(fn.Body.List)})
		if line, dead := deadAfter(fn.Body, fset); dead {
			out.DeadAfter[name] = line
		}
		fields(fn.Recv, name)
		if fn.Type != nil {
			fields(fn.Type.Params, name)
		}

		// Loop depth, so a `go` statement inside a range is told apart from one
		// spawned once. A single `go worker()` is a worker, not a fan-out.
		depth := 0
		var walk func(ast.Node) bool
		walk = func(n ast.Node) bool {
			switch v := n.(type) {
			case *ast.ForStmt, *ast.RangeStmt:
				out.Loops = append(out.Loops, loop{Fn: name,
					Line: line(v.Pos()), End: line(v.End())})
				depth++
				for _, child := range children(v) {
					ast.Inspect(child, walk)
				}
				depth--
				return false
			case *ast.GoStmt:
				out.Spawns = append(out.Spawns,
					spawn{Line: line(v.Pos()), Fn: name, InLoop: depth > 0})
			case *ast.ReturnStmt:
				values := []string{}
				for _, r := range v.Results {
					values = append(values, types.ExprString(r))
				}
				out.Returns = append(out.Returns,
					retstmt{Fn: name, Line: line(v.Pos()), Values: values})
			case *ast.CompositeLit:
				for _, el := range v.Elts {
					kv, ok := el.(*ast.KeyValueExpr)
					if !ok {
						continue
					}
					key := ""
					if id, ok := kv.Key.(*ast.Ident); ok {
						key = id.Name
					} else if k, ok := literalOf(kv.Key); ok {
						key = k
					}
					// Unquoted for `Fields`: a map key is written
					// `"Idempotency-Key"` and the caller matches it against a
					// vocabulary of names, not of Go source text. `Lits` keeps
					// the quoted form, because there the key is part of a value
					// being compared across two revisions.
					bare := key
					if unq, err := strconv.Unquote(bare); err == nil {
						bare = unq
					}
					if bare != "" {
						out.Fields = append(out.Fields, field{Fn: name,
							Line: line(kv.Pos()), Key: bare})
					}
					if val, ok := literalOf(kv.Value); ok && key != "" {
						out.Lits = append(out.Lits, lit{Fn: name,
							Line: line(kv.Pos()), In: "field", Key: key,
							Value: val})
					}
				}
			case *ast.IfStmt:
				c := check{Fn: name, Line: line(v.Pos()), Lits: []string{},
					Calls: []string{}}
				ast.Inspect(v.Cond, func(x ast.Node) bool {
					if b, ok := x.(*ast.BinaryExpr); ok && isComparison(b.Op) {
						for _, side := range []ast.Expr{b.X, b.Y} {
							if val, ok := literalOf(side); ok {
								c.Lits = append(c.Lits, val)
							}
						}
					}
					return true
				})
				ast.Inspect(v.Body, func(x ast.Node) bool {
					if call, ok := x.(*ast.CallExpr); ok {
						if d := dotted(call.Fun); d != "" {
							c.Calls = append(c.Calls, d)
						}
					}
					return true
				})
				if len(c.Lits) > 0 {
					out.Checks = append(out.Checks, c)
				}
				if errName, ok := errNilTest(v.Cond); ok {
					e := errcheck{Line: line(v.Pos()), Fn: name, Name: errName,
						Stmts: len(v.Body.List), End: line(v.Body.End()),
						Calls: []string{}}
					ast.Inspect(v.Body, func(x ast.Node) bool {
						switch b := x.(type) {
						case *ast.FuncLit:
							// A `return` inside a closure returns from the
							// closure. The handler around it still falls
							// through, and a bypass fixture is exactly that
							// move -- the Python half stops at the same
							// boundary through `pysource.reachable_nodes`,
							// for the same reason and after the same fixture.
							return false
						case *ast.ReturnStmt:
							e.Returns = true
						case *ast.BranchStmt:
							e.Breaks = true
						case *ast.CallExpr:
							d := dotted(b.Fun)
							if d == "panic" {
								e.Panics = true
							}
							if d != "" {
								e.Calls = append(e.Calls, d)
							}
						}
						return true
					})
					out.Errs = append(out.Errs, e)
				}
			case *ast.DeclStmt:
				if gen, ok := v.Decl.(*ast.GenDecl); ok {
					for _, spec := range gen.Specs {
						vs, ok := spec.(*ast.ValueSpec)
						if !ok || vs.Type == nil {
							continue
						}
						t := types.ExprString(vs.Type)
						for _, n := range vs.Names {
							out.Binds = append(out.Binds, bind{Name: n.Name,
								Type: t, Fn: name, Line: line(n.Pos())})
						}
					}
				}
			case *ast.AssignStmt:
				targets, lits, names := []string{}, []string{}, []string{}
				// `c := &http.Client{}` names its own type on the right. A
				// composite literal is the only right-hand side that does;
				// everything else needs the type checker, which needs the
				// whole package, which is not what this reads.
				for i, l := range v.Lhs {
					id, ok := l.(*ast.Ident)
					if !ok || i >= len(v.Rhs) {
						continue
					}
					r := v.Rhs[i]
					if u, ok := r.(*ast.UnaryExpr); ok && u.Op == token.AND {
						r = u.X
					}
					if lit, ok := r.(*ast.CompositeLit); ok && lit.Type != nil {
						out.Binds = append(out.Binds, bind{Name: id.Name,
							Type: types.ExprString(lit.Type), Fn: name,
							Line: line(id.Pos())})
					}
				}
				for _, l := range v.Lhs {
					if id, ok := l.(*ast.Ident); ok {
						targets = append(targets, id.Name)
					}
				}
				for _, r := range v.Rhs {
					lits = append(lits, strLits(r)...)
					names = append(names, idents(r)...)
				}
				if len(targets) > 0 {
					out.Assigns = append(out.Assigns, assign{
						Fn: name, Line: line(v.Pos()), Targets: targets,
						Strings: lits, Names: names})
				}
				// `_ = err`, and only when the right-hand side is a plain name
				// that looks like an error. `_ = someBuffer` throws away a
				// value nobody was going to check.
				for i, l := range v.Lhs {
					id, ok := l.(*ast.Ident)
					if !ok || id.Name != "_" || i >= len(v.Rhs) {
						continue
					}
					if r, ok := v.Rhs[i].(*ast.Ident); ok && looksLikeErr(r.Name) {
						out.Blanks = append(out.Blanks,
							blank{Line: line(v.Pos()), Fn: name, Name: r.Name})
					}
				}
			case *ast.CallExpr:
				d := dotted(v.Fun)
				if d != "" {
					args, argNames := []string{}, []string{}
					for _, a := range v.Args {
						args = append(args, strLits(a)...)
						argNames = append(argNames, idents(a)...)
					}
					out.Calls = append(out.Calls,
						call{Name: d, Line: line(v.Pos()), Fn: name,
							Strings: args, Argc: len(v.Args), Names: argNames})
					for i, arg := range v.Args {
						if val, ok := literalOf(arg); ok {
							out.Lits = append(out.Lits, lit{Fn: name,
								Line: line(v.Pos()), In: "arg", Call: d,
								Idx: i, Value: val})
						}
					}
				}
			case *ast.SelectorExpr:
				if d := dotted(v); d != "" {
					out.Refs = append(out.Refs, ref{Name: d, Line: line(v.Pos()), Fn: name})
				}
			case *ast.Ident:
				out.Refs = append(out.Refs, ref{Name: v.Name, Line: line(v.Pos()), Fn: name})
			}
			return true
		}
		ast.Inspect(fn.Body, walk)
	}

	// Strings, with the outermost named function they are written in -- which
	// is what `_enclosing` resolves to on the Python side, where a nested
	// definition loses to the one that contains it.
	collect := func(n ast.Node, fn string) {
		if n == nil {
			return
		}
		var walk func(ast.Node) bool
		walk = func(x ast.Node) bool {
			if x == nil {
				return false
			}
			if v, ok := foldStr(exprOf(x)); ok {
				out.Strs = append(out.Strs,
					str{Fn: fn, Line: line(x.Pos()), Value: v})
				return false
			}
			return true
		}
		ast.Inspect(n, walk)
	}
	for _, decl := range file.Decls {
		if fn, ok := decl.(*ast.FuncDecl); ok {
			collect(fn.Body, fn.Name.Name)
			continue
		}
		if gen, ok := decl.(*ast.GenDecl); ok && gen.Tok == token.IMPORT {
			// An import path is a string and can never be a credential.
			// `Imports` above already carries it for `layer-boundary`.
			continue
		}
		collect(decl, "")
	}

	b, err := json.Marshal(out)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println(string(b))
}

// children returns the sub-nodes of a loop, so the walker can descend with the
// depth counter raised.
func children(n ast.Node) []ast.Node {
	switch v := n.(type) {
	case *ast.ForStmt:
		out := []ast.Node{}
		for _, c := range []ast.Node{v.Init, v.Cond, v.Post, v.Body} {
			if c != nil && !isNil(c) {
				out = append(out, c)
			}
		}
		return out
	case *ast.RangeStmt:
		out := []ast.Node{}
		for _, c := range []ast.Node{v.Key, v.Value, v.X, v.Body} {
			if c != nil && !isNil(c) {
				out = append(out, c)
			}
		}
		return out
	}
	return nil
}

// errNilTest recognises `err != nil` and `err == nil` in either order, and
// returns the name being tested. Only a plain identifier: `x.Err != nil` is a
// field and the rule below is about the value a call just returned.
func errNilTest(cond ast.Expr) (string, bool) {
	bin, ok := cond.(*ast.BinaryExpr)
	if !ok || bin.Op != token.NEQ {
		return "", false
	}
	for _, pair := range [][2]ast.Expr{{bin.X, bin.Y}, {bin.Y, bin.X}} {
		id, ok := pair[0].(*ast.Ident)
		if !ok || !looksLikeErr(id.Name) {
			continue
		}
		if nilID, ok := pair[1].(*ast.Ident); ok && nilID.Name == "nil" {
			return id.Name, true
		}
	}
	return "", false
}

// looksLikeErr is a name convention and nothing more, which is the honest
// description: without type information there is no way to know a value is an
// `error`, and Go's convention is strong enough that a rule built on it is
// worth having. `err`, `err2`, `readErr`, `Err`.
func looksLikeErr(name string) bool {
	l := strings.ToLower(name)
	return l == "err" || strings.HasSuffix(l, "err") ||
		strings.HasPrefix(l, "err")
}

func isNil(n ast.Node) bool {
	switch v := n.(type) {
	case ast.Expr:
		return v == nil
	case ast.Stmt:
		return v == nil
	}
	return n == nil
}
