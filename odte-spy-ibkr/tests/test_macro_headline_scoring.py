from odte_spy_ibkr.providers.macro_news import score_headline


def test_bearish_cpi_headline():
    assert score_headline("CPI inflation hotter than expected") < 0


def test_bullish_cut_headline():
    assert score_headline("Fed signals rate cut amid rally") > 0