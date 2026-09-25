from __future__ import annotations

import json

import pytest

from controller_config import __version__
from controller_config.app_release_summary import load_app_release_summary
from controller_config.i18n import ENGLISH, JAPANESE, SIMPLIFIED_CHINESE


def test_packaged_release_summary_matches_the_app_and_stays_customer_facing() -> None:
    developer_terms = {
        "api", "build_id", "commit", "git", "manifest", "qt", "thread",
        "接口", "构建编号", "提交记录", "线程",
    }

    for language in (SIMPLIFIED_CHINESE, ENGLISH, JAPANESE):
        summary = load_app_release_summary(language)

        assert summary.version == __version__
        assert summary.from_version == "0.1.92"
        assert 1 <= len(summary.highlights) <= 4
        joined = " ".join(summary.highlights).casefold()
        assert not any(term in joined for term in developer_terms)


def test_release_summary_for_another_version_is_rejected(tmp_path) -> None:
    path = tmp_path / "app-release-summary.json"
    path.write_text(
        json.dumps(
            {
                "version": "0.0.0",
                "highlights": {
                    language: ["Customer-facing improvement"]
                    for language in (SIMPLIFIED_CHINESE, ENGLISH, JAPANESE)
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match"):
        load_app_release_summary(SIMPLIFIED_CHINESE, path)


def test_release_summary_requires_the_user_delivery_baseline(tmp_path) -> None:
    path = tmp_path / "app-release-summary.json"
    path.write_text(
        json.dumps(
            {
                "version": __version__,
                "highlights": {
                    language: ["Customer-facing improvement"]
                    for language in (SIMPLIFIED_CHINESE, ENGLISH, JAPANESE)
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="from_version"):
        load_app_release_summary(SIMPLIFIED_CHINESE, path)
