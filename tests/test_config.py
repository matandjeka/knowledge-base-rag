"""Configuration tests."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_have_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.bm25_k1 == 1.5
    assert settings.bm25_b == 0.75
    assert settings.lexical_title_boost == 0.5
    assert settings.lexical_min_score == 0
    assert settings.fusion_rrf_k == 60
    assert settings.fusion_candidate_multiplier == 3
    assert settings.fusion_vector_weight == 0.4
    assert settings.fusion_sentence_window_weight == 0.25
    assert settings.fusion_graph_weight == 0.25
    assert settings.fusion_lexical_weight == 0.1


def test_settings_require_at_least_one_positive_fusion_weight() -> None:
    with pytest.raises(ValidationError, match="fusion retriever weight"):
        Settings(
            _env_file=None,
            fusion_vector_weight=0,
            fusion_sentence_window_weight=0,
            fusion_graph_weight=0,
            fusion_lexical_weight=0,
        )
