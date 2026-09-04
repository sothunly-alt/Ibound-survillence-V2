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
  - Display XL: clamp ~2.8–4.6rem, Bebas, tracking ~0.04–0.08em, uppercase where brand-facing
  - H2: ~2–3rem Bebas
  - H3: ~1.25–1.5rem Bebas
  - Body: 1rem / 1.55–1.65 IBM Plex Sans
  - Mono metrics: 0.8–0.95rem IBM Plex Mono
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
