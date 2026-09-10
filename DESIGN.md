# Design System — Inbound Surveillance

## Product Context
- **What this is:** Invisible edge-AI infrastructure for high-performance garages. Tracks billable wrench-time and payroll with zero manual input — from bay entry to Telegram scorecard.
- **Reliability layer:** Full-body / posture tracking keeps sessions alive when mechanics are under cars, masked, or facing away. Face ID is attendance assist when faces are visible — not the primary continuity mechanism.
- **Who it's for:** Performance / motorsport garage owners and operators who want relentless background automation, not another phone workflow.
- **Space/industry:** Garage ops + edge computer vision (not consumer NVR, not SMB shop CRM).
- **Project type:** Marketing landing + operator dashboard + Tauri desktop + edge hub.
- **Memorable thing:** Ruthless, invisible industrial efficiency — dark, gritty B2B edge-compute for greasy garage floors, not a Silicon Valley coffee shop.

## Aesthetic Direction
- **Direction:** Industrial / utilitarian overwatch (brutalist edges, terminal neon)
- **Decoration level:** Intentional and sparse — high-contrast neon against pitch black; no soft Material chrome
- **Mood:** Aggressive machine terminal. The monkey lockup (tactical helmet, camera-eye) owns the brand. Soft blue “quiet security SaaS” is forbidden.
- **Supersedes:** `docs/stitch/clearview-camera-hub/DESIGN.md` (ClearView: Inter, seed `#3B82F6`, 8px+ radii, “no neon”). Do not apply ClearView tokens to product UI.

## Typography
- **Display/Hero:** Bebas Neue — tall condensed industrial wordmark; logo text, all headings (h1–h3), primary CTAs
- **Body:** IBM Plex Sans — engineered terminal readability; paragraphs, nav links, forms, secondary UI
- **UI/Labels:** IBM Plex Sans (medium/semibold)
- **Data/Tables/Logs:** IBM Plex Mono — wrench timers, pose state, bay IDs, Telegram telemetry
- **Code:** IBM Plex Mono
- **Loading:** Google Fonts — `Bebas+Neue`, `IBM+Plex+Sans`, `IBM+Plex+Mono`
- **Blacklist for this product:** Inter, Roboto, Plus Jakarta Sans, Outfit, soft geometric “SaaS” stacks as primary body
- **Scale (landing / marketing):**
  - Display XL: `clamp(2.75rem, 4.2vw + 1rem, 4.5rem)` (~44–72px) — CSS `--fs-display`
  - H2: `clamp(2rem, 2.2vw + 1rem, 3rem)` (~32–48px) — `--fs-h2`
  - Lede / section copy: `clamp(1.125rem, 0.35vw + 1rem, 1.35rem)` — `--fs-lede`
  - Body: `clamp(1rem, 0.2vw + 0.95rem, 1.125rem)` — `--fs-body`
  - Mono / UI labels: `--fs-ui`
  - Large desktop (`min-width: 1280px`): content column up to 1200px so type and frame scale together
- **Navbar logo:** Bebas Neue, uppercase, letter-spacing ~0.08em beside the mark

## Color
- **Approach:** Restrained — one aggressive accent; everything else is graphite / white / muted
- **Background base:** `#000000` (True Black) — primary canvas
- **Surface / Deep Graphite:** `#0D0D0D` — panels, cards, chrome
- **Surface elevated:** `#121212` · hover `#1A1A1A`
- **Terminal Green (sole accent):** `#00FF41` — live states, primary CTAs, focus rings, active HUD / iris
- **Stealth Green (support only):** `#0A7A2F` — borders, trailing glow, inactive-but-related chrome (never a second brand color)
- **Crisp White:** `#FAFAFA` — primary type and mascot silhouette
- **Muted:** `#8E9297` — secondary copy
- **Forbidden:** Soft blue seeds (`#3B82F6` and Material blue families), purple/cyan “cyber SaaS” gradients, “no neon” quiet themes
- **Semantic:** success = Terminal Green; warning `#FABD34`; error `#FF4D4D`; info stays muted white — never blue as brand
- **Dark mode:** Product is dark-first. Light mode is not a first-class surface.

## Spacing
- **Base unit:** 8px
- **Density:** Compact-comfortable (ops density without crush)
- **Scale:** 2xs(2) xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48) 3xl(64)

## Layout
- **Approach:** Hybrid — marketing hero is poster / brand-first; app surfaces are dense grid
- **Grid:** Marketing max ~1120px; dashboard fluid with tight gutters
- **Border radius (geometry — hard rule):**
  - `none`: 0px (panels, inputs, tables preferred)
  - `sm`: 2px
  - `md`: 4px (maximum allowed)
  - **Forbidden:** 8px+, 12px+, 18px cards, `9999px` pills on product UI
  - Marketing may use at most 4px on buttons — no friendly full-pill CTAs
  - **Exception — device chrome & native third-party UI (Telegram):** phone bezels may use ~30px radius; Telegram chat bubbles may use ~12px / Telegram-native corner radii. Applies only to marketing device mockups that imitate real OS/app chrome. All other product/marketing UI remains radius ≤4px.
- **Header chrome:** Sticky site header must stay solid/opaque (`--absolute-black` / `#000000`) during scroll — no translucent `color-mix` or alpha that lets page content bleed through the nav.
- **Navigation muscle memory:** Logo mark + wordmark are one control. On the landing page they point to `#top` (smooth scroll back to hero). On subpages they link home (`index.html`). Never split mark and text into separate hit targets.

## Motion
- **Approach:** Intentional, short — live pulse on Terminal Green dots; no scroll theater
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:** micro(50–100ms) short(150–250ms) medium(250–400ms)
- **Allowed:** Live indicator pulse, subtle HUD glow on active bay
- **Forbidden:** Soft bounce, long parallax heroes, decorative blob motion

## Messaging & Conversion Hierarchy (marketing)
Money first. Edge-AI is infrastructure, not the headline.

- **Hero:** zero AI / “Edge compute” jargon in H1 or eyebrow. Speak recovering unbilled labor, margins, liability. Proof plane is brutalist CCTV `<video>` (`1px solid #00FF41`, radius 0) — not Bay telemetry chips.
- **Features (top 3 only):** (1) Active vs. Idle Tracking, (2) Margins Loss Prevention, (3) Safety Tracking. Pitch deck, landing feature copy, and [`privacy.html`](privacy.html) must use these exact names. **Attendance is never a primary feature** — payroll clock stays background telemetry / Telegram scorecard only.
- **CTA budget:** max one neon primary booking CTA per viewport. Hero stays visible; final / mobile-sticky Book reveals only after ~50vh scroll.
- **ROI framing:** kicker = daily capital bleed; primary metric = projected monthly revenue recovered. Interactive projection — owner sets minutes + rate.
- **ROI honesty override (2026-09-08):** baseline copy may say “conservative 45 min/day unbilled baseline reported by local operators.” Still do not invent per-shop measured minutes for a named garage without written permission.

## Interaction / Hick’s lock (marketing)
Hick’s Law: more choices → slower decisions. Marketing surfaces lock to **one** primary path so a garage owner does not stall.

- **One primary action:** open the bay-demo modal. Nothing else is a “main” conversion.
- **Canonical labels:**
  - Hero / final CTA / sticky / header / pricing Turnkey neon / modal submit: **`[ BOOK DEMO ]`**
- **One accent treatment:** only the primary path uses `btn-neon` / Terminal Green fill. All other actions are `btn-ghost` or quiet text links.
- **Choice budget per viewport (hero):** brand + one headline + one support line + **one** CTA group — primary only, or primary + one ghost “See how it works” → `#architecture`. No second neon in the hero fold.
- **Nav (judge scan):** max four anchors — Problem (`#roi`), Engine (`#demo`), Field Notes (`#field-notes`), Team (`#team`) — plus one neon `[ BOOK DEMO ]`. Pricing / FAQ stay on-page, not in chrome.
- **Pricing:** both plans visible for clarity; **only Turnkey** uses `btn-neon`. Software = `btn-ghost`. Both open the same demo modal with `data-plan`.
- **Lead capture:** `#demo-form` `data-formspree` must be a live POST endpoint (Formspree `https://formspree.io/f/…`, bare Formspree ID, or FormSubmit AJAX). Prefer Formspree when the crew has a dashboard ID. Empty endpoint is forbidden for “silent success” UX — if unset, JS must fall back to mailto / loud failure, never fake a received lead.
- **Boot:** skip by default; opt-in theater only via `?boot=1`.
- **Forbidden:** competing neon CTAs with different verbs in the same fold; a second “primary” accent color for buttons; inventing alternate primary labels (“Recover Your Lost Revenue”, “Book turnkey install” as neon hero verbs); form success UI without a real capture path; TBD / placeholder case outcomes on the live marketing surface; Attendance as a hero/feature headline.

## Location / Map (marketing)
Location is **city hub presence** — show we are based in Phnom Penh, Cambodia. Never invent a street address. No directions CTA.

- **Basemap:** Leaflet + Esri World Dark Gray (base + reference labels), taller frame (~400px). No CARTO raster tiles (those burn “API KEY REQUIRED” without a key). Suppress pastel Google roads / retail POIs.
- **Pin:** one Terminal Green pulse marker at Phnom Penh city center (coords live in JS only — never displayed as UI “codes”).
- **Frame:** ≤4px radius, Terminal Green / stealth border, corner brackets allowed.
- **Overlay label:** ambient mono strip — **`Phnom Penh · Cambodia`**. No lat/lng, no `PP-NODE` / tactical codes.
- **Directions:** none. No “Open directions”, no Google Maps deep-link CTA on this block.
- **Attribution:** on-map Leaflet watermark forbidden. Required credit is a discreet **off-map** muted mono line under the frame (`Map © Esri`).
- **Motion:** short pin pulse only; respect `prefers-reduced-motion` (static pin).
- **Forbidden:** light Google Maps iframe; invented street address; displayed fake location codes; on-map API attribution badge; CARTO unauthenticated watermarked tiles; second neon CTA competing with `[ BOOK DEMO ]`.

## Contact / sales channel
- **Primary sales email:** `inboundcrew82@gmail.com` (footer, forms, legal pages, FormSubmit / mailto fallback). Never use a different public sales inbox.
- **Primary sales contact:** direct Telegram founder line — display `+855 96 518 8669`, href `https://t.me/+855965188669` (new tab, `noopener noreferrer`).
- **Footer Contact:** city line (Phnom Penh) + email + `> DIRECT TELEGRAM:` link. No `#location` footer link. Do not use `@Inbound_Surveillance_bot` as the primary sales CTA.
- **Demo modal:** keep form submit; Telegram bypass under `[ BOOK DEMO ]`: `Or message us directly on Telegram: +855 96 518 8669`. Dropdown = **Current shop cameras** (qualify hardware for walkthrough — not billing plan). Operator ack = one short authority sentence; full indemnification stays on [`privacy.html`](privacy.html) / install contracts only.
- **Product bot:** `@Inbound_Surveillance_bot` may remain in scorecard demo/FAQ copy — that is product dispatch, not sales contact.

## Token contract (canonical)
Source files: [`src/theme/tokens.ts`](src/theme/tokens.ts), [`src/theme/digital-overwatch.css`](src/theme/digital-overwatch.css)

| Token | Value |
|-------|--------|
| `--color-bg-base` | `#000000` |
| `--color-surface-charcoal` | `#0D0D0D` |
| `--color-surveillance-green` / Terminal Green | `#00FF41` |
| `--color-stealth-green` | `#0A7A2F` |
| `--color-crisp-white` | `#FAFAFA` |
| `--radius-max` | `4px` |
| `--font-display` | `"Bebas Neue", sans-serif` |
| `--font-body` | `"IBM Plex Sans", sans-serif` |
| `--font-mono` | `"IBM Plex Mono", monospace` |

## Bilingual (EN | KH)
- **Mechanism:** Live English text stays in the DOM. Mapped nodes carry `data-kh`. `js/main.js` `initLangToggle()` stashes `data-en` from `textContent` on first switch, then swaps `data-kh` / `data-en`. Skips any `[data-kh]` node that still has element children (so inputs/links/SVG are never wiped).
- **Control:** Compact `[ EN | KH ]` in `.header-actions` — IBM Plex Mono, Terminal Green when pressed, radius ≤4px. System setting, not a corporate dropdown. Present on landing + team.
- **Persistence:** `localStorage.inbound_lang` (`en` | `kh`). Default `en`. `document.documentElement.lang` = `en` / `km`.
- **Latin stays Latin:** Edge-AI, Telegram, IP, KIT Hub, Inbound Surveillance, Phnom Penh, person names, emails, phones, form `value=` attributes.
- **Tone:** Khmer must read as a local founder on a Phnom Penh bay floor — short, punchy, money-aware. Forbidden: government/HR/textbook register.
- **Typography (KH):** Kantumruy Pro via `html[lang="km"]` token remap (`--font-display` / `--font-sans` / `--font-mono`). EN keeps Bebas + IBM Plex. KH resets letter-spacing, raises line-height to 1.6, and bumps optical size (~8–10%) so tall vowels/subscripts do not clip.
- **Scope:** Full visible marketing copy on `index.html` + `team.html` (pitch path). Legal page bodies (`about` / `privacy` / `terms`) deferred.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-09-04 | Industrial Overwatch system created | Design consultation: greasy-floor efficiency; pose-first reliability; kill ClearView softness |
| 2026-09-04 | Bebas Neue + IBM Plex Sans + IBM Plex Mono | Matches monkey lockup; Plex = machine terminal, not Inter HR dashboard |
| 2026-09-04 | Accent `#00FF41`, radius ≤4px | Reinstate grit/neon; override ClearView blue seed / soft radii / “no neon” |
| 2026-09-04 | Pose/body primary; Face ID assist | Mechanics under cars / masked / facing away |
| 2026-09-07 | Hick’s lock on marketing CTAs | One neon primary (Book a Free Bay Demo); kill decision fatigue before Thursday pitch |
| 2026-09-07 | Tactical PP-NODE location HUD | Carto Dark + neon pin; brand immunity vs pastel Google; city hub only |
| 2026-09-08 | FirstWave AIM 70 landing lock | Judge-first scan: Book Pilot Bay; pruned nav; boot bypass; Field Discovery matrix; interactive ROI; kill TBD/cases |
| 2026-09-08 | Desktop type scale raised | Hero/h2/lede/body clamps match Display XL; 1200px container at 1280px+; kill parallax; Request Pilot |
| 2026-09-08 | Mentor revenue DNA pivot | Video-first hero; Revenue Recovery / Turnover / Loss Prevention; capital-bleed ROI; attendance demoted; REQUEST PILOT BAY |
| 2026-09-08 | Primary CTA → `[ BOOK DEMO ]` | Hick’s lock: one verb on header, hero, pricing, sticky, team, modal submit |
| 2026-09-08 | Location → city presence | Drop directions + PP-NODE/coords theater; HD retina map; off-map OSM/CARTO credit only |
| 2026-09-08 | Basemap → Esri Dark Gray | Kill CARTO “API KEY REQUIRED” tile burn-in; keep dark city presence without keys |
| 2026-09-09 | [PRICING UPDATE] $99/mo/bay + $295 upfront | Replaced consumer baseline ($39/$250) with commercial B2B anchors across cards, ROI logic, FAQ, modals, and schema |
| 2026-09-09 | Outcome-led pricing copy + PP pilot bonus | Bullets sell revenue/efficiency/setup outcomes; free on-site integration; $295 = camera + cable/mount only |
| 2026-09-09 | [ROI UPDATE] Calculator UI consolidated into a single terminal grid; baseline aligned to $99/mo B2B anchor; redundant Model/Fix cards removed. | Monthly software vs monthly revenue apples-to-apples; Day 04 payback in footer |
| 2026-09-09 | Sales contact → direct Telegram +855 96 518 8669 | Replace footer bot CTA with founder line; strip footer Location link; modal bypass; scorecard bot stays in product copy |
| 2026-09-09 | Pitch triad lock + MVP privacy | Features = Active vs. Idle Tracking / Margins Loss Prevention / Safety Tracking; privacy.html pre-incorporation pilot entity; Zero-Cloud once in §1 |
| 2026-09-09 | Sales contact unified | Public sales = inboundcrew82@gmail.com + Telegram +855 96 518 8669 everywhere; kill deploy@ drift; terms/thank-you/llms sales CTAs use founder line |
| 2026-09-09 | Terms ↔ Privacy legal twin | terms.html MVP entity + disclaimer; scope = pitch triad; operator notice = safety/active-idle; contact stays inboundcrew82@gmail.com |
| 2026-09-09 | FAQ rewritten for non-technical owners | Kill model/RTSP/edge jargon in FAQ; garage-owner language only |
| 2026-09-09 | Team + About owner-language scrub | Kill model/RTSP/inference jargon on public marketing pages; bios map to B2B pillars |
| 2026-09-10 | Demo modal conversion friction | Short authority Operator Ack (full legal stays on privacy/install); dropdown = Current shop cameras, not Plan interest |
| 2026-09-10 | Device chrome / Telegram radius exception | Phone bezel ~30px + chat bubbles ~12px only on marketing mockups; product UI stays ≤4px |
| 2026-09-10 | Sticky header solid chrome + logo → `#top` | Kill translucent header bleed; logo/mark unified back-to-top on landing |
| 2026-09-10 | FirstWave consistency audit | Features triad locked on landing; HUD/Telegram Mechanic labels; payroll demoted in meta (background dual-clock only) |
| 2026-09-10 | EN|KH `data-kh` bilingual layer | Lightweight DOM swap + localStorage; bay-floor Khmer tone; Latin for Edge-AI/Telegram/KIT Hub; freeze web for FirstWave after ship |
| 2026-09-10 | Full EN|KH sweep index + team | Expand `data-kh` across landing + team bios/roles; legal page bodies still deferred |
| 2026-09-10 | Khmer Kantumruy Pro via `html[lang="km"]` | Glyph-safe tracking/line-height/optical bump; EN Bebas/Plex unchanged |
