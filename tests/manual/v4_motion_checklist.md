# V4 manual acceptance — the motion system

Owner-run, visual polish pass. V4 gives the chrome a coherent, CSS-first motion layer
(no framer-motion, no lucide — DECISIONS #125): overlay enter animations, control
micro-interactions, emoji micro-animation on real state, and accents that track the live
pipeline hue — all collapsing under reduced-motion / performance mode / the 2D floor.
Automated coverage: `ui/app` `npm run build` + `npm test` (`motion.ts` level logic). This
is the eyeball + 60 fps pass automation can't cover; it doubles as the PR screenshot/GIF
source.

Prereq: on branch `feature/v4-native-3d-brain`. Build then start:
`npm --prefix ui/app run build` → `uv run python run.py --ui` → open
`http://127.0.0.1:8765/`. A GPU/CPU monitor helps for §5.

---

## §1 — Overlay enter animations

- [ ] Trigger a **confirm request** (a gated tool) → the dialog **fades + scales in**
  (backdrop fades); it does not just pop.
- [ ] Open the 🧠 **memory** dialog → same fade/scale in.
- [ ] Click a graph node → the **inspector drawer slides in** from the right.
- [ ] Open the **search** omnibox (Ctrl/⌘-K) and type → the results **pop in**.
- [ ] Cause a **toast** (e.g. kill switch) → it **slides in** from the right.
- [ ] On a narrow window, open the side panel → it **slides in** (mobile slide-over);
  the backdrop fades.

## §2 — Controls feel responsive

- [ ] Hover the header buttons (🎮 / 🧠 / ⚡ / ■ Stop) + the Chat/Activity tabs → colour
  eases in, not a hard snap; press → a subtle **scale-down** on click.
- [ ] Router health change (pull Wi-Fi / restore) → the **router dot eases** its colour;
  the state chip transitions rather than jumping.

## §3 — Emoji micro-animation (real state only)

- [ ] Toggle **game mode on** → 🎮 chip **pulses**; off → it goes still.
- [ ] Arm the one-turn **boost** → the boost chip **glows**; disarm → still.
- [ ] Force a governor **demote** (load the 9B) → the ⚙ **tier chip spins**; back to
  full3d → the chip is gone.
- [ ] Disconnect the backend briefly → the **reconnect pill pulses**; reconnect → gone.
- [ ] Nothing animates that isn't backed by real state (no idle fabricated motion).

## §4 — Cohesion + reduced-motion / perf collapse

- [ ] During a turn, the **active-tab underline + omnibox focus ring** shift hue with the
  pipeline (idle grey → listening green → thinking blue → speaking violet → executing
  amber), matching the sphere gauge.
- [ ] Toggle ⚡ **performance mode** → all decorative motion **stops** (no emoji loops, no
  enter animations — overlays appear instantly). Toggle off → motion returns.
- [ ] Turn on OS **"reduce motion"** → same calm behaviour automatically.
- [ ] Force the **2D floor** (`ui.brain: 2d` or a governor demote to 2d) → decorative
  motion collapses (`data-motion="off"`).

## §5 — Safety + performance

- [ ] **Confirm dialog is never gated behind animation:** on a confirm request, click
  **Approve** (and separately **Deny**, and **Esc**) → the action fires **immediately**
  and the dialog closes at once (no wait for an exit animation). This is the load-bearing
  safety check.
- [ ] **60 fps holds:** with motion on and a turn firing, the UI stays smooth —
  sustained **≈ ____ fps**, no jank from the CSS animations. If motion introduces a
  frame-budget regression, STOP and report.

## §6 — Gates

- [ ] `npm --prefix ui/app run build` + `npm --prefix ui/app test` green.
- [ ] `uv run pytest -q` green; `uv run pytest tests/test_safety.py -q` green;
  `uv run ruff check .` clean.
- [ ] `git branch --show-current` = `feature/v4-native-3d-brain`. Zero commits on master.

---

**Capture PR screenshots/GIF here.** If the confirm dialog's Approve/Deny is ever
delayed by an animation, or motion drops the frame budget, that is a blocker — escalate
before V5.
