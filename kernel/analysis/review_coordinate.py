"""Verified JS declaration coordinates; no arbitrary symbol/file override."""
import re
import hashlib

from .symbols import ts_mask, TS_SUFFIXES


def declaration(source, symbol):
    """A unique simple top-level value initializer, or None when not supported.

    Callable declarations retain function-entry proof. This intentionally
    refuses destructuring, multiple declarators, ASI and nested/ambiguous names.
    Offsets for V8 are UTF16 code units, not Python string indices.
    """
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z_$][\w$]*", symbol):
        return None
    mask = ts_mask(source)
    declarations = list(re.finditer(
        r"\b(?:const|let|var)\s+" + re.escape(symbol) + r"\s*(?::[^=;\n]+)?=(?!=|>)", mask))
    if len(declarations) != 1:
        return None
    match = declarations[0]
    prefix = mask[:match.start()]
    if prefix.count('{') != prefix.count('}'):
        return None
    start = match.end()
    while start < len(mask) and source[start].isspace():
        start += 1
    depth, end = [], None
    for i in range(start, len(mask)):
        ch = mask[i]
        if ch in '([{':
            depth.append(ch)
        elif ch in ')]}':
            if not depth or '([{'.index(depth.pop()) != ')]}'.index(ch):
                return None
        elif ch == ';' and not depth:
            end = i
            break
        elif ch == ',' and not depth:
            return None
    if end is None or not source[start:end].strip():
        return None
    expression = mask[start:end]
    if '{' in expression or '=>' in expression or re.search(r'\b(?:function|class)\b', expression):
        return None
    # A second callable with this name would make the original coordinate
    # ambiguous, even if the value declaration itself was found only once.
    if re.search(r'\b(?:function|class)\s+' + re.escape(symbol) + r'\b', mask):
        return None
    return {'kind':'declaration', 'symbol':symbol,
            'source_sha256':hashlib.sha256(source.encode('utf-8')).hexdigest(),
            'line':source.count('\n', 0, start)+1,
            'statement_line':source.count('\n',0,match.start()),
            'statement_column':len(source[source.rfind('\n',0,match.start())+1:match.start()].encode('utf-16-le'))//2,
            'end_line':source.count('\n',0,end),
            'end_column':len(source[source.rfind('\n',0,end)+1:end].encode('utf-16-le'))//2,
            'offset':len(source[:start].encode('utf-16-le'))//2,
            'end_offset':len(source[:end].encode('utf-16-le'))//2}
