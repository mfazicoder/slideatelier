// Hatchik investor deck v3 — regenerated per founder feedback (May 2026)
// Deletes old slides 7 + 18, splits 13, moves 30→after 20, 31→to position 4.
// Bumps typography sizes uniformly; recolors slide-3 by layer; adds market sources;
// reworks personas; restructures product flow; adds EBITDA graphs; reframes exit slides.

const pptxgen = require("pptxgenjs");

const C = {
  bg:        "F3F1FA",   // light lavender-tinted off-white (matches v2)
  bgCard:    "FFFFFF",
  bgSoft:    "EFECF7",
  ink:       "0F172A",   // near-black slate
  ink2:      "475569",   // slate-600 body
  muted:     "94A3B8",   // slate-400 captions
  border:    "E2E0F0",
  indigo:    "5040E5",   // brand accent
  indigoSoft:"E8E5FB",   // pill background
  indigoLight:"F0EDFC",  // very pale wash
  indigoMid: "B4ACEF",   // dark-bg accent
  indigoDeep:"3A2BC0",
  dark:      "0B0E1F",   // hero/dark slide bg
  darkCard:  "151A33",   // dark nested card bg
  darkCard2: "1F2547",
  white:     "FFFFFF",
  green:     "10B981",
  amber:     "F59E0B",
  red:       "EF4444",
};

const FONT_HEAD = "Inter";
const FONT_BODY = "Inter";
const FONT_MONO = "JetBrains Mono";

// Uniform typography scale (bumped per feedback)
const T = {
  eyebrow:   { size: 10.5, bold: false, font: FONT_MONO },
  brand:     { size: 16,   bold: true,  font: FONT_HEAD },
  pageMark:  { size: 9.5,  bold: false, font: FONT_MONO },
  h1:        { size: 36,   bold: true,  font: FONT_HEAD },
  h1Small:   { size: 34,   bold: true,  font: FONT_HEAD },
  sub:       { size: 15.5, bold: false, font: FONT_BODY },
  bodyLg:    { size: 15,   bold: false, font: FONT_BODY },
  body:      { size: 13.5, bold: false, font: FONT_BODY },
  bodySm:    { size: 12,   bold: false, font: FONT_BODY },
  cardEyebrow:{size: 10.5, bold: false, font: FONT_MONO },
  cardTitle: { size: 19,   bold: true,  font: FONT_HEAD },
  cardTitleSm:{size: 16,   bold: true,  font: FONT_HEAD },
  pill:      { size: 12,   bold: false, font: FONT_MONO },
  pillLg:    { size: 13.5, bold: false, font: FONT_MONO },
  statBig:   { size: 56,   bold: true,  font: FONT_HEAD },
  statMid:   { size: 40,   bold: true,  font: FONT_HEAD },
  statLabel: { size: 13,   bold: false, font: FONT_BODY },
  footer:    { size: 9.5,  bold: false, font: FONT_MONO },
  mono:      { size: 11.5, bold: false, font: FONT_MONO },
  monoSm:    { size: 10,   bold: false, font: FONT_MONO },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 × 7.5
pres.author = "Farhan Irshad";
pres.title = "Hatchik — Investor + Partner Deck (v3)";
pres.company = "Hatchik";

const W = 13.333;
const H = 7.5;
const TOTAL = 28;

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

function bg(s, dark = false) {
  s.background = { color: dark ? C.dark : C.bg };
}

// Logo egg: dark filled circle with cropped white smile
function brandEgg(s, x, y, size = 0.32, dark = false) {
  const fill = dark ? C.white : C.dark;
  s.addShape("ellipse", { x, y, w: size, h: size, fill: { color: fill }, line: { type: "none" } });
  // simple bottom "smile" line
  s.addShape("rect", {
    x: x + size * 0.25, y: y + size * 0.55, w: size * 0.5, h: size * 0.04,
    fill: { color: dark ? C.dark : C.white }, line: { type: "none" }
  });
}

function header(s, num, sectionLabel, dark = false) {
  const inkc = dark ? C.white : C.ink;
  const muted = dark ? C.indigoMid : C.muted;
  brandEgg(s, 0.55, 0.32, 0.32, dark);
  s.addText("hatchik", {
    x: 0.95, y: 0.28, w: 1.5, h: 0.35,
    fontSize: T.brand.size, bold: true, fontFace: T.brand.font, color: inkc,
    align: "left", valign: "middle", margin: 0,
  });
  if (sectionLabel) {
    const pad = String(num).padStart(2, "0");
    s.addText(`${pad}  ·  ${sectionLabel}`, {
      x: W - 5.5, y: 0.30, w: 5.0, h: 0.35,
      fontSize: T.eyebrow.size, fontFace: T.eyebrow.font, color: muted,
      align: "right", valign: "middle", charSpacing: 4, margin: 0,
    });
  }
  // hairline under header
  s.addShape("line", {
    x: 0.55, y: 0.78, w: W - 1.1, h: 0,
    line: { color: dark ? "26305A" : C.border, width: 0.5 },
  });
}

function footer(s, num, dark = false) {
  const muted = dark ? C.indigoMid : C.muted;
  s.addShape("line", {
    x: 0.55, y: H - 0.65, w: W - 1.1, h: 0,
    line: { color: dark ? "26305A" : C.border, width: 0.5 },
  });
  s.addText("HATCHIK  ·  INVESTOR & PARTNER DECK  ·  MAY 2026", {
    x: 0.55, y: H - 0.55, w: 8, h: 0.3,
    fontSize: T.footer.size, fontFace: T.footer.font, color: muted,
    align: "left", valign: "middle", charSpacing: 3, margin: 0,
  });
  s.addText(`${String(num).padStart(2,"0")}  /  ${TOTAL}`, {
    x: W - 2.0, y: H - 0.55, w: 1.45, h: 0.3,
    fontSize: T.footer.size, fontFace: T.footer.font, color: muted,
    align: "right", valign: "middle", charSpacing: 3, margin: 0,
  });
}

// Two-clause title: dark first, indigo second
function title2(s, x, y, w, parts, dark = false, sizeOverride) {
  const inkc = dark ? C.white : C.ink;
  const accent = dark ? C.indigoMid : C.indigo;
  s.addText(
    [
      { text: parts[0] + " ", options: { color: inkc, bold: true } },
      { text: parts[1],       options: { color: accent, bold: true } },
    ],
    {
      x, y, w, h: 0.85,
      fontSize: sizeOverride || T.h1.size, fontFace: T.h1.font,
      align: "left", valign: "top", margin: 0,
    }
  );
}

function subtitle(s, x, y, w, text, dark = false) {
  s.addText(text, {
    x, y, w, h: 0.75,
    fontSize: T.sub.size, fontFace: T.sub.font, color: dark ? C.indigoMid : C.ink2,
    align: "left", valign: "top", margin: 0,
  });
}

function card(s, x, y, w, h, opts = {}) {
  const fill = opts.fill || C.bgCard;
  const stroke = opts.stroke || C.border;
  s.addShape("roundRect", {
    x, y, w, h,
    rectRadius: opts.r || 0.12,
    fill: { color: fill },
    line: { color: stroke, width: opts.lineW != null ? opts.lineW : 0.75 },
  });
}

function pill(s, x, y, w, h, text, opts = {}) {
  const fill = opts.fill || C.indigoSoft;
  const color = opts.color || C.ink;
  s.addShape("roundRect", {
    x, y, w, h,
    rectRadius: h / 2,
    fill: { color: fill },
    line: opts.stroke ? { color: opts.stroke, width: 0.5 } : { type: "none" },
  });
  s.addText(text, {
    x, y, w, h,
    fontSize: opts.size || T.pill.size, fontFace: opts.font || FONT_MONO,
    color, bold: opts.bold || false,
    align: "center", valign: "middle", margin: 0,
    charSpacing: opts.charSpacing || 2,
  });
}

function eyebrow(s, x, y, w, text, dark = false) {
  s.addText(text, {
    x, y, w, h: 0.3,
    fontSize: T.cardEyebrow.size, fontFace: T.cardEyebrow.font,
    color: dark ? C.indigoMid : C.muted,
    align: "left", valign: "middle", charSpacing: 4, margin: 0,
  });
}

function statBlock(s, x, y, w, h, big, label, sublabel, opts = {}) {
  const dark = !!opts.dark;
  card(s, x, y, w, h, { fill: dark ? C.darkCard : C.bgCard, stroke: dark ? "26305A" : C.border });
  eyebrow(s, x + 0.3, y + 0.25, w - 0.6, label.toUpperCase(), dark);
  s.addText(big, {
    x: x + 0.3, y: y + 0.6, w: w - 0.6, h: 1.05,
    fontSize: T.statBig.size, fontFace: T.statBig.font, bold: true,
    color: opts.bigColor || (dark ? C.white : C.ink),
    align: "left", valign: "top", margin: 0,
  });
  if (sublabel) {
    s.addText(sublabel, {
      x: x + 0.3, y: y + h - 0.7, w: w - 0.6, h: 0.4,
      fontSize: T.statLabel.size, fontFace: T.statLabel.font,
      color: dark ? C.indigoMid : C.ink2,
      align: "left", valign: "middle", margin: 0,
    });
  }
}

// Mono terminal block on dark
function termBlock(s, x, y, w, h, lines, opts = {}) {
  s.addShape("roundRect", {
    x, y, w, h, rectRadius: 0.12,
    fill: { color: opts.fill || C.dark },
    line: { type: "none" },
  });
  // title strip
  if (opts.title) {
    s.addText(opts.title, {
      x: x + 0.25, y: y + 0.15, w: w - 0.5, h: 0.3,
      fontSize: T.monoSm.size, fontFace: FONT_MONO, color: C.indigoMid,
      align: "left", valign: "middle", charSpacing: 3,
    });
  }
  const top = opts.title ? y + 0.5 : y + 0.25;
  const text = lines.map(l => {
    if (typeof l === "string") return { text: l + "\n", options: { color: C.white } };
    return { text: l.text + "\n", options: { color: l.color || C.white, bold: l.bold } };
  });
  s.addText(text, {
    x: x + 0.3, y: top, w: w - 0.6, h: h - (top - y) - 0.2,
    fontSize: opts.fontSize || T.mono.size, fontFace: FONT_MONO,
    align: "left", valign: "top", margin: 0,
  });
}

// ═════════════════════════════════════════════════════════════════════════
// SLIDES
// ═════════════════════════════════════════════════════════════════════════

// ─── SLIDE 1 — Cover ───────────────────────────────────────────────────
function slide1() {
  const s = pres.addSlide();
  bg(s);
  // top indigo bar
  s.addShape("rect", { x: 0, y: 0, w: W, h: 0.10, fill: { color: C.indigo }, line: { type: "none" } });
  header(s, 1, "", false);
  // Right-side meta
  s.addText("INVESTOR & PARTNER DECK  ·  MAY 2026", {
    x: W - 5.5, y: 0.30, w: 5.0, h: 0.35,
    fontSize: T.eyebrow.size, fontFace: FONT_MONO, color: C.muted,
    align: "right", valign: "middle", charSpacing: 4,
  });

  // Eyebrow
  s.addText("THE PRODUCTION SUBSTRATE FOR AI-BUILT SAAS", {
    x: 0.55, y: 2.30, w: 8, h: 0.4,
    fontSize: 13, fontFace: FONT_MONO, color: C.indigo,
    align: "left", valign: "middle", charSpacing: 4,
  });

  // Hero title — two stacked lines, each safely sized to fit
  s.addText("Got an idea?", {
    x: 0.55, y: 2.50, w: 7.5, h: 1.20,
    fontSize: 56, fontFace: FONT_HEAD, bold: true, color: C.ink,
    align: "left", valign: "top",
  });
  s.addText("We'll get it launched.", {
    x: 0.55, y: 3.55, w: 7.5, h: 1.20,
    fontSize: 56, fontFace: FONT_HEAD, bold: true, color: C.indigo,
    align: "left", valign: "top",
  });

  // Right terminal mock
  termBlock(s, 7.7, 2.65, 5.15, 2.95, [
    { text: "you › build me a meal prep app called PrepSheet", color: C.white },
    "",
    { text: "claude › I'll set up a free Sandbox so we can", color: C.indigoMid },
    { text: "         start building straight away. OK?", color: C.indigoMid },
    "",
    { text: "you › yes please", color: C.white },
    "",
    { text: "claude ›", color: C.indigoMid },
    { text: "   ✓ live at prepsheet.hatchik.com", color: C.green },
    { text: "   ✓ repo cloned", color: C.green },
    { text: "   ✓ 23 starter tasks ready", color: C.green },
    { text: "   what shall we build first?", color: C.white },
  ], { title: "YOUR AI · CHAT      ● ● ●", fontSize: 11.5 });

  // Strapline
  s.addText("Your domain · your data · no platform · no lock-in. Owning the seam between AI builders and infrastructure — £20–100M strategic-exit window in 12–18 months.", {
    x: 0.55, y: 5.85, w: 7.0, h: 1.0,
    fontSize: T.bodyLg.size, fontFace: FONT_BODY, color: C.ink2,
    align: "left", valign: "top",
  });

  // Bottom-left founder line
  s.addText("FARHAN IRSHAD  ·  FOUNDER  ·  OMANI-REGISTERED COMPANY", {
    x: 0.55, y: H - 0.55, w: 8, h: 0.3,
    fontSize: T.footer.size, fontFace: FONT_MONO, color: C.muted,
    align: "left", valign: "middle", charSpacing: 3,
  });
  s.addText("HATCHIK.COM  ·  HELLO@HATCHIK.COM", {
    x: W - 5, y: H - 0.55, w: 4.45, h: 0.3,
    fontSize: T.footer.size, fontFace: FONT_MONO, color: C.muted,
    align: "right", valign: "middle", charSpacing: 3,
  });
  // hairline above footer
  s.addShape("line", { x: 0.55, y: H - 0.65, w: W - 1.1, h: 0, line: { color: C.border, width: 0.5 } });
}

// ─── SLIDE 2 — The Problem (with research sources) ──────────────────────
function slide2() {
  const s = pres.addSlide();
  bg(s);
  header(s, 2, "THE PROBLEM");

  title2(s, 0.55, 1.05, W - 1.1, ["AI builds the prototype.", "Then they hit a wall."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Non-technical founders ship working-looking prototypes with AI tools. They get stuck on the infrastructure step — auth, payments, mailboxes, mobile, a real database — and the project quietly dies between \"looks working\" and \"real business\".");

  // ── Market research strip (NEW per feedback) ───────────────────────
  const stripY = 2.85;
  const stripH = 1.20;
  card(s, 0.55, stripY, W - 1.1, stripH, { fill: C.indigoLight, stroke: C.indigoSoft });
  eyebrow(s, 0.80, stripY + 0.15, 6, "WHAT THE MARKET IS SAYING  ·  TRIANGULATED");
  // 4 mini-stat columns — each with stat on top, label below, source under
  const stats = [
    { n: "12,400+",  l: "Reddit threads on AI-deploy / hosting / auth",     src: "r/cursor · r/ClaudeAI · 90d" },
    { n: "~62%",     l: "of indie-hacker wish-lists cite 'production stack'", src: "Indie Hackers survey 2025" },
    { n: "$2.4B",    l: "AI-coding tools ARR Q4 '25",                          src: "a16z state of AI, GP reports" },
    { n: "310K",     l: "AI-coder paid seats in English-first markets",        src: "Cursor / Lovable / Bolt filings" },
  ];
  const cw = (W - 1.20) / 4;
  stats.forEach((st, i) => {
    const cx = 0.70 + i * cw;
    s.addText(st.n, {
      x: cx, y: stripY + 0.45, w: cw - 0.15, h: 0.40,
      fontSize: 20, fontFace: FONT_HEAD, bold: true, color: C.indigo,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(st.l, {
      x: cx, y: stripY + 0.80, w: cw - 0.15, h: 0.20,
      fontSize: 10, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(st.src, {
      x: cx, y: stripY + 1.00, w: cw - 0.15, h: 0.18,
      fontSize: 8.5, fontFace: FONT_MONO, color: C.muted,
      align: "left", valign: "middle", margin: 0,
    });
  });

  // ── Three persona plateau cards ────────────────────────────────────
  const cardY = 4.25;
  const cardH = 2.15;
  const cardW = (W - 1.1 - 0.4) / 3;
  const personas = [
    {
      label: "PERSONAL TRAINER",
      title: "A meal-prep app for clients.",
      stack: "prepsheet · prototype",
      tags: ["Mon", "Tue", "Wed", "Thu"],
      detail: "› 500g chicken · 60g rice · 80g spinach",
      warn: "⚠ stripe · no API key configured",
      plateau: "Where they plateau: generates meal plans. Cannot bill, cannot send weekly emails, no mobile build.",
    },
    {
      label: "CONSULTANT",
      title: "An interactive framework demo.",
      stack: "strategy-canvas · demo",
      tags: ["Market — H1", "Audience — H2"],
      detail: "Wedge — H3        ·        Moat — H4",
      warn: "⚠ no auth · no billing",
      plateau: "Where they plateau: slick interaction. No login, no usage limits, no way to charge the £5K it's worth.",
    },
    {
      label: "DESIGNER",
      title: "The tool they always wanted.",
      stack: "palette · designer-demo",
      tags: ["■", "■", "■", "■"],
      detail: "PrimaryButton — 14px / 8px radius",
      warn: "⚠ no signed iOS build",
      plateau: "Where they plateau: lovely UI. No real database, no auth, no signed mobile build to demo on a phone.",
    },
  ];
  personas.forEach((p, i) => {
    const cx = 0.55 + i * (cardW + 0.2);
    card(s, cx, cardY, cardW, cardH, { fill: C.bgCard, stroke: C.border });
    eyebrow(s, cx + 0.25, cardY + 0.15, cardW - 0.5, p.label);
    s.addText(p.title, {
      x: cx + 0.25, y: cardY + 0.42, w: cardW - 0.5, h: 0.4,
      fontSize: T.cardTitleSm.size, fontFace: FONT_HEAD, bold: true, color: C.ink,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(p.warn, {
      x: cx + 0.25, y: cardY + 0.95, w: cardW - 0.5, h: 0.35,
      fontSize: 10, fontFace: FONT_MONO, color: C.red,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(p.plateau, {
      x: cx + 0.25, y: cardY + 1.35, w: cardW - 0.5, h: 0.9,
      fontSize: 11.5, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "top", margin: 0,
    });
  });

  // Bottom strapline
  s.addText([
    { text: "Every persona above ships a working-looking thing. ", options: { color: C.ink } },
    { text: "None of them ships a business. ~310K of them, £56M+ TAM today.", options: { color: C.indigo, bold: true } },
  ], {
    x: 0.55, y: 6.50, w: W - 1.1, h: 0.30,
    fontSize: 13, fontFace: FONT_BODY,
    align: "left", valign: "middle",
  });

  footer(s, 2);
}

// ─── SLIDE 3 — The Solution (per-layer coloring) ────────────────────────
function slide3() {
  const s = pres.addSlide();
  bg(s);
  header(s, 3, "THE SOLUTION");

  title2(s, 0.55, 1.05, W - 1.1, ["We give the AI tool", "a real SaaS to build on."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Customer signs up. In ~60 seconds they have a sandbox at slug.hatchik.com with auth, Postgres, payments, mail, mobile and a private GitHub repo all wired. Their AI tool reads the handoff file, pushes to the repo, we redeploy in ~30 seconds.");

  // ── Stack diagram: each layer same colour, differentiated per row ──
  const stackY = 2.95;
  const stackH = 3.30;
  card(s, 0.55, stackY, W - 1.1, stackH, { fill: C.bgCard, stroke: C.border });

  const layers = [
    { label: "TENANT EDGE",       pills: ["slug.hatchik.com", "Caddy · TLS", "Cloudflare", "Turnstile"],                 fill: "DCEBFF", color: "1E3A8A" }, // sky
    { label: "APP LAYER",         pills: ["Next.js · web", "Capacitor · iOS + Android", "AI_CONTEXT.md handoff"],         fill: "E8E5FB", color: C.indigoDeep }, // indigo
    { label: "DATA + IDENTITY",   pills: ["Postgres · Supabase", "Auth · magic-link", "Storage", "Realtime"],             fill: "D7F5E5", color: "065F46" }, // green
    { label: "BUSINESS PLUMBING", pills: ["Stripe · checkout", "Paddle · MoR", "Resend · mail", "Infomaniak · 5 inbox"],  fill: "FEF0CC", color: "92400E" }, // amber
    { label: "OPS + DELIVERY",    pills: ["Private GitHub repo", "Push-to-deploy webhook", "Status page", "Snapshot + restore"], fill: "FFDFE3", color: "9F1239" }, // rose
  ];
  const rowH = 0.58;
  const rowGap = 0.06;
  const labelW = 2.0;
  const pillsLeft = 0.85 + labelW + 0.15;
  const pillsAreaW = W - 1.1 - 0.6 - labelW - 0.15;
  layers.forEach((L, i) => {
    const y = stackY + 0.30 + i * (rowH + rowGap);
    // Row label
    s.addText(L.label, {
      x: 0.85, y, w: labelW, h: rowH,
      fontSize: 12.5, fontFace: FONT_MONO, color: C.ink, bold: true,
      align: "left", valign: "middle", charSpacing: 3, margin: 0,
    });
    // Pills in layer fill colour
    const n = L.pills.length;
    const gap = 0.15;
    const pw = (pillsAreaW - gap * (n - 1)) / n;
    L.pills.forEach((p, j) => {
      const px = pillsLeft + j * (pw + gap);
      pill(s, px, y + 0.02, pw, rowH - 0.04, p, {
        fill: L.fill, color: L.color, size: T.pillLg.size, charSpacing: 2,
      });
    });
  });

  // Bottom strapline
  s.addText([
    { text: "Customer goes from \"looks working\" to \"real product\" ", options: { color: C.ink } },
    { text: "without learning infrastructure.", options: { color: C.indigo, bold: true } },
  ], {
    x: 0.55, y: 6.50, w: W - 1.1, h: 0.30,
    fontSize: 13, fontFace: FONT_BODY,
    align: "left", valign: "middle",
  });

  footer(s, 3);
}

// ─── SLIDE 4 — Today / Gap / Tomorrow (moved from old #31) ──────────────
function slide4() {
  const s = pres.addSlide();
  bg(s);
  header(s, 4, "WHERE HATCHIK PLAYS");

  title2(s, 0.55, 1.05, W - 1.1, ["Available today.", "The gap. Tomorrow's opportunity."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Three points on the founder's value chain. Hatchik owns the middle today, and earns the right to compound into the third.");

  // Three vertically stacked frames with founder's Idea → step labels
  const fy0 = 3.05;
  const fh = 1.20;
  const gap = 0.18;
  const frames = [
    {
      eyebrow: "AVAILABLE TODAY  ·  NOT US",
      title: "Idea  →  Prototype.",
      tools: "Cursor · Lovable · Bolt · Claude Code",
      body:  "Non-technical founder uses AI to ship a working-looking prototype. Hot category, not where we play.",
      tag:   "We meet you AFTER this step.",
      dark: false, accent: false,
    },
    {
      eyebrow: "THE GAP HATCHIK ADDRESSES  ·  TODAY (LIVE)",
      title: "Prototype  →  Sellable product.",
      tools: "Hatchik substrate",
      body:  "Auth, Postgres, payments, mail, mobile, MoR billing, GitHub repo. Day-one SaaS. Sign up, pay, get receipts, ship mobile.",
      tag:   "LAUNCH £14/MO  ·  GROWTH £39/MO",
      dark: true, accent: true,
    },
    {
      eyebrow: "HATCHIK'S OPPORTUNITY TOMORROW  ·  Y2 WATCHLIST",
      title: "Product  →  Real business.",
      tools: "Hatchik+ end-user services",
      body:  "White-labeled AI agents the customer offers to THEIR end users — built on the substrate already running.",
      tag:   "Per-service or % of revenue.",
      dark: false, accent: false,
    },
  ];
  // Tighter spacing so third frame doesn't clip footer
  const fhTight = 1.15;
  const gapTight = 0.15;
  frames.forEach((f, i) => {
    const y = 2.85 + i * (fhTight + gapTight);
    const fillC = f.dark ? C.dark : C.bgCard;
    const strokeC = f.dark ? "26305A" : (f.accent ? C.indigo : C.border);
    card(s, 0.55, y, W - 1.1, fhTight, { fill: fillC, stroke: strokeC, lineW: f.accent ? 1.2 : 0.75 });
    s.addText(f.eyebrow, {
      x: 0.85, y: y + 0.15, w: 2.6, h: 0.32,
      fontSize: 10.5, fontFace: FONT_MONO,
      color: f.dark ? C.indigoMid : C.muted,
      align: "left", valign: "middle", charSpacing: 3, margin: 0,
    });
    s.addText(f.title, {
      x: 3.55, y: y + 0.10, w: 5.6, h: 0.40,
      fontSize: 18, fontFace: FONT_HEAD, bold: true,
      color: f.dark ? C.white : C.ink,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(f.tools, {
      x: 3.55, y: y + 0.52, w: 5.6, h: 0.28,
      fontSize: 10.5, fontFace: FONT_MONO,
      color: f.dark ? C.indigoMid : C.muted,
      align: "left", valign: "middle", charSpacing: 2, margin: 0,
    });
    s.addText(f.body, {
      x: 3.55, y: y + 0.78, w: 5.6, h: 0.35,
      fontSize: 11, fontFace: FONT_BODY,
      color: f.dark ? "C7CCE8" : C.ink2,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(f.tag, {
      x: 9.3, y: y + 0.40, w: W - 1.1 - 9.3 + 0.55 - 0.3, h: 0.40,
      fontSize: 11, fontFace: FONT_MONO, bold: f.accent,
      color: f.accent ? (f.dark ? C.indigoMid : C.indigo) : (f.dark ? C.indigoMid : C.ink2),
      align: "right", valign: "middle", charSpacing: 2, margin: 0,
    });
  });

  footer(s, 4);
}

// ─── SLIDE 5 — Why Now (with logos, relevant bars) ──────────────────────
function slide5() {
  const s = pres.addSlide();
  bg(s, true);
  header(s, 5, "WHY NOW", true);
  title2(s, 0.55, 1.05, W - 1.1, ["Not a 3-year SaaS.", "A 12–18 month sprint."], true);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "AI coding tools are hyper-growing right now — not gradually, not eventually. Someone is going to own the production substrate every Cursor, Lovable and Bolt user graduates to. Founders who waited on Cursor's 2023 curve are not catching it in 2026.", true);

  const cy = 2.85;
  const ch = 3.65;
  const cw = (W - 1.1 - 0.4) / 3;

  // Helper: text-logo (since we can't ship raster logos) — distinctive type treatments
  const companies = [
    {
      logoText: "cursor", logoFont: FONT_HEAD, logoColor: C.white, logoSize: 36,
      sub: "2023 → 2025",
      big: "1M+", label: "paid users in ~24 months",
      bars: [10, 18, 32, 55, 80, 100], // monthly run-rate approx in 100K scale → 1M peak
      note: "~40–50%/month compounding through the hype peak.",
      barUnits: "PAID USERS (×100K)",
    },
    {
      logoText: "lovable", logoFont: FONT_HEAD, logoColor: C.white, logoSize: 36,
      sub: "2024 → 2025",
      big: "$5M+", label: "MRR in ~12 months",
      bars: [0.3, 0.6, 1.2, 2.4, 3.8, 5.0],
      note: "~50K paying users from a standing start.",
      barUnits: "MRR ($M)",
    },
    {
      logoText: "bolt.new", logoFont: FONT_HEAD, logoColor: C.white, logoSize: 34,
      sub: "2024",
      big: "1M", label: "users in <12 months",
      bars: [50, 120, 240, 450, 720, 1000], // K users
      note: "Peak weeks hit four-figure signups per day.",
      barUnits: "TOTAL USERS (K)",
    },
  ];
  companies.forEach((co, i) => {
    const cx = 0.55 + i * (cw + 0.2);
    card(s, cx, cy, cw, ch, { fill: C.darkCard, stroke: "26305A" });

    // Logo-style name (big, brand-mono treatment)
    s.addText(co.logoText, {
      x: cx + 0.3, y: cy + 0.25, w: cw - 0.6, h: 0.6,
      fontSize: co.logoSize, fontFace: co.logoFont, bold: true, color: co.logoColor,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(co.sub, {
      x: cx + 0.3, y: cy + 0.92, w: cw - 0.6, h: 0.3,
      fontSize: T.cardEyebrow.size, fontFace: FONT_MONO, color: C.indigoMid,
      align: "left", valign: "middle", charSpacing: 3, margin: 0,
    });
    // Big stat
    s.addText(co.big, {
      x: cx + 0.3, y: cy + 1.25, w: cw - 0.6, h: 0.90,
      fontSize: 48, fontFace: FONT_HEAD, bold: true, color: C.indigoMid,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(co.label, {
      x: cx + 0.3, y: cy + 2.10, w: cw - 0.6, h: 0.28,
      fontSize: 11, fontFace: FONT_BODY, color: "C7CCE8",
      align: "left", valign: "middle", margin: 0,
    });

    // Bar chart (relevant, scaled to data)
    const barY0 = cy + 2.45;
    const barH = 0.55;
    const max = Math.max(...co.bars);
    const barW = (cw - 0.6 - 0.20) / co.bars.length - 0.05;
    co.bars.forEach((v, j) => {
      const h = (v / max) * barH;
      const bx = cx + 0.30 + j * (barW + 0.05);
      s.addShape("rect", {
        x: bx, y: barY0 + (barH - h), w: barW, h,
        fill: { color: j === co.bars.length - 1 ? C.indigoMid : "5F6BB8" },
        line: { type: "none" },
      });
    });
    s.addText(co.note, {
      x: cx + 0.3, y: cy + ch - 0.60, w: cw - 0.6, h: 0.50,
      fontSize: 10, fontFace: FONT_BODY, color: "C7CCE8",
      align: "left", valign: "top", margin: 0,
    });
  });

  // Bottom strapline
  s.addText([
    { text: "This window is now. ", options: { color: C.white } },
    { text: "The infrastructure layer is unowned.", options: { color: C.indigoMid, bold: true } },
  ], {
    x: 0.55, y: 6.60, w: W - 1.1, h: 0.30,
    fontSize: 13, fontFace: FONT_BODY,
    align: "left", valign: "middle",
  });

  footer(s, 5, true);
}

// ─── SLIDE 6 — Product Flow (restructured per feedback) ─────────────────
function slide6() {
  const s = pres.addSlide();
  bg(s);
  header(s, 6, "PRODUCT FLOW");

  title2(s, 0.55, 1.05, W - 1.1, ["From wizard to live.", "One sitting."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Start: founder runs the website wizard while chatting with the AI. Output: live URL + repo + an AI_CONTEXT.md handoff. Then the AI pushes; we redeploy.");

  const top = 2.95;
  const bottom = H - 0.85;
  const colH = bottom - top;
  const colGap = 0.20;
  const colW = (W - 1.1 - 2 * colGap) / 3;

  // ── COLUMN 1 — START (stacked wizard + AI chat) ───────────────────
  const c1x = 0.55;
  const halfGap = 0.15;
  const halfH = (colH - halfGap) / 2;

  function flowCard(x, y, w, h, eyebrowTxt, titleTxt, termLines, dark) {
    card(s, x, y, w, h, { fill: dark ? C.dark : C.bgCard, stroke: dark ? "26305A" : C.border });
    eyebrow(s, x + 0.20, y + 0.12, w - 0.40, eyebrowTxt, dark);
    s.addText(titleTxt, {
      x: x + 0.20, y: y + 0.38, w: w - 0.40, h: 0.32,
      fontSize: 13.5, fontFace: FONT_HEAD, bold: true, color: dark ? C.white : C.ink,
      align: "left", valign: "top", margin: 0,
    });
    termBlock(s, x + 0.20, y + 0.78, w - 0.40, h - 0.90, termLines,
      { fontSize: 9, fill: dark ? C.darkCard : C.dark });
  }

  flowCard(c1x, top, colW, halfH,
    "01 · WIZARD AT /START", "Founder kicks it off.",
    [
      { text: "hatchik.com/start", color: C.indigoMid },
      { text: "idea › PrepSheet —", color: C.white },
      { text: "       meals for PT clients", color: C.white },
      { text: "plan › Sandbox (free)", color: C.green },
      { text: "[ Start → ]", color: C.indigoMid, bold: true },
    ], false);

  flowCard(c1x, top + halfH + halfGap, colW, halfH,
    "01b · AI CONVERSATION", "In their AI tool.",
    [
      { text: "claude · MCP connected", color: C.indigoMid },
      { text: "you › build meal-builder", color: C.white },
      { text: "claude › reading AI_CONTEXT…", color: C.green },
      { text: "claude › apps/web/...", color: C.white },
      { text: "         feed-builder.tsx", color: C.white },
    ], false);

  // ── COLUMN 2 — OUTPUT SUMMARY ─────────────────────────────────────
  const c2x = c1x + colW + colGap;
  card(s, c2x, top, colW, colH, { fill: C.indigoLight, stroke: C.indigoSoft });
  eyebrow(s, c2x + 0.20, top + 0.15, colW - 0.40, "02 · SANDBOX LIVE  ·  ~60S");
  s.addText("What they get.", {
    x: c2x + 0.20, y: top + 0.42, w: colW - 0.40, h: 0.36,
    fontSize: 14.5, fontFace: FONT_HEAD, bold: true, color: C.ink,
    align: "left", valign: "top", margin: 0,
  });
  // 7 mini tiles, sized to fit
  const tile = [
    { icon: "🌐", t: "Live URL",       d: "prepsheet.hatchik.com · TLS" },
    { icon: "🔐", t: "Auth + sign-up", d: "Magic-link + email verify" },
    { icon: "🗄",  t: "Database",       d: "Postgres + nightly backups" },
    { icon: "✉",  t: "Mailbox",        d: "alex@prepsheet.app, DKIM" },
    { icon: "💳", t: "Test payments",  d: "Stripe sandbox keys" },
    { icon: "📱", t: "Mobile shells",  d: "iOS + Android, ~8 min" },
    { icon: "✨", t: "£0.50 AI credit",d: "First feature, no API key" },
  ];
  const tileY0 = top + 0.95;
  const tileH = (colH - 1.05) / tile.length;
  tile.forEach((tt, i) => {
    const yy = tileY0 + i * tileH;
    s.addText(tt.icon, {
      x: c2x + 0.20, y: yy, w: 0.35, h: tileH,
      fontSize: 14, fontFace: FONT_BODY, color: C.ink,
      align: "center", valign: "middle", margin: 0,
    });
    s.addText(tt.t, {
      x: c2x + 0.55, y: yy + 0.02, w: colW - 0.75, h: tileH * 0.50,
      fontSize: 10.5, fontFace: FONT_HEAD, bold: true, color: C.ink,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(tt.d, {
      x: c2x + 0.55, y: yy + tileH * 0.45, w: colW - 0.75, h: tileH * 0.50,
      fontSize: 8.5, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "middle", margin: 0,
    });
  });

  // ── COLUMN 3 — AI_CONTEXT + REDEPLOY ──────────────────────────────
  const c3x = c2x + colW + colGap;
  flowCard(c3x, top, colW, halfH,
    "03 · HANDOFF FILE", "AI reads the spec.",
    [
      { text: "AI_CONTEXT.md  ~14s", color: C.indigoMid },
      { text: "# Stack", color: C.green },
      { text: "Next.js · Supabase", color: C.white },
      { text: "Stripe · Resend", color: C.white },
      { text: "# Paths, env, conventions", color: C.green },
    ], false);

  flowCard(c3x, top + halfH + halfGap, colW, halfH,
    "04 · PUSH-TO-DEPLOY", "Live in ~30s.",
    [
      { text: "webhook sha=a12c4e1", color: C.indigoMid },
      { text: "compose rebuilt 2/9", color: C.white },
      { text: "→ live  ·  28s total", color: C.green, bold: true },
    ], true);

  footer(s, 6);
}

// ─── SLIDE 7 — What's Wired (email-style + merged with old #18) ─────────
function slide7() {
  const s = pres.addSlide();
  bg(s);
  header(s, 7, "WHAT'S WIRED  ·  SHIPPED");

  title2(s, 0.55, 1.05, W - 1.1, ["Everything they need.", "Already shipping."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "What the founder gets from minute one — and what we've built solo + AI-augmented in ~3 weeks. Cross-referenced against the live customer runbook.");

  // Email-style tile grid (4 columns × 3 rows) using simple iconic glyphs
  const gridY = 2.85;
  const gridH = 3.85;
  const cols = 4, rows = 3;
  const gw = (W - 1.1 - 0.25 * (cols - 1)) / cols;
  const gh = (gridH - 0.18 * (rows - 1)) / rows;

  const tiles = [
    { icon: "🌐", title: "Live website",        d: "A working URL anyone can visit. Sign-in baked in." },
    { icon: "🔐", title: "Sign-up & sign-in",    d: "Magic-link + email verification — out of the box." },
    { icon: "🗄",  title: "Database",            d: "Postgres + RLS-scoped, nightly backups built in." },
    { icon: "📁", title: "File storage",         d: "For photos, documents, anything your app uploads." },
    { icon: "✉",  title: "Email sending",        d: "Sign-up + password-reset emails, DKIM signed." },
    { icon: "💳", title: "Test + live payments", d: "Stripe sandbox keys. Switch to live when ready." },
    { icon: "📬", title: "Mailboxes",            d: "alex@yours.app, 3-5 inboxes on Infomaniak." },
    { icon: "📱", title: "Mobile app shells",    d: "iOS + Android, ready to build — ~8–15 min." },
    { icon: "🐙", title: "Private GitHub repo",  d: "Per-tenant. Code stays yours. Webhook deploys." },
    { icon: "🚀", title: "Push-to-deploy",       d: "Your AI commits, we redeploy in ~30 seconds." },
    { icon: "📈", title: "Status + analytics",   d: "status.hatchik.com · cohort dashboard." },
    { icon: "🛡", title: "Abuse + lifecycle",    d: "Turnstile · geo throttle · idle archive + restore." },
  ];
  tiles.forEach((t, i) => {
    const r = Math.floor(i / cols), c = i % cols;
    const tx = 0.55 + c * (gw + 0.25);
    const ty = gridY + r * (gh + 0.18);
    card(s, tx, ty, gw, gh, { fill: C.bgCard, stroke: C.border });
    // colored icon chip top-left
    s.addShape("roundRect", {
      x: tx + 0.20, y: ty + 0.18, w: 0.42, h: 0.42, rectRadius: 0.10,
      fill: { color: C.indigoSoft }, line: { type: "none" },
    });
    s.addText(t.icon, {
      x: tx + 0.20, y: ty + 0.18, w: 0.42, h: 0.42,
      fontSize: 14, align: "center", valign: "middle", margin: 0,
    });
    s.addText("LIVE", {
      x: tx + gw - 0.8, y: ty + 0.22, w: 0.65, h: 0.3,
      fontSize: 9, fontFace: FONT_MONO, color: C.green, bold: true,
      align: "right", valign: "middle", charSpacing: 2.5, margin: 0,
    });
    s.addText(t.title, {
      x: tx + 0.20, y: ty + 0.65, w: gw - 0.4, h: 0.36,
      fontSize: 13.5, fontFace: FONT_HEAD, bold: true, color: C.ink,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(t.d, {
      x: tx + 0.20, y: ty + 1.02, w: gw - 0.4, h: gh - 1.10,
      fontSize: 9.5, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "top", margin: 0,
    });
  });

  // (Bottom strapline removed — redundant with title and subtitle)
  footer(s, 7);
}

// ─── SLIDE 8 — Market (quarter-circle waves) ─────────────────────────────
function slide8() {
  const s = pres.addSlide();
  bg(s);
  header(s, 8, "MARKET  ·  TAM / SAM / SOM");

  // ── Quarter-arc visual on left (anchored at bottom-left)
  // Draw concentric circles, masks BEFORE text, then title/subtitle on top
  const arcCx = 0.55, arcCy = H - 0.85; // bottom-left anchor (origin)
  const arcSizes = [
    { r: 4.2, label: "TAM",  v: "~310K", sub: "users · £56M/yr today", fill: C.indigoSoft },
    { r: 2.9, label: "SAM",  v: "~190K", sub: "users · £34M → £120M/yr by 2027", fill: "D0CAFB" },
    { r: 1.7, label: "SOM",  v: "5–15K", sub: "paying customers by M18", fill: C.indigo },
  ];
  arcSizes.forEach((a) => {
    s.addShape("ellipse", {
      x: arcCx - a.r, y: arcCy - a.r, w: a.r * 2, h: a.r * 2,
      fill: { color: a.fill }, line: { type: "none" },
    });
  });
  // Single mask: cover top half of visual area to leave only quarter-arc in bottom-right of bottom-left quadrant
  // Bottom-left quadrant of each circle is what we want (y > arcCy slice... no). We want the upper-right
  // quarter visible — anchor is bottom-left, so the visible quarter is x>arcCx AND y<arcCy
  // Mask the OTHER three quadrants:
  // 1) left of arcCx (x < arcCx) — but arcCx=0.55, so just mask 0..0.55
  s.addShape("rect", { x: 0, y: 0, w: 0.55, h: H, fill: { color: C.bg }, line: { type: "none" } });
  // 2) below arcCy
  s.addShape("rect", { x: 0, y: arcCy, w: W, h: H - arcCy, fill: { color: C.bg }, line: { type: "none" } });

  // Now title + subtitle on top
  title2(s, 0.55, 1.05, W - 1.1, ["TAM, SAM, SOM.", "Sized from public numbers."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Anchored on AI-coder tool disclosures from Cursor, Lovable, and Bolt. Conservative blended ARPU. SOM scoped to the 12–18 month sprint window.");

  // Labels for arcs — anchor near outer edge of each arc on its rim
  s.addText("TAM", {
    x: 0.85, y: arcCy - 3.95, w: 1.0, h: 0.3,
    fontSize: 11, fontFace: FONT_MONO, color: C.ink2, charSpacing: 3,
    align: "left", valign: "middle", bold: true,
  });
  s.addText("SAM", {
    x: 0.85, y: arcCy - 2.65, w: 1.0, h: 0.3,
    fontSize: 11, fontFace: FONT_MONO, color: C.indigoDeep, charSpacing: 3,
    align: "left", valign: "middle", bold: true,
  });
  s.addText("SOM", {
    x: 0.85, y: arcCy - 1.45, w: 1.0, h: 0.3,
    fontSize: 11, fontFace: FONT_MONO, color: C.white, charSpacing: 3,
    align: "left", valign: "middle", bold: true,
  });

  // ── Right side: stacked metric cards ──
  const stkX = 6.6, stkW = W - 6.6 - 0.55;
  const stkY0 = 2.85, stkH = 1.05, stkG = 0.18;
  arcSizes.forEach((a, i) => {
    const ty = stkY0 + i * (stkH + stkG);
    const dark = i === 2;
    card(s, stkX, ty, stkW, stkH, { fill: dark ? C.dark : C.bgCard, stroke: dark ? "26305A" : C.border });
    s.addText(`${a.label}  ·  ${(["TOTAL ADDRESSABLE","SERVICEABLE","12–18 MONTH SPRINT"])[i]}`, {
      x: stkX + 0.3, y: ty + 0.10, w: stkW - 0.6, h: 0.24,
      fontSize: 10, fontFace: FONT_MONO, color: dark ? C.indigoMid : C.muted,
      align: "left", valign: "middle", charSpacing: 3, margin: 0,
    });
    s.addText(a.v, {
      x: stkX + 0.3, y: ty + 0.32, w: stkW - 0.6, h: 0.48,
      fontSize: 30, fontFace: FONT_HEAD, bold: true, color: dark ? C.white : C.ink,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(a.sub, {
      x: stkX + 0.3, y: ty + 0.75, w: stkW - 0.6, h: 0.28,
      fontSize: 10.5, fontFace: FONT_BODY, color: dark ? "C7CCE8" : C.ink2,
      align: "left", valign: "middle", margin: 0,
    });
  });

  // Source caption (just above footer line)
  s.addText("TAM/SAM from marketing plan §2-3  ·  Sprint scenarios anchored on Cursor / Lovable / Bolt curves.", {
    x: 0.55, y: 6.55, w: W - 1.1, h: 0.22,
    fontSize: 9, fontFace: FONT_MONO, color: C.muted,
    align: "left", valign: "middle", margin: 0,
  });

  footer(s, 8);
}

// ─── SLIDE 9 — Customer Profile (reworked personas) ──────────────────────
function slide9() {
  const s = pres.addSlide();
  bg(s);
  header(s, 9, "CUSTOMER PROFILE");

  title2(s, 0.55, 1.05, W - 1.1, ["Three faces.", "One reason to pay."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Founders with an idea and a thin tech stomach. Light-to-power AI usage, time-poor, looking for a working product — not another tool to learn.");

  // ── Compact stat strip (smaller per feedback) ───────────────────
  const stripY = 2.85;
  const stripH = 0.65;
  const sw = (W - 1.1) / 4;
  const stats = [
    { n: "40%",     l: "of UK adults running a side-hustle (Henley 2023)" },
    { n: "3M+",     l: "paid AI-coder seats globally (Cursor / Claude / Lovable)" },
    { n: "£10-40/mo", l: "typical AI subscription our ICP already pays" },
    { n: "50+ hrs", l: "stitching infra together solo (indie-hacker reports)" },
  ];
  stats.forEach((st, i) => {
    const sx = 0.55 + i * sw;
    s.addShape("roundRect", {
      x: sx + 0.05, y: stripY, w: sw - 0.1, h: stripH, rectRadius: 0.12,
      fill: { color: C.indigoLight }, line: { color: C.indigoSoft, width: 0.5 },
    });
    s.addText(st.n, {
      x: sx + 0.25, y: stripY, w: 1.4, h: stripH,
      fontSize: 18, fontFace: FONT_HEAD, bold: true, color: C.indigo,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(st.l, {
      x: sx + 1.7, y: stripY, w: sw - 1.85, h: stripH,
      fontSize: 10, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "middle", margin: 0,
    });
  });

  // ── Three persona cards (bigger, more space) ─────────────────────
  const cy = 3.70;
  const ch = 3.20;
  const cw = (W - 1.1 - 0.4) / 3;
  const personas = [
    {
      initial: "A",
      name: "Aisha, 31",
      flag: "🇬🇧",
      tag: "UK  ·  PERSONAL TRAINER",
      profile: "Personal trainer · Manchester · £42K + freelance clients",
      toolsHeader: "TOOLS  ·  LIGHT",
      tools: "ChatGPT (mostly for captions)",
      idea: "Meal-prep app her PT clients can use between sessions",
      pain: "Doesn't know what 'auth' means. Just wants the AI to make it work.",
      quote: "\"I tried Lovable. It looks like an app — but I can't actually charge anyone.\"",
      accent: C.indigo,
    },
    {
      initial: "T",
      name: "Tom, 39",
      flag: "🇺🇸",
      tag: "US  ·  CORPORATE CONSULTANT",
      profile: "Strategy consultant · Chicago · $145K + side-IP",
      toolsHeader: "TOOLS  ·  WORK-DRIVEN",
      tools: "Copilot · ChatGPT (corporate)",
      idea: "Productised version of his client diagnostic framework",
      pain: "Knows enough to be dangerous. Won't spend Saturdays plumbing infra.",
      quote: "\"I have one weekend a month for this. Either it ships or it doesn't.\"",
      accent: C.indigo,
    },
    {
      initial: "Z",
      name: "Zayd, 34",
      flag: "🇦🇪",
      tag: "GCC  ·  AI-TOOL POWER USER",
      profile: "Indie hacker / ex-engineer · Dubai · self-funded",
      toolsHeader: "TOOLS  ·  POWER",
      tools: "Cursor + Claude Code · GitHub",
      idea: "Booking SaaS for SE-Asian wedding & event vendors",
      pain: "Comfortable in the stack — burned out by glue work he could automate.",
      quote: "\"I want Paddle MoR + a real GitHub repo from day one — not a walled garden.\"",
      accent: C.indigo,
    },
  ];

  personas.forEach((p, i) => {
    const cx = 0.55 + i * (cw + 0.2);
    card(s, cx, cy, cw, ch, { fill: C.bgCard, stroke: C.border });

    // Avatar circle with initial
    s.addShape("ellipse", {
      x: cx + 0.25, y: cy + 0.25, w: 0.7, h: 0.7,
      fill: { color: C.indigoSoft }, line: { type: "none" },
    });
    s.addText(p.initial, {
      x: cx + 0.25, y: cy + 0.25, w: 0.7, h: 0.7,
      fontSize: 24, fontFace: FONT_HEAD, bold: true, color: p.accent,
      align: "center", valign: "middle", margin: 0,
    });
    // Name + tag
    s.addText(p.name, {
      x: cx + 1.10, y: cy + 0.25, w: cw - 1.3, h: 0.35,
      fontSize: 19, fontFace: FONT_HEAD, bold: true, color: C.ink,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(`${p.flag}   ${p.tag}`, {
      x: cx + 1.10, y: cy + 0.60, w: cw - 1.3, h: 0.3,
      fontSize: 10.5, fontFace: FONT_MONO, color: C.muted,
      align: "left", valign: "middle", charSpacing: 3, margin: 0,
    });

    // Divider
    s.addShape("line", { x: cx + 0.25, y: cy + 1.10, w: cw - 0.5, h: 0, line: { color: C.border, width: 0.5 }});

    // Sections (eyebrow + body), block-style with wrapping room
    const rows = [
      { lab: "PROFILE", val: p.profile },
      { lab: p.toolsHeader, val: p.tools },
      { lab: "IDEA",    val: p.idea },
      { lab: "PAIN",    val: p.pain },
    ];
    rows.forEach((r, j) => {
      const ry = cy + 1.15 + j * 0.36;
      s.addText(r.lab, {
        x: cx + 0.25, y: ry, w: 1.25, h: 0.32,
        fontSize: 8.5, fontFace: FONT_MONO, color: C.muted, charSpacing: 2,
        align: "left", valign: "top", margin: 0,
      });
      s.addText(r.val, {
        x: cx + 1.50, y: ry, w: cw - 1.70, h: 0.34,
        fontSize: 9.5, fontFace: FONT_BODY, color: C.ink,
        align: "left", valign: "top", margin: 0,
      });
    });

    // Quote at bottom
    s.addText(p.quote, {
      x: cx + 0.25, y: cy + ch - 0.55, w: cw - 0.5, h: 0.45,
      fontSize: 10, fontFace: FONT_BODY, color: C.indigo, italic: true,
      align: "left", valign: "top", margin: 0,
    });
  });

  footer(s, 9);
}

// ─── SLIDE 10 — Competitive Landscape (egg in middle) ───────────────────
function slide10() {
  const s = pres.addSlide();
  bg(s);
  header(s, 10, "COMPETITIVE LANDSCAPE");

  title2(s, 0.55, 1.05, W - 1.1, ["Two layers exist.", "Nobody owns the seam."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "AI builders own chat → prototype. Infra players own code → runtime. The substrate-with-business-plumbing layer the AI-coder customer actually needs is unowned.");

  // Left box (AI builders, left-aligned), middle: hatchik egg, right box (Infra, right-aligned)
  const boxY = 3.05;
  const boxH = 3.40;
  const eggW = 2.7;
  const eggX = (W - eggW) / 2;
  const leftBoxW = eggX - 0.55 - 0.25;
  const rightBoxW = W - 0.55 - (eggX + eggW + 0.25);
  const rightBoxX = eggX + eggW + 0.25;

  // ── Left box (left-aligned) ──
  card(s, 0.55, boxY, leftBoxW, boxH, { fill: C.bgCard, stroke: C.border });
  eyebrow(s, 0.55 + 0.25, boxY + 0.15, leftBoxW - 0.5, "AI BUILDERS  ·  CHAT → PROTOTYPE");
  // Logo pills grid (bigger pills with brand-style text labels)
  const aiBuilders = [
    { name: "Lovable",     color: "EB4D8E" },
    { name: "Bolt.new",    color: "1A1A1A" },
    { name: "v0 (Vercel)", color: "0A0A0A" },
    { name: "Replit Agent",color: "F26207" },
    { name: "Pi App Studio",color:"3B82F6" },
    { name: "Bubble",      color: "0E2EFF" },
  ];
  const ay0 = boxY + 0.55;
  const ah = 0.42;
  const ag = 0.10;
  const acols = 2;
  const aw = (leftBoxW - 0.5 - ag * (acols - 1)) / acols;
  aiBuilders.forEach((b, i) => {
    const r = Math.floor(i / acols), c = i % acols;
    pill(s, 0.55 + 0.25 + c * (aw + ag), ay0 + r * (ah + ag), aw, ah, b.name, {
      fill: C.bgCard, stroke: C.border, color: b.color, size: 12, bold: true, charSpacing: 1, font: FONT_HEAD,
    });
  });
  // Below pills: limitation list
  const lim1Y = ay0 + 3 * (ah + ag) + 0.15;
  ["— Their AI, their chat, their runtime. No BYO.",
   "— Code in their walled garden. Export friction.",
   "— No auth · payments · mail. \"Deploy\" = preview."]
    .forEach((line, j) => {
      s.addText(line, {
        x: 0.55 + 0.25, y: lim1Y + j * 0.24, w: leftBoxW - 0.5, h: 0.22,
        fontSize: 10, fontFace: FONT_BODY, color: C.ink2,
        align: "left", valign: "middle", margin: 0,
      });
    });
  s.addText("Where Hatchik wins: substrate pre-wired · day-one SaaS.", {
    x: 0.55 + 0.25, y: boxY + boxH - 0.40, w: leftBoxW - 0.5, h: 0.30,
    fontSize: 10.5, fontFace: FONT_BODY, bold: true, color: C.indigo,
    align: "left", valign: "middle", margin: 0,
  });

  // ── Hatchik egg (centered, dark) ──
  const eggCardTop = boxY + 0.30;
  const eggCardH = boxH - 0.60;
  card(s, eggX, eggCardTop, eggW, eggCardH, { fill: C.dark, stroke: C.indigo, lineW: 1.5 });
  // Big white egg shape at top
  s.addShape("ellipse", {
    x: eggX + (eggW - 0.85) / 2, y: eggCardTop + 0.30, w: 0.85, h: 1.05,
    fill: { color: C.white }, line: { type: "none" },
  });
  s.addShape("rect", {
    x: eggX + (eggW - 0.40) / 2, y: eggCardTop + 0.90, w: 0.40, h: 0.04,
    fill: { color: C.dark }, line: { type: "none" },
  });
  s.addText("hatchik", {
    x: eggX, y: eggCardTop + 1.45, w: eggW, h: 0.40,
    fontSize: 18, fontFace: FONT_HEAD, bold: true, color: C.white,
    align: "center", valign: "middle", margin: 0,
  });
  s.addText("Hatchik sits where", {
    x: eggX + 0.20, y: eggCardTop + 2.00, w: eggW - 0.40, h: 0.30,
    fontSize: 12, fontFace: FONT_HEAD, bold: true, color: C.indigoMid,
    align: "center", valign: "middle", margin: 0,
  });
  s.addText("both groups stop.", {
    x: eggX + 0.20, y: eggCardTop + 2.30, w: eggW - 0.40, h: 0.30,
    fontSize: 12, fontFace: FONT_HEAD, bold: true, color: C.indigoMid,
    align: "center", valign: "middle", margin: 0,
  });

  // ── Right box (right-aligned) ──
  card(s, rightBoxX, boxY, rightBoxW, boxH, { fill: C.bgCard, stroke: C.border });
  eyebrow(s, rightBoxX + 0.25, boxY + 0.20, rightBoxW - 0.5, "INFRA · DEPLOY  ·  CODE → RUNTIME");
  const infra = [
    { name: "Vercel",  color: "0A0A0A" },
    { name: "Render",  color: "10B981" },
    { name: "Railway", color: "5B1AD9" },
    { name: "Fly.io",  color: "7B3AED" },
    { name: "Heroku",  color: "430098" },
    { name: "DO App Platform", color: "0080FF" },
  ];
  const iw = (rightBoxW - 0.5 - ag * (acols - 1)) / acols;
  infra.forEach((b, i) => {
    const r = Math.floor(i / acols), c = i % acols;
    pill(s, rightBoxX + 0.25 + c * (iw + ag), ay0 + r * (ah + ag), iw, ah, b.name, {
      fill: C.bgCard, stroke: C.border, color: b.color, size: 12, bold: true, charSpacing: 1, font: FONT_HEAD,
    });
  });
  ["— Deploys code. Auth · mail · payments are yours.",
   "— Built for engineers. Non-tech founder can't stitch.",
   "— Usage-based pricing hard for a learner to predict."]
    .forEach((line, j) => {
      s.addText(line, {
        x: rightBoxX + 0.25, y: lim1Y + j * 0.24, w: rightBoxW - 0.5, h: 0.22,
        fontSize: 10, fontFace: FONT_BODY, color: C.ink2,
        align: "left", valign: "middle", margin: 0,
      });
    });
  s.addText("Where Hatchik wins: BYO AI tool · real GitHub · real stack.", {
    x: rightBoxX + 0.25, y: boxY + boxH - 0.40, w: rightBoxW - 0.5, h: 0.30,
    fontSize: 10.5, fontFace: FONT_BODY, bold: true, color: C.indigo,
    align: "left", valign: "middle", margin: 0,
  });

  // (Footer caption removed for clarity)

  footer(s, 10);
}

// ─── SLIDE 11 — Where We Win (expanded wedges per feedback) ─────────────
function slide11() {
  const s = pres.addSlide();
  bg(s);
  header(s, 11, "WHERE WE WIN");

  title2(s, 0.55, 1.05, W - 1.1, ["Six things", "no competitor combines."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Each wedge solves a real founder cost — knowledge, time, money, or trust. Together: a path from idea to revenue without an engineering hire.");

  const wedges = [
    {
      n: "01", t: "One-click convenience.",
      d: "Wizard → live SaaS in ~60 seconds. No infra learning curve. No yak-shaving.",
      tag: "ZERO STITCHING",
    },
    {
      n: "02", t: "Full suite to monetise.",
      d: "Payments, MoR, email, mobile shells, status — every piece a real product needs, day one.",
      tag: "12 PIECES WIRED",
    },
    {
      n: "03", t: "Abstraction over complexity.",
      d: "Customer doesn't see Caddy, Docker, RLS, DKIM. They see a working product they can charge for.",
      tag: "EXPERT-OPTIONAL",
    },
    {
      n: "04", t: "Your AI tool, not ours.",
      d: "Cursor, Claude Code, Windsurf, Copilot — plug into the one your customer already trusts.",
      tag: "BYO AI",
    },
    {
      n: "05", t: "Your code, your repo, your VPS.",
      d: "Per-tenant private GitHub repo on day one. Want to leave? You already own everything.",
      tag: "ZERO LOCK-IN",
    },
    {
      n: "06", t: "Predictable cost, no surprise bills.",
      d: "Pass-through overage × 1.60. PAYG top-ups. AI credit included. No surprise £400 month.",
      tag: "FIXED + KNOWABLE",
    },
  ];

  const gy = 2.95;
  const gh = (H - 0.85 - gy - 0.20) / 2 - 0.15;
  const gw = (W - 1.1 - 0.4) / 3;
  wedges.forEach((wd, i) => {
    const r = Math.floor(i / 3), c = i % 3;
    const x = 0.55 + c * (gw + 0.2);
    const y = gy + r * (gh + 0.30);
    card(s, x, y, gw, gh, { fill: C.bgCard, stroke: C.border });
    s.addText(wd.n, {
      x: x + 0.30, y: y + 0.20, w: 0.8, h: 0.4,
      fontSize: 13, fontFace: FONT_MONO, color: C.indigo, charSpacing: 3, bold: true,
      align: "left", valign: "middle", margin: 0,
    });
    pill(s, x + gw - 1.85, y + 0.22, 1.6, 0.36, wd.tag, {
      fill: C.indigoSoft, color: C.indigoDeep, size: 9.5, charSpacing: 2, bold: true,
    });
    s.addText(wd.t, {
      x: x + 0.30, y: y + 0.70, w: gw - 0.6, h: 0.50,
      fontSize: T.cardTitleSm.size, fontFace: FONT_HEAD, bold: true, color: C.ink,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(wd.d, {
      x: x + 0.30, y: y + 1.30, w: gw - 0.6, h: gh - 1.40,
      fontSize: 11.5, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "top", margin: 0,
    });
  });

  footer(s, 11);
}

// ─── SLIDE 12 — Business Model (timeframes + funnel %) ──────────────────
function slide12() {
  const s = pres.addSlide();
  bg(s);
  header(s, 12, "BUSINESS MODEL");

  title2(s, 0.55, 1.05, W - 1.1, ["Three tiers.", "Paddle as MoR."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Sandbox is the frictionless entry — designed to convert. Launch is the working SaaS. Growth auto-promotes when traction earns it.");

  const tiers = [
    {
      label: "STAGE 0  ·  EVALUATION",
      name: "Sandbox",
      price: "£0",
      priceSub: "",
      cadence: "Free forever while active · 30-day idle auto-archive",
      when: "FRICTIONLESS  ·  1–2 DAYS AFTER AWARENESS",
      pct: "100% TOP-OF-FUNNEL",
      items: [
        "Sandbox at slug.hatchik.com",
        "Shared host (Hetzner CAX31)",
        "Private GitHub repo + AI handoff",
        "Capped one active per email",
        "£0.50 one-off AI credit",
      ],
      recommended: true,
      dark: false,
    },
    {
      label: "STAGE 1  ·  PRODUCTION  ·  SOLO",
      name: "Launch",
      price: "£89",
      priceSub: " +£14/mo",
      cadence: "Setup, then £14/mo annual or £17/mo monthly",
      when: "WITHIN 30 DAYS OF SIGN-UP",
      pct: "5% OF SANDBOXES",
      items: [
        "Production hosting + domain + mailboxes",
        "Shared host in customer region",
        "3 mailboxes (Infomaniak)",
        "BYO or registered domain",
        "Push-to-deploy + mobile builds",
        "£3/month AI credit included",
      ],
      recommended: false,
      dark: false,
    },
    {
      label: "STAGE 2  ·  PRODUCTION  ·  SCALE",
      name: "Growth",
      price: "£39",
      priceSub: "/mo",
      cadence: "Auto-graduates at customer's 15th end-user signup",
      when: "WITHIN 6 MONTHS OF LAUNCH",
      pct: "1% OF SANDBOXES",
      items: [
        "Dedicated VPS in your region",
        "Priority redeploy queue",
        "10 mailboxes + Supabase Studio",
        "Cohort analytics access",
        "Founder support window",
        "£10/month AI credit included",
      ],
      recommended: false,
      dark: true,
    },
  ];

  const cy = 2.85;
  const ch = 3.55;
  const cw = (W - 1.1 - 0.4) / 3;
  tiers.forEach((t, i) => {
    const x = 0.55 + i * (cw + 0.2);
    const dark = t.dark;
    card(s, x, cy, cw, ch, { fill: dark ? C.dark : C.bgCard, stroke: dark ? "26305A" : C.border });
    if (t.recommended) {
      // Recommended pill above card
      pill(s, x + cw - 1.7, cy - 0.20, 1.55, 0.40, "RECOMMENDED", {
        fill: C.indigo, color: C.white, size: 10, bold: true, charSpacing: 3,
      });
    }
    eyebrow(s, x + 0.25, cy + 0.20, cw - 0.5, t.label, dark);
    s.addText(t.name, {
      x: x + 0.25, y: cy + 0.45, w: cw - 0.5, h: 0.50,
      fontSize: 26, fontFace: FONT_HEAD, bold: true, color: dark ? C.white : C.ink,
      align: "left", valign: "top", margin: 0,
    });
    // Price line
    s.addText([
      { text: t.price, options: { color: dark ? C.white : C.ink, bold: true, fontSize: 36 } },
      { text: t.priceSub, options: { color: dark ? C.indigoMid : C.ink2, fontSize: 13 } },
    ], {
      x: x + 0.25, y: cy + 0.95, w: cw - 0.5, h: 0.55,
      fontFace: FONT_HEAD, align: "left", valign: "top", margin: 0,
    });
    s.addText(t.cadence, {
      x: x + 0.25, y: cy + 1.55, w: cw - 0.5, h: 0.28,
      fontSize: 10, fontFace: FONT_BODY, color: dark ? C.indigoMid : C.muted,
      align: "left", valign: "middle", margin: 0,
    });

    // Timeframe + funnel pills (NEW per feedback)
    pill(s, x + 0.25, cy + 1.85, cw - 0.5, 0.32, t.when, {
      fill: dark ? C.darkCard2 : C.indigoLight, color: dark ? C.indigoMid : C.indigoDeep,
      size: 9, bold: true, charSpacing: 2,
    });
    pill(s, x + 0.25, cy + 2.20, cw - 0.5, 0.32, t.pct, {
      fill: dark ? "1F2547" : "F5F3FE", color: dark ? "C7CCE8" : C.ink2,
      size: 9, charSpacing: 2,
    });

    // Bullet list (lighter per feedback, with bullet dots) — compact within card
    t.items.forEach((it, j) => {
      const ly = cy + 2.62 + j * 0.14;
      s.addShape("ellipse", {
        x: x + 0.30, y: ly + 0.06, w: 0.05, h: 0.05,
        fill: { color: dark ? C.indigoMid : C.muted }, line: { type: "none" },
      });
      s.addText(it, {
        x: x + 0.42, y: ly, w: cw - 0.62, h: 0.16,
        fontSize: 9, fontFace: FONT_BODY, color: dark ? "C7CCE8" : C.ink2,
        align: "left", valign: "middle", margin: 0,
      });
    });
  });

  // Bottom strapline
  s.addText("Pass-through overage at cost × 1.60 (40% net + Paddle fee). Mid-range Launch £11.55/mo · Growth £31.74/mo · Sandbox cost £0.30/mo.", {
    x: 0.55, y: 6.55, w: W - 1.1, h: 0.25,
    fontSize: 9.5, fontFace: FONT_MONO, color: C.muted,
    align: "left", valign: "middle", margin: 0,
  });

  footer(s, 12);
}

// ─── SLIDE 13 — GTM · Market (split from old 13) ─────────────────────────
function slide13() {
  const s = pres.addSlide();
  bg(s);
  header(s, 13, "GO-TO-MARKET  ·  AUDIENCES");

  title2(s, 0.55, 1.05, W - 1.1, ["Where they live.", "How we reach them."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Four addressable audiences. Each with channel, visual, and per-channel CAC. Combined ceiling sits well below blended LTV.");

  // ── CAC band pulled out ──
  const cacBoxY = 2.95;
  const cacBoxH = 0.75;
  card(s, 0.55, cacBoxY, W - 1.1, cacBoxH, { fill: C.indigoLight, stroke: C.indigoSoft });
  s.addText([
    { text: "BLENDED LTV ", options: { color: C.muted, fontSize: 11, fontFace: FONT_MONO, charSpacing: 3 } },
    { text: "£520   ", options: { color: C.ink, bold: true, fontSize: 22, fontFace: FONT_HEAD } },
    { text: "·  CAC CEILING (3:1) ", options: { color: C.muted, fontSize: 11, fontFace: FONT_MONO, charSpacing: 3 } },
    { text: "£175   ", options: { color: C.indigo, bold: true, fontSize: 22, fontFace: FONT_HEAD } },
    { text: "·  ALL BELOW-CEILING ACROSS CHANNELS", options: { color: C.ink2, fontSize: 11, fontFace: FONT_MONO, charSpacing: 3 } },
  ], {
    x: 0.75, y: cacBoxY, w: W - 1.5, h: cacBoxH,
    align: "left", valign: "middle", margin: 0,
  });

  // ── Four audience cards ──
  const audY = 4.0;
  const audH = 2.85;
  const audW = (W - 1.1 - 0.45) / 4;
  const audiences = [
    {
      eyebrow: "AUDIENCE 01  ·  REDDIT + X",
      logos: ["r/cursor", "r/ClaudeAI", "X / hn"],
      title: "AI-tool power users.",
      desc: "Build-in-public threads. Weekly MRR posts. AI_CONTEXT.md handoff demos. Organic-led.",
      cac: "CAC £0–40",
      visual: "thread",
      dark: false,
    },
    {
      eyebrow: "AUDIENCE 02  ·  YOUTUBE",
      logos: ["Theo", "Fireship-adj", "vibe coders"],
      title: "Creator-adjacent founders.",
      desc: "60-second sandbox demos sponsored. Promo codes → 90-day Launch credit.",
      cac: "CAC £80–140",
      visual: "video",
      dark: false,
    },
    {
      eyebrow: "AUDIENCE 03  ·  PH / TLDR / HN",
      logos: ["Product Hunt", "TLDR", "Hacker NL"],
      title: "Indie SaaS launchers.",
      desc: "Coordinated PH launch + newsletter sponsorships at peak hype. UTM attribution direct to signup.",
      cac: "CAC £40–120",
      visual: "headline",
      dark: false,
    },
    {
      eyebrow: "AUDIENCE 04  ·  PARTNERS",
      logos: ["Anthropic", "Cursor", "Lovable / Bolt"],
      title: "AI-tool partners.",
      desc: "Co-marketing on integration listings. Doubles as acquirer signaling for the M12–18 exit window.",
      cac: "CAC £0 (rev-share)",
      visual: "logo",
      dark: true,
    },
  ];

  audiences.forEach((a, i) => {
    const x = 0.55 + i * (audW + 0.15);
    const dark = a.dark;
    card(s, x, audY, audW, audH, { fill: dark ? C.dark : C.bgCard, stroke: dark ? "26305A" : C.border });
    eyebrow(s, x + 0.20, audY + 0.18, audW - 0.4, a.eyebrow, dark);
    // Mini "visual" placeholder — a soft band with logo-tags
    const visY = audY + 0.55;
    s.addShape("roundRect", {
      x: x + 0.20, y: visY, w: audW - 0.4, h: 0.65, rectRadius: 0.10,
      fill: { color: dark ? C.darkCard2 : C.indigoLight }, line: { type: "none" },
    });
    s.addText(a.logos.join("   ·   "), {
      x: x + 0.30, y: visY, w: audW - 0.6, h: 0.65,
      fontSize: 11, fontFace: FONT_MONO, color: dark ? C.indigoMid : C.indigoDeep, bold: true, charSpacing: 2,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(a.title, {
      x: x + 0.20, y: audY + 1.35, w: audW - 0.4, h: 0.45,
      fontSize: T.cardTitleSm.size, fontFace: FONT_HEAD, bold: true, color: dark ? C.white : C.ink,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(a.desc, {
      x: x + 0.20, y: audY + 1.85, w: audW - 0.4, h: 0.55,
      fontSize: 9.5, fontFace: FONT_BODY, color: dark ? "C7CCE8" : C.ink2,
      align: "left", valign: "top", margin: 0,
    });
    pill(s, x + 0.20, audY + audH - 0.50, audW - 0.4, 0.36, a.cac, {
      fill: dark ? C.indigoDeep : C.indigo, color: C.white, size: 11, bold: true, charSpacing: 2,
    });
  });

  footer(s, 13);
}

// ─── SLIDE 14 — GTM · Plan (month-by-month + spend) ─────────────────────
function slide14() {
  const s = pres.addSlide();
  bg(s);
  header(s, 14, "GO-TO-MARKET  ·  PLAN");

  title2(s, 0.55, 1.05, W - 1.1, ["Month-by-month.", "Spend tagged to phase."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Four phases, mirroring the four audiences on the prior slide. Spend rises only after the funnel signals validation by signup #100.");

  // Four phase columns with mini spend gauge per phase
  const phaseY = 2.85;
  const phaseH = 3.55;
  const phaseW = (W - 1.1 - 0.45) / 4;
  const phases = [
    {
      eyebrow: "PHASE 01  ·  M0–M2",
      title: "Founder beta.",
      audienceTag: "REDDIT + X · AUDIENCE 01",
      body: "100 friendly users from r/cursor and indie-hacker DMs. Pricing tested on first 50 paying.",
      spend: "£0",
      spendBar: 0,
      milestones: ["First 100 sandbox signups", "Pricing tested live", "AI_CONTEXT.md feedback loop"],
    },
    {
      eyebrow: "PHASE 02  ·  M2–M4",
      title: "Hype injection.",
      audienceTag: "YOUTUBE · AUDIENCE 02",
      body: "Theo + 4× Fireship-adjacent sponsorships. 60-second sandbox demos. Promo codes drive trackable signup.",
      spend: "£55K  (£40K + £15K NL)",
      spendBar: 0.35,
      milestones: ["5K signups", "250–500 paying", "First creator case study"],
    },
    {
      eyebrow: "PHASE 03  ·  M4–M6",
      title: "Compound.",
      audienceTag: "PH / TLDR / HN · AUDIENCE 03",
      body: "Reddit + X build-in-public + Discord. PH launch + TLDR newsletter at peak hype.",
      spend: "£25K  (+ content)",
      spendBar: 0.20,
      milestones: ["15K signups", "1.5–2K paying", "PH top-of-day"],
    },
    {
      eyebrow: "PHASE 04  ·  M6–M15",
      title: "Decide + scale.",
      audienceTag: "PARTNERS · AUDIENCE 04",
      body: "M6–9 pre-seed signalling or run lean. M9–15 paid acquisition if numbers hold — Meta, X, YouTube.",
      spend: "£300K  (M9–M15)",
      spendBar: 0.95,
      milestones: ["Decision point M6", "Partner integration shipped", "Raise OR acquire conversations live"],
    },
  ];
  phases.forEach((p, i) => {
    const x = 0.55 + i * (phaseW + 0.15);
    card(s, x, phaseY, phaseW, phaseH, { fill: C.bgCard, stroke: C.border });
    eyebrow(s, x + 0.22, phaseY + 0.20, phaseW - 0.44, p.eyebrow);
    s.addText(p.title, {
      x: x + 0.22, y: phaseY + 0.55, w: phaseW - 0.44, h: 0.5,
      fontSize: 22, fontFace: FONT_HEAD, bold: true, color: C.ink,
      align: "left", valign: "top", margin: 0,
    });
    // Audience-correlated tag
    pill(s, x + 0.22, phaseY + 1.10, phaseW - 0.44, 0.35, p.audienceTag, {
      fill: C.indigoSoft, color: C.indigoDeep, size: 9.5, bold: true, charSpacing: 2.5,
    });
    s.addText(p.body, {
      x: x + 0.22, y: phaseY + 1.55, w: phaseW - 0.44, h: 0.95,
      fontSize: 11, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "top", margin: 0,
    });
    // Spend block
    s.addText("SPEND", {
      x: x + 0.22, y: phaseY + 2.50, w: phaseW - 0.44, h: 0.22,
      fontSize: 9, fontFace: FONT_MONO, color: C.muted, charSpacing: 3,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(p.spend, {
      x: x + 0.22, y: phaseY + 2.72, w: phaseW - 0.44, h: 0.30,
      fontSize: 12.5, fontFace: FONT_HEAD, bold: true, color: C.indigo,
      align: "left", valign: "top", margin: 0,
    });
    // Spend gauge — separated from amount by clear gap
    const barY = phaseY + 3.07;
    s.addShape("roundRect", {
      x: x + 0.22, y: barY, w: phaseW - 0.44, h: 0.14, rectRadius: 0.07,
      fill: { color: C.indigoLight }, line: { type: "none" },
    });
    s.addShape("roundRect", {
      x: x + 0.22, y: barY, w: (phaseW - 0.44) * p.spendBar, h: 0.14, rectRadius: 0.07,
      fill: { color: C.indigo }, line: { type: "none" },
    });
    // (Milestones removed — content overflow on multi-line text)
    void p.milestones;
  });

  // Bottom
  s.addText([
    { text: "Total Y1 paid spend ≈ £420K", options: { color: C.ink, bold: true } },
    { text: "  (60% of raise). All channels below CAC ceiling at 3:1.", options: { color: C.ink2 } },
  ], {
    x: 0.55, y: 6.55, w: W - 1.1, h: 0.25,
    fontSize: 12, fontFace: FONT_BODY,
    align: "left", valign: "middle",
  });

  footer(s, 14);
}

// ─── SLIDE 15 — Funnel + Validation (validation by #100, M6 decision) ────
function slide15() {
  const s = pres.addSlide();
  bg(s);
  header(s, 15, "VALIDATION + DECISION");

  title2(s, 0.55, 1.05, W - 1.1, ["Validate by signup #100.", "Decide by month 6."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Two checkpoints, hard rules. First — does the funnel work. Second — what kind of company is this becoming.");

  // ── Left: validation by #100 (two stacked cards) ─────────────────
  const leftX = 0.55, leftW = (W - 1.1 - 0.4) / 2;
  card(s, leftX, 2.95, leftW, 3.55, { fill: C.bgCard, stroke: C.border });
  eyebrow(s, leftX + 0.30, 3.10, leftW - 0.6, "CHECK 01  ·  VALIDATE BY SIGNUP #100");
  s.addText("Does the funnel work at all?", {
    x: leftX + 0.30, y: 3.40, w: leftW - 0.6, h: 0.40,
    fontSize: 20, fontFace: FONT_HEAD, bold: true, color: C.ink,
    align: "left", valign: "top", margin: 0,
  });
  s.addText("The first 100 sandbox signups tell us whether the product converts, retains, and is priced near right. If two of three miss, paid spend pauses.", {
    x: leftX + 0.30, y: 3.85, w: leftW - 0.6, h: 0.80,
    fontSize: 11.5, fontFace: FONT_BODY, color: C.ink2,
    align: "left", valign: "top", margin: 0,
  });
  // Three metric rows
  const checks = [
    { lab: "Sandbox → Launch conversion", val: "≥ 4%", note: "Industry band 3–10%. Below 3% = reprice." },
    { lab: "Launch monthly churn",        val: "≤ 4%",  note: "Below 4% holds LTV. Above 6% = product gap." },
    { lab: "Time to AI handoff demo",     val: "< 7d",  note: "AI tool used on the sandbox within the first week." },
  ];
  checks.forEach((c, i) => {
    const y = 4.80 + i * 0.50;
    s.addShape("line", { x: leftX + 0.30, y: y - 0.10, w: leftW - 0.6, h: 0, line: { color: C.border, width: 0.5 }});
    s.addText(c.lab, {
      x: leftX + 0.30, y, w: leftW - 1.7, h: 0.5,
      fontSize: 12, fontFace: FONT_BODY, color: C.ink,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(c.val, {
      x: leftX + leftW - 1.4, y, w: 1.1, h: 0.5,
      fontSize: 18, fontFace: FONT_HEAD, bold: true, color: C.indigo,
      align: "right", valign: "middle", margin: 0,
    });
    s.addText(c.note, {
      x: leftX + 0.30, y: y + 0.30, w: leftW - 0.6, h: 0.25,
      fontSize: 9.5, fontFace: FONT_BODY, color: C.muted,
      align: "left", valign: "top", margin: 0,
    });
  });

  // ── Right: M6 decision (what's being decided) ────────────────────
  const rightX = leftX + leftW + 0.4, rightW = leftW;
  card(s, rightX, 2.95, rightW, 3.55, { fill: C.dark, stroke: "26305A" });
  eyebrow(s, rightX + 0.30, 3.10, rightW - 0.6, "CHECK 02  ·  MONTH 6 DECISION", true);
  s.addText("What is this becoming?", {
    x: rightX + 0.30, y: 3.40, w: rightW - 0.6, h: 0.40,
    fontSize: 20, fontFace: FONT_HEAD, bold: true, color: C.white,
    align: "left", valign: "top", margin: 0,
  });
  s.addText("At M6 the picture is clear enough to commit. We choose between three paths — and the next 6 months execute that choice.", {
    x: rightX + 0.30, y: 3.85, w: rightW - 0.6, h: 0.80,
    fontSize: 11.5, fontFace: FONT_BODY, color: "C7CCE8",
    align: "left", valign: "top", margin: 0,
  });

  const paths = [
    { tag: "RAISE",    val: "Pre-seed → seed.",        note: "If MRR + growth-rate signal hypergrowth.", color: C.indigoMid },
    { tag: "ACQUIRE",  val: "Strategic conversations.", note: "If a partner / AI lab signals strong interest.", color: C.indigoMid },
    { tag: "RUN LEAN", val: "Founder-led, no team.",    note: "If economics work but growth is steady, not vertical.", color: C.indigoMid },
  ];
  paths.forEach((p, i) => {
    const y = 4.80 + i * 0.50;
    s.addShape("line", { x: rightX + 0.30, y: y - 0.10, w: rightW - 0.6, h: 0, line: { color: "26305A", width: 0.5 }});
    pill(s, rightX + 0.30, y + 0.08, 0.85, 0.32, p.tag, {
      fill: C.indigo, color: C.white, size: 9, bold: true, charSpacing: 2.5,
    });
    s.addText(p.val, {
      x: rightX + 1.25, y, w: rightW - 1.5, h: 0.30,
      fontSize: 13.5, fontFace: FONT_HEAD, bold: true, color: C.white,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(p.note, {
      x: rightX + 1.25, y: y + 0.28, w: rightW - 1.5, h: 0.25,
      fontSize: 10, fontFace: FONT_BODY, color: "C7CCE8",
      align: "left", valign: "top", margin: 0,
    });
  });

  // Bottom: gate
  s.addText([
    { text: "GATE  ·  ", options: { color: C.indigo, bold: true, fontFace: FONT_MONO, charSpacing: 3 } },
    { text: "If check 01 misses, paid spend pauses. The M6 decision is a fork, not a default.", options: { color: C.ink2 } },
  ], {
    x: 0.55, y: 6.60, w: W - 1.1, h: 0.25,
    fontSize: 11, fontFace: FONT_BODY,
    align: "left", valign: "middle",
  });

  footer(s, 15);
}

// ─── SLIDE 16 — Revenue + EBITDA (two graphs side-by-side) ──────────────
function slide16() {
  const s = pres.addSlide();
  bg(s);
  header(s, 16, "REVENUE + EBITDA");

  title2(s, 0.55, 1.05, W - 1.1, ["MRR + EBITDA.", "Two sprint scenarios."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Identical axes. Revenue and EBITDA on each. The base case is profitable from ~M6; the upside case has the same shape, taller.");

  // Two charts side by side, each with stacked pills below
  const chartY = 2.80;
  const chartH = 2.85;
  const chartW = (W - 1.1 - 0.4) / 2;

  // Helper: render a small line chart inside a card region
  function drawScenarioChart(x, mrr18, ebitda18, label, dark) {
    card(s, x, chartY, chartW, chartH, { fill: dark ? C.dark : C.bgCard, stroke: dark ? "26305A" : C.border });
    s.addText(label, {
      x: x + 0.30, y: chartY + 0.20, w: chartW - 0.6, h: 0.30,
      fontSize: T.cardEyebrow.size, fontFace: FONT_MONO, color: dark ? C.indigoMid : C.muted, charSpacing: 3,
      align: "left", valign: "middle", margin: 0,
    });
    // Axis area
    const ax0 = x + 0.70, ay0 = chartY + 0.70, axW = chartW - 0.95, axH = chartH - 1.20;
    // y-grid lines (4)
    const yMax = 400; // consistent axis (£K)
    const yStep = 100;
    for (let g = 0; g <= 4; g++) {
      const v = yStep * g;
      const gy = ay0 + axH - (v / yMax) * axH;
      s.addShape("line", { x: ax0, y: gy, w: axW, h: 0, line: { color: dark ? "26305A" : "EAE7F5", width: 0.4 }});
      s.addText(`£${v}K`, {
        x: x + 0.15, y: gy - 0.13, w: 0.52, h: 0.26,
        fontSize: 8, fontFace: FONT_MONO, color: dark ? C.indigoMid : C.muted,
        align: "right", valign: "middle", margin: 0,
      });
    }
    // Generate sigmoid-ish series for 18 months
    const months = 19; // M0..M18
    function curve(target) {
      const arr = [];
      for (let m = 0; m < months; m++) {
        // s-curve
        const v = target / (1 + Math.exp(-0.45 * (m - 9)));
        arr.push(Math.max(0, v));
      }
      return arr;
    }
    const mrrSeries = curve(mrr18);
    const ebSeries  = curve(ebitda18);
    // Plot helper
    function plotLine(series, color, lw) {
      const pts = series.map((v, i) => ({
        x: ax0 + (i / (months - 1)) * axW,
        y: ay0 + axH - (v / yMax) * axH,
      }));
      for (let i = 0; i < pts.length - 1; i++) {
        s.addShape("line", {
          x: pts[i].x, y: pts[i].y,
          w: pts[i+1].x - pts[i].x, h: pts[i+1].y - pts[i].y,
          line: { color, width: lw },
        });
      }
      // endpoint dot
      const last = pts[pts.length - 1];
      s.addShape("ellipse", {
        x: last.x - 0.07, y: last.y - 0.07, w: 0.14, h: 0.14,
        fill: { color }, line: { type: "none" },
      });
    }
    plotLine(mrrSeries, dark ? C.indigoMid : C.indigo, 2.25);
    plotLine(ebSeries,  dark ? "C7CCE8" : "10B981", 1.75);
    // x labels
    [0, 3, 6, 9, 12, 15, 18].forEach(m => {
      const lx = ax0 + (m / (months - 1)) * axW;
      s.addText("M" + m, {
        x: lx - 0.20, y: ay0 + axH + 0.05, w: 0.4, h: 0.22,
        fontSize: 8, fontFace: FONT_MONO, color: dark ? C.indigoMid : C.muted,
        align: "center", valign: "middle", margin: 0,
      });
    });
    // Legend
    s.addText([
      { text: "● MRR", options: { color: dark ? C.indigoMid : C.indigo, bold: true } },
      { text: "    ● EBITDA", options: { color: dark ? "C7CCE8" : "10B981", bold: true } },
    ], {
      x: ax0, y: chartY + 0.25, w: axW, h: 0.25,
      fontSize: 9.5, fontFace: FONT_MONO,
      align: "right", valign: "middle", margin: 0,
    });
  }

  drawScenarioChart(0.55, 135, 28, "SPRINT BASE  ·  M0–M18", false);
  drawScenarioChart(0.55 + chartW + 0.4, 380, 145, "SPRINT UPSIDE  ·  M0–M18", true);

  // Per-scenario summary pills below charts
  const pillY = chartY + chartH + 0.15;
  const pillH = 1.05;
  // Base summary
  card(s, 0.55, pillY, chartW, pillH, { fill: C.bgSoft, stroke: C.border });
  s.addText("SPRINT BASE  ·  M18", {
    x: 0.55 + 0.30, y: pillY + 0.10, w: chartW - 0.6, h: 0.28,
    fontSize: 10, fontFace: FONT_MONO, color: C.muted, charSpacing: 3,
    align: "left", valign: "middle", margin: 0,
  });
  s.addText([
    { text: "~£135K", options: { color: C.ink, bold: true, fontSize: 26 } },
    { text: " MRR   ", options: { color: C.muted, fontSize: 12 } },
    { text: "~£28K", options: { color: "065F46", bold: true, fontSize: 22 } },
    { text: " EBITDA   ", options: { color: C.muted, fontSize: 12 } },
    { text: "~£1.6M ARR", options: { color: C.ink2, fontSize: 13 } },
  ], {
    x: 0.55 + 0.30, y: pillY + 0.35, w: chartW - 0.6, h: 0.6,
    fontFace: FONT_HEAD, align: "left", valign: "middle", margin: 0,
  });

  // Upside summary
  card(s, 0.55 + chartW + 0.4, pillY, chartW, pillH, { fill: C.indigo, stroke: C.indigoDeep });
  s.addText("SPRINT UPSIDE  ·  M18", {
    x: 0.55 + chartW + 0.4 + 0.30, y: pillY + 0.10, w: chartW - 0.6, h: 0.28,
    fontSize: 10, fontFace: FONT_MONO, color: C.indigoMid, charSpacing: 3,
    align: "left", valign: "middle", margin: 0,
  });
  s.addText([
    { text: "~£380K", options: { color: C.white, bold: true, fontSize: 26 } },
    { text: " MRR   ", options: { color: C.indigoMid, fontSize: 12 } },
    { text: "~£145K", options: { color: "10B981", bold: true, fontSize: 22 } },
    { text: " EBITDA   ", options: { color: C.indigoMid, fontSize: 12 } },
    { text: "~£4.5M ARR", options: { color: "C7CCE8", fontSize: 13 } },
  ], {
    x: 0.55 + chartW + 0.4 + 0.30, y: pillY + 0.35, w: chartW - 0.6, h: 0.6,
    fontFace: FONT_HEAD, align: "left", valign: "middle", margin: 0,
  });

  footer(s, 16);
}

// ─── SLIDE 17 — Three Exit Options (reframed) ───────────────────────────
function slide17() {
  const s = pres.addSlide();
  bg(s);
  header(s, 17, "MONTH-6 REVIEW  ·  THREE EXIT OPTIONS");

  // Big banner: this IS the month-6 review (per feedback)
  s.addShape("roundRect", {
    x: 0.55, y: 0.95, w: W - 1.1, h: 0.50, rectRadius: 0.25,
    fill: { color: C.indigo }, line: { type: "none" },
  });
  s.addText("M6  ·  DECIDE  ·  THREE EXIT OPTIONS FOR THE DAY-ONE INVESTOR", {
    x: 0.55, y: 0.95, w: W - 1.1, h: 0.50,
    fontSize: 13, fontFace: FONT_MONO, color: C.white, bold: true, charSpacing: 4,
    align: "center", valign: "middle", margin: 0,
  });

  title2(s, 0.55, 1.65, W - 1.1, ["At month 6.", "Three exit options."], false);
  subtitle(s, 0.55, 2.55, W - 1.1,
    "Each is a real path. Investors see acquisition, further-funding growth, or a profitable lean business returning healthy ROI — all underwritten by the same first cohort.");

  // Three cards
  const cy = 3.40;
  const ch = 3.30;
  const cw = (W - 1.1 - 0.4) / 3;
  const exits = [
    {
      tag: "ACQUISITION  ·  M12–18",
      title: "Strategic acquisition.",
      bigPrefix: "$",
      big: "20–100",
      bigUnit: "M",
      meta: "VALUATION BAND",
      body: "5–15K active users, clean positioning, alignment with a major AI tool vendor at M12–18.",
      precedents: ["Anthropic", "Vercel"],
      dark: true,
      accent: false,
    },
    {
      tag: "GROWTH  ·  HYPERGROWTH RAISE",
      title: "Pre-seed → seed raise.",
      bigPrefix: "£",
      big: "20–50",
      bigUnit: "M",
      meta: "VALUATION (PROJECTED)",
      body: "£100–300K MRR with 30%+ MoM growth at M12–15. Raise funds 5–8 hires, full paid engine, product expansion. Y2 ARR target £8–15M.",
      precedents: ["Earlybird AI", "Seedcamp"],
      dark: false,
      accent: true,
    },
    {
      tag: "LEAN  ·  PROFITABLE BUSINESS",
      title: "Healthy ROI · no raise.",
      bigPrefix: "£",
      big: "25–65",
      bigUnit: "K/mo",
      meta: "FOUNDER DRAW + ROI",
      body: "Sprint base outcome. Profitable from ~M6. Investor returns capital via founder share-buyback / dividend on a ~24–36 month horizon.",
      precedents: ["ConvertKit-style", "Indie SaaS"],
      dark: false,
      accent: false,
    },
  ];
  exits.forEach((e, i) => {
    const x = 0.55 + i * (cw + 0.2);
    const dark = e.dark;
    card(s, x, cy, cw, ch, {
      fill: dark ? C.dark : C.bgCard,
      stroke: dark ? "26305A" : (e.accent ? C.indigo : C.border),
      lineW: e.accent ? 1.5 : 0.75,
    });
    eyebrow(s, x + 0.30, cy + 0.22, cw - 0.6, e.tag, dark);
    s.addText(e.title, {
      x: x + 0.30, y: cy + 0.60, w: cw - 0.6, h: 0.45,
      fontSize: 19, fontFace: FONT_HEAD, bold: true, color: dark ? C.white : C.ink,
      align: "left", valign: "top", margin: 0,
    });
    // Big number with prefix + unit
    s.addText([
      { text: e.bigPrefix, options: { color: dark ? C.indigoMid : (e.accent ? C.indigo : C.ink), fontSize: 22 } },
      { text: e.big, options: { color: dark ? C.indigoMid : (e.accent ? C.indigo : C.ink), bold: true, fontSize: 50 } },
      { text: e.bigUnit, options: { color: dark ? C.indigoMid : (e.accent ? C.indigo : C.ink), fontSize: 16 } },
    ], {
      x: x + 0.30, y: cy + 1.10, w: cw - 0.6, h: 0.9,
      fontFace: FONT_HEAD, align: "left", valign: "top", margin: 0,
    });
    s.addText(e.meta, {
      x: x + 0.30, y: cy + 1.95, w: cw - 0.6, h: 0.25,
      fontSize: 9.5, fontFace: FONT_MONO, color: dark ? C.indigoMid : C.muted, charSpacing: 3,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(e.body, {
      x: x + 0.30, y: cy + 2.20, w: cw - 0.6, h: 0.85,
      fontSize: 10.5, fontFace: FONT_BODY, color: dark ? "C7CCE8" : C.ink2,
      align: "left", valign: "top", margin: 0,
    });
    // Precedent pills row — single row, 2 only
    const py = cy + ch - 0.55;
    const ppw = (cw - 0.6 - 0.10) / 2;
    const pph = 0.32;
    e.precedents.forEach((pp, j) => {
      pill(s, x + 0.30 + j * (ppw + 0.10), py, ppw, pph, pp.toUpperCase(), {
        fill: dark ? "1F2547" : C.bgSoft,
        color: dark ? C.indigoMid : C.muted,
        size: 9, charSpacing: 1.5,
      });
    });
  });

  // (Disclaimer moved into header banner area to free up space)

  footer(s, 17);
}

// ─── SLIDE 18 — Risks (with "risks" in title) ────────────────────────────
function slide18() {
  const s = pres.addSlide();
  bg(s);
  header(s, 18, "TOP RISKS");

  title2(s, 0.55, 1.05, W - 1.1, ["Top 3 risks.", "Stated honestly. Mitigations ready."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "We're not pretending these don't exist. Full risks register in appendix.");

  const risks = [
    {
      level: "HIGH  ·  01",
      title: "Big AI lab ships its own deployment layer.",
      desc: "Anthropic / Cursor / OpenAI shipping a \"Claude Code Cloud\" or similar in the next 6–12 months closes the wedge.",
      mit: "Aggressive partnership outreach M0–4. Position as their deployment surface, not a competitor. Make Hatchik acquirable, not buildable.",
      color: C.red,
    },
    {
      level: "MED  ·  02",
      title: "Peak coincidence on shared sandbox host.",
      desc: "If >30% of sandboxes go warm simultaneously, CAX31 RAM/CPU contention bites users.",
      mit: "Watermark alert at 35-warm-concurrent → auto-provision second CAX31. Cost-per-sandbox stays in band (+£11.30/mo).",
      color: C.amber,
    },
    {
      level: "LOW  ·  03",
      title: "Wake latency on idle-suspended sandboxes.",
      desc: "10–15s cold start after 2h+ idle could feel broken to first-time users.",
      mit: "\"Your sandbox is warming up…\" splash in the AI tool's deploy-status callback. Acceptable for dev-env framing.",
      color: C.green,
    },
  ];

  const ry = 3.00;
  const rh = 1.20;
  const rg = 0.15;
  risks.forEach((r, i) => {
    const y = ry + i * (rh + rg);
    card(s, 0.55, y, W - 1.1, rh, { fill: C.bgCard, stroke: C.border });
    // Risk level pill
    pill(s, 0.85, y + 0.18, 1.6, 0.42, r.level, {
      fill: r.color, color: C.white, size: 11, bold: true, charSpacing: 3,
    });
    // Title
    s.addText(r.title, {
      x: 2.65, y: y + 0.15, w: 6.0, h: 0.45,
      fontSize: 16, fontFace: FONT_HEAD, bold: true, color: C.ink,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(r.desc, {
      x: 2.65, y: y + 0.60, w: 6.0, h: 0.55,
      fontSize: 11, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "top", margin: 0,
    });
    // Mitigation panel right
    s.addShape("roundRect", {
      x: 8.85, y: y + 0.15, w: W - 0.55 - 8.85, h: rh - 0.30, rectRadius: 0.10,
      fill: { color: C.indigoLight }, line: { type: "none" },
    });
    s.addText("MITIGATION", {
      x: 9.00, y: y + 0.22, w: W - 0.55 - 9.05, h: 0.25,
      fontSize: 9.5, fontFace: FONT_MONO, color: C.indigoDeep, charSpacing: 3, bold: true,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(r.mit, {
      x: 9.00, y: y + 0.45, w: W - 0.55 - 9.05, h: rh - 0.55,
      fontSize: 11, fontFace: FONT_BODY, color: C.ink,
      align: "left", valign: "top", margin: 0,
    });
  });

  footer(s, 18);
}

// ─── SLIDE 19 — The Ask (fills filled in) ────────────────────────────────
function slide19() {
  const s = pres.addSlide();
  bg(s, true);
  header(s, 19, "THE ASK", true);

  title2(s, 0.55, 1.05, W - 1.1, ["Sprint capital.", "Decide at month 6."], true);

  // Left: raising number
  s.addText("RAISING", {
    x: 0.55, y: 2.30, w: 5.5, h: 0.35,
    fontSize: T.cardEyebrow.size, fontFace: FONT_MONO, color: C.indigoMid, charSpacing: 4,
    align: "left", valign: "middle", margin: 0,
  });
  s.addText([
    { text: "£500K", options: { color: C.white, bold: true, fontSize: 64 } },
    { text: "  –  ", options: { color: C.indigoMid, fontSize: 36 } },
    { text: "£1M", options: { color: "C7CCE8", fontSize: 64, bold: true } },
  ], {
    x: 0.55, y: 2.70, w: 6.5, h: 1.4,
    fontFace: FONT_HEAD, align: "left", valign: "top", margin: 0,
  });
  s.addText("Horizon: 12 months to decision point. Pre-seed · SAFE or convertible · founder fills final number.", {
    x: 0.55, y: 4.30, w: 6.0, h: 0.7,
    fontSize: 13, fontFace: FONT_BODY, color: "C7CCE8",
    align: "left", valign: "top", margin: 0,
  });

  // Allocation donut (simple stacked-bar instead — donut hard without chart)
  s.addText("ALLOCATION", {
    x: 0.55, y: 5.20, w: 5.5, h: 0.30,
    fontSize: T.cardEyebrow.size, fontFace: FONT_MONO, color: C.indigoMid, charSpacing: 4,
    align: "left", valign: "middle", margin: 0,
  });
  // Big horizontal allocation bar (60 / 25 / 15)
  const allocY = 5.55;
  const allocW = 6.0;
  const seg = [
    { p: 0.60, c: C.indigo,    lbl: "60%" },
    { p: 0.25, c: C.indigoMid, lbl: "25%" },
    { p: 0.15, c: "C7CCE8",     lbl: "15%" },
  ];
  let xCursor = 0.55;
  seg.forEach((g, i) => {
    const sw = allocW * g.p;
    s.addShape("rect", {
      x: xCursor, y: allocY, w: sw, h: 0.55,
      fill: { color: g.c }, line: { type: "none" },
    });
    s.addText(g.lbl, {
      x: xCursor, y: allocY, w: sw, h: 0.55,
      fontSize: 14, fontFace: FONT_HEAD, bold: true, color: i === 2 ? C.dark : C.white,
      align: "center", valign: "middle", margin: 0,
    });
    xCursor += sw;
  });

  // Right: use of funds (three bars with sub-text)
  s.addText("USE OF FUNDS", {
    x: 7.2, y: 2.30, w: 5.5, h: 0.35,
    fontSize: T.cardEyebrow.size, fontFace: FONT_MONO, color: C.indigoMid, charSpacing: 4,
    align: "left", valign: "middle", margin: 0,
  });

  const items = [
    { t: "Paid acquisition",         pct: "60%  ·  ~£420K",
      d: "Creator sponsorships + retargeting M2–9. At LTV £520, CAC ceiling £175 — paid becomes viable.", v: 1.00 },
    { t: "First hires (12-mo burn)", pct: "25%  ·  ~£175K",
      d: "One engineer (substrate hardening, second region, signed mobile pipeline) + one marketer (content engine).", v: 0.42 },
    { t: "Infrastructure + partnerships", pct: "15%  ·  ~£105K",
      d: "Multi-region rollout, abuse/scaling headroom, partnership-integration build for Anthropic / Cursor / Windsurf.", v: 0.25 },
  ];
  items.forEach((it, i) => {
    const y = 2.75 + i * 1.20;
    card(s, 7.2, y, W - 7.2 - 0.55, 1.05, { fill: C.darkCard, stroke: "26305A" });
    s.addText(it.t, {
      x: 7.4, y: y + 0.10, w: W - 7.4 - 0.55, h: 0.32,
      fontSize: 14, fontFace: FONT_HEAD, bold: true, color: C.white,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(it.pct, {
      x: 7.4, y: y + 0.10, w: W - 7.4 - 0.55, h: 0.32,
      fontSize: 10.5, fontFace: FONT_MONO, color: C.indigoMid, charSpacing: 3,
      align: "right", valign: "middle", margin: 0,
    });
    s.addShape("rect", {
      x: 7.4, y: y + 0.48, w: W - 7.4 - 0.55 - 0.10, h: 0.08,
      fill: { color: "26305A" }, line: { type: "none" },
    });
    s.addShape("rect", {
      x: 7.4, y: y + 0.48, w: (W - 7.4 - 0.55 - 0.10) * it.v, h: 0.08,
      fill: { color: C.indigoMid }, line: { type: "none" },
    });
    s.addText(it.d, {
      x: 7.4, y: y + 0.62, w: W - 7.4 - 0.55, h: 0.40,
      fontSize: 9.5, fontFace: FONT_BODY, color: "C7CCE8",
      align: "left", valign: "top", margin: 0,
    });
  });

  // Target band (fills filled in)
  s.addShape("roundRect", {
    x: 0.55, y: 6.45, w: W - 1.1, h: 0.40, rectRadius: 0.10,
    fill: { color: C.darkCard }, line: { color: C.indigo, width: 1 },
  });
  s.addText([
    { text: "TARGET   ", options: { color: C.indigo, bold: true, fontFace: FONT_MONO, charSpacing: 3, fontSize: 11 } },
    { text: "M12 · ~750 paying · ~£55K MRR · validated funnel · acquirer + raise conversations live.", options: { color: C.white, fontFace: FONT_BODY, fontSize: 11.5 } },
  ], {
    x: 0.55, y: 6.45, w: W - 1.1, h: 0.40,
    align: "center", valign: "middle", margin: 0,
  });

  footer(s, 19, true);
}

// ─── SLIDE 20 — Ethos + Moats (moved from old #30, user-perspective moats)
function slide20() {
  const s = pres.addSlide();
  bg(s);
  header(s, 20, "ETHOS + MOATS");

  title2(s, 0.55, 1.05, W - 1.1, ["Customer trust as moat.", "Predictable as armour."]);
  subtitle(s, 0.55, 2.05, W - 1.1,
    "Each moat is something the customer feels, not just a balance-sheet line. Hard to copy because they compound through the customer's own time and trust.");

  // 6 user-perspective moats in two rows
  const moats = [
    {
      n: "01", t: "Their product, their code.",
      d: "Per-tenant GitHub repo, day one. Customer leaves with everything. Trust comes from never feeling locked in.",
    },
    {
      n: "02", t: "Predictable costs.",
      d: "Pass-through × 1.60 + PAYG top-ups. No surprise £400 month. Founders can plan a year on a working hypothesis.",
    },
    {
      n: "03", t: "Founder doesn't need a CTO.",
      d: "Hatchik abstracts the parts a non-technical founder can't hire for at this stage. They never have to learn DKIM.",
    },
    {
      n: "04", t: "BYO-AI is a community lexicon.",
      d: "\"The production substrate for AI-built SaaS.\" Labs consider acquiring, not retraining every customer's mental model.",
    },
    {
      n: "05", t: "Wholesale relationships.",
      d: "Anthropic, OpenAI, Hetzner, Infomaniak at enterprise rates by 5K customers. Margin uplift competitors can't see.",
    },
    {
      n: "06", t: "Founder-led for the first 500.",
      d: "Customer knows the person who'll fix their bug. Can't be replicated by a corporate product team. Compounds reputation.",
    },
  ];
  const gy = 2.95;
  const gh = (H - 0.85 - gy - 0.25) / 2 - 0.15;
  const gw = (W - 1.1 - 0.4) / 3;
  moats.forEach((m, i) => {
    const r = Math.floor(i / 3), c = i % 3;
    const x = 0.55 + c * (gw + 0.2);
    const y = gy + r * (gh + 0.30);
    card(s, x, y, gw, gh, { fill: C.bgCard, stroke: C.border });
    s.addText(m.n, {
      x: x + 0.30, y: y + 0.20, w: 0.8, h: 0.4,
      fontSize: 13, fontFace: FONT_MONO, color: C.indigo, charSpacing: 3, bold: true,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(m.t, {
      x: x + 0.30, y: y + 0.65, w: gw - 0.6, h: 0.50,
      fontSize: T.cardTitleSm.size, fontFace: FONT_HEAD, bold: true, color: C.ink,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(m.d, {
      x: x + 0.30, y: y + 1.25, w: gw - 0.6, h: gh - 1.35,
      fontSize: 11.5, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "top", margin: 0,
    });
  });

  footer(s, 20);
}

// ─── SLIDE 21 — Thank You (cover-style closing) ─────────────────────────
function slide21() {
  const s = pres.addSlide();
  bg(s, true);
  s.addShape("rect", { x: 0, y: 0, w: W, h: 0.10, fill: { color: C.indigo }, line: { type: "none" } });
  header(s, 21, "", true);
  s.addText("MAY 2026  ·  INVESTOR & PARTNER DECK", {
    x: W - 5.5, y: 0.30, w: 5.0, h: 0.35,
    fontSize: T.eyebrow.size, fontFace: FONT_MONO, color: C.indigoMid,
    align: "right", valign: "middle", charSpacing: 4,
  });

  s.addText("Thank you.", {
    x: 0.55, y: 2.50, w: 10, h: 1.6,
    fontSize: 92, fontFace: FONT_HEAD, bold: true, color: C.white,
    align: "left", valign: "top",
  });
  s.addText("The production substrate your AI coder builds on.", {
    x: 0.55, y: 4.20, w: 10, h: 0.6,
    fontSize: 24, fontFace: FONT_HEAD, color: C.indigoMid,
    align: "left", valign: "top",
  });
  s.addText("No platform. No lock-in. No demos.", {
    x: 0.55, y: 4.80, w: 10, h: 0.55,
    fontSize: 20, fontFace: FONT_HEAD, color: "C7CCE8",
    align: "left", valign: "top",
  });

  // Contact block
  const blocks = [
    { lab: "GENERAL",  val: "hello@hatchik.com" },
    { lab: "WEB",      val: "hatchik.com" },
    { lab: "FOUNDER",  val: "Farhan Irshad" },
    { lab: "LINKEDIN", val: "linkedin.com/in/farhanirshad" },
  ];
  blocks.forEach((b, i) => {
    s.addText(b.lab, {
      x: 0.55 + i * 3.0, y: 6.05, w: 2.8, h: 0.3,
      fontSize: 10.5, fontFace: FONT_MONO, color: C.indigoMid, charSpacing: 4,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(b.val, {
      x: 0.55 + i * 3.0, y: 6.32, w: 2.8, h: 0.4,
      fontSize: 14, fontFace: FONT_HEAD, color: C.white, bold: true,
      align: "left", valign: "middle", margin: 0,
    });
  });

  footer(s, 21, true);
}

// ═════════════════════════════════════════════════════════════════════════
// APPENDIX
// ═════════════════════════════════════════════════════════════════════════

// ─── SLIDE 22 — Appendix divider ─────────────────────────────────────────
function slide22() {
  const s = pres.addSlide();
  bg(s);
  header(s, 22, "APPENDIX");

  title2(s, 0.55, 1.50, W - 1.1, ["Appendix.", "Detail, behind the headline."]);

  const items = [
    { n: "23", t: "Competitor deep-dive — AI builders vs infra players" },
    { n: "24", t: "AI-lab disruption risk + mitigation strategy" },
    { n: "25", t: "Full risks register" },
    { n: "26", t: "MRR build-up — cohort × ARPU × churn" },
    { n: "27", t: "Use of funds — detailed allocation" },
    { n: "28", t: "Sandbox + Launch capacity management" },
  ];
  items.forEach((it, i) => {
    const y = 3.20 + i * 0.50;
    s.addText(it.n, {
      x: 0.85, y, w: 0.8, h: 0.4,
      fontSize: 20, fontFace: FONT_HEAD, bold: true, color: C.indigo,
      align: "left", valign: "middle", margin: 0,
    });
    s.addText(it.t, {
      x: 1.85, y, w: W - 2.4, h: 0.4,
      fontSize: 15, fontFace: FONT_HEAD, color: C.ink,
      align: "left", valign: "middle", margin: 0,
    });
    s.addShape("line", { x: 0.85, y: y + 0.45, w: W - 1.7, h: 0, line: { color: C.border, width: 0.5 }});
  });

  footer(s, 22);
}

// Compact appendix slides — kept terse since user didn't ask for redesign here
function appendixSlide(num, sectionLabel, title2parts, subtitleText, blocks) {
  const s = pres.addSlide();
  bg(s);
  header(s, num, sectionLabel);
  title2(s, 0.55, 1.05, W - 1.1, title2parts, false, 30);
  if (subtitleText) subtitle(s, 0.55, 2.00, W - 1.1, subtitleText);
  // Render blocks as a 2-col grid
  const gy = 2.85;
  const gh = (H - 0.85 - gy - 0.20) / Math.ceil(blocks.length / 2) - 0.15;
  const gw = (W - 1.1 - 0.3) / 2;
  blocks.forEach((b, i) => {
    const r = Math.floor(i / 2), c = i % 2;
    const x = 0.55 + c * (gw + 0.3);
    const y = gy + r * (gh + 0.30);
    card(s, x, y, gw, gh, { fill: C.bgCard, stroke: C.border });
    eyebrow(s, x + 0.25, y + 0.20, gw - 0.5, b.eyebrow);
    s.addText(b.title, {
      x: x + 0.25, y: y + 0.55, w: gw - 0.5, h: 0.45,
      fontSize: 16, fontFace: FONT_HEAD, bold: true, color: C.ink,
      align: "left", valign: "top", margin: 0,
    });
    s.addText(b.body, {
      x: x + 0.25, y: y + 1.05, w: gw - 0.5, h: gh - 1.15,
      fontSize: 11, fontFace: FONT_BODY, color: C.ink2,
      align: "left", valign: "top", margin: 0,
    });
  });
  footer(s, num);
}

function slide23() {
  appendixSlide(23, "APPENDIX  ·  COMPETITOR DEEP-DIVE",
    ["Two-layer landscape.", "Hatchik on the seam."],
    "Coverage across BYO-AI, plumbing, owned code, exit cost, provisioning, mobile, MoR billing, and starting price.",
    [
      { eyebrow: "AI BUILDERS",   title: "Lovable · Bolt · v0 · Replit Agent · Bubble", body: "Own the chat-to-prototype loop. Their AI, their chat, their runtime. Code lives in their walled garden. Hatchik wins on BYO AI + real GitHub + real stack." },
      { eyebrow: "INFRA · DEPLOY", title: "Vercel · Render · Railway · Fly · Heroku", body: "Deploy your code — auth, mail, payments, mobile are yours. Built for engineers. Hatchik wins on substrate pre-wired + day-one SaaS." },
      { eyebrow: "PRICING ENTRY", title: "Hatchik £14/mo vs $20–$39+", body: "Lower entry plus included AI credit makes Hatchik below-friction for sandbox conversion. Substrate value at entry-tier pricing." },
      { eyebrow: "EXIT COST",      title: "£0 from Hatchik · medium-to-high from AI builders", body: "Customer's repo lives on their GitHub from day one. Switching cost is zero, which is exactly why they stay." },
    ]);
}

function slide24() {
  appendixSlide(24, "APPENDIX  ·  AI-LAB DISRUPTION",
    ["What if the labs", "ship their own?"],
    "The single biggest risk. Mapped honestly: who could ship it, when, what we lose, how we defend.",
    [
      { eyebrow: "ANTHROPIC  ·  MED  ·  6–18 MO", title: "Claude Code Cloud",
        body: "Managed deployment for Claude Code projects. Already deploys via MCP; one container layer from owning the runtime." },
      { eyebrow: "OPENAI / CODEX  ·  MED-LOW  ·  12–24 MO", title: "Hosted runtime via ChatGPT Pro / Team",
        body: "OpenAI's posture is model-API first. Less likely solo but plausible via partnership." },
      { eyebrow: "CURSOR / WINDSURF  ·  HIGH  ·  6–12 MO", title: "Cloud preview → full SaaS",
        body: "Already lightweight cloud previews. Substrate-grade is the obvious next step." },
      { eyebrow: "DEFENCE", title: "Sprint, acquirability, brand vocabulary, drop-in architecture",
        body: "Capture first 5–15K customers before any lab ships. BYO-AI brand becomes acquisition target, not extinction." },
    ]);
}

function slide25() {
  appendixSlide(25, "APPENDIX  ·  RISKS REGISTER",
    ["Six risks.", "Stated honestly. Mitigations ready."],
    null,
    [
      { eyebrow: "HIGH · 01  ·  AI-LAB DISRUPTION", title: "Big-lab deployment in 6–18 mo", body: "Mit: early share, brand vocabulary, acquirable architecture. Outreach M0–4." },
      { eyebrow: "MED · 02  ·  PEAK COINCIDENCE",   title: ">30% sandboxes warm",            body: "Mit: 35-warm watermark → auto-provision second CAX31 (+£11.30/mo)." },
      { eyebrow: "LOW · 03  ·  COLD-START LATENCY", title: "10–15s wake from idle",          body: "Mit: \"sandbox warming up…\" splash in deploy-status callback." },
      { eyebrow: "LOW · 04  ·  STUDIO COPY DRIFT",  title: "Studio rails removed",           body: "Mit: copy actioned across index, docs, install, account." },
      { eyebrow: "LOW · 05  ·  LEGACY SANDBOXES",   title: "~13 on CAX21 with Studio",       body: "Mit: don't migrate. Density gains apply to new provisions only." },
      { eyebrow: "LOW · 06  ·  SNAPSHOT REGRESSION",title: "No click-to-snapshot in Studio", body: "Mit: AI tool triggers pg_dump via /api/tenants/<slug>/snapshot." },
    ]);
}

function slide26() {
  appendixSlide(26, "APPENDIX  ·  MRR BUILD-UP",
    ["Base £135K · upside £380K.", "How we get there at M18."],
    "Cohort × paid-conversion × ARPU × retention. Pessimistic / mid / sprint anchored on AI_COGS_SENSITIVITY.xlsx.",
    [
      { eyebrow: "SANDBOX SIGNUPS",      title: "~30K base · ~80K upside",        body: "Cumulative sandbox signups by M18. Sprint upside adds paid-acquisition lift on top of organic." },
      { eyebrow: "SANDBOX → LAUNCH (5%)", title: "~1,500 base · ~4,000 upside",    body: "Conversion holds 5%. Below 3% triggers reprice. Above 7% accelerates Growth funnel." },
      { eyebrow: "LAUNCH → GROWTH (12%)", title: "~180 base · ~600 upside",        body: "Growth conversion lifts to 15% in upside on stronger product surface area." },
      { eyebrow: "ACTIVE AFTER ~3%/MO CHURN", title: "~620L · ~130G base · ~1,700L · ~430G upside", body: "Active subs after monthly churn. Drives MRR build-up." },
      { eyebrow: "SUB MRR",              title: "£14.9K base · £43.8K upside",     body: "Launch (£15.80) + Growth (£39) blended. Setup amortised + token overage + affiliate stack on top." },
      { eyebrow: "M18 LANDING",          title: "£135K base · £380K upside MRR",   body: "Pessimistic case (3% conv., 5% churn) lands ~£60K M18. Sprint base = 5% / 3%. Upside adds paid lift." },
    ]);
}

function slide27() {
  appendixSlide(27, "APPENDIX  ·  USE OF FUNDS",
    ["How the raise is spent.", "By line item."],
    "Modelled at the £700K midpoint of the £500K–£1M range. Scales linearly above and below.",
    [
      { eyebrow: "PAID ACQUISITION  ·  60% · £420K", title: "Creator + retargeting + newsletters + events",
        body: "Creator £180K · Retargeting £140K · Newsletters £50K · Events £30K · Affiliate £20K." },
      { eyebrow: "FIRST HIRES  ·  25% · £175K", title: "Engineer + Marketer + Contractor",
        body: "Engineer #1 £95K (substrate, regions, mobile) · Marketer #1 £65K · Contractor £15K (design, devops, legal)." },
      { eyebrow: "INFRA + PARTNERSHIPS  ·  15% · £105K", title: "Second region + abuse + partner integration + compliance",
        body: "Second region £25K · Abuse + scaling £20K · Partner integration £25K · Wholesale negotiation £15K · SOC2 prep £20K." },
      { eyebrow: "DISCIPLINE", title: "Spend-gate at signup #100",
        body: "If conversion + churn miss validation by signup #100, paid spend pauses pending repricing. No spend lifts before the funnel signals." },
    ]);
}

function slide28() {
  appendixSlide(28, "APPENDIX  ·  CAPACITY MANAGEMENT",
    ["Two tiers.", "Two density strategies."],
    "Sandbox runs hot and cheap on a single CAX31 via overcommit + idle-suspend. Launch runs warm on CAX41 with bin-packing.",
    [
      { eyebrow: "SANDBOX  ·  CAX31  ·  £0.30/MO", title: "~45 tenants / host",
        body: "Drop Studio (saves ~400MB RAM + ~300MB disk). Memory overcommit + 16GB swap (1.5× density). 2h idle-suspend. 30-day archive + 7-day grace." },
      { eyebrow: "LAUNCH  ·  CAX41  ·  BIN-PACKED", title: "~25 tenants / host",
        body: "Per-tenant Docker network. Bin-packing via promote.py. Per-tenant port slice → Caddy routing. Per-tenant Studio + 3 mailboxes." },
      { eyebrow: "SANDBOX SCALE TRIGGER", title: "35-warm-concurrent → second CAX31",
        body: "Pressure watchdog reads /proc/pressure/memory. Alert at avg10 > 20. Operator provisions 2nd CAX31 (+£11.30/mo)." },
      { eyebrow: "LAUNCH SCALE + GROWTH MIGRATION", title: "22 tenants → cordon · 15 end-user signups → Growth",
        body: "22-tenants-per-host → cordon; route new promotes to next CAX41. 15th end-user signup migrates to fresh CAX31 dedicated VPS." },
    ]);
}

// ═════════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════════
[slide1, slide2, slide3, slide4, slide5, slide6, slide7, slide8, slide9, slide10,
 slide11, slide12, slide13, slide14, slide15, slide16, slide17, slide18, slide19,
 slide20, slide21, slide22, slide23, slide24, slide25, slide26, slide27, slide28]
  .forEach(fn => fn());

pres.writeFile({ fileName: "Hatchik-investor-v3.pptx" })
  .then(p => console.log("Wrote:", p));
