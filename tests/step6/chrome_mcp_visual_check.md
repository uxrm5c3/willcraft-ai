# Chrome MCP Visual Verification — Round 5

For visual regressions that programmatic tests can't catch (color, layout,
hover states, banner visibility, font rendering), use Chrome MCP for a
manual visual confirmation pass.

## Why this is separate from the autonomous loop

`bug_checklist.py` catches every PROGRAMMATIC bug (data shape, field
presence, will text content, chat pattern). It runs in seconds, no
authentication needed.

Chrome MCP catches VISUAL bugs:
- Banner color (Tailwind `text-accent-700` was undefined → invisible
  white-on-purple — see §10x.179)
- Element overlap / z-index issues
- Hover-only tooltip content
- Print stylesheet differences
- Mobile responsive breakpoints

Chrome MCP requires `request_access` which interrupts the autonomous
loop. Run it manually after each significant UI change.

## Quick visual-check checklist (ad-hoc, run after any UI change)

For each item, take a screenshot and verify the result.

### Step 10 Review page (https://will.alantanjb.com/wizard/step/10)

| Check | Expected | Bug refs |
|-------|----------|----------|
| Upfront amber banner visible at top | Yes — "X gifts have missing required fields" header in amber-100 background | §10x.140 |
| Per-gift amber border-l-4 + missing pill | Yes on every gift with missing fields | §10x.140 |
| "Fill now" link present per gift | Links to `/wizard/step/6` | §10x.140 |
| "Go to Step 6" CTA button on top banner | amber-600 bg, hover→amber-700, white text | §10x.140 |
| White-text-on-white invisible elements | None | §10x.179 |
| Validation Errors banner (red) when has_errors | Visible if any ERROR-severity validation result | §10x.121 |

### Generate Will button

| Check | Expected | Bug refs |
|-------|----------|----------|
| Blue when no errors | bg-primary-600 + text-white | §10x.121 |
| Amber when errors | bg-amber-500 + label "Generate with Missing Fields…" | §10x.121 |
| Override modal opens on click (when has_errors) | Modal lists errors + Cancel + "Generate anyway" buttons | §10x.121 |

### Chat right-pane snapshot

| Check | Expected | Bug refs |
|-------|----------|----------|
| Step indicator pulses on the actual current step | Matches `_current_stage_num` | §10x.38 |
| Step 6 expander stays open after click (5s+ no auto-close) | User-set state preserved across 5s polls | §10x.98 |

## How to run a Chrome MCP pass

1. `request_access` for Chrome with the production URL
2. Navigate to the wizard step 10 for KOID test client
3. For each row above, screenshot the relevant element
4. Compare visually against expected
5. If any FAIL → file as bug-table row + fix in template

## Why this is NOT in `autonomous_loop.sh`

`request_access` blocks waiting for user OK. The autonomous loop must
run unattended. Visual checks are valuable but don't run silently.

The right separation:
- `autonomous_loop.sh` — run this every iteration, must exit 0
- `chrome_mcp_visual_check.md` — run ad-hoc after UI changes,
  manual confirmation gate
