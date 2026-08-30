"""T12b: measured rates, simulated bands, enforced ceilings - and no point estimates."""

import pytest

from repro import estimates as e


# --- per-attempt: a measured rate, nothing more ----------------------------

def test_attempt_eta_is_the_observed_seed_rate():
    assert e.attempt_eta(done=2, total=5, elapsed_s=40) == 60.0   # 20s/seed x 3 left
    assert e.attempt_eta(done=4, total=5, elapsed_s=40) == 10.0


def test_no_eta_before_a_seed_has_finished():
    """Nothing has been measured yet, so there is nothing honest to report."""
    assert e.attempt_eta(0, 5, 40) is None
    assert e.attempt_eta(3, 0, 40) is None
    assert e.attempt_eta(2, 5, 0) is None


# --- the prior chain -------------------------------------------------------

def test_prior_prefers_this_run_then_history_then_config():
    current = [100, 200, 300]
    history = [1000, 2000, 3000]
    assert e.duration_prior(current, history)["basis"] == "current_run"
    assert e.duration_prior([], history)["basis"] == "ledger_history"
    assert e.duration_prior([], [])["basis"] == "config_default"
    # two samples is not a median worth trusting; fall through
    assert e.duration_prior([100, 200], history)["basis"] == "ledger_history"


def test_config_default_prior_carries_no_band():
    """One number from a config file is not a measurement and must not be dressed up
    as one, so the band collapses and the UI is told to show the ceiling alone."""
    prior = e.duration_prior([], [], default_s=900)
    assert prior["low_s"] is None and prior["high_s"] is None
    fleet = e.fleet_eta(0, [], 4, 2, prior)
    assert fleet["low_s"] is None and "ceiling only" in fleet["note"]


# --- queue simulation ------------------------------------------------------

def test_queue_simulation_over_pool_width():
    # 4 pending, width 2, 100s each -> two rounds of 100s
    assert e.simulate_queue(0, [], 4, 2, 100.0) == 200.0
    # 5 pending over width 2 -> three rounds
    assert e.simulate_queue(0, [], 5, 2, 100.0) == 300.0
    assert e.simulate_queue(0, [], 0, 2, 100.0) == 0.0


def test_running_attempts_are_credited_for_time_already_spent():
    running = [{"started": 0, "ttl_s": 10_000}]
    # 60s in on a 100s median leaves 40s, then one pending attempt on the free slot
    assert e.simulate_queue(60, running, 1, 2, 100.0) == 100.0


def test_simulation_never_projects_past_the_ttl_the_platform_enforces():
    """The provider kills the sandbox at its TTL whatever the median says."""
    running = [{"started": 0, "ttl_s": 30}]
    assert e.simulate_queue(0, running, 0, 1, 100.0) == 30.0


def test_fleet_reports_a_band_never_a_single_number():
    prior = e.duration_prior([100, 200, 300])
    fleet = e.fleet_eta(0, [], 2, 1, prior)
    assert fleet["low_s"] < fleet["mid_s"] < fleet["high_s"]
    assert fleet["basis"] == "current_run" and fleet["n_samples"] == 3


# --- the ceiling is enforced, not estimated --------------------------------

def test_ceiling_sums_remaining_ttls():
    open_ = [{"started": 0, "ttl_s": 600}, {"started": 0, "ttl_s": 900}]
    assert e.run_ceiling_s(open_, now=0) == 1500.0
    assert e.run_ceiling_s(open_, now=300) == 900.0      # 300s consumed on each


def test_ceiling_is_bounded_by_the_budget_that_refuses_to_overspend():
    open_ = [{"started": 0, "ttl_s": 100_000}]
    assert e.run_ceiling_s(open_, now=0, budget_remaining_minutes=10) == 600.0


def test_ceiling_never_goes_negative_once_a_ttl_is_spent():
    assert e.run_ceiling_s([{"started": 0, "ttl_s": 60}], now=9999) == 0.0


def test_ceiling_covers_the_remaining_round_cap():
    assert e.run_ceiling_s([], now=0, rounds_remaining=2, round_ceiling_s=300) == 600.0


# --- the honesty rule ------------------------------------------------------

def test_no_run_completion_while_an_implementer_round_is_pending():
    """With a further round possible, any completion time is a guess about work that
    has not been proposed yet."""
    fleet = e.fleet_eta(0, [], 3, 2, e.duration_prior([100, 200, 300]))
    assert e.run_completion(fleet, round_pending=True) is None
    assert e.run_completion(fleet, round_pending=False)["mid_s"] == fleet["mid_s"]


def test_no_run_completion_without_measurements():
    fleet = e.fleet_eta(0, [], 3, 2, e.duration_prior([], []))
    assert e.run_completion(fleet, round_pending=False) is None


def test_rounds_are_counted_and_capped_never_predicted():
    assert e.round_label(2, 4) == "round 2 of ≤4"
