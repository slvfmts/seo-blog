"""Tests for pipeline stage registration — all 10 stages must be present."""

import pytest
from unittest.mock import MagicMock

from src.services.writing_pipeline.stages import (
    IntentStage,
    ResearchStage,
    StructureStage,
    DraftingStage,
    EditingStage,
    LinkingStage,
    SeoPolishStage,
    QualityGateStage,
    MetaStage,
    FormattingStage,
)

EXPECTED_STAGES = [
    "intent",
    "research",
    "structure",
    "drafting",
    "editing",
    "linking",
    "seo_polish",
    "quality_gate",
    "meta",
    "formatting",
]

STAGE_CLASSES = [
    IntentStage,
    ResearchStage,
    StructureStage,
    DraftingStage,
    EditingStage,
    LinkingStage,
    SeoPolishStage,
    QualityGateStage,
    MetaStage,
    FormattingStage,
]


class TestStageRegistration:
    """Regression: commit 413f69c — missing stages in completion list."""

    def test_all_10_stages_importable(self):
        """All 10 stage classes must be importable from stages __init__."""
        assert len(STAGE_CLASSES) == 10

    def test_all_10_stages_have_name(self):
        """Each stage class must have a 'name' property."""
        client = MagicMock()
        for cls in STAGE_CLASSES:
            # Some stages need extra kwargs
            kwargs = {"client": client, "model": "test-model"}
            if cls == ResearchStage:
                kwargs["serper_api_key"] = ""
                kwargs["jina_api_key"] = ""
            elif cls == LinkingStage:
                kwargs["linker"] = None
            elif cls == FormattingStage:
                kwargs["openai_api_key"] = ""
                kwargs["openai_proxy_url"] = ""
                kwargs["openai_proxy_secret"] = ""
                kwargs["ghost_url"] = ""
                kwargs["ghost_admin_key"] = ""

            stage = cls(**kwargs)
            assert hasattr(stage, "name"), f"{cls.__name__} missing 'name'"
            assert isinstance(stage.name, str), f"{cls.__name__}.name not str"

    def test_stage_names_match_expected(self):
        """Stage names must match the expected list exactly."""
        client = MagicMock()
        names = []
        for cls in STAGE_CLASSES:
            kwargs = {"client": client, "model": "test-model"}
            if cls == ResearchStage:
                kwargs.update(serper_api_key="", jina_api_key="")
            elif cls == LinkingStage:
                kwargs["linker"] = None
            elif cls == FormattingStage:
                kwargs.update(openai_api_key="", openai_proxy_url="",
                              openai_proxy_secret="", ghost_url="",
                              ghost_admin_key="")
            names.append(cls(**kwargs).name)

        assert names == EXPECTED_STAGES

    def test_stages_init_exports_all(self):
        """__all__ in stages/__init__.py must list all 10 classes."""
        from src.services.writing_pipeline import stages
        assert len(stages.__all__) == 10
