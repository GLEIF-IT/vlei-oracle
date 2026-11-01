"""Drive actual KERIpy OOBI HTTP resolution for peer fixtures."""

import falcon
from hio.base import doing
from hio.core import http
from keri.app import httping, oobiing
from keri.db import basing
from keri.end import ending
from keri.help import helping


def resolve_peer(hby, url, source, port, *, resolver=None):
    """Serve the source's real OOBI and wait for the native resolution outcome.

    A caller may borrow its runtime resolver. The expected-AID authorization
    check remains with the receiver; this helper reports only native completion.
    """
    app = falcon.App()
    app.add_route("/oobi/{aid}", ending.OOBIEnd(hby=source))
    server = http.Server(host="127.0.0.1", port=port, app=app)
    resolver = resolver or oobiing.Oobiery(hby=hby, clienter=httping.Clienter())
    hby.db.roobi.rem(keys=(url,))
    hby.db.oobis.pin(keys=(url,), val=basing.OobiRecord(date=helping.nowIso8601()))

    def wait(tymth, tock=0.0, **opts):
        yield tock
        while hby.db.roobi.get(keys=(url,)) is None:
            resolver.parser.kvy.processEscrows()
            yield 0.01
        parent.remove([server_doer, *resolver.doers])

    server_doer = http.ServerDoer(server=server)
    parent = doing.DoDoer(doers=[server_doer, *resolver.doers, doing.doify(wait)])
    doing.Doist(real=True, tock=0.01, limit=3).do(doers=[parent])
    assert parent.done, "peer OOBI resolution did not complete"
    return hby.db.roobi.get(keys=(url,))
