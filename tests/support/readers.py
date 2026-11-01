"""Observe actual LMDB read transactions in isolated persistence tests."""


def active_readers(environment):
    """Return active reader rows, excluding reset/cached slots marked with '-'."""
    return [
        line
        for line in environment.readers().splitlines()[1:]
        if line.split()[-1] != "-"
    ]
