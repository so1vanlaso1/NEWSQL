"""Analytic pipeline package (plan §7-9, §11-20).

Phase 12 ships the front of the pipeline behind ``ANALYTIC_ENABLED``:
- ``mode_detector``          heuristic 4-mode router (plan §3)
- ``review_target_resolver`` previous-result -> ReviewSeed (plan §8)
- ``context_builder``        AnalyticContext assembly (plan §11)
- ``models``                 AnalyticContext / ReviewSeed shapes

Later phases add the planner, task runner, profiler, writer, and controller.
"""
