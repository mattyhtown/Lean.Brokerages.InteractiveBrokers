from __future__ import annotations

from research_data.http import LoaderError
from research_data.pipeline import Sentiment

try:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
except ImportError:
    # Both come from the optional `local` extra; LocalFinbert reports how to install it.
    torch = None

MODEL = "ProsusAI/finbert"
# Pinned so a later upload to the model repository can neither change stored scores nor run unreviewed.
REVISION = "0bc2ac10dda6929bcb1f77ccc43c7ec5c424f031"
_MAX_TOKENS = 512


class LocalFinbert:
    """Scores text with FinBERT on this machine. The first use downloads the model (about 440 MB)."""

    def __init__(self) -> None:
        if torch is None:
            raise LoaderError(
                "Local FinBERT needs torch and transformers: run `poetry install --extras local`"
            )
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION)
            model = AutoModelForSequenceClassification.from_pretrained(
                MODEL, revision=REVISION, use_safetensors=True
            )
        except OSError as exc:
            raise LoaderError(f"Cannot load {MODEL}: {exc}") from None
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._model = model.to(self._device).eval()
        self._labels = [str(model.config.id2label[index]).lower() for index in range(model.config.num_labels)]

    def __call__(self, text: str) -> Sentiment:
        inputs = self._tokenizer(text, truncation=True, max_length=_MAX_TOKENS, return_tensors="pt").to(self._device)
        with torch.no_grad():
            probabilities = self._model(**inputs).logits.softmax(dim=-1)[0].tolist()
        return Sentiment(MODEL, dict(zip(self._labels, probabilities)))
