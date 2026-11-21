from __future__ import annotations

from datetime import date


def walk_forward_windows(
    days: list[date],
    *,
    train_days: int,
    test_days: int,
) -> list[tuple[list[date], list[date]]]:
    """Non-overlapping train/test folds; last fold's test window is used for holdout scoring."""
    if train_days <= 0 or test_days <= 0:
        return [(days, [])]
    folds: list[tuple[list[date], list[date]]] = []
    i = 0
    while i + train_days + test_days <= len(days):
        train = days[i : i + train_days]
        test = days[i + train_days : i + train_days + test_days]
        folds.append((train, test))
        i += test_days
    if not folds:
        split = max(1, len(days) // 2)
        return [(days[:split], days[split:])]
    return folds


def holdout_test_days(days: list[date], *, train_days: int, test_days: int) -> list[date]:
    """Final walk-forward test slice — score improvement candidates only here."""
    folds = walk_forward_windows(days, train_days=train_days, test_days=test_days)
    return folds[-1][1] if folds else days