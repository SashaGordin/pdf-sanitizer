---
status: accepted
---

# Local CLI only, not a network service

The sanitizer runs as a local CLI invoked directly by the operator against
files on their own machine. It is not deployed as a server and is not offered
to remote users or other firms. Chosen to keep the AGPL analysis simple
(PyMuPDF's and Ghostscript's "remote network interaction" clauses do not
trigger for a tool nobody else calls over a network) and to avoid building
speculative multi-tenant worker isolation before there is a real threat model
that needs it.

Revisit this if the tool is ever offered as a service that other people or
organizations submit files to — that changes both the licensing posture and
the security requirements (Phase 5 worker isolation, in particular, is scoped
assuming this ADR holds).
