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

## Motion
- **Approach:** Intentional, short — live pulse on Terminal Green dots; no scroll theater
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:** micro(50–100ms) short(150–250ms) medium(250–400ms)
- **Allowed:** Live indicator pulse, subtle HUD glow on active bay
- **Forbidden:** Soft bounce, long parallax heroes, decorative blob motion

## Messaging & Conversion Hierarchy (marketing)
Money first. Edge-AI is infrastructure, not the headline.

- **Hero:** zero AI / “Edge compute” jargon in H1 or eyebrow. Speak recovering unbilled labor, margins, liability. Proof plane is brutalist CCTV `<video>` (`1px solid #00FF41`, radius 0) — not Bay telemetry chips.
- **Features (top 3 only):** (1) Active Revenue Recovery, (2) Workflow & Route Optimization / bay turnover, (3) Loss Prevention & Safety. **Attendance is never a primary feature** — payroll clock stays background telemetry / Telegram scorecard only.
- **CTA budget:** max one neon primary booking CTA per viewport. Hero stays visible; final / mobile-sticky Book reveals only after ~50vh scroll.
- **ROI framing:** kicker = daily capital bleed; primary metric = projected monthly revenue recovered. Interactive projection — owner sets minutes + rate.
- **ROI honesty override (2026-09-08):** baseline copy may say “conservative 45 min/day unbilled baseline reported by local operators.” Still do not invent per-shop measured minutes for a named garage without written permission.

## Interaction / Hick’s lock (marketing)
Hick’s Law: more choices → slower decisions. Marketing surfaces lock to **one** primary path so a garage owner does not stall.

- **One primary action:** open the bay-demo modal. Nothing else is a “main” conversion.
- **Canonical labels:**
  - Hero: **[ REQUEST PILOT BAY ]**
  - Final CTA / sticky (scroll-gated): **Book Pilot Bay**
  - Header (space-constrained): **Book Pilot**
  - Modal submit: **Request Pilot**
- **One accent treatment:** only the primary path uses `btn-neon` / Terminal Green fill. All other actions are `btn-ghost` or quiet text links.
- **Choice budget per viewport (hero):** brand + one headline + one support line + **one** CTA group — primary only, or primary + one ghost “See how it works” → `#architecture`. No second neon in the hero fold.
- **Nav (judge scan):** max four anchors — Problem (`#roi`), Engine (`#demo`), Field Notes (`#field-notes`), Team (`#team`) — plus one neon Book Pilot. Pricing / FAQ stay on-page, not in chrome.
- **Pricing:** both plans visible for clarity; **only Turnkey** uses `btn-neon`. Software = `btn-ghost`. Both open the same demo modal with `data-plan`.
- **Lead capture:** `#demo-form` `data-formspree` must be a live POST endpoint (Formspree `https://formspree.io/f/…`, bare Formspree ID, or FormSubmit AJAX). Prefer Formspree when the crew has a dashboard ID. Empty endpoint is forbidden for “silent success” UX — if unset, JS must fall back to mailto / loud failure, never fake a received lead.
- **Boot:** skip by default; opt-in theater only via `?boot=1`.
- **Forbidden:** competing neon CTAs with different verbs in the same fold; a second “primary” accent color for buttons; inventing alternate primary labels (“Recover Your Lost Revenue”, “Book turnkey install” as neon hero verbs); form success UI without a real capture path; TBD / placeholder case outcomes on the live marketing surface; Attendance as a hero/feature headline.

## Location / Map (marketing)
Location is a **tactical node HUD**, not consumer map chrome. City hub only until a permanent shop address is published — never invent a street address.

- **Basemap:** dark tiles (e.g. Leaflet + Carto Dark Matter). Suppress pastel Google roads / retail POIs.
- **Pin:** one Terminal Green pulse marker at the published city coords (`11.5564, 104.9282` for Phnom Penh). Label: `PP-NODE // PHNOM PENH` in IBM Plex Mono.
- **Frame:** ≤4px radius, Terminal Green / stealth border, corner brackets allowed; telemetry strip over the map is ambient, not a CTA.
- **Directions:** one `btn-ghost` “Open directions” → Google Maps city search (or real address when published). Never neon on the location block.
- **Motion:** short pin pulse only; respect `prefers-reduced-motion` (static pin).
- **Forbidden:** light Google Maps iframe as the hero visual; invented street address; second neon CTA competing with Book Pilot Bay.

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
