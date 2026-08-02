# Research: publicly-available construction doc sourcing for the locked corpus (issue #10)

**Ticket:** [#10 — Survey publicly-available construction spec/design documents beyond MLK for
locked-corpus sourcing](https://github.com/SashaGordin/pdf-sanitizer/issues/10)
**Deferred by:** [#5 — Define locked generalization corpus composition and labeling schema (Phase 3)](https://github.com/SashaGordin/pdf-sanitizer/issues/5)
**Dimension table referenced throughout:** `PRODUCTION-READINESS-PLAN.md`, "Phase 3 — Build a
generalization corpus."
**Public-availability bar used:** same logic as `tests/golden/mlk_labels.json`'s `_about` block —
public *because the underlying project is a public agency's project*, published as part of a
public bid/procurement process, not because the document lacks real party-identifying content.

This is a research memo, not a sourcing decision. Nothing here has been reviewed for licensing by
counsel, and no documents have been downloaded into the repo or added to any corpus.

## Summary

There is a real, non-trivial supply of public-agency construction bid packages (state agencies,
counties, transit authorities, school districts) that match MLK's public-availability profile and
that run to hundreds of pages of genuine specifications plus separate drawing sets with real
project names, addresses, agency/firm names, and named individuals — two candidates below were
directly downloaded and verified at 368-599 pages each. Volume looks sufficient to build a real
locked corpus from procurement portals alone, provided someone commits to per-project manual
vetting (checking freshness of the bid, confirming no login/paywall, and confirming the docs are
still hosted after award). The biggest structural gaps that public procurement sourcing will
probably **not** close, and that likely need synthetic fill-in regardless: **encrypted PDFs**
(bid docs are essentially never encrypted — they must be freely downloadable to bidders),
**malformed PDFs** (official portal output is by construction clean), and **non-English /
unsupported-language documents** (every source found here is English-language, US public-agency
procurement; no cheap public non-English construction spec/drawing corpus was identified). Scanned,
skewed, low-contrast, and handwritten-annotation cases are also unevenly distributed — modern
digital-native spec books (DGS, Miami-Dade) won't naturally produce them; older DOT as-let plan
archives and historic-building survey collections are better bets for that cell but come with
their own caveats (paywalls, age, weaker coverage of contemporary sensitive-content types).

## Candidates

| Source | What it is / why public | Scale (verified where noted) | Dimension-table coverage (plausible) | Likely gaps | Link |
|---|---|---|---|---|---|
| **California Dept. of General Services (DGS), Office of Business and Acquisition Services (OBAS) — Bid Opportunities portal** | State-agency capital projects (demolition, renovation, new construction) posted for public bid. Each listing carries a Project Manual (specs) and a separate Plans (drawings) PDF, no login. **Verified directly**: project 25-277693 ("RESD Street Demolition," Dept. of Housing & Community Development, 345 Ash St / 1301 State St, San Diego) — Project Manual Book I of III = 368 pages (fetched, `pdfinfo` confirmed), named client agency, real addresses, a named project director. Companion `Plans.pdf` = 34 pages of CAD-authored drawings (this was a demolition-scope project; new-construction listings on the same portal should run much larger). ~19 active listings at time of check. | Verified: 368 pp (specs, Book I only, 3 books total) + 34 pp (drawings), one project. Portal has ~19 concurrent live listings, each multi-document. | PDF construction (searchable, likely layered/CAD-native), layout (title blocks, tables, forms, stamps possible), sensitive content (people, firms, projects, addresses — all real), negative content (manufacturers/standards/schedules — typical CSI-format specs) | Scanned/skew/low-contrast (unlikely — born-digital), encrypted/malformed (no), non-English (no) | [dgs.ca.gov/OBAS/Bid-Opportunities](https://www.dgs.ca.gov/OBAS/Bid-Opportunities) |
| **Miami-Dade County Dept. of Transportation & Public Works (DTPW) — procurement bid document portal** | County-agency construction bid packages, published for public procurement, no login. **Verified directly**: "USS Equipment Replacement – Phase 1," Project No. IRP151, RPQ TP-0000008861 — full Contract Specifications PDF = 599 pages (fetched, `pdfinfo` confirmed), includes invitation to bid, bonding/affidavit forms, general/special conditions, and technical specifications. | Verified: 599 pp for one contract's specifications; separate addenda/plan-set PDFs also posted per project. | Layout (forms, tables, boilerplate-heavy contract sections), sensitive content (named county department, contract/project numbers, likely named officials in affidavits/signatures), negative content (technical schedules, standards, manufacturers in tech specs sections) | Same as above — clean digital PDFs, unlikely source for scanned/degraded-image cells or non-English | [miamidade.gov procurement PDF listing](https://www.miamidade.gov/Apps/ISD/StratProc/ProcurementNAS/pdf_Files/TP0000008861MCC7040/Final_Bid_Documents_compressed.pdf) |
| **Public K-12 school district bid postings** (e.g. Lakeside Union SD, Palm Springs USD, Jurupa USD — CA) | Individual districts post full bid packages (contract docs + technical specs + drawings) directly on district websites or via portals like SmartBidNet, generally viewable without login for the public-facing PDF link. Same public-procurement logic as MLK. **Partially verified**: Lakeside Union SD "Lemon Crest ESS Relocatable" bid PDF = 115 pages, real district/school/project names — but this file turned out to be the front-end contract/general-conditions document only (no technical specs or drawings included in that particular PDF); the technical specs and drawing sheets for that project would be separate files on the district's bid page, not checked in this pass. | Verified 115 pp for one contract-admin document; full spec+drawing sets for the same or other district projects not yet located/verified. | Sensitive content (district, school, board members, addresses), layout (forms), negative content (boilerplate) confirmed for the piece verified; drawings/tech-specs coverage unconfirmed | Needs a second pass per district to find the actual technical-spec and drawing-sheet PDFs (this survey only reached the legal front matter); volume/scale unverified beyond one 115-page document | [Lakeside Union SD bid PDF](https://www.lsusd.net/wp-content/uploads/2026/03/LUSD_Lemon-Crest-ESS-Bldg_BID-DOC.pdf) (example only) |
| **State DOT "as-let" and standard-plan archives** (WSDOT, ODOT, MDOT, ALDOT, Missouri DOT via Internet Archive, SCDOT "Falcon") | Standard drawings and (for some states) as-let project construction plans, published by state transportation agencies. Missouri's *Standard Plans for Highway Construction* is mirrored on the Internet Archive as a free download. | Missouri standard-plans PDF: full statewide standard-details book (not project-specific). SCDOT "Falcon" as-let plans: **requires a paid $60/year subscription** — verified via fetch of the portal's own description — so not freely public despite being a government system. | Layout (title blocks, tables, stamps, multi-sheet drawings), negative content (standards/manufacturers/model numbers — this is literally what standard plans contain), PDF construction (scanned — many DOT as-let archives are digitized paper) | **Weak on sensitive content** — standard-plan books are generic details, not tied to a real project/party/address, so they mostly help the "negative content" and layout/structural cells, not the party-identifying cells the corpus most needs. SCDOT-style paywalled as-let archives should not be used without confirming reuse terms despite being government-run. | [Missouri Standard Plans (Internet Archive)](https://archive.org/details/2022JulHwyStdPlans); [SCDOT Plans Online](https://falcon.scdot.org/falconwebv4/default.aspx) (paywalled — verification needed) |
| **HABS/HAER/HALS Collection, Library of Congress** | Historic American Buildings Survey / Historic American Engineering Record — measured drawings, large-format photographs, and written histories of historic structures, many of them public buildings, produced under National Park Service / Library of Congress agreements since 1933. Confirmed public-domain, no restriction on reuse. | ~40,000 structures documented; individual building folders vary from a handful of sheets to dozens of measured drawings plus photograph sets. Not a single "spec book" — no CSI-style specifications, just drawings/photos/reports. | **Image quality** (photographed pages, many are scans of large-format photo prints and hand-drafted drawings — good source for skew, low contrast, noise, handwriting), PDF construction (scanned) | **No technical specifications at all** (this only covers the "drawings" side of the dimension table, and even then as measured/documentation drawings rather than construction bid drawings). Weak on modern sensitive-content categories — most subjects and structures are decades to a century old, so real contact data / account IDs / paths won't appear the way they do in a contemporary bid package. Also generally not a "construction spec/design document set" in the procurement sense the ticket asks about — flagging as a possible *image-quality* supplement, not a corpus backbone. | [LOC HABS/HAER/HALS Collection](https://www.loc.gov/pictures/item/2009632512/) |
| **Transit-authority procurement portals** (WMATA, NYC MTA Construction & Development, Maryland MTA) | Public transit agencies post capital-project solicitations, generally through agency procurement pages, no login mentioned for solicitation notices. | Not verified this pass — search surfaced live procurement/solicitation pages but no specific full spec+drawing PDF was downloaded and measured. | Plausibly similar to county/state agency packages (real agency/project/address names, technical specs, drawings) | **Unconfirmed** — needs someone to actually open a live solicitation and check page counts/doc types before relying on it | [WMATA procurement](https://www.wmata.com/business/procurement/solicitations/index.cfm); [MTA Construction & Development](https://en.wikipedia.org/wiki/MTA_Construction_and_Development_Company) (start point only) |
| **Federal GSA / SAM.gov construction solicitations** | GSA Public Buildings Service design/construction contracts are advertised on SAM.gov. Solicitation notices themselves are searchable by anyone without an account. | Not verified this pass. | Would plausibly cover federal-agency party info, contact data (contracting officer names/emails are typically in these packages), stamps | **Actual drawings/specs for many federal solicitations require SAM.gov registration or a separate secure document request** (confirmed via search: "you'll need to register on SAM.gov to obtain access to drawings and specifications," and some are marked for controlled/classified distribution) — likely gated beyond the notice itself; needs per-solicitation verification, and some may not be freely redistributable even if viewable. | [sam.gov/opportunities](https://sam.gov/opportunities) |
| **PlanetBids-hosted municipal/county portals generally** (many CA cities/counties) | Common vendor-portal software used by public agencies; PlanetBids' own documentation states the public does not need to register to view certain public bid information/documents, but whether full plan/spec downloads are gated is configured per agency. | Not systematically surveyed — generic pattern, not a specific verified project. | Same profile as other municipal sources when configured for public access | **Access is agency-configured** — some cities require vendor registration even to download documents; must be checked per agency/project rather than assumed public. | [planetbids.com](https://www.planetbids.com/) |

## Verification needed

- **SCDOT "Falcon" as-let plans archive** — explicitly paywalled ($60/year subscription per the
  portal's own terms). A government-run system charging a subscription fee is a materially
  different reuse posture than MLK's fully-open public bid posting; do not treat as pre-cleared
  even though it's a `.gov`-adjacent state system.
- **SAM.gov / GSA federal solicitations** — the solicitation notice is public, but actual drawing
  and specification packages for many listings require SAM.gov account registration or a separate
  controlled-document request. Needs a named example checked end-to-end (can a document actually
  be downloaded and redistributed, or only viewed by a registered, verified vendor) before counting
  it as a source.
- **Transit-authority portals (WMATA, MTA, Maryland MTA)** — only the existence of public
  procurement pages was confirmed this pass; no specific document was downloaded, measured, or
  checked for login requirements. Treat as an unverified lead, not a confirmed source.
- **PlanetBids/BidNet-hosted city and county portals in general** — public-viewability is a
  per-agency configuration choice in the underlying software, not a platform-wide guarantee.
  Any specific portal must be checked individually (open the document link in a private/incognito
  browser session with no prior login) before being relied on.
- **School district bid postings beyond the one file checked** — the single Lakeside Union SD
  file downloaded turned out to be the contract front-matter only, not the technical specs or
  drawing sheets. Whether the same public-posting pattern extends to the actual technical
  specification and drawing-sheet PDFs (as opposed to just the legal/bidding-procedure front end)
  was not confirmed and needs a follow-up pass per district.
- **HABS/HAER material's licensing is confirmed public-domain**, but its fitness as "construction
  spec/design documents" in the sense the ticket and Phase 3 dimension table mean (bid-ready specs
  and drawings) is questionable — it's historical/documentation drawings, not procurement
  packages. Flagging so it isn't miscounted as filling the same role as the DGS/Miami-Dade
  examples.
- No candidate in this survey was checked for embedded PDF metadata (author/organization in
  document properties, file paths, hidden CAD layers, PDF annotation/comment layers, or embedded
  attachments). Those "placement" dimension cells (metadata, hidden layers, annotations,
  attachments) need direct per-file inspection before assuming any of these sources cover them —
  this research only confirmed page counts and visible body text via `pdfinfo`/`pdftotext`, not
  a full structural audit of each PDF.
- General caveat for every candidate above: bid documents on live procurement portals are often
  taken down or moved once a project is awarded. Anything sourced from here should be captured
  (downloaded and hashed) promptly and its provenance recorded, since the live URL is not a stable
  long-term reference.
