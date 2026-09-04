# Inbound Surveillance

Edge-AI wrench-time and garage ops platform. Read product architecture in `docs/PROJECT_STRUCTURE.md`.

## Design System
Always read `DESIGN.md` before making any visual or UI decisions.
All font choices, colors, spacing, radius, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any code that doesn't match `DESIGN.md`.

**Hard overrides (non-negotiable):**
- Typography: Bebas Neue (display) + IBM Plex Sans (body) + IBM Plex Mono (data)
- Accent: Terminal Green `#00FF41` on True Black `#000000` / Deep Graphite `#0D0D0D`
- Geometry: border-radius 0–4px max — no pills, no Soft ClearView Material chrome
- Ignore `docs/stitch/clearview-camera-hub/DESIGN.md` (superseded Soft blue / Inter / “no neon”)

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
