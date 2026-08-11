"""Backend test package.

Deliberately empty of logic. `backend/field_server.py` is imported by the tests
via an explicit `sys.path` insert of the *backend* directory, not via this
package — so a test file stays runnable directly:

    backend/env/bin/python backend/tests/test_physics_contracts.py

pytest is NOT installed in `backend/env` (verified 2026-08-11). Every test file
here must therefore carry its own `main()` runner. A gate that depends on a
package which is not present is not a gate.
"""
