# Hatchik Paddle setup

Creates the two Paddle products and three prices Hatchik needs, plus a
hosted Payment Link ready to paste into the marketing page's "Launch your
idea" button.

Paddle is Hatchik's **Merchant of Record** — see `PRODUCT_OFFERING.md §8.1`.
Paddle is the legal seller, handles tax in 100+ jurisdictions, accepts
local payment methods (SEPA / iDEAL / Klarna / UPI / Pix / Konbini / etc.)
and settles to Hatchik's Omani company account in GBP.

This script uses **Paddle Billing** (the current product, launched 2023),
not Paddle Classic. Endpoints look like `api.paddle.com/products` — not
`vendors.paddle.com/...`.

## Prerequisites (one-time)

1. **Paddle account.** Sign up at https://paddle.com.
   - **Sandbox** is instant — use it for testing.
   - **Live** account approval takes 1-2 weeks. You'll need:
     - Business registration documents (Omani entity)
     - Bank account in a Paddle-supported settlement currency (GBP works)
     - Website live with terms, privacy, refund policy (we have these
       in `proposals/hatchik/{terms,privacy}.html`)
     - Tax ID where applicable
2. **API key.** Paddle dashboard → Developer Tools → Authentication →
   "Create API key". Sandbox keys start `pdl_sdbx_apikey_…`, live keys
   start `pdl_live_apikey_…`.
3. **Python 3.10+ installed.**

## Install

```bash
cd proposals/hatchik/paddle-setup
pip install -r requirements.txt
```

## Run it

```bash
export PADDLE_API_KEY=pdl_sdbx_apikey_xxxxxxxxxxxxx
python setup.py                # SANDBOX mode (default)
python setup.py --enable-ppp   # add localized PPP overrides (USD/EUR/INR/BRL)
```

The script will:

1. Create the **Hatchik Launch** product (or reuse if it already exists,
   identified by `custom_data.lookup_key`)
2. Create a **£89 one-time** price (lookup_key: `hatchik_launch_setup`)
3. Create a **£14/month** price with a 30-day trial (lookup_key:
   `hatchik_launch_monthly`) — the trial means the first £14 charges 30
   days after signup, so the £89 setup naturally covers month 1
4. Create the **Hatchik Growth** product
5. Create a **£39/month** price (lookup_key: `hatchik_growth_monthly`)
6. Create a hosted **Payment Link** (Paddle transaction) bundling setup
   + monthly. The response includes `checkout.url` — that's the link
   you paste into `index.html`.

At the end you'll see something like:

```
Payment Link (paste into index.html):

  https://sandbox-buy.paddle.com/checkout/abc123-...
```

## Localized pricing (PPP)

Pass `--enable-ppp` to attach Paddle `unit_price_overrides` for:

| Country | Launch setup | Launch monthly | Growth monthly |
|---|---|---|---|
| US | $115 | $18 | $49 |
| DE / FR / ES / IT / NL | €99 | €16 | €45 |
| IN | ₹3999 (~£40) | ₹449 (~£4.50) | ₹1299 (~£13) |
| BR | R$299 (~£54) | R$35 (~£6) | R$95 (~£17) |

Everywhere else falls back to the GBP base price, converted at Paddle's
mid-market FX rate at checkout time.

These overrides live in a clearly-marked `PRICE_OVERRIDES` dict at the
top of `setup.py` — edit them as you learn more about each market.

## Wire it into the marketing page

1. Open `proposals/hatchik/index.html`
2. Find both occurrences of:

   ```
   mailto:hello@hatchik.com?subject=Hatchik%20Launch%20-%20early%20access
   ```

   (one on line ~1186, one on line ~1416)
3. Replace each with the Payment Link URL from the script output.

## Test before going live

Paddle's sandbox accepts these test card numbers (any future expiry,
any CVC, any postal code):

- **Success:** `4242 4242 4242 4242`
- **Decline:** `4000 0000 0000 0119`
- Full list + 3DS / SCA / per-region cards:
  https://developer.paddle.com/concepts/payment-methods/credit-debit-card

Walk through the Checkout flow yourself. Verify:

- £89 setup + £14/month line items appear correctly
- 30-day trial notice on the monthly line
- Local-currency display if you set browser/IP to a PPP country
  (and you ran with `--enable-ppp`)
- VAT / tax line appears for EU IPs
- Test card completes successfully
- Customer lands on `https://hatchik.com/welcome?txn=…`
- The corresponding event lands in **Paddle dashboard → Events**

## Webhooks (separate one-time action)

Webhooks tell your VPS when a payment succeeds / a subscription cancels /
a payment fails. Set this up *once*, separately from this script:

1. Paddle dashboard → Developer Tools → Notifications → "Add destination"
2. **URL:** `https://hatchik.com/api/paddle/webhook`
3. **Events** (minimum useful set):
   - `transaction.completed`
   - `transaction.payment_failed`
   - `subscription.created`
   - `subscription.updated`
   - `subscription.canceled`
   - `subscription.trialing`
   - `customer.created`
4. Save. Paddle will show a **signing secret** like `pdl_ntfset_…`.
5. Copy that into the VPS:

   ```bash
   # On hatchik VPS, edit the systemd unit:
   sudo systemctl edit hatchik-signup.service
   # add: Environment="PADDLE_WEBHOOK_SECRET=pdl_ntfset_..."
   sudo systemctl restart hatchik-signup.service
   ```

The receiving handler must verify the `Paddle-Signature` header against
`PADDLE_WEBHOOK_SECRET` before trusting the body. See
https://developer.paddle.com/webhooks/signature-verification.

## Going live

Once your live Paddle account is approved:

```bash
export PADDLE_API_KEY=pdl_live_apikey_xxxxxxxxxxxxx
python setup.py --live --enable-ppp
```

This creates the **same** products + prices in the live account. Lookup
keys mean re-running is safe — already-existing entries are reused, not
duplicated.

You'll get a different (live) Payment Link URL. Swap it into
`index.html` to replace the sandbox link.

## What the customer pays

| When | Amount (GBP base) | Why |
|---|---|---|
| At Checkout | £89 | One-time setup fee |
| 30 days later | £14 | First monthly charge (trial ends) |
| Every month after | £14 | Until graduation |
| After 15th sign-up | £39/month | Graduation (manual switch via Paddle dashboard or subscription update API — see below) |

Local-currency amounts shown at checkout depend on the customer's
country + your `--enable-ppp` flag.

## Graduation to Growth (when customer hits 15 sign-ups)

This isn't automatic. When you see a customer cross the threshold:

1. Paddle dashboard → Customers → find them → their subscription
2. "Manage" → "Add charge / change items"
3. Remove the `hatchik_launch_monthly` price, add `hatchik_growth_monthly`
4. Set proration to "do not prorate" (per `PRODUCT_OFFERING.md §2.3`)

Email the customer a month in advance per `PRODUCT_OFFERING.md §2.3`.
The provisioning worker (when built) will do this via Paddle's
subscription update API:
https://developer.paddle.com/api-reference/subscriptions/update-subscription

## Refunds

Paddle is the Merchant of Record, so refunds go through them:

1. Paddle dashboard → Transactions → find the charge
2. "Refund" → full or partial → enter reason
3. Customer gets the refund through their original payment method,
   typically 5-10 business days

Chargebacks are also Paddle's responsibility — they handle the dispute
on Hatchik's behalf and only forward the resolution.

See `EXIT_JOURNEY.md` for the broader off-boarding flow.

## Troubleshooting

**"Authentication failed (401)"** — your API key is wrong or you're
hitting the wrong base. Sandbox keys only work against
`api.sandbox.paddle.com`, live keys only against `api.paddle.com`. The
`--live` flag picks the right base.

**"Tax category required"** — Paddle requires every product to map to
a tax category. The script sets `tax_category: "saas"`. If your account
needs a different category (e.g. `digital-goods`), edit the constant in
`ensure_product()`.

**"checkout.url is empty in transaction response"** — Paddle's hosted
checkout requires a **Default checkout URL** configured in dashboard →
Checkout Settings. Set it to your overlay/inline-checkout page (or use
Paddle's hosted-page template) and re-run. The transaction ID is still
printed so you can recover.

**Pricing override rejected** — Paddle requires each `country_code` in
`unit_price_overrides` to use a currency the account is approved to
settle/display in. Some currencies (e.g. RUB, CNY) need extra approval.
Comment out unsupported entries in `PRICE_OVERRIDES`.

**"Webhook URLs failing TLS"** — Paddle requires HTTPS. If you haven't
set up Caddy + Let's Encrypt yet, see `DEPLOY_MARKETING.md`.

## API uncertainties to verify before live run

A few fields are coded defensively (with `TODO:` comments in `setup.py`)
because Paddle's API has evolved and we're being careful:

- **`Paddle-Version` header** — left unset to use Paddle's default. Pin
  it if you see deprecation warnings in responses.
- **`tax_mode: "account_setting"`** on prices — inherits from account
  defaults. Override to `"internal"` or `"external"` if you need
  specific tax-inclusive behaviour.
- **Payment Link via `/transactions`** — Paddle's recommended way to
  produce a hosted checkout URL. An alternative is to configure a
  default-checkout-URL in dashboard and pass `?items[0][priceId]=…`
  query params (overlay checkout style). Choose whichever fits your
  flow; the script uses the transaction approach for a static link.

## What's NOT in this script (for v1)

- Paddle product images (upload via dashboard for now)
- Paddle Retain (dunning + smart-recovery) — enable in dashboard once
  you have paying customers
- Discount codes — create in dashboard → Discounts when you want them
- AI passthrough metering — handled separately by the `proxy.atelo`
  service, not by Paddle billing
- Webhook handler code on the VPS — separate task; this script only
  configures Paddle, not the receiver
