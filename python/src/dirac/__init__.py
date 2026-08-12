"""Dirac — molecular fields, addressable and typed, from Python.

    from dirac import DiracClient
    c = DiracClient()
    r = c.field('homo', molfile=open('ligand.mol').read(), basis='def2-svp')
    print(r.homo_ev, r.version, r.artifact('field.cube')['sha256'])
    r.save('homo.cube')

The SDK is the layer the audit says must exist BEFORE the CLI, and the reason is
structural rather than stylistic: `MCP → spawn CLI → parse stdout` puts a text
serialisation where a function call belongs, and every consumer then reimplements
parsing. Here, the CLI and an MCP adapter are both thin shells over DiracClient, and
DiracClient is a thin shell over a transport — so all three get identical semantics and
only the LOCATION of the work varies.

Nothing in this package validates parameters, classifies refusals or decides whether an
artifact travels inline. Those belong to the kernel, and an SDK that re-decided them
would drift from the descriptors within a week — at which point the SDK and the server
would disagree about what is legal, and the SDK would be wrong.
"""
from .client import DiracClient, Result
from .errors import CODES, DiracDigestMismatch, DiracError, exception_for
from .transport import HttpTransport, LocalTransport

__version__ = '0.1.0'
__all__ = ['DiracClient', 'Result', 'DiracError', 'DiracDigestMismatch',
           'exception_for', 'CODES', 'LocalTransport', 'HttpTransport']

# Every declared error code also gets a class, generated in errors.py from
# contracts/errors.json. Re-exported so `except dirac.DiracUnsupported` works without
# reaching into a submodule — and generated rather than listed, because a hand-written
# list here would be a second home for the vocabulary.
from . import errors as _errors                                     # noqa: E402

for _name in dir(_errors):
    if _name.startswith('Dirac') and _name not in __all__:
        globals()[_name] = getattr(_errors, _name)
        __all__.append(_name)
