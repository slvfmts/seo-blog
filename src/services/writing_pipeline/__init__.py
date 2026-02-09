"""
Writing Pipeline - Multi-Stage Article Generation

Разделение процесса написания статей на независимые этапы с контрактами:
1. Intent Analysis - анализ интента и формирование редакционного контракта
2. Research - сбор фактуры через поиск
3. Structure - построение outline статьи
4. Drafting - написание текста по outline
5. Editing - редактура и markdown-вёрстка
6. Linking - внутренняя перелинковка через keyword-based индекс
7. Meta - генерация SEO-метаданных (meta_title, meta_description, slug)
"""

from .core.runner import PipelineRunner
from .core.context import WritingContext
from .contracts import IntentResult, ResearchResult, OutlineResult, TopicBoundaries, MetaResult

__all__ = [
    "PipelineRunner",
    "WritingContext",
    "IntentResult",
    "ResearchResult",
    "OutlineResult",
    "TopicBoundaries",
    "MetaResult",
]
