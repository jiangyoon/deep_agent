"""附件下载口径（create_attachment_reader）的连库回归测试。

验证三件事：正常附件命中、不存在 id 拒绝、可下载集合与
ATTACHMENTS 白名单 + download_policy + 父来源 REVIEWED_FILE
三个条件同时成立（口径不漂移）。库不可达时整组跳过。
"""

import pytest

pytest.importorskip("psycopg")

from career_agent.core.app_db import open_app_pool  # noqa: E402
from career_agent.knowledge.notice_attachments import (  # noqa: E402
    create_attachment_reader,
)


@pytest.fixture(scope="module")
def pool():
    try:
        ctx = open_app_pool()
        with ctx as p:
            yield p
    except Exception as exc:  # 环境无库时不失败，只跳过
        pytest.skip(f"应用库不可达：{exc}")


def test_reader_returns_display_name_and_path(pool):
    reader = create_attachment_reader(pool)
    with pool.connection() as db:
        row = db.execute("""
            SELECT a.id FROM public.kb1_attachments a
            JOIN public.kb1_sources f ON f.id = a.attachment_source_id
            JOIN public.kb1_sources p ON p.id = a.main_source_id
            WHERE f.validity_status = '现行有效'
              AND f.status IN ('review_dataset', 'opaque_attachment_no_body_parsing',
                               'history_metadata_only')
              AND a.download_policy = 'requires_authenticated_internal_access_and_release_review'
              AND p.status = 'review_dataset'
              AND p.validity_status = '现行有效'
            LIMIT 1
        """).fetchone()
    assert row, "库里没有满足完整口径的附件，测试数据异常"
    found = reader(row["id"])
    assert found is not None
    display_name, original_path = found
    assert display_name and original_path


def test_reader_rejects_unknown_id(pool):
    reader = create_attachment_reader(pool)
    assert reader("A-999-999") is None


def test_downloadable_set_matches_full_criteria(pool):
    """reader 的可下载集合 == 白名单∩policy∩父来源有效，不多不少。"""
    reader = create_attachment_reader(pool)
    with pool.connection() as db:
        rows = db.execute("""
            SELECT a.id,
                   (p.validity_status = '现行有效'
                    AND p.status = 'review_dataset') AS parent_ok
            FROM public.kb1_attachments a
            JOIN public.kb1_sources f ON f.id = a.attachment_source_id
            JOIN public.kb1_sources p ON p.id = a.main_source_id
            WHERE f.validity_status = '现行有效'
              AND f.status IN ('review_dataset', 'opaque_attachment_no_body_parsing',
                               'history_metadata_only')
              AND a.download_policy = 'requires_authenticated_internal_access_and_release_review'
        """).fetchall()
    assert rows, "白名单为空，测试数据异常"
    mismatches = [r["id"] for r in rows if (reader(r["id"]) is not None) != r["parent_ok"]]
    assert not mismatches, f"口径漂移（reader 与父来源校验不一致）：{mismatches}"
