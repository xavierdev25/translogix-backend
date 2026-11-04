"""Lexicon-based sentiment (heuristic, labelled as such). Feeds CRIT-002 only."""

import json
from pathlib import Path

from app.contracts.conversation import Sentiment
from app.shared.text import fold, tokens


class LexiconSentiment:
    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.version = str(raw.get("version", "?"))
        self._very = {fold(w) for w in raw["muy_negativo"]}
        self._negative = {fold(w) for w in raw["negativo"]}
        self._positive = {fold(w) for w in raw["positivo"]}

    def classify(self, text: str) -> Sentiment:
        words = tokens(text)
        very = sum(w in self._very for w in words)
        negative = sum(w in self._negative for w in words)
        positive = sum(w in self._positive for w in words)
        shouting = text.count("!") >= 2 or (len(text) > 12 and text.isupper())
        if very >= 2 or (very and (negative or shouting)):
            return Sentiment.MUY_NEGATIVO
        if very or negative > positive:
            return Sentiment.NEGATIVO
        if positive > negative:
            return Sentiment.POSITIVO
        return Sentiment.NEUTRO
