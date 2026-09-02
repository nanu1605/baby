"""v6 W5: what Baby can do on this machine, said plainly, before it is used.

The EULA covers this legally at install time -- which is exactly when nobody
reads. This is the same substance shown once, in the first-run wizard, in the
words a non-technical owner would use, with an explicit acknowledgement recorded
in setup.json (`disclosure_ack`).

Two rules shape the content:

  * It describes what the SHIPPED DEFAULTS actually do, not what the app is
    capable of if reconfigured. The public template ships `safety.mode: enforce`
    with an empty `auto_allow_app_close`, so "it asks first" is a true statement
    about this build -- and stays true only while that template does (a test
    pins the two together).
  * Cloud wording follows the install mode. Telling a cloud-only user their
    chats "can stay on this PC" would be false; telling a Full user everything
    goes to the cloud would be equally false.

This module holds no policy of its own -- the safety gate is frozen ground. It
only describes it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    """One disclosure line: a short title and the plain-language detail."""

    key: str
    title: str
    detail: str


# Shown for every install.
_COMMON: tuple[Item, ...] = (
    Item(
        key="actions",
        title="Baby can act on this PC",
        detail="It can run commands, open and close apps, read and write files, "
        "and browse the web -- the same things you could do yourself.",
    ),
    Item(
        key="confirm",
        title="It asks before anything changes",
        detail="Every action that would change something on your PC stops and "
        "asks you first. Nothing is auto-approved out of the box. You can see "
        "everything it did in the activity feed.",
    ),
    Item(
        key="local_data",
        title="Your conversations stay on this PC",
        detail="Chats, memory and settings are stored in a folder only you can "
        "read. Nothing is uploaded to Baby's authors, ever -- there is no "
        "account and no telemetry service.",
    ),
    Item(
        key="keys",
        title="Your API keys stay on this PC",
        detail="Keys you enter are saved locally and sent only to the provider "
        "they belong to. They are never logged and never shown in full again.",
    ),
    Item(
        key="mic",
        title='Say "Hey Jarvis" to talk to Baby',
        detail='The wake phrase is "Hey Jarvis", not "Hey Baby" -- the pretrained '
        "wake-word model this build ships has no phrase for Baby's own name. You can "
        "also press Ctrl+Alt+B to talk without it. Voice runs on this PC: audio is "
        "processed locally to hear the wake phrase and is not streamed anywhere "
        "while Baby is idle.",
    ),
    Item(
        key="responsibility",
        title="You are responsible for what you approve",
        detail="Baby does what you confirm. It can make mistakes, so read what "
        "it is asking to do before approving -- especially commands that delete "
        "things or spend money.",
    ),
)

_CLOUD_ONLY = Item(
    key="cloud",
    title="Your messages go to cloud AI providers",
    detail="This install has no local model, so what you type or say is sent to "
    "the cloud provider you configured, under their terms. Avoid pasting "
    "anything you would not send to a third party.",
)

_FULL = Item(
    key="cloud",
    title="Some messages go to cloud AI providers",
    detail="Cloud models answer by default because they are faster. A local "
    "model on your own GPU handles anything marked private, and takes over "
    "entirely when you are offline or have no key.",
)


def items(mode: str) -> list[dict]:
    """The disclosure list for an install mode, ordered as shown.

    Cloud sits second: it is the one with real privacy consequences, so it
    belongs near the top rather than buried under the reassuring lines.
    """
    cloud = _CLOUD_ONLY if mode == "cloud_only" else _FULL
    ordered = (_COMMON[0], cloud, *_COMMON[1:])
    return [{"key": i.key, "title": i.title, "detail": i.detail} for i in ordered]


def keys_shown(mode: str) -> set[str]:
    """The item keys a given mode shows -- used by tests and the repair view."""
    return {i["key"] for i in items(mode)}
