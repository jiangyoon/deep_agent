"""创建 Milvus 的 career_policy_sections 集合。默认离线预览；--apply 才执行。"""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import time


COLLECTION = "career_policy_sections"
ID_LIMIT = 128
TEXT_LIMIT = 16384
BM25_FUNCTION = "career_bm25"
FIELDS = ("section_id", "source_id", "search_text", "dense_vector", "sparse_vector", "indexed_at")
INDEXES = (
    ("career_dense", "dense_vector", "AUTOINDEX", "COSINE"),
    ("career_sparse", "sparse_vector", "SPARSE_INVERTED_INDEX", "BM25"),
)


def create_collection(client, config, timeout):
    """Milvus 建表入口：定义 6 个字段、配置 BM25、调用服务端建表。"""
    from pymilvus import DataType, Function, FunctionType

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False,
                                  description=config.collection_description())

    # 条款 ID 来自 PostgreSQL；主键由我们提供，禁止 Milvus 自动编号。
    schema.add_field("section_id", DataType.VARCHAR, is_primary=True, max_length=ID_LIMIT)
    # 文件 ID 用于按 PostgreSQL 给出的有效来源清单过滤。
    schema.add_field("source_id", DataType.VARCHAR, max_length=ID_LIMIT)
    # 检索文本是「章名＋正文」；中文分词供 BM25 使用，也预留 TEXT_MATCH 筛选。
    schema.add_field("search_text", DataType.VARCHAR, max_length=TEXT_LIMIT,
                     enable_analyzer=True, enable_match=True, analyzer_params={"type": "chinese"})
    # 稠密向量由 Embedding 接口返回，默认 1024 维。
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=config.dim)
    # 稀疏向量由下方 BM25 函数生成，导入脚本不用提供。
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    # UTC 毫秒时间戳，与 PostgreSQL 的 indexed_at 表示同一时刻。
    schema.add_field("indexed_at", DataType.INT64)

    schema.add_function(Function(name=BM25_FUNCTION, function_type=FunctionType.BM25,
                                 input_field_names=["search_text"], output_field_names=["sparse_vector"]))
    client.create_collection(collection_name=COLLECTION, schema=schema, timeout=timeout)


class CareerError(RuntimeError):
    pass


def positive_int(name, default, maximum=None):
    value = int(os.getenv(name, str(default)))
    if value < 1 or (maximum is not None and value > maximum):
        raise CareerError(f"{name} 超出取值范围")
    return value


@dataclass(frozen=True)
class MilvusConfig:
    milvus_uri: str
    milvus_db: str
    milvus_timeout: int
    startup_timeout: int
    model: str
    dim: int

    @classmethod
    def from_env(cls):
        host = os.getenv("MILVUS_HOST", "172.18.10.133")
        if not host or any(char in host for char in "/@?# \\"):
            raise CareerError("MILVUS_HOST 必须是主机名或 IP")
        model = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-VL-Embedding-8B").strip()
        if not model:
            raise CareerError("EMBEDDING_MODEL 不能为空")
        port = positive_int("MILVUS_PORT", 19630, 65535)
        return cls(
            milvus_uri=f"http://{host}:{port}",
            milvus_db=os.getenv("MILVUS_DB_NAME", "career"),
            milvus_timeout=positive_int("MILVUS_TIMEOUT", 15),
            startup_timeout=positive_int("MILVUS_STARTUP_TIMEOUT", 30),
            model=model,
            dim=positive_int("EMBEDDING_DIM", 1024),
        )

    def collection_description(self):
        return f"career-v1; search_text=chapter+body; embedding={self.model}; dim={self.dim}"


def load_local_env(path):
    if path is None:
        return
    if not path.is_file():
        raise CareerError("指定的本地配置文件不存在")
    from dotenv import load_dotenv

    load_dotenv(path, override=False)


def check_schema(client, config, timeout):
    """已有同名集合时，核对模型、字段和维度，不覆盖不兼容的集合。"""
    from pymilvus import DataType

    info = client.describe_collection(COLLECTION, timeout=timeout)
    fields = {field["name"]: field for field in info["fields"]}
    expected = (DataType.VARCHAR, DataType.VARCHAR, DataType.VARCHAR,
                DataType.FLOAT_VECTOR, DataType.SPARSE_FLOAT_VECTOR, DataType.INT64)
    if (fields.keys() != set(FIELDS) or info.get("auto_id") or info.get("enable_dynamic_field")
            or info.get("description") != config.collection_description()):
        raise CareerError("同名集合的结构或 Embedding 模型不一致，未覆盖")
    for name, data_type in zip(FIELDS, expected, strict=True):
        field = fields[name]
        if field["type"] != data_type or bool(field.get("is_primary")) != (name == "section_id"):
            raise CareerError(f"同名集合的字段类型或主键不同：{name}")
    for name, key, value in (
            ("section_id", "max_length", ID_LIMIT), ("source_id", "max_length", ID_LIMIT),
            ("search_text", "max_length", TEXT_LIMIT), ("dense_vector", "dim", config.dim)):
        if int(fields[name].get("params", {}).get(key, -1)) != value:
            raise CareerError(f"同名集合的 {name}.{key} 不一致")
    text_params = fields["search_text"].get("params", {})
    if str(text_params.get("enable_match", False)).lower() != "true":
        raise CareerError("同名集合的 search_text 未启用 TEXT_MATCH，未覆盖")
    if not any(function.get("name") == BM25_FUNCTION for function in info.get("functions", [])):
        raise CareerError("集合缺少 BM25 函数")


def ensure_kb_collection(config, allow_create=True):
    """准备数据库与集合；导入脚本调用时只接受已准备好的库和集合。"""
    from pymilvus import MilvusClient

    deadline = time.monotonic() + config.startup_timeout

    def remaining_timeout():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CareerError("Milvus 准备阶段超时")
        return min(config.milvus_timeout, remaining)

    initial_db = "default" if allow_create else config.milvus_db
    client = MilvusClient(uri=config.milvus_uri, db_name=initial_db,
                          token=os.getenv("MILVUS_TOKEN", ""), timeout=remaining_timeout())
    try:
        # 建库脚本先从 default 检查目标库，缺库才创建，然后在目标库建集合。
        if allow_create and config.milvus_db != initial_db:
            if config.milvus_db not in client.list_databases(timeout=remaining_timeout()):
                client.create_database(config.milvus_db, timeout=remaining_timeout())
            client.using_database(config.milvus_db, timeout=remaining_timeout())
        if not client.has_collection(COLLECTION, timeout=remaining_timeout()):
            if not allow_create:
                raise CareerError("集合还不存在，请先执行建集合脚本")
            create_collection(client, config, remaining_timeout())
        check_schema(client, config, remaining_timeout())

        # 两种检索各建一个索引：稠密用 COSINE，稀疏用 BM25。
        existing = set(client.list_indexes(COLLECTION, timeout=remaining_timeout()))
        for index_name, field, index_type, metric in INDEXES:
            if index_name not in existing:
                if not allow_create:
                    raise CareerError(f"缺少 {index_name}，请先执行建集合脚本")
                indexes = client.prepare_index_params()
                indexes.add_index(field, index_name=index_name, index_type=index_type, metric_type=metric)
                client.create_index(COLLECTION, index_params=indexes, timeout=remaining_timeout())
            index = client.describe_index(COLLECTION, index_name, timeout=remaining_timeout())
            if not index or index.get("field_name") != field or index.get("metric_type") != metric:
                raise CareerError(f"集合索引定义不一致：{index_name}")
        client.load_collection(COLLECTION, timeout=remaining_timeout())
        remaining_timeout()
    except Exception:
        client.close()
        raise
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="读取本地 .env，已有环境变量优先")
    parser.add_argument("--apply", action="store_true", help="允许连接 Milvus 并建库、建集合")
    args = parser.parse_args()
    try:
        load_local_env(args.env_file)
        config = MilvusConfig.from_env()
        if not args.apply:
            print(f"离线预览：{config.milvus_uri}/{config.milvus_db}/{COLLECTION}")
            print(f"字段：{', '.join(FIELDS)}")
            print(f"稠密向量：{config.model} / {config.dim} 维；稀疏向量：BM25")
            return 0
        client = ensure_kb_collection(config)
        client.close()
        print(f"集合已就绪：{config.milvus_db}.{COLLECTION}")
        return 0
    except CareerError as error:
        print(f"错误：{error}", file=sys.stderr)
    except ImportError:
        print("缺少运行依赖，请查看 scripts/kb1/requirements.txt", file=sys.stderr)
    except Exception as error:
        print(f"建集合已停止（{type(error).__name__}）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
