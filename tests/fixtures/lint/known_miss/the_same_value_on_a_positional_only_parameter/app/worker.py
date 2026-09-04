def run(retry_backoff_seconds=37.5, /, *, attempts):
    # RED: the same duplication as the sibling case, on a positional-only
    # parameter. `_restated` sliced `args.args`, and `/` puts the parameter in
    # `args.posonlyargs` -- so `args.args` was empty, the default paired with
    # no name at all, and the rule saw nothing. The language puts defaults on
    # the tail of the combined list.
    return retry_backoff_seconds, attempts
