from odte_spy_ibkr.config import Settings
from odte_spy_ibkr.improvement.evaluator import EvaluationResult
from odte_spy_ibkr.improvement.experiment import ExperimentCandidate
from odte_spy_ibkr.improvement.promoter import should_promote


def test_promoter_rejects_without_margin():
    settings = Settings(PAPER=True, IMPROVEMENT_AUTO_PROMOTE_PAPER=True)
    active = {"metrics": {"sharpe_rolling": 1.6}}
    challenger = EvaluationResult(
        candidate=ExperimentCandidate("x", {}),
        max_rolling_sharpe=1.62,
        max_drawdown=0.05,
        score=1.5,
    )
    decision = should_promote(settings, active, challenger)
    assert not decision.promoted


def test_promoter_accepts_strong_challenger():
    settings = Settings(PAPER=True, IMPROVEMENT_AUTO_PROMOTE_PAPER=True, IMPROVEMENT_SHARPE_MARGIN=0.05)
    active = {"metrics": {"sharpe_rolling": 1.4}}
    challenger = EvaluationResult(
        candidate=ExperimentCandidate("x", {"min_credit": 0.2}),
        max_rolling_sharpe=1.7,
        max_drawdown=0.08,
        score=1.6,
    )
    decision = should_promote(settings, active, challenger)
    assert decision.promoted