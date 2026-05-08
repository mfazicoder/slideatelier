"""Pre-canned consulting briefs for demos.

These power the "Try a sample brief" affordance on the workflow landing
page. Each brief is a self-contained, realistic input — concrete numbers,
named stakeholders, a clear decision point — so a first-time user can
click through to a finished deck without typing anything.
"""

UAE_SETUP = """A founder is launching a B2B SaaS company targeting Gulf-region enterprise customers (initial revenue target: $4M ARR by end of year two). They're deciding between three jurisdiction options for the holding entity:

1. ADGM (Abu Dhabi Global Market) — common-law framework, 0% corporate tax up to AED 375K, English courts, $25K setup + $15K annual.
2. DIFC (Dubai International Financial Centre) — common-law, similar tax position, more mature ecosystem, $30K setup + $22K annual, more fintech-aligned.
3. Mainland Dubai (DED) — civil-law, requires local sponsor under most categories despite recent reforms, broader trade rights, $12K setup + $9K annual.

Founder priorities: (a) optionality on a future Series A, (b) ability to invoice GCC enterprise clients without VAT friction, (c) low ongoing compliance overhead. Founder will personally be UK tax-resident for the first 18 months.

Current capital: $1.5M seed already in escrow, expected close in 6 weeks. First two hires (CTO, founding sales) will be UAE-based. No physical office needed for year one.

Key open questions: corporate tax exposure on UK-resident director, double-tax treaty considerations, banking access (ADGM banks have been slow lately), substance requirements under UAE economic substance rules.

Decision required: pick a single jurisdiction and outline the 12-week setup plan."""

ACME_Q3 = """Acme Corp Q3 business review.

Revenue grew 12% YoY to $48M, beating plan by 4%. Growth driven entirely by enterprise segment (+34%); SMB flat at $11M.

New-customer acquisition down 8% — pipeline conversion fell from 22% to 17%. Sales cycle lengthened from 47 to 63 days. Win rate against competitor "Beta" dropped from 58% to 41% on deals over $250K ARR.

Churn ticked up to 4.2% (vs 3.1% prior quarter), concentrated in customers under $50K ARR. Net retention for enterprise (>$250K ARR) remains strong at 124%.

CAC up 19% in SMB (now $14K against an $18K average first-year contract); enterprise CAC steady at $42K with 8-month payback.

Recommendation under discussion: shift $2M from SMB marketing to enterprise demand-gen, enforce a $50K ARR floor for new SMB deals starting Q4, and stand up a competitive war-room targeting Beta.

Audience: board of directors, 30-minute slot, decision required on the $2M reallocation and the SMB ARR floor."""

GO_TO_MARKET = """A high-growth B2B SaaS company ($28M ARR, 70% YoY growth, predominantly mid-market) is launching a new enterprise tier next quarter. List price will be $150K/year minimum, 5x the current top SKU.

Three GTM motions are on the table:

1. Field sales overlay — hire 6 enterprise AEs (avg fully-loaded $320K), an SE leader, and 2 solutions architects. 9-month ramp, target $12M new ARR in year one.

2. Channel-led — partner with 2 global SIs (Accenture, Deloitte) plus one regional partner per geography. Lower direct cost ($1.2M program investment), longer time-to-revenue, less control over deal quality.

3. Product-led upgrade path — invest in self-serve enterprise features (SSO, audit logs, multi-region) and let existing $50K-tier customers expand naturally; add a small (2-person) inside-sales team to handle inbound enterprise leads.

Pricing model is open: per-seat, consumption-based, or platform fee + usage. CFO wants 75%+ gross margin maintained. Current NPS in mid-market is 62. The CRO believes #1 wins; the VP Product believes #3 is faster; the CFO is partial to #2 for capital efficiency.

Audience: CEO + CRO; 25 minutes; recommend a single GTM motion plus a defensible pricing model."""


SAMPLES: dict[str, dict] = {
    "uae-setup": {
        "title": "UAE company setup",
        "summary": "Jurisdiction decision driving a market-entry plan",
        "brief": UAE_SETUP,
        "requirements": "Audience: founder; 12-min walkthrough; decision required.",
    },
    "acme-q3": {
        "title": "Acme Q3 board review",
        "summary": "Asymmetric quarter — recommend reallocating SMB budget",
        "brief": ACME_Q3,
        "requirements": "Audience: board of directors; 30-minute slot; decision required on $2M reallocation.",
    },
    "go-to-market": {
        "title": "Enterprise tier GTM",
        "summary": "Pick the right launch motion + pricing model",
        "brief": GO_TO_MARKET,
        "requirements": "Audience: CEO + CRO; 25 min; recommend a single GTM motion.",
    },
}
