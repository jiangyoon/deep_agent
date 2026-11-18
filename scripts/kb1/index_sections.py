"""将 PostgreSQL 的条款写入已经建好的 Milvus 集合。默认只预览。"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from create_vector_store import (
    COLLECTION, ID_LIMIT, TEXT_LIMIT, CareerError, MilvusConfig, ensure_kb_collection,
    load_local_env, positive_int,
)


class BatchTooLarge(CareerError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def boolean(name, default):
    value = os.getenv(name, default).lower()
    if value not in {"true", "false"}:
        raise CareerError(f"{name} must be true or false")
    return value == "true"


@dataclass(frozen=True)
class Config(MilvusConfig):
    embedding_url: str
    batch_size: int
    embedding_timeout: int
    request_dimensions: bool

    @classmethod
    def from_env(cls):
        url = os.getenv("EMBEDDING_API_URL", "http://36.137.78.150:18001/v1/embeddings")
        endpoint = urlsplit(url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username is not None or endpoint.query or endpoint.fragment):
            raise CareerError("EMBEDDING_API_URL must be an HTTP(S) endpoint without credentials or query")
        return cls(
            **vars(MilvusConfig.from_env()), embedding_url=url,
            batch_size=positive_int("EMBEDDING_BATCH_SIZE", 8, 8),
            embedding_timeout=positive_int("EMBEDDING_TIMEOUT", 60),
            request_dimensions=boolean("EMBEDDING_REQUEST_DIMENSIONS", "true"),
        )


def search_text(chapter, body):
    body = body.strip()
    if not body:
        raise CareerError("Empty policy section text")
    chapter = (chapter or "").strip()
    if chapter and body.splitlines()[0].strip() != chapter:
        body = f"{chapter}\n{body}"
    if len(body.encode("utf-8")) > TEXT_LIMIT:
        raise CareerError(f"search_text exceeds {TEXT_LIMIT} UTF-8 bytes")
    return body


def decode_embeddings(payload, size, dim):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise CareerError("Embedding response is missing data")
    by_index = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            raise CareerError("Invalid item in embedding response")
        index, vector = item.get("index"), item.get("embedding")
        if type(index) is not int or index in by_index or not 0 <= index < size:
            raise CareerError("Embedding response has missing, duplicate, or invalid indices")
        if not isinstance(vector, list) or len(vector) != dim:
            actual = len(vector) if isinstance(vector, list) else "not an array"
            raise CareerError(f"Expected {dim} embedding dimensions, got {actual}")
        if not all(type(value) in (int, float) and abs(value) <= 3.4e38 and math.isfinite(value) for value in vector):
            raise CareerError("Embedding contains an invalid FLOAT_VECTOR value")
        by_index[index] = vector
    if len(by_index) != size:
        raise CareerError("Embedding response count does not match the input count")
    return [by_index[index] for index in range(size)]


def embedding_requester(config, api_key):
    opener = build_opener(NoRedirect())

    def request(texts):
        payload = {"model": config.model, "input": texts}
        if config.request_dimensions:
            payload["dimensions"] = config.dim
        req = Request(config.embedding_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                      method="POST")
        try:
            with opener.open(req, timeout=config.embedding_timeout) as response:
                parsed = json.load(response)
        except HTTPError as error:
            error.close()
            if error.code == 413:
                raise BatchTooLarge("Embedding endpoint returned HTTP 413") from None
            raise CareerError(f"Embedding endpoint returned HTTP {error.code}") from None
        except (URLError, TimeoutError, ValueError):
            raise CareerError("Embedding request timed out, failed, or returned invalid JSON") from None
        return decode_embeddings(parsed, len(texts), config.dim)

    return request


def pg_connection():
    import psycopg

    return psycopg.connect(host=os.getenv("PGHOST", "172.18.10.133"),
                           port=positive_int("PGPORT", 6543, 65535),
                           dbname=os.getenv("PGDATABASE", "career"), user=os.getenv("PGUSER", "sa"),
                           connect_timeout=positive_int("PGCONNECT_TIMEOUT", 10), autocommit=True)


def pending_sections(db, args):
    allowed = ["待入库", "失败"] if args.retry_failed else ["待入库"]
    return db.execute("""
        SELECT s.id, s.source_id, s.chapter, s.text
        FROM public.kb1_sections s JOIN public.kb1_sources f ON f.id = s.source_id
        WHERE s.embedding_status = ANY(%s)
          AND f.status NOT IN ('excluded', 'planned_later', 'history_metadata_only', 'opaque_attachment_no_body_parsing')
          AND (%s::text IS NULL OR s.source_id = %s)
        ORDER BY s.id LIMIT %s
    """, (allowed, args.source_id, args.source_id, args.limit)).fetchall()


def mark_failed(db, rows):
    db.execute("""
        UPDATE public.kb1_sections SET embedding_status = '失败'
        WHERE id = ANY(%s) AND embedding_status IN ('待入库','失败')
    """, ([row[0] for row in rows],))


def transfer_batch(db, client, config, request, rows):
    # 整条正文生成稠密向量；只有服务返回 413 时才把请求批次拆小。
    try:
        texts = []
        for section_id, source_id, chapter, body in rows:
            if not all(0 < len(value.encode("utf-8")) <= ID_LIMIT for value in (section_id, source_id)):
                raise CareerError("A section or source ID exceeds the Milvus field length")
            texts.append(search_text(chapter, body))
        vectors = request(texts)
    except BatchTooLarge:
        if len(rows) > 1:
            middle = len(rows) // 2
            for part in (rows[:middle], rows[middle:]):
                transfer_batch(db, client, config, request, part)
            return
        mark_failed(db, rows)
        raise CareerError(f"HTTP 413 for single section {rows[0][0]}; text left intact") from None
    except CareerError:
        mark_failed(db, rows)
        raise

    # 同一批次两边共用一个 UTC 时间，Milvus 存毫秒，PG 存带时区时间戳。
    indexed_ms = time.time_ns() // 1_000_000
    timestamp = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=indexed_ms)
    entities = [
        dict(section_id=row[0], source_id=row[1], search_text=text, dense_vector=vector, indexed_at=indexed_ms)
        for row, text, vector in zip(rows, texts, vectors, strict=True)
    ]
    try:
        result = client.upsert(collection_name=COLLECTION, data=entities, timeout=config.milvus_timeout)
        if result.get("upsert_count") != len(rows):
            raise CareerError("Milvus did not acknowledge the full batch")
    except Exception:
        mark_failed(db, rows)
        raise
    # Milvus 确认写入后，才把这一批的 PG 状态改为「已入库」。
    with db.transaction():
        updated = db.execute("""
            UPDATE public.kb1_sections
            SET embedding_status = '已入库', embedding_model = %s, indexed_at = %s
            WHERE id = ANY(%s) AND embedding_status IN ('待入库','失败')
        """, (config.model, timestamp, [row[0] for row in rows]))
        if updated.rowcount != len(rows):
            raise CareerError("Milvus wrote the batch, but PostgreSQL state changed; status update rolled back")


def ingest(config, args):
    api_key = os.getenv("EMBEDDING_API_KEY")
    if not api_key:
        raise CareerError("Set EMBEDDING_API_KEY before ingestion")
    with pg_connection() as db:
        acquired = db.execute("SELECT pg_try_advisory_lock(hashtext('career-milvus-indexer'))").fetchone()[0]
        if not acquired:
            raise CareerError("Another career indexer is running")
        indexed_models = db.execute("""
            SELECT DISTINCT embedding_model FROM public.kb1_sections WHERE embedding_status = '已入库'
        """).fetchall()
        if any(row[0] != config.model for row in indexed_models):
            raise CareerError("PostgreSQL already has sections indexed with a different model; rebuild explicitly")
        rows = pending_sections(db, args)
        if not rows:
            print("No matching sections to index.")
            return
        client = ensure_kb_collection(config, allow_create=False)
        try:
            request = embedding_requester(config, api_key)
            for start in range(0, len(rows), config.batch_size):
                batch = rows[start:start + config.batch_size]
                transfer_batch(db, client, config, request, batch)
                print(f"Indexed {start + len(batch)}/{len(rows)} sections")
        finally:
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="optional local dotenv file; existing environment values win")
    parser.add_argument("--apply", action="store_true", help="allow connections and writes; omit for an offline plan")
    parser.add_argument("--source-id", help="index only sections of this source")
    parser.add_argument("--limit", type=int, help="maximum number of sections to index")
    parser.add_argument("--retry-failed", action="store_true", help="also retry sections marked 失败")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    try:
        load_local_env(args.env_file)
        config = Config.from_env()
        if not args.apply:
            print(json.dumps({
                "mode": "offline plan", "action": "index into existing collection",
                "collection": COLLECTION, "milvus": config.milvus_uri, "milvus_db": config.milvus_db,
                "embedding_url": config.embedding_url, "model": config.model, "dim": config.dim,
                "batch_size": config.batch_size, "request_dimensions": config.request_dimensions,
                "milvus_timeout": config.milvus_timeout, "milvus_startup_timeout": config.startup_timeout,
                "source_id": args.source_id, "limit": args.limit, "retry_failed": args.retry_failed,
            }, ensure_ascii=False, indent=2))
        else:
            ingest(config, args)
        return 0
    except CareerError as error:
        print(f"Error: {error}", file=sys.stderr)
    except ImportError:
        print("Missing runtime dependencies; see scripts/kb1/requirements.txt", file=sys.stderr)
    except Exception as error:
        print(f"Operation stopped ({type(error).__name__})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
