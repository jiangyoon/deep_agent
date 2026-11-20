"""从 Milvus 取候选条款 ID，合并 BM25 与向量检索的排名。

检索策略——两路混合（Hybrid）：
  BM25 稀疏路（sparse_vector）  精确关键词强项：文号、证书名、「破格」等术语
  稠密向量路（dense_vector）     语义相似强项：问法与原文措辞不同也能召回
两路得分量纲不同，不能直接加总，按名次融合（_merge_rankings，RRF 风格）。

降级设计：单路失败不中断（embedding 服务或 BM25 索引任一故障时返回
可用一路），retrieval_mode 告知模型实际用了哪路；两路全挂才抛异常，
由上层转成 unavailable。

一致性保障：_embed_query 会对比 Milvus 索引描述里记录的模型名/维度与
配置项，不一致直接报错——换 embedding 模型必须全量重建索引，宁可拒绝
检索也不允许静默用错向量空间。
"""

import logging
import math
import time
from contextlib import closing
from functools import lru_cache

import httpx
from pymilvus import MilvusClient
from pymilvus.exceptions import MilvusException

from career_agent import config


logger = logging.getLogger(__name__)
# 建库时以「model=...; dim=...」写入 collection description，检索前读取校验。
COLLECTION = "career_policy_sections"
# 索引描述缓存窗口（秒）：换模型/重建索引后最迟一分钟内被感知。
INDEX_REFRESH_SECONDS = 60
# 每路多取几倍候选，给名次融合去重留余量。
CANDIDATES_PER_RESULT = 3
# RRF 常数：名次得分 1/(60+名次)，60 压平头部名次差距，避免单路独大。
RANK_OFFSET = 60


@lru_cache(maxsize=8)
def _index_embedding(uri, db_name, token, timeout, refresh_window):
    """以分钟窗口为缓存键，读取索引的模型标识和实际向量维度。

    模型名与维度由建库脚本写进 collection description（约定格式
    「key=value; key=value」），避免每次查询都 describe_collection。
    """
    with closing(MilvusClient(uri=uri, db_name=db_name, token=token, timeout=timeout)) as client:
        index = client.describe_collection(COLLECTION, timeout=timeout)
    description = {}
    for part in (index.get("description") or "").split(";"):
        name, separator, value = part.partition("=")
        if separator:
            description[name.strip()] = value.strip()
    model = description.get("embedding", "").strip()
    dense_field = next((field for field in index["fields"] if field["name"] == "dense_vector"), None)
    if not model or dense_field is None:
        raise ValueError("索引缺少向量模型或字段")
    return model, int(dense_field["params"]["dim"])


def _embed_query(query, key, connection):
    """调用 embedding 服务把问题转向量，全程校验模型/维度/数值。

    校验链（任一失败抛 ValueError，上层降级到 BM25 单路）：
      索引模型 vs 配置模型 → 索引维度 vs 配置维度 → 响应条数与索引
      → 向量长度与维度 → 每个分量是有限 float32 范围内的数值。
    """
    refresh_window = int(time.monotonic() // INDEX_REFRESH_SECONDS)
    model, dim = _index_embedding(**connection, refresh_window=refresh_window)
    configured_model = config._setting("EMBEDDING_MODEL")
    configured_dim = config._setting("EMBEDDING_DIM")
    if configured_model and configured_model != model:
        raise ValueError("查询模型与索引模型不一致")
    if configured_dim and int(configured_dim) != dim:
        raise ValueError("查询维度与索引维度不一致")

    payload = {"model": model, "input": [query]}
    if (config._setting("EMBEDDING_REQUEST_DIMENSIONS") or "true").lower() == "true":
        payload["dimensions"] = dim
    response = httpx.post(
        config._setting("EMBEDDING_API_URL"),
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=float(config._setting("EMBEDDING_TIMEOUT") or "15"),
        follow_redirects=False,
    )
    response.raise_for_status()
    entries = response.json()["data"]
    if len(entries) != 1 or entries[0]["index"] != 0:
        raise ValueError("Embedding 响应条数或索引异常")
    vector = entries[0]["embedding"]
    if not isinstance(vector, list) or len(vector) != dim or not all(
        type(value) in (int, float) and abs(value) <= 3.4e38 and math.isfinite(value)
        for value in vector
    ):
        raise ValueError("Embedding 的向量维度或数值异常")
    return vector


def _search(client, query, field, metric, source_ids, limit, timeout):
    """在 Milvus 单路上执行检索，返回 [(section_id, source_id), ...]。

    白名单经 filter='source_id in [...]' 下推到 Milvus（服务器侧过滤，
    不会把 limit 名额浪费在白名单外）；返回前再按 Python 侧集合复核
    一次，防服务器版本对 filter 语义的差异。
    """
    batches = client.search(
        COLLECTION,
        data=[query],
        anns_field=field,
        filter="source_id in {source_ids}",
        filter_params={"source_ids": source_ids},
        output_fields=["source_id"],
        search_params={"metric_type": metric, "params": {}},
        limit=limit,
        timeout=timeout,
    )
    if not batches:
        return []
    allowed = set(source_ids)
    hits = []
    for hit in batches[0]:
        section_id = hit.get("section_id") or hit.get("id")
        source_id = hit.get("entity", {}).get("source_id")
        if section_id and source_id in allowed:
            hits.append((section_id, source_id))
    return hits


def _merge_rankings(rankings):
    # 两路得分量纲不同。按名次计分，共同命中的条款累加两路分数。
    scores = {}
    for ranking in rankings:
        for position, key in enumerate(dict.fromkeys(ranking), start=1):
            scores[key] = scores.get(key, 0) + 1 / (RANK_OFFSET + position)
    return sorted(scores, key=scores.get, reverse=True)


def retrieve_candidates(query, source_ids, limit):
    """两路共用来源清单，单路失败时返回可用一路的结果。

    返回 (candidates, mode)：candidates 是按融合名次排序的
    [(section_id, source_id), ...]；mode ∈ {'hybrid', 'bm25', 'dense'}。
    两路全失败时抛 ValueError，由 policy_search 统一转 unavailable。
    """
    host = config._setting("MILVUS_HOST")
    if not host:
        raise ValueError("MILVUS_HOST is missing")
    port = config._setting("MILVUS_PORT") or "19630"
    connection = {
        "uri": f"http://{host}:{port}",
        "db_name": config._setting("MILVUS_DB_NAME") or "career",
        "token": config._setting("MILVUS_TOKEN"),
        "timeout": float(config._setting("MILVUS_TIMEOUT") or "15"),
    }
    candidates_per_route = limit * CANDIDATES_PER_RESULT
    rankings = {}
    with closing(MilvusClient(**connection)) as client:
        # BM25 路：Milvus 2.6 原生稀疏向量，无需外部 embedding 服务，
        # 永远先试它——embedding 服务故障时它单独也能撑起检索。
        try:
            rankings["bm25"] = _search(
                client, query, "sparse_vector", "BM25",
                source_ids, candidates_per_route, connection["timeout"],
            )
        except MilvusException as error:
            logger.warning("政策 BM25 检索失败（%s）", type(error).__name__)

        # 稠密向量路：问题先过 embedding 服务，模型/维度与索引不一致
        # 时降级为 BM25 单路，不静默用错向量空间。
        key = config._setting("EMBEDDING_API_KEY")
        if key:
            try:
                vector = _embed_query(query, key, connection)
                rankings["dense"] = _search(
                    client, vector, "dense_vector", "COSINE",
                    source_ids, candidates_per_route, connection["timeout"],
                )
            except (
                httpx.HTTPError, httpx.InvalidURL, MilvusException,
                KeyError, IndexError, TypeError, ValueError,
            ) as error:
                logger.warning("政策向量检索失败（%s）", type(error).__name__)

    if not rankings:
        raise ValueError("政策检索的两条路径均不可用")
    mode = "hybrid" if len(rankings) == 2 else next(iter(rankings))
    return _merge_rankings(rankings.values()), mode
