#!/usr/bin/env python3
"""
MongoDB 数据迁移脚本
从源库逐集合读取数据，批量写入目标库。
不依赖 mongodump/mongorestore，只需要 pymongo。

安装依赖: pip3 install pymongo

使用方法:
  ./migrate_mongo.py --src-uri "mongodb://user:pass@host:port/db" \
                     --dst-uri "mongodb://user:pass@host:port/db"

  # 或者分开指定参数
  ./migrate_mongo.py --src-host 127.0.0.1 --src-port 3015 --src-user admin \
                     --src-pass xxx --src-db datas \
                     --dst-host 10.0.0.1 --dst-port 3717 --dst-user admin \
                     --dst-pass xxx --dst-db mydb

  # 指定只迁移部分集合
  ./migrate_mongo.py --src-uri "..." --dst-uri "..." --collections col1,col2
"""

import argparse
import sys
import time

from pymongo import MongoClient


def parse_args():
    parser = argparse.ArgumentParser(description="MongoDB 数据迁移工具")

    # URI 方式
    parser.add_argument("--src-uri", help="源库 MongoDB URI (优先级高于分开参数)")
    parser.add_argument("--dst-uri", help="目标库 MongoDB URI (优先级高于分开参数)")

    # 分开参数方式 - 源库
    parser.add_argument("--src-host", default="127.0.0.1", help="源库地址")
    parser.add_argument("--src-port", type=int, default=27017, help="源库端口")
    parser.add_argument("--src-user", default="", help="源库用户名")
    parser.add_argument("--src-pass", default="", help="源库密码")
    parser.add_argument("--src-db", help="源库数据库名")
    parser.add_argument("--src-auth-db", default="", help="源库认证库 (默认与 src-db 相同)")

    # 分开参数方式 - 目标库
    parser.add_argument("--dst-host", default="127.0.0.1", help="目标库地址")
    parser.add_argument("--dst-port", type=int, default=27017, help="目标库端口")
    parser.add_argument("--dst-user", default="", help="目标库用户名")
    parser.add_argument("--dst-pass", default="", help="目标库密码")
    parser.add_argument("--dst-db", help="目标库数据库名")
    parser.add_argument("--dst-auth-db", default="", help="目标库认证库 (默认与 dst-db 相同)")

    # 迁移选项
    parser.add_argument("--collections", default="", help="只迁移指定集合 (逗号分隔)")
    parser.add_argument("--batch-size", type=int, default=1000, help="每批插入文档数 (默认 1000)")
    parser.add_argument("--skip-indexes", action="store_true", help="跳过索引迁移")

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


def migrate_collection(src_col, dst_col, col_name, batch_size, skip_indexes):
    total = src_col.estimated_document_count()
    print(f"  [{col_name}] 文档数: {total}")

    if total == 0:
        print(f"  [{col_name}] 跳过（空集合）")
        return 0

    # 复制索引
    if not skip_indexes:
        indexes = src_col.index_information()
        for idx_name, idx_info in indexes.items():
            if idx_name == "_id_":
                continue
            keys = idx_info["key"]
            opts = {}
            if idx_info.get("unique"):
                opts["unique"] = True
            if idx_info.get("sparse"):
                opts["sparse"] = True
            if idx_info.get("expireAfterSeconds") is not None:
                opts["expireAfterSeconds"] = idx_info["expireAfterSeconds"]
            try:
                dst_col.create_index(keys, name=idx_name, **opts)
            except Exception as e:
                print(f"  [{col_name}] 索引 {idx_name} 创建失败: {e}")

    # 批量迁移数据
    migrated = 0
    batch = []

    for doc in src_col.find():
        batch.append(doc)
        if len(batch) >= batch_size:
            _write_batch(dst_col, batch, col_name)
            migrated += len(batch)
            batch = []
            print(f"  [{col_name}] 已迁移 {migrated}/{total}", end="\r")

    # 处理剩余
    if batch:
        _write_batch(dst_col, batch, col_name)
        migrated += len(batch)

    print(f"  [{col_name}] 完成: {migrated}/{total}          ")
    return migrated


def _write_batch(dst_col, batch, col_name):
    """批量写入，逐条 upsert 以处理分片集合"""
    from pymongo import ReplaceOne

    operations = [ReplaceOne({"_id": doc["_id"]}, doc, upsert=True) for doc in batch]
    try:
        dst_col.bulk_write(operations, ordered=False)
    except Exception as e:
        # 回退到逐条写入
        for doc in batch:
            try:
                dst_col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            except Exception as e2:
                try:
                    dst_col.delete_one({"_id": doc["_id"]})
                    dst_col.insert_one(doc)
                except Exception as e3:
                    print(f"  [{col_name}] 文档 {doc['_id']} 写入失败: {e3}")


def main():
    args = parse_args()

    # 构建连接 URI
    if args.src_uri:
        src_uri = args.src_uri
        # 从 URI 中提取 db 名
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

    print("=" * 50)
    print("MongoDB 数据迁移")
    print(f"源: {src_db_name}")
    print(f"目标: {dst_db_name}")
    print("=" * 50)

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

    # 确定要迁移的集合
    if args.collections:
        collections = [c.strip() for c in args.collections.split(",")]
    else:
        collections = [c for c in src_db.list_collection_names() if not c.startswith("system.")]

    print(f"\n共 {len(collections)} 个集合: {collections}\n")

    start_time = time.time()
    total_docs = 0

    for col_name in sorted(collections):
        src_col = src_db[col_name]
        dst_col = dst_db[col_name]
        total_docs += migrate_collection(src_col, dst_col, col_name,
                                         args.batch_size, args.skip_indexes)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 50}")
    print(f"迁移完成! 共 {total_docs} 文档, 耗时 {elapsed:.1f}s")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
