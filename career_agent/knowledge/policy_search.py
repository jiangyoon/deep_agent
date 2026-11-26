"""网页 Agent 的政策原文检索工具（Milvus 混合检索 + PostgreSQL 回表）。

三步编排（本文件是编排层，实现在 policy_sources / policy_retrieval）：
  ① find_source_ids   (policy_sources)  按渠道/地区/年度/类型选出有效来源白名单
  ② retrieve_candidates (policy_retrieval) BM25 + 向量双路检索 Milvus，
                                          按 RRF 风格名次合并出候选条款 ID
  ③ load_ranked_sections (policy_sources) 用同一快照回 PG 取条款原文，
                                          白名单复核 + 按检索排名定序

异常分两类返回：PG/Milvus/embedding 故障 → unavailable（见下方 except）；
白名单为空 → no_verified_source（渠道下没有已入库文件，不是故障）。
"""

import logging
from typing import Annotated

import psycopg
from langchain_core.tools import tool
from pydantic import Field
from pymilvus.exceptions import MilvusException

from career_agent.knowledge.policy_retrieval import retrieve_candidates
from career_agent.knowledge.policy_sources import Scope, find_source_ids, load_ranked_sections, region_required


logger = logging.getLogger(__name__)


def create_policy_search_tool(pool):
    @tool
    def search_policy_sections(
        query: Annotated[str, Field(min_length=1, max_length=300, pattern=r"^[^\x00]+$")],
        scope: Scope | None = None,
        region: Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[^\x00]+$")] | None = None,
        year: Annotated[int, Field(ge=1900, le=2200)] | None = None,
        source_kind: Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[^\x00]+$")] | None = None,
        source_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")] | None = None,
        limit: Annotated[int, Field(ge=1, le=6)] = 4,
    ) -> dict:
        """查政策原文，返回文件名、渠道、适用年度、条款正文和原件位置。

        query 写政策关键词。scope 用已确认的申报渠道，地方人社需要 region。
        year 用四位申报年度，约束年度通知及其年度附件。edition_year 是奖励目录版次，
        专项奖励的适用年度以条款原文为准。
        source_kind 可填库内类型，如资格条件、年度通知、制度办法。source_id 填已经定位的文件 ID。
        核对个人资格时分别核验证书对应、资历起算、年限和截止日期。
        文件只说明证书批准时间时，仍需核实渠道认可的资历起算日期。
        """
        query = query.strip()
        region = region.strip() if region else None
        if not query:
            return {"status": "invalid_input", "results": []}
        # 地方人社必须先确认省市：不同地区文件口径完全不同，混合返回
        # 会让模型把外省条件当成员工所在地的条件。
        if scope == "地方人社" and not region:
            return region_required()

        filters = {
            "scope": scope,
            "region": region,
            "year": year,
            "source_kind": source_kind,
            "source_id": source_id,
        }
        try:
            # ① 白名单：先在 PG 里把「渠道 + 地区 + 年度 + 类型」的来源
            #    ID 选出来，检索只在白名单内做，防止越界召回。
            source_ids = find_source_ids(pool, filters)
            if not source_ids:
                return {"status": "no_verified_source", "results": []}

            # ② Milvus 混合检索（BM25 + 向量），返回候选条款 ID 与检索模式；
            #    ③ 回 PG 取原文并复核白名单，按检索排名输出。
            candidates, mode = retrieve_candidates(query, source_ids, limit)
            results = load_ranked_sections(pool, candidates, filters, limit) if candidates else []
        except (psycopg.Error, MilvusException, ValueError) as error:
            logger.warning("政策原文检索失败（%s）", type(error).__name__)
            return {"status": "unavailable", "message": "政策原文检索暂不可用。", "results": []}

        return {"status": "found" if results else "no_match", "retrieval_mode": mode, "results": results}

    return search_policy_sections
