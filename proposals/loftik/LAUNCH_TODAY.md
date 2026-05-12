# Hatchik — Launch Today (Marketing Page + Waitlist)

A practical, hour-by-hour checklist for getting the Hatchik marketing page live
**today**, collecting waitlist signups, and starting controlled announcements.

The product itself (wizard, orchestrator, substrate template) is still in
build. This launch is **marketing page + waitlist only**. Be honest about that
in everything you publish.

Rough total time: **5-7 hours of focused work**, more if you have to register a
new domain (DNS propagation eats time).

---

## 0. Pre-flight (15 min)

Before opening any account, decide:

- [ ] **Brand name lock**: "Hatchik" — confirmed final. (Yes.)
- [ ] **Primary email** for signups & support — `hello@hatchik.com` recommended.
- [ ] **Where the waitlist data lives**: Google Sheet (fastest), Notion DB
      (prettier), or a real service (Buttondown / EmailOctopus / Resend
      Audience). Recommendation below.
- [ ] **Day-1 announcement audience**: only personal network + indie-hacker
      community. **Do NOT post to HN today.** Save HN for when the product is
      real.

---

## 1. Domain decision (30-90 min)

### Recommended: `hatchik.com`

- `.app` is Google-operated, HTTPS-required (free TLS), reads as "modern SaaS".
- Cost: ~£14/year at most registrars (Cloudflare ~£12, Namecheap ~£15).
- Check availability: <https://www.cloudflare.com/products/registrar/> (search
  domain).

### Fallbacks (in priority order)

| Domain | Notes |
|---|---|
| `hatchik.com` | First choice |
| `hatchik.com` | Tech-friendly, ~£32/yr |
| `loftik.co` | ~£25/yr, harder to remember |
| `gethatchik.com` | Last resort — adds friction |

### Steps

1. **Register at Cloudflare Registrar** if available (no markup, free WHOIS
   privacy, 2FA-friendly).
2. If unavailable, register at Namecheap or Porkbun (avoid GoDaddy upsells).
3. Enable WHOIS privacy.
4. Enable 2FA on the registrar account immediately.
5. Add domain to Cloudflare (free plan) for DNS + proxy + analytics.

**Time budget**: 30 min if `hatchik.com` is free; 60-90 min if you're hunting
alternatives.

---

## 2. Hosting decision (15 min)

The marketing page is a single static HTML file plus a Tailwind CDN. Pick **one** of:

### Recommended: Cloudflare Pages (free)

- Already where the DNS is.
- Deploys from GitHub on push.
- Free TLS, free unlimited bandwidth, edge caching.
- Custom domain in 2 clicks.

### Alternative: Vercel (free)

- Slightly nicer UI, marginally better DX for static sites.
- 100 GB bandwidth/month on free tier (plenty for day 1).

### Don't bother with

- Netlify (fine but no advantage over CF Pages).
- GitHub Pages (works but slower edge).
- A VPS (overkill, you'll burn time).

### Steps (Cloudflare Pages)

1. Create new project in Cloudflare Pages → "Direct upload" or "Connect to
   Git".
2. If using Git: push `proposals/loftik/index.html` to a public or private repo
   (e.g. `loftik-marketing`).
3. Build settings: none (static HTML). Root directory: `proposals/loftik/`.
4. Deploy.
5. Add custom domain `hatchik.com` → Cloudflare auto-configures DNS.
6. Verify TLS padlock loads.

**Time budget**: 15 min.

---

## 3. DNS configuration (15 min, then 1-24h propagation)

If domain is at Cloudflare Registrar already, nameservers are already pointed.
If registered elsewhere, point nameservers to Cloudflare's
(`tom.ns.cloudflare.com`, `lily.ns.cloudflare.com` — exact names shown in CF
dashboard).

DNS records needed today:

| Type | Name | Value | Proxy |
|---|---|---|---|
| A or CNAME (auto) | `hatchik.com` | (CF Pages target) | Proxied |
| CNAME | `www` | `hatchik.com` | Proxied |
| MX (optional) | `hatchik.com` | placeholder if you want email later | — |

Add a redirect rule: `www.hatchik.com/*` → `https://hatchik.com/$1` (301).

**Time budget**: 15 min config + up to a few hours propagation. Use
<https://dnschecker.org> to monitor.

---

## 4. Waitlist backend (1-2 hours)

You have three paths. Pick **one**. Don't over-engineer.

### Path A — Quickest: Google Sheet via Tally or Sheety (~30 min)

- Create a Google Sheet `loftik-waitlist` with columns `email`, `source`,
  `created_at`.
- Sign up at <https://tally.so> (free) — create a form with one email field,
  Tally embeds nicely or POSTs to a webhook.
- Or sign up at <https://sheety.co> — turns a Google Sheet into a POST API in
  3 clicks.
- Update the form's `action="/api/waitlist"` to point at the Tally/Sheety
  endpoint, or proxy via a Cloudflare Worker (see Path B).

**Pros**: zero servers, zero cost, easy to inspect.
**Cons**: no email confirmation; you'll manually export when ready to email
the cohort.

### Path B — Recommended: Cloudflare Worker + Buttondown (~1 hour)

- Sign up at <https://buttondown.email> (free for first 100 subs, then
  $9/month) or <https://emailoctopus.com> (free for first 2,500 subs, no
  card).
- Create a Cloudflare Worker at `api.hatchik.com/waitlist`:
  ```js
  export default {
    async fetch(req) {
      if (req.method !== 'POST') return new Response('Method not allowed', { status: 405 });
      const { email, source } = await req.json();
      if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        return Response.json({ error: 'invalid email' }, { status: 400 });
      }
      const r = await fetch('https://api.buttondown.email/v1/subscribers', {
        method: 'POST',
        headers: {
          'Authorization': `Token ${BUTTONDOWN_KEY}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ email, tags: ['waitlist', source || 'marketing'] })
      });
      return Response.json({ ok: r.ok });
    }
  }
  ```
- Bind `BUTTONDOWN_KEY` as a secret in the Worker.
- Route: `api.hatchik.com/waitlist` → this Worker.
- Update form `action="https://api.hatchik.com/waitlist"` (or proxy from
  `/api/waitlist` on the Pages site).

**Pros**: real subscriber list, automatic email confirmation, real
unsubscribe link, ready for the "Hatchik is live" broadcast.
**Cons**: 1 hour of fiddling.

### Path C — Heaviest, only if you already use it: Resend Audience (~1 hour)

Same idea as Path B, but Resend is more developer-leaning. Use if you also
plan to send transactional email from the same account.

### Recommendation

**Go with Path B (Cloudflare Worker + EmailOctopus free tier)**. No card,
generous free limit, real subscriber list, and you'll need an email service
anyway.

**Time budget**: 60-90 min.

---

## 5. Email confirmation flow (30 min)

Two emails, both automated via your chosen service:

### Email 1 — Immediate "you're in"

Sent the moment they sign up. Keep it under 80 words.

> Subject: You're on the Hatchik waitlist
>
> Hi —
>
> Thanks for signing up. You're on the list to hear when Hatchik opens.
>
> What we're building: a wired-up SaaS substrate so people with ideas can ship
> a real product on their own domain without learning DevOps. Hosting,
> payments, mobile, mail — all sorted on day one.
>
> One more email from us when we open the doors. No newsletters. No drip
> campaign.
>
> — Farhan (@hatchik.com)

### Email 2 — Periodic "still working on it" (every 3-4 weeks)

Genuinely useful update. No marketing fluff. Examples of what to include:

- "We finished the substrate template; here's a 90-second video."
- "We made the AI-coder integration work end-to-end. Here's a screenshot."
- "We're 2-3 weeks out. Here's what's left to ship."

Cap it: **3 update emails maximum** before launch. People hate broken
promises about how often you'll email.

---

## 6. Privacy / GDPR (30 min)

You're collecting personal data (email) from EU/UK residents. Minimum
compliance:

- [ ] **Privacy notice** linked from the waitlist form footer. Use a template
      — <https://www.iubenda.com/en/privacy-policy-generator> (free tier) or
      <https://termly.io>. Hatchik's processing basis = "legitimate interest"
      (pre-launch interest list). Data is stored on Cloudflare + EmailOctopus
      (both have DPAs).
- [ ] **No cookies needed** for the static page (no analytics today, or use
      Plausible which is cookieless). If you add anything beyond Plausible
      later, you'll need a cookie banner.
- [ ] **Unsubscribe** mechanism — EmailOctopus / Buttondown include one
      automatically. Don't disable it.
- [ ] **DPA** — accept Cloudflare and EmailOctopus' standard DPAs (one click
      each in their dashboards).

Update the footer "Privacy" link to point at the privacy notice URL.

**Time budget**: 30 min.

---

## 7. Analytics (15 min)

Two free options, both cookieless:

### Recommended: Plausible (free trial / £6/mo) or Cloudflare Web Analytics (free)

- **Cloudflare Web Analytics** — already in your CF dashboard. One toggle.
  Zero cost. Decent enough for "how many visits did the HN post send".
- **Plausible** — prettier, easier dashboard. Self-hosted option exists if you
  want zero ongoing cost.

Enable Cloudflare Web Analytics today; switch to Plausible later if you want
nicer dashboards.

**Time budget**: 15 min.

---

## 8. Announcement plan (phased, NOT day 1 for cold audiences)

The product isn't live. The mistake to avoid: posting to Hacker News today,
getting 200 waitlist signups, then disappointing all of them when the wizard
doesn't ship for 6 weeks. **Stage the launches.**

### Day 1 (today) — Warm only

- [ ] **Personal email** to 20-50 close contacts (template in
      `LAUNCH_COMMS.md`). Ask for honest feedback as much as for signups.
- [ ] **Twitter/X thread** from your personal account (template in
      `LAUNCH_COMMS.md`). Quote-tweet by 1-2 friends if they're game.
- [ ] **LinkedIn post** for the more professional audience (template in
      `LAUNCH_COMMS.md`).
- [ ] **WhatsApp / Telegram groups** you're already in — anywhere indie-hacker
      or founder-shaped.

Target: 20-50 signups from warm circles.

### Day 2-3 — Adjacent communities

- [ ] **Indie Hackers** "milestone" or "show IH" post (template in
      `LAUNCH_COMMS.md`).
- [ ] **Reddit** — `r/SaaS`, `r/SideProject`, `r/EntrepreneurRideAlong` —
      one post each, framed as "feedback wanted on the landing page" not
      "buy my thing".
- [ ] **Slack/Discord communities** you're already in (Indie Hackers Discord,
      Makerlog, On Deck founders, etc.) — share where appropriate.

Target: 50-150 signups by end of week 1.

### Day 4-7 — Newsletter inclusions

- [ ] **Bram Kanstein's newsletter** (Startup Stash) — submit.
- [ ] **Indie Hackers newsletter** — pitch via DM.
- [ ] **Product Hunt "Upcoming"** — list there, don't actually launch yet.

### Week 2+ (only after product is real) — Cold audiences

- [ ] **Hacker News** "Show HN: Hatchik" — once the wizard works end-to-end.
- [ ] **Product Hunt** full launch — when you have a real demo.
- [ ] Paid acquisition (Google / Reddit / niche newsletters) — after PMH
      signal.

---

## 9. Day-1 hour-by-hour (rough)

Assuming you start at 09:00 today.

| Time | Task |
|---|---|
| 09:00-09:30 | Pre-flight decisions, domain check, payment cards ready |
| 09:30-10:30 | Register domain, set up Cloudflare, add to Pages |
| 10:30-11:00 | Push marketing HTML to repo, deploy to CF Pages, verify domain |
| 11:00-12:30 | Set up EmailOctopus + Cloudflare Worker for waitlist |
| 12:30-13:00 | Lunch / DNS propagation |
| 13:00-14:00 | Write & schedule the two confirmation emails |
| 14:00-14:30 | Privacy notice, link from footer |
| 14:30-15:00 | Enable Cloudflare Web Analytics, test end-to-end signup |
| 15:00-16:00 | Send personal-network emails (15-30 individual sends) |
| 16:00-17:00 | Post Twitter/X thread, LinkedIn, WhatsApp groups |
| 17:00-18:00 | Monitor first signups, reply to early responses |
| 18:00 | Quiet evening. Resist the urge to post to HN. |

---

## 10. Day 7 check-in: when to start sales

After one week of phased announcements, look at the waitlist count:

| Signups after 7 days | What it means |
|---|---|
| < 25 | Either your channels are too small, or the offer isn't landing. Talk to 5 people who signed up. Iterate the page. |
| 25-100 | Healthy organic signal from warm circles. Keep building the product. Plan the HN launch for product-ready date. |
| 100-300 | Strong signal. Start lining up first 5 paid customers (concierge onboarding) before the public wizard ships. |
| 300+ | Excellent. Slow down. You don't need more signups; you need to ship the product before the list goes stale. |

**Talk to 5 signups in week 1**: 15-min calls. Ask what they're building, what
they tried before, what worried them about Hatchik. This is worth more than any
metric.

---

## 11. Sanity checks before going live

Before sending the first announcement:

- [ ] Click every CTA on the page — they all scroll to `#waitlist` or are
      intentional `href="#"`.
- [ ] Submit the form yourself — verify the email arrives.
- [ ] Open the page on mobile — Tailwind responsive, but double-check the
      hero chat doesn't overflow.
- [ ] Check the page in incognito / fresh browser.
- [ ] Run <https://pagespeed.web.dev/> on `hatchik.com` — should be 95+
      (static page).
- [ ] Verify privacy notice link works.
- [ ] Update `{{COUNT}}` placeholder in the waitlist form — either to a real
      number once you have some signups, or remove the sentence for day 1.
- [ ] Sign-in link near the waitlist points at a placeholder (`href="#"`);
      that's OK until the wizard exists, but consider hiding it if it looks
      misleading.

---

## 12. What to NOT do today

- ❌ Don't post to Hacker News.
- ❌ Don't run paid ads.
- ❌ Don't quote fake user numbers.
- ❌ Don't promise a launch date you don't believe.
- ❌ Don't add live chat — you can't staff it.
- ❌ Don't add a "request a demo" CTA — there's nothing to demo yet.
- ❌ Don't set up Stripe Checkout on the marketing page — there's nothing
     to sell yet.

---

## 13. The honesty principle

In every public touchpoint today, be explicit:

- "We're not live yet."
- "The waitlist is real — one email when we open the doors."
- "We won't spam you in between."

This builds the trust that will convert when the product is ready.

---

*Last updated: launch day. This document is a checklist, not a contract.*
