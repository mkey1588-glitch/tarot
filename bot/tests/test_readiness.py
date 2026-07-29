"""Tests for the launch gates.

The value of this module is that it cannot be satisfied by someone deciding
it has been. Each check reads the artefact that would have changed if the
work were actually done, so these tests mostly verify that the checks are
still wired to those artefacts.
"""

import pytest

from bot import readiness
from bot.config import Config


@pytest.fixture
def config():
    return Config.from_env({})


# --- The gates as they stand today ----------------------------------------

def test_all_six_gates_are_present():
    keys = [gate.key for gate in readiness.gates()]
    assert keys == ["engine_reviewed", "prompts_written", "ai_disclosure",
                    "crisis_routing", "tokushoho", "legal_review"]


def test_we_are_not_ready_for_real_users():
    """If this starts failing, either the practitioner has delivered and the
    world has changed, or someone has quietly weakened a check."""
    assert readiness.ready_for_real_users() is False


def test_the_open_practitioner_rulings_are_what_blocks_the_engine_gate():
    gate = next(g for g in readiness.gates() if g.key == "engine_reviewed")
    assert not gate.met
    for question in ("P1", "P2", "P3", "P4", "P5", "P6"):
        assert question in gate.detail


def test_placeholder_prompts_block_their_gate():
    gate = next(g for g in readiness.gates() if g.key == "prompts_written")
    assert not gate.met
    assert "P5" in gate.detail


def test_the_disclosure_gate_is_met_structurally():
    """It is the one gate that engineering can close on its own, and it is
    closed: outbound.reading() applies it where a generation cannot skip it."""
    gate = next(g for g in readiness.gates() if g.key == "ai_disclosure")
    assert gate.met


def test_the_helpline_gate_is_open_until_counsel_confirms():
    gate = next(g for g in readiness.gates() if g.key == "crisis_routing")
    assert not gate.met
    assert "TODO(legal)" in gate.detail


def test_the_tokushoho_gate_turns_on_with_payment(config):
    assert next(g for g in readiness.gates(config)
                if g.key == "tokushoho").met
    paid = Config.from_env({"STRIPE_SECRET_KEY": "sk_test"})
    assert not next(g for g in readiness.gates(paid)
                    if g.key == "tokushoho").met


def test_the_legal_gate_closes_only_with_a_recorded_date(config):
    assert not next(g for g in readiness.gates(config)
                    if g.key == "legal_review").met
    reviewed = Config.from_env({"LEGAL_REVIEW_COMPLETED_ON": "2026-09-01"})
    gate = next(g for g in readiness.gates(reviewed)
                if g.key == "legal_review")
    assert gate.met and "2026-09-01" in gate.detail


# --- The two thresholds ----------------------------------------------------

def test_friends_and_family_is_a_lower_bar_than_real_users():
    """CLAUDE.md draws this line explicitly: friends-and-family smoke
    testing is fine before all six gates. So nothing on that list blocks a
    board demo, and the floor is a separate, shorter list."""
    assert readiness.ready_for_friends_and_family() is True
    assert readiness.ready_for_real_users() is False


def test_the_floor_is_not_part_of_the_six():
    six = {gate.key for gate in readiness.gates()}
    assert {gate.key for gate in readiness.floor()}.isdisjoint(six)


def test_the_floor_is_disclosure_and_a_wired_crisis_path():
    """A board member forwards a link, and anyone at all might type 死にたい
    into a box. Neither of those can wait for the practitioner."""
    assert {gate.key for gate in readiness.floor()} == \
           {"disclosure_applied", "crisis_wired"}
    assert all(gate.met for gate in readiness.floor())


def test_the_crisis_floor_check_is_distinct_from_counsel_confirming_numbers():
    """The gate asks whether counsel signed off. The floor asks the narrower
    question of whether a 死にたい message reaches a helpline instead of a
    model — a property of the code, true today."""
    gate = next(g for g in readiness.gates() if g.key == "crisis_routing")
    floor_check = next(g for g in readiness.floor() if g.key == "crisis_wired")
    assert not gate.met
    assert floor_check.met


def test_the_floor_would_fail_if_the_crisis_screen_stopped_gating_the_model():
    import inspect
    source = inspect.getsource(readiness._crisis_routing_is_wired)
    assert "_CRISIS_PATTERNS" in source
    assert "ScreeningToken(_MINT)" in source   # screening mints the token
    assert "0120-279-338" in source            # the 24h toll-free line


def test_blocking_lists_only_unmet_gates():
    assert all(not gate.met for gate in readiness.blocking())
    assert readiness.blocking()


def test_summary_counts_gates():
    assert readiness.summary().endswith("/6 gates met")


# --- The checks read real artefacts ---------------------------------------

def test_the_ruling_scan_reads_decisions_md():
    pending = readiness._pending_rulings()
    assert pending, "DECISIONS.md has no pending rulings — did the file move?"
    assert all(p.startswith("P") for p in pending)


def test_the_helpline_check_reads_safety_py():
    """Wired to the TODO(legal) marker, not to a boolean someone can flip."""
    import inspect
    source = inspect.getsource(readiness._helplines_confirmed)
    assert "TODO(legal)" in source
    assert "SAFETY" in source
