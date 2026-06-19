"""v1.4 processors package: text normalization utilities.

- text_processor.TextProcessor / text_processor singleton
- stopwords built-in lists
"""
from .text_processor import TextProcessor, text_processor
from . import stopwords

__all__ = ["TextProcessor", "text_processor", "stopwords"]