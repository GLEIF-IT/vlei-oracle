"""Advisory startup warnings; operators own compatibility with retained state."""

import logging

logger = logging.getLogger(__name__)


def note_inbox(store, scope):
    """Warn on changed producer/chain settings and save the latest inbox baseline."""
    previous = store.inbox()
    if previous is not None and previous != scope:
        logger.warning(
            "Oracle inbox settings changed: previous=%s current=%s. "
            "Continuing with retained state; store compatibility is the operator's responsibility.",
            previous,
            scope,
        )
    store.save_inbox(scope)


def note_posting(store, scope):
    """Warn on changed deployment/writer settings without altering posting work."""
    previous = store.posting()
    if previous is not None and previous != scope:
        logger.warning(
            "Oracle posting settings changed: previous=%s current=%s. "
            "Continuing with retained state; store compatibility is the operator's responsibility.",
            previous,
            scope,
        )
    store.save_posting(scope)
