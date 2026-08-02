# AGPL licensing posture for PyMuPDF/Ghostscript — 2026-08-02

Research for [GitHub issue #4](https://github.com/SashaGordin/pdf-sanitizer/issues/4)
("AGPL licensing posture for PyMuPDF/Ghostscript under the local-CLI-only
deployment model"), itself resolving PRODUCTION-READINESS-PLAN.md's "Open
decisions blocking benchmark qualification" item 2.

**This is research to inform a legal-review conversation, not legal advice.**
Nothing here is a legal determination; qualified counsel should confirm any
conclusion before it's relied on for a launch decision.

## The deployment this is evaluated against

Per `docs/adr/0001-local-cli-only-deployment.md`: a local CLI tool, run by a
single operator, on their own machine, with no remote/hosted access and no
distribution of the tool to any other party. That's the fact pattern every
finding below is anchored to — it changes if the deployment model changes.

## What actually triggers AGPL obligations

Two independent triggers exist, one inherited from ordinary GPL and one
unique to AGPL. Both require an affirmative act; neither fires on private,
non-networked use by design.

**1. Conveying (ordinary GPL, inherited by AGPL).** The AGPL's own Section 0
definitions ([gnu.org/licenses/agpl-3.0.html](https://www.gnu.org/licenses/agpl-3.0.html)):

> **Propagate:** "To do anything with it that, without permission, would make
> you directly or secondarily liable for infringement under applicable
> copyright law, except executing it on a computer or modifying a private
> copy."
>
> **Convey:** "Any kind of propagation that enables other parties to make or
> receive copies. Mere interaction with a user through a computer network,
> with no transfer of a copy, is not conveying."

Running the program, or modifying your own copy of it, is explicitly carved
out of "propagate" — so it can never be "conveying" either. The GNU GPL FAQ
confirms this isn't a loophole but the intended reading
([gnu.org/licenses/gpl-faq.html](https://www.gnu.org/licenses/gpl-faq.html)):

> "You are free to make modifications and use them privately, without ever
> releasing them." And on multiple copies inside one organization: "the
> organization is just making the copies for itself... when the organization
> transfers copies to other organizations or individuals, that is
> distribution."

So: no obligation exists until a copy is actually handed to a different
organization or person.

**2. Remote network interaction (AGPL-specific, Section 13).** AGPL adds one
narrow obligation GPL doesn't have, and it does *not* depend on conveying at
all:

> "if you modify the Program, your modified version must prominently offer
> all users interacting with it remotely through a computer network (if your
> version supports such interaction) an opportunity to receive the
> Corresponding Source of your version..."

This is why AGPL exists: it closes the "SaaS loophole" where a modified
program never gets conveyed to anyone (only its output is seen over the
network), so ordinary GPL's conveying-based trigger would never fire. Artifex
(PyMuPDF and Ghostscript's own commercial licensor) states this plainly on
its licensing page ([artifex.com/licensing](https://artifex.com/licensing)):
a commercial license becomes necessary once you "deploy \[Artifex] open-source
as part of a server-based application or service, without disclosing your
own application's full source code."

## Applying this to the current deployment

Under `docs/adr/0001-local-cli-only-deployment.md`'s model, as it stands
today:

- **No conveying occurs.** The tool isn't given to another firm, a client, a
  contractor, or any party outside its current single-operator use. Trigger 1
  is not met.
- **No remote network interaction occurs.** Nobody — not even a colleague at
  the same firm — interacts with the tool "remotely through a computer
  network." It's invoked locally by the one operator. Trigger 2 is not met.

**Neither PyMuPDF's nor Ghostscript's AGPL terms currently require any source
offer or disclosure for this tool.** This holds regardless of how deeply the
tool's own code (`tools/anonymize_construction_pdfs.py`) integrates with
PyMuPDF's Python bindings or shells out to the Ghostscript binary — Section 0
exempts execution and private modification categorically, before any question
of "how the two programs are combined" even arises.

## What would change this

Either trigger, independently, activates AGPL obligations:

- **Distributing the tool** (or a build of it) to another firm, a client, a
  contractor, or anyone outside the current usage — this is conveying, which
  under ordinary GPL Section 6 terms requires accompanying the executable
  with the Corresponding Source (or a written offer for it).
- **Running it as any kind of network-accessible service** — even an
  internal one where only colleagues connect to it remotely rather than
  running it themselves locally — activates AGPL Section 13 regardless of
  whether any copy is ever conveyed to those users.

If either happens, the practical choices become the same two Artifex already
frames on its licensing page: (a) comply with AGPL — offer the complete
Corresponding Source of the exact deployed version, which would extend to
this project's own wrapper code as a combined/derivative work, or (b)
purchase a commercial license from Artifex, the exclusive commercial
licensor for both PyMuPDF/MuPDF
([pymupdf.readthedocs.io/en/latest/about.html](https://pymupdf.readthedocs.io/en/latest/about.html))
and Ghostscript
([ghostscript.com/licensing](https://ghostscript.com/licensing/)).

## One nuance this research does not resolve

Whether invoking Ghostscript as an external, unmodified subprocess (as this
tool does today) sits closer to GPL's "mere aggregation" of independent
programs than statically/dynamically linking a modified library (as PyMuPDF's
Python bindings arguably do) is a real, unsettled distinction with
consequences for *how much* of this tool's own source would need disclosing
if a trigger were ever met. That line is genuinely disputed in FOSS licensing
practice and is exactly the kind of question qualified legal counsel should
answer before any deployment-model change (distribution, or any networked/
multi-user access) is made — not something this research settles on its own.

## Sources

- [GNU AGPLv3 full text](https://www.gnu.org/licenses/agpl-3.0.html) — Section 0 (propagate/convey definitions), Section 13 (remote network interaction)
- [GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html) — private use, distribution vs. internal copying, AGPL network-service Q&A
- [Artifex Licensing](https://artifex.com/licensing) — official commercial-license triggers for Artifex-owned AGPL software
- [Ghostscript Licensing](https://ghostscript.com/licensing/) — Ghostscript-specific licensing page (redirects to Artifex)
- [PyMuPDF/MuPDF licensing (docs)](https://pymupdf.readthedocs.io/en/latest/about.html) — dual AGPL/commercial licensing statement
