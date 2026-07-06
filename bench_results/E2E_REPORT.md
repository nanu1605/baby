# Live E2E regression report

**16/16 passed** — router state during run: `cloud`

Brain-dependent tests (T03, T07–T09) exercise the serving model's tool
discipline and under-read when the state is not `cloud` (the local 9B
serves during congestion, and its discipline drops on a long summary).
Score the N5 full course in a `cloud` window; the remaining tests are
deterministic pipeline checks.

| # | test | result | note |
|---|---|---|---|
| T01 | stats sanity | PASS | state=cloud |
| T02 | plain chat turn | PASS | brain=daily 12.2s |
| T03 | tool turn (get_time) | PASS | tool=True time_in_reply=True attempts=1 |
| T04 | memory round-trip | PASS | Your e2e probe word is kumquat. |
| T05 | privacy pin (read_file) | PASS | pinned=True brain=daily attempts=1 |
| T06 | language pin (Devanagari) | PASS | pinned=True |
| T07 | browser goto+read | PASS | The main heading on example.com is: Example Domain

Next: Ch |
| T08 | browser screenshot | PASS | new=['shot_1783337129.png'] attempts=1 |
| T09 | screen awareness | PASS | I'm currently viewing https://example.com/ with its main hea |
| T10 | background task | PASS | status=done — queued without dialog (benign spec is ALLOW), attempts=1 |
| T11 | game-mode VRAM cycle | PASS | unloaded=True reloaded=True |
| T12 | kill switch cancels turn | PASS | status=cancelled |
| T13 | heavy escalation attempted | PASS | state=degraded at run time — fallback ladder served (by design) |
| T14 | game-mode escape hatch (no model) | PASS | 0.0s ui_served=[] |
| T15 | GET endpoints | PASS | all 200 |
| T16 | orchestrator project (--with-project) | PASS | status=done attempts=1 |