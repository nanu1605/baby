# Live E2E regression report

**11/15 passed**

| # | test | result | note |
|---|---|---|---|
| T01 | stats sanity | PASS | state=cloud |
| T02 | plain chat turn | PASS | brain=daily 14.8s |
| T03 | tool turn (get_time) | **FAIL** | tool=False time_in_reply=False |
| T04 | memory round-trip | PASS | I'm not finding any stored fact about an e2e probe word call |
| T05 | privacy pin (read_file) | PASS | pinned=True brain=daily |
| T06 | language pin (Devanagari) | PASS | pinned=True |
| T07 | browser goto+read | **FAIL** | (no response) |
| T08 | browser screenshot | **FAIL** | new=[] |
| T09 | screen awareness | **FAIL** | (no response) |
| T10 | background task | PASS | status=done |
| T11 | game-mode VRAM cycle | PASS | unloaded=True reloaded=True |
| T12 | kill switch cancels turn | PASS | status=cancelled |
| T13 | heavy escalation attempted | PASS | state=degraded at run time — fallback ladder served (by design) |
| T14 | game-mode escape hatch (no model) | PASS | 0.0s routes=[] |
| T15 | GET endpoints | PASS | all 200 |