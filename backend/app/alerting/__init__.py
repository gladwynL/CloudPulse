"""Alert evaluation and incident lifecycle.

Layers: ``evaluator`` (pure state machine), ``lifecycle`` (incident/event writes),
``engine`` (runs inside the check-completion transaction). SQL lives in ``repositories.alerts``.
"""
