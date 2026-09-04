"""A module a design document points at."""

SCHEME = "v4-chain-3"
GENESIS = "genesis"


class RulerMoved(RuntimeError):
    pass


def _row_hash(prev, row):
    return prev + str(row)


def run_checker(claim):
    return 0


def assert_ruler_unmoved(conn, path):
    return None


def verify(case):
    return True


def worktree_digest(root):
    # this_is_only_in_a_comment is named here and defined nowhere
    return "d"
