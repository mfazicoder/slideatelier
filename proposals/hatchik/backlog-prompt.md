# Starter Backlog Generation Prompt

The single most magical moment in LaunchKit: customer describes their product
in one paragraph, and minutes later they have a Linear board with twenty
real user stories, properly structured, ready to drive their AI coder.

This document specifies the prompt, the structured output format, and the
guardrails. The output of this prompt feeds directly into Linear's GraphQL
API as a batch of `issueCreate` mutations.

## Inputs

```jsonc
{
  "product_name": "MealMate",
  "tagline": "Weekly meal plans for couples",                  // optional
  "description": "A meal planner for couples that generates a weekly menu and a consolidated shopping list from each partner's favourite recipes.",
  "comparable_product": "Plan to Eat",                          // optional, helps anchor scope
  "primary_user": "Couples cooking together",                   // optional
  "pricing_model": "subscription",                              // one of: free | trial | subscription | one_time
  "target_launch_date": "2026-08-01"                            // optional, drives "Launch" epic urgency
}
```

Only `product_name` and `description` are required. The rest sharpen the
output but the prompt must produce a sensible backlog with just those two.

## Output schema

```typescript
type StarterBacklog = {
  epics: Epic[]
}

type Epic = {
  key: "substrate" | "core" | "polish" | "launch"
  name: string                              // human-readable
  description: string
  stories: Story[]
}

type Story = {
  title: string                             // imperative, e.g. "Couples can invite a partner via email"
  description: string                       // 2-4 sentences, markdown allowed
  acceptance_criteria: string[]             // 3-6 bullets, each testable
  labels: string[]                          // from a fixed set; see below
  priority: 1 | 2 | 3 | 4                   // 1=urgent, 4=low
  estimate: 1 | 2 | 3 | 5 | 8               // fibonacci, story points
  pre_completed: boolean                    // true ONLY for stories in the "substrate" epic
}
```

Allowed labels: `frontend`, `backend`, `infra`, `auth`, `payments`, `mobile`,
`design`, `content`, `marketing`, `ai-friendly`, `gdpr`, `nice-to-have`.

The prompt is responsible for picking ~20 stories total, distributed roughly:
- `substrate`: 5 stories (all pre-completed)
- `core`: 8–10 product-specific stories
- `polish`: 4–5 generic SaaS polish stories
- `launch`: 3–4 go-live tasks

## The prompt

```
SYSTEM:

You are a senior product manager helping a solo founder set up their backlog
for a new SaaS product. Your job is to turn a one-paragraph product
description into a starter backlog of ~20 well-formed user stories,
distributed across four epics: Substrate, Core, Polish, Launch.

Principles you follow without exception:

1. Stories are written in user-value language ("Users can ..."), not
   technical language ("Implement X endpoint"). One exception: the
   Substrate epic, which describes infrastructure that's already done.
2. Every story is a vertical slice — covering UI, backend, and data
   together. Never produce stories like "design the UI" separately from
   "build the backend".
3. The Substrate epic is always identical in structure (see template
   below) and ALL its stories are pre_completed=true. These represent
   the fully-wired SaaS substrate LaunchKit delivers on day one.
4. The Core epic is THE PRODUCT. Generate 8–10 stories that, if all
   shipped, would constitute a usable v1 of what the customer described.
   Be opinionated. If the description is ambiguous, pick a coherent
   interpretation rather than producing vague stories.
5. The Polish epic covers things every SaaS needs but a vibe-coder
   typically forgets: onboarding, empty states, account deletion (GDPR),
   ToS/Privacy pages, mobile responsiveness check, error pages.
6. The Launch epic covers go-live: switching Stripe to live mode,
   submitting to app stores (only if the product description suggests
   mobile matters), drafting marketing copy, setting up a support inbox,
   announcement plan.
7. Acceptance criteria are testable. Bad: "Works well." Good: "When the
   user clicks 'Invite partner', an email is sent and the partner appears
   in the household with 'Pending' status until they accept."
8. Estimates: be honest. A full CRUD page is 3 points. A workflow with
   external integration is 5. Anything you'd want to break down further
   in real life is 8 (and you should consider splitting it).
9. Labels: pick the smallest accurate set. Prefer 1–3 labels per story.
10. Output ONLY valid JSON matching the StarterBacklog schema. No prose
    before or after.

Substrate epic template (ALWAYS use exactly these 5 stories, pre_completed=true):

  - "Users can sign up with email, magic link, or Google"
    (labels: auth, frontend, backend; estimate: 5)
  - "Customers can subscribe and manage their plan through Stripe"
    (labels: payments, backend, frontend; estimate: 5)
  - "App is live at the customer's custom domain with TLS"
    (labels: infra; estimate: 3)
  - "Transactional emails send from the customer's domain"
    (labels: infra, backend; estimate: 3)
  - "iOS and Android shells build from the same codebase"
    (labels: mobile, infra; estimate: 5)

USER:

Product name: {product_name}
Tagline: {tagline_or_empty}
Description: {description}
Comparable product: {comparable_product_or_empty}
Primary user: {primary_user_or_empty}
Pricing model: {pricing_model}
Target launch: {target_launch_date_or_empty}

Generate the starter backlog as JSON.
```

## Worked example

**Input:**
```json
{
  "product_name": "MealMate",
  "description": "A meal planner for couples that generates a weekly menu and a consolidated shopping list from each partner's favourite recipes.",
  "primary_user": "Couples cooking together",
  "pricing_model": "subscription"
}
```

**Expected output (abbreviated):**
```jsonc
{
  "epics": [
    {
      "key": "substrate",
      "name": "Substrate (delivered by LaunchKit)",
      "description": "The wired-up SaaS foundation. All of these are live the moment your app deploys.",
      "stories": [
        {
          "title": "Users can sign up with email, magic link, or Google",
          "description": "Sign-up, login, password reset, and Google OAuth all work out of the box. ...",
          "acceptance_criteria": [
            "User can register with email + password",
            "User receives a confirmation email and can click to verify",
            "User can request a magic link instead of using a password",
            "User can sign in with Google",
            "User can reset a forgotten password"
          ],
          "labels": ["auth", "frontend", "backend"],
          "priority": 2,
          "estimate": 5,
          "pre_completed": true
        }
        // ... 4 more substrate stories per template
      ]
    },
    {
      "key": "core",
      "name": "Core product",
      "description": "The MealMate experience. Ship these to have a working v1.",
      "stories": [
        {
          "title": "Couples can create a household and invite a partner",
          "description": "A user creates a 'household' (the shared planning unit) and invites their partner by email. Both partners can later edit the household's recipes and menus.",
          "acceptance_criteria": [
            "Authenticated user can create a household with a name",
            "Household creator can invite one partner via email address",
            "Invited partner receives an email with a join link",
            "Accepting the invite adds them to the household",
            "Either partner can see the household's content"
          ],
          "labels": ["frontend", "backend", "auth"],
          "priority": 1,
          "estimate": 5,
          "pre_completed": false
        },
        {
          "title": "Each partner can add recipes to the household library",
          "description": "Recipes have a name, ingredients, cooking time, and a notes field. Either partner can add, edit, or remove recipes.",
          "acceptance_criteria": [
            "Recipe form captures name, ingredients (free text), time, notes",
            "Recipes are listed on a household 'Library' page",
            "Either partner can edit or delete any recipe",
            "Recipes are searchable by name"
          ],
          "labels": ["frontend", "backend"],
          "priority": 1,
          "estimate": 3,
          "pre_completed": false
        },
        // ... 6–8 more core stories: favourites, weekly menu generation,
        //     drag-and-drop reorder, shopping list rollup, list sharing,
        //     leftover handling, dietary preferences ...
      ]
    },
    {
      "key": "polish",
      "name": "Polish",
      "description": "Things every SaaS needs that are easy to forget.",
      "stories": [
        {
          "title": "First-time users see a 5-step onboarding tour",
          "description": "On first sign-in, walk the user through creating their household, inviting their partner, and adding their first recipe.",
          "acceptance_criteria": [
            "Tour shows on first session only",
            "User can skip at any step",
            "Tour state is stored per user",
            "Tour is dismissible from the help menu later"
          ],
          "labels": ["frontend", "design"],
          "priority": 3,
          "estimate": 3,
          "pre_completed": false
        },
        // ... empty states, account deletion (GDPR), ToS/Privacy pages,
        //     mobile responsiveness pass, 404/500 pages ...
      ]
    },
    {
      "key": "launch",
      "name": "Launch",
      "description": "Go-live checklist before opening the doors.",
      "stories": [
        {
          "title": "Switch Stripe from test mode to live mode",
          "description": "Confirm pricing, tax setup, and live webhook URL. Take a test payment with a real card and refund it.",
          "acceptance_criteria": [
            "Stripe Live keys configured",
            "Live webhook receiving events from Stripe",
            "Subscription gating tested end-to-end with a real card",
            "Tax configuration verified for target countries"
          ],
          "labels": ["payments", "infra"],
          "priority": 1,
          "estimate": 2,
          "pre_completed": false
        },
        // ... app store submission, marketing copy, support inbox setup,
        //     launch announcement ...
      ]
    }
  ]
}
```

## Failure modes and fallbacks

| Failure | Fallback |
|---|---|
| Model returns non-JSON | Retry once with `"Respond with valid JSON only."` appended; if still bad, fall back to the generic backlog (no Core epic specifics) and email customer with apology. |
| Model produces fewer than 5 Core stories | Re-prompt with "The Core epic must have at least 8 stories. Add more." Retry once. |
| Model produces stories with skipped fields | Validator coerces missing fields to defaults (priority=3, estimate=3, labels=[]) and proceeds. |
| Description is too vague ("an app for stuff") | Pre-validator catches this before calling the LLM and asks the customer for more detail before submitting. |
| Description suggests an out-of-scope product (gambling, scams, anything our denylist matches) | Reject with a friendly error before calling the LLM. |

## Cost estimate

Per invocation, with Claude Sonnet 4.6:
- Input: ~1.5k tokens (system + user)
- Output: ~4-5k tokens (structured backlog)
- Cost: ~€0.05–€0.08 per customer

Negligible at LaunchKit's price point. Worth caching by `hash(description)`
in case the customer reruns wizard or refines and re-submits.

## Iteration plan

v1: This prompt, Claude Sonnet 4.6.
v2: Few-shot with 3 worked examples from real customer products (with
    permission), to anchor format and quality.
v3: Customer can "Regenerate" or "Add more stories" from inside their
    LaunchKit dashboard, which streams stories straight into Linear.
v4: After launch, the same generator pattern (with customer's existing
    backlog + new product description input) lets us suggest stories
    based on what's already there ("here are 5 stories I think you're
    missing for billing flows").
