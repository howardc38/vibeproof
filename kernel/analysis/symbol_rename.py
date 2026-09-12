"""Prove a unique Python function rename from two source snapshots."""
import ast
import copy


def definitions(source):
    result = []

    def visit(node, scope=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.append((node.name, scope, node))
        nested = scope + (node.name,) if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else scope
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    visit(ast.parse(source))
    return result


def unique(source, symbol):
    found = [entry for entry in definitions(source) if entry[0] == symbol]
    if len(found) != 1:
        raise ValueError(f"expected one function named {symbol!r}, found {len(found)}")
    return found[0]


def _shape(node):
    node = copy.deepcopy(node)
    node.name = "__renamed_function__"
    return ast.dump(node, include_attributes=False)


def prove(before, after, symbol):
    """(new name, enclosing scope), with unchanged signature/decorators/body."""
    _, scope, original = unique(before, symbol)
    old_defs, new_defs = definitions(before), definitions(after)
    if any(name == symbol for name, _, _ in new_defs):
        raise ValueError(f"{symbol!r} still exists after the proposed rename")
    candidates = [(name, where) for name, where, node in new_defs
                  if where == scope and _shape(node) == _shape(original)
                  and not any(old_name == name for old_name, _, _ in old_defs)]
    if len(candidates) != 1:
        raise ValueError("no unique rename with unchanged scope, signature, decorators and body")
    name, where = candidates[0]
    unique(after, name)  # The execution tracer matches a bare name within one file.
    return name, where
