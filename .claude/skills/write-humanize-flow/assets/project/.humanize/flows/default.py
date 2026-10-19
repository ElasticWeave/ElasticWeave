"""Explicit project root using the launcher-sealed child flow descriptor."""

from __future__ import annotations

import os
from typing import NamedTuple

from hmz.flows import Agent, flow, load


class Agents(NamedTuple):
    """The executor and independent reviewer reused by the called slot."""

    executor: Agent
    reviewer: Agent


def _sealed_slot() -> str:
    """Return the one sealed slot descriptor bound by the launcher."""
    expected = os.environ.get("HUMANIZE_SLOT_SHA256", "").strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise RuntimeError("HUMANIZE_SLOT_SHA256 must contain the validator-approved digest")
    value = os.environ.get("HUMANIZE_SLOT_FD", "")
    if not value.isdecimal() or not value:
        raise RuntimeError("HUMANIZE_SLOT_FD must name the launcher-sealed child descriptor")
    # The launcher seals and hashes this descriptor before passing it through;
    # the root must only carry that exact opaque descriptor to Humanize's loader.
    return f"/proc/self/fd/{value}"


@flow
def run(agents: Agents, task: str) -> None:
    """Run the sealed child; evaluating load here remains a fresh dispatch."""
    load(_sealed_slot())(agents, task)
