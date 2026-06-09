#!/usr/bin/env python3
"""
MongoDB 数据对比脚本
对比源库和目标库的差异：集合、文档数量、索引、抽样数据。

安装依赖: pip3 install pymongo

使用方法:
  ./diff_mongo.py --src-uri "mongodb://user:pass@host:port/db" \
                  --dst-uri "mongodb://user:pass@host:port/db"

  # 或者分开指定参数
  ./diff_mongo.py --src-host 127.0.0.1 --src-port 3015 --src-user admin \
                  --src-pass xxx --src-db datas \
                  --dst-host 10.0.0.1 --dst-port 3717 --dst-user admin \
                  --dst-pass xxx --dst-db mydb

  # 指定抽样数量
  ./diff_mongo.py --src-uri "..." --dst-uri "..." --sample-size 200
"""

import argparse
import sys

from pymongo import MongoClient


def parse_args():
    parser = argparse.ArgumentParser(description="MongoDB 数据对比工具")

    # URI 方式
    parser.add_argument("--src-uri", help="源库 MongoDB URI")
    parser.add_argument("--dst-uri", help="目标库 MongoDB URI")

    # 分开参数方式 - 源库
    parser.add_argument("--src-host", default="127.0.0.1", help="源库地址")
    parser.add_argument("--src-port", type=int, default=27017, help="源库端口")
    parser.add_argument("--src-user", default="", help="源库用户名")
    parser.add_argument("--src-pass", default="", help="源库密码")
    parser.add_argument("--src-db", help="源库数据库名")
    parser.add_argument("--src-auth-db", default="", help="源库认证库")

    # 分开参数方式 - 目标库
    parser.add_argument("--dst-host", default="127.0.0.1", help="目标库地址")
    parser.add_argument("--dst-port", type=int, default=27017, help="目标库端口")
    parser.add_argument("--dst-user", default="", help="目标库用户名")
    parser.add_argument("--dst-pass", default="", help="目标库密码")
    parser.add_argument("--dst-db", help="目标库数据库名")
    parser.add_argument("--dst-auth-db", default="", help="目标库认证库")

    # 对比选项
    parser.add_argument("--sample-size", type=int, default=100, help="抽样对比文档数 (默认 100)")
    parser.add_argument("--collections", default="", help="只对比指定集合 (逗号分隔)")
    parser.add_argument("--skip-data", action="store_true", help="跳过数据抽样对比，只对比集合和索引")

    return parser.parse_args()


def build_uri(host, port, user, password, db, auth_db):
    auth_db = auth_db or db
    if user and password:
        return f"mongodb://{user}:{password}@{host}:{port}/{db}?authSource={auth_db}"
    return f"mongodb://{host}:{port}/{db}"


def connect(uri, db_name):
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]
    db.command("ping")
    return db


def compare_indexes(src_col, dst_col, col_name):
    src_indexes = src_col.index_information()
    dst_indexes = dst_col.index_information()

    src_names = set(src_indexes.keys())
    dst_names = set(dst_indexes.keys())

    diffs = []
    missing_in_dst = src_names - dst_names
    extra_in_dst = dst_names - src_names

    if missing_in_dst:
        diffs.append(f"    目标缺少索引: {missing_in_dst}")
    if extra_in_dst:
        diffs.append(f"    目标多出索引: {extra_in_dst}")

    for name in src_names & dst_names:
        if src_indexes[name]["key"] != dst_indexes[name]["key"]:
            diffs.append(f"    索引 {name} 定义不同: 源={src_indexes[name]['key']} 目标={dst_indexes[name]['key']}")

    return diffs


def compare_documents(src_col, dst_col, col_name, sample_size):
    """抽样对比文档内容"""
    diffs = []
    missing_count = 0
    diff_count = 0

    pipeline = [{"$sample": {"size": sample_size}}]
    try:
        sample_docs = list(src_col.aggregate(pipeline))
    except Exception:
        sample_docs = list(src_col.find().limit(sample_size))

    for doc in sample_docs:
        doc_id = doc["_id"]
        dst_doc = dst_col.find_one({"_id": doc_id})

        if dst_doc is None:
            missing_count += 1
            continue

        src_keys = set(doc.keys())
        dst_keys = set(dst_doc.keys())

        if src_keys != dst_keys:
            diff_count += 1
            if diff_count <= 3:
                missing_fields = src_keys - dst_keys
                extra_fields = dst_keys - src_keys
                if missing_fields:
                    diffs.append(f"    文档 {doc_id}: 目标缺少字段 {missing_fields}")
                if extra_fields:
                    diffs.append(f"    文档 {doc_id}: 目标多出字段 {extra_fields}")
            continue

        for key in src_keys:
            if doc[key] != dst_doc[key]:
                diff_count += 1
                if diff_count <= 3:
                    diffs.append(f"    文档 {doc_id}: 字段 '{key}' 值不同")
                break

    if missing_count > 0:
        diffs.append(f"    抽样 {len(sample_docs)} 文档中，目标缺少 {missing_count} 个")
    if diff_count > 0:
        diffs.append(f"    抽样 {len(sample_docs)} 文档中，{diff_count} 个内容不一致")

    return diffs


def main():
    args = parse_args()

    # 构建连接 URI
    if args.src_uri:
        src_uri = args.src_uri
        src_db_name = args.src_db or args.src_uri.rsplit("/", 1)[-1].split("?")[0]
    else:
        if not args.src_db:
            print("错误: 必须指定 --src-uri 或 --src-db")
            sys.exit(1)
        src_uri = build_uri(args.src_host, args.src_port, args.src_user,
                            args.src_pass, args.src_db, args.src_auth_db)
        src_db_name = args.src_db

    if args.dst_uri:
        dst_uri = args.dst_uri
        dst_db_name = args.dst_db or args.dst_uri.rsplit("/", 1)[-1].split("?")[0]
    else:
        if not args.dst_db:
            print("错误: 必须指定 --dst-uri 或 --dst-db")
            sys.exit(1)
        dst_uri = build_uri(args.dst_host, args.dst_port, args.dst_user,
                            args.dst_pass, args.dst_db, args.dst_auth_db)
        dst_db_name = args.dst_db

    print("=" * 60)
    print("MongoDB 数据对比")
    print(f"源: {src_db_name}")
    print(f"目标: {dst_db_name}")
    print("=" * 60)

    print("\n连接源库...")
    try:
        src_db = connect(src_uri, src_db_name)
    except Exception as e:
        print(f"连接源库失败: {e}")
        sys.exit(1)

    print("连接目标库...")
    try:
        dst_db = connect(dst_uri, dst_db_name)
    except Exception as e:
        print(f"连接目标库失败: {e}")
        sys.exit(1)

    # 确定要对比的集合
    if args.collections:
        src_collections = set(c.strip() for c in args.collections.split(","))
        dst_collections = set(c.strip() for c in args.collections.split(","))
    else:
        src_collections = set(c for c in src_db.list_collection_names() if not c.startswith("system."))
        dst_collections = set(c for c in dst_db.list_collection_names() if not c.startswith("system."))

    print(f"\n源库集合数: {len(src_collections)}")
    print(f"目标库集合数: {len(dst_collections)}")

    missing_in_dst = src_collections - dst_collections
    extra_in_dst = dst_collections - src_collections

    has_diff = False

    if missing_in_dst:
        has_diff = True
        print(f"\n❌ 目标缺少集合: {sorted(missing_in_dst)}")
    if extra_in_dst:
        has_diff = True
        print(f"\n⚠️  目标多出集合: {sorted(extra_in_dst)}")

    common = sorted(src_collections & dst_collections)
    print(f"\n对比 {len(common)} 个共有集合:\n")

    for col_name in common:
        src_col = src_db[col_name]
        dst_col = dst_db[col_name]

        src_count = src_col.estimated_document_count()
        dst_count = dst_col.estimated_document_count()

        diffs = []

        if src_count != dst_count:
            diff_pct = abs(src_count - dst_count) / max(src_count, 1) * 100
            diffs.append(f"    文档数: 源={src_count} 目标={dst_count} (差 {abs(src_count - dst_count)}, {diff_pct:.1f}%)")

        idx_diffs = compare_indexes(src_col, dst_col, col_name)
        diffs.extend(idx_diffs)

        if not args.skip_data and src_count > 0:
            doc_diffs = compare_documents(src_col, dst_col, col_name, args.sample_size)
            diffs.extend(doc_diffs)

        if diffs:
            has_diff = True
            print(f"  ❌ {col_name}:")
            for d in diffs:
                print(d)
        else:
            print(f"  ✅ {col_name}: 一致 (源={src_count} 目标={dst_count})")

    print(f"\n{'=' * 60}")
    if has_diff:
        print("⚠️  存在差异，请检查上方详情")
    else:
        print("✅ 两库数据完全一致")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
