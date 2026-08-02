# Issue tracker: Local Markdown + GitHub Issues (wayfinding)

Issues and specs (you may know a spec as a PRD) for this repo live as markdown files in `.scratch/`. As of the `pdf-sanitizer` GitHub repo's creation, `/wayfinder` maps and tickets live on GitHub Issues instead — see "Wayfinding operations" below. Everything else (feature specs, non-wayfinder implementation tickets) stays on the local-markdown convention described in this file unless a later decision migrates it too.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` — never a single combined tickets file
- Triage state is recorded as a `Status:` line near the top of each issue file (see `triage-labels.md` for the role strings)
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Wayfinding operations (GitHub Issues)

Used by `/wayfinder` against the `SashaGordin/pdf-sanitizer` GitHub repo. The
**map** is an issue; its tickets are child issues linked as native GitHub
sub-issues (not a separate convention file).

- **Map**: a GitHub issue labelled `wayfinder:map`. Body holds the Destination
  / Notes / Decisions-so-far / Not-yet-specified / Out-of-scope sections
  verbatim per the skill's map-body template.
- **Child ticket**: a GitHub issue in the same repo, labelled `wayfinder:<type>`
  (`wayfinder:research` / `wayfinder:prototype` / `wayfinder:grilling` /
  `wayfinder:task`), linked to the map as a **native sub-issue** — GitHub's
  real parent/child relationship (`Issue.parent` / `Issue.subIssues` in the
  GraphQL schema), not a markdown link. Body holds the `## Question`. Set it
  via the `addSubIssue` GraphQL mutation:
  ```
  gh api graphql -f query='mutation { addSubIssue(input: {issueId: "<map node id>", subIssueUrl: "<ticket URL>"}) { subIssue { number } } }'
  ```
  (`gh issue view <map#> --json id --jq .id` gives the map's node id;
  `subIssueUrl` accepts the ticket's plain `https://github.com/.../issues/N` URL.)
- **Blocking**: GitHub's **native issue-dependency** relationship
  (`Issue.blockedBy` / `Issue.blocking` in the GraphQL schema — the same
  mechanism behind the "blocked by" UI in the issue sidebar). Confirmed
  present via schema introspection on this repo; `gh` has no dedicated
  subcommand for it yet, so set it with `gh api graphql`:
  ```
  gh api graphql -f query='mutation { addBlockedBy(input: {issueId: "<ticket node id>", blockingIssueId: "<blocker node id>"}) { issue { number } } }'
  ```
  Node ids come from `gh api repos/SashaGordin/pdf-sanitizer/issues/<N> --jq .node_id`.
  A ticket is **unblocked** when every issue in its `blockedBy` connection is
  closed — check via:
  ```
  gh api graphql -f query='{ repository(owner:"SashaGordin", name:"pdf-sanitizer") { issue(number: <N>) { blockedBy(first: 10) { nodes { number state } } } } }'
  ```
- **Frontier**: open, unassigned child issues of the map whose `blockedBy`
  nodes are all `state: CLOSED`. In practice: `gh issue list --label wayfinder:<type> --state open` filtered to unassigned, cross-checked against the `blockedBy` query above (no single `gh` flag does this filter yet).
- **Claim**: `gh issue edit <N> --add-assignee @me` before any work.
- **Resolve**: `gh issue comment <N> --body "## Answer\n\n<the answer>"`, then
  `gh issue close <N>`, then append a context pointer (gist + issue link) to
  the map issue's Decisions-so-far — edit the map issue body with
  `gh issue edit <map#> --body-file -`.
