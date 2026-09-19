"""No React hook may sit below an early return (v6.0.2).

`RepairPanel` shipped in the 6.0.2 candidate with `const [autoBusy] = useState(false)`
written under `if (!open) return null`, next to the handler that used it. Closed, the
component ran 19 hooks; open, 20. React answers that with

    Rendered more hooks than during the previous render.

which is an INVARIANT, not a dev-only warning -- the production bundle threw it too.
The whole app went blank the moment anyone opened Settings, and the feature that
dialog had just gained was reported as missing, because from outside those are the
same thing.

Nothing caught it. The repo has no DOM or component tests by design
(`ui/app/vitest.config.ts` says so), there is no eslint, and `tsc` does not model the
Rules of Hooks -- so a crash on the app's only settings surface passed 1102 pytest,
206 vitest, a clean typecheck and a payload audit, and was found by opening the page.

This gate is deliberately written against the PATTERN and not against RepairPanel. The
last time a guard here listed the offenders it knew about (DECISIONS #149), the next
component was added beside them and inherited the exact bug the guard existed to stop.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "ui" / "app" / "src"

#: A hook call at the top level of a component body. Two-space indentation is what
#: makes it top level -- a hook inside a nested callback or handler is indented
#: further, and is not what this is about. `useBrain.getState()` is not a hook call
#: and does not match: the `(` must follow the name directly.
_HOOK = re.compile(r"^ {2}(?! )(?:const |let |var )?[^\n]*?\buse[A-Z]\w*\(")
#: An early return at the top level of a component body.
_EARLY_RETURN = re.compile(r"^  if \(.*\)\s*return\b|^  if \(.*\)\s*\{?\s*$")
#: `return null;` / `return (` inside a two-space `if` block, the other common shape.
_GUARD_RETURN = re.compile(r"^  if \(.*\) return ")


def _tsx_files() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.tsx") if not p.name.endswith(".test.tsx"))


#: The start of a top-level function -- one per component. A file holds several
#: (`InspectorDrawer.tsx` has ten), and each has its OWN hook order: a hook on the
#: first line of the second component is correct, and scanning the file as one body
#: reports it as sitting after the first component's guard.
_COMPONENT = re.compile(r"^(?:export default |export )?(?:function|const) \w")


def _blocks(text: str) -> list[tuple[int, list[str]]]:
    """Split into top-level function bodies, each with its starting line number."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _COMPONENT.match(line)]
    if not starts:
        return [(0, lines)]
    bounds = starts + [len(lines)]
    return [(bounds[n], lines[bounds[n] : bounds[n + 1]]) for n in range(len(starts))]


def _offenders(text: str) -> list[tuple[int, str]]:
    """Hook calls that appear after a guard return WITHIN THE SAME component."""
    found: list[tuple[int, str]] = []
    for offset, lines in _blocks(text):
        guard = next((i for i, ln in enumerate(lines) if _GUARD_RETURN.match(ln)), None)
        if guard is None:
            continue
        found += [
            (offset + i + 1, ln.strip())
            for i, ln in enumerate(lines)
            if i > guard and _HOOK.match(ln)
        ]
    return found


@pytest.mark.parametrize("path", _tsx_files(), ids=lambda p: p.name)
def test_no_hook_below_an_early_return(path: Path):
    bad = _offenders(path.read_text(encoding="utf-8"))
    assert not bad, (
        f"{path.name} calls a React hook after a top-level early return: {bad}. "
        "The component then runs a different number of hooks depending on which "
        "branch it took, and React throws 'Rendered more hooks than during the "
        "previous render' -- in production as well as in dev. Move it up with the "
        "other hooks."
    )


def test_the_gate_can_actually_see_the_bug_it_was_written_for():
    """A source-shape gate that cannot fail is decoration. This is the exact shape
    that shipped, reduced to the smallest thing that still reproduces it."""
    shipped = (
        "export default function Panel() {\n"
        '  const [a, setA] = useState("");\n'
        "\n"
        "  if (!open) return null;\n"
        "\n"
        "  const [autoBusy, setAutoBusy] = useState(false);\n"
        "  return null;\n"
        "}\n"
    )
    assert _offenders(shipped), "the gate would not have caught the bug it exists for"

    fixed = (
        "export default function Panel() {\n"
        '  const [a, setA] = useState("");\n'
        "  const [autoBusy, setAutoBusy] = useState(false);\n"
        "\n"
        "  if (!open) return null;\n"
        "  return null;\n"
        "}\n"
    )
    assert not _offenders(fixed), "the gate fires on correct code"


def test_the_gate_ignores_hooks_that_are_not_at_the_top_level():
    """A `useState` inside a nested handler is fine, and flagging it would push the
    next person to disable the gate rather than read it."""
    nested = (
        "export default function Panel() {\n"
        "  if (!open) return null;\n"
        "  const onClick = () => {\n"
        "    const x = useThing();\n"
        "  };\n"
        "  useBrain.getState().doThing();\n"
        "  return null;\n"
        "}\n"
    )
    assert not _offenders(nested)
