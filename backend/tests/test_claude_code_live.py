"""Opt-in: run the adapter against the real ``claude`` program on this computer (``YTC_LIVE_CLAUDE=1 python -m pytest tests/test_claude_code_live.py``).

It spends a few tokens of the signed-in account's plan. The ordinary suite never runs it.
"""

from __future__ import annotations

import os

import pytest

from app.providers import claude_code as cc

pytestmark = pytest.mark.skipif(os.environ.get("YTC_LIVE_CLAUDE") != "1" or cc.locate() is None, reason="set YTC_LIVE_CLAUDE=1 with Claude Code installed and signed in")


def test_the_doctor_passes_against_the_real_program():
    d = cc.ClaudeCodeClient().doctor()
    assert d["ok"] is True, d["problems"]
    assert d["tool_free"] and d["supported"] and d["signed_in"] and d["model"]


def test_a_real_call_returns_text_and_usage():
    g = cc.ClaudeCodeClient().generate(system="You answer in one short sentence.", prompt="Name one colour that suits a garden birthday party.")
    assert g.text and g.output_tokens > 0 and g.model and g.num_turns == 1 and g.tools_denied == []
