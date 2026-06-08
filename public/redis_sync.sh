#!/bin/bash
# redis_sync.sh
# 将一个 Redis 实例配置为另一个的 Slave，实现数据同步
#
# 使用说明:
#   ./redis_sync.sh <master_host> <master_port> <master_pass> <slave_host> <slave_port> <slave_pass>
#
# 同步完成后取消主从:
#   redis-cli -h <slave_host> -p <slave_port> SLAVEOF NO ONE

set -euo pipefail

MASTER_HOST="${1:-}"
MASTER_PORT="${2:-}"
MASTER_PASS="${3:-}"
SLAVE_HOST="${4:-}"
SLAVE_PORT="${5:-}"
SLAVE_PASS="${6:-}"

if [ $# -lt 6 ]; then
    echo "Usage: $0 <master_host> <master_port> <master_pass> <slave_host> <slave_port> <slave_pass>"
    exit 1
fi

# 检查 redis-cli 是否安装
if ! command -v redis-cli &>/dev/null; then
    echo "错误: redis-cli 未安装"
    exit 1
fi

# 封装 redis-cli 调用
redis_master() {
    redis-cli -h "$MASTER_HOST" -p "$MASTER_PORT" -a "$MASTER_PASS" --no-auth-warning "$@"
}

redis_slave() {
    redis-cli -h "$SLAVE_HOST" -p "$SLAVE_PORT" -a "$SLAVE_PASS" --no-auth-warning "$@"
}

# 测试连接
echo "=== 测试连接 ==="
if ! redis_master PING | grep -q PONG; then
    echo "错误: 无法连接 Master ($MASTER_HOST:$MASTER_PORT)"
    exit 1
fi
if ! redis_slave PING | grep -q PONG; then
    echo "错误: 无法连接 Slave ($SLAVE_HOST:$SLAVE_PORT)"
    exit 1
fi
echo "Master 和 Slave 连接正常"

# 配置主从
echo ""
echo "=== 设置 Slave 的 masterauth ==="
redis_slave CONFIG SET masterauth "$MASTER_PASS"

echo ""
echo "=== 配置 Slave 指向 Master ==="
redis_slave SLAVEOF "$MASTER_HOST" "$MASTER_PORT"

# 等待同步完成
echo ""
echo "=== 等待同步... ==="
MAX_WAIT=60
for i in $(seq 1 $MAX_WAIT); do
    link_status=$(redis_slave INFO replication | grep "master_link_status" | tr -d '\r')
    if echo "$link_status" | grep -q "up"; then
        echo "同步连接已建立 ($link_status)"
        break
    fi
    if [ $i -eq $MAX_WAIT ]; then
        echo "警告: 等待 ${MAX_WAIT}s 后同步连接仍未建立，请手动检查"
        redis_slave INFO replication | grep -E "master_link_status|master_sync"
        exit 1
    fi
    sleep 1
done

# 显示最终状态
echo ""
echo "=== Master 状态 ==="
redis_master INFO replication | grep -E "role|connected_slaves|slave[0-9]"

echo ""
echo "=== Slave 状态 ==="
redis_slave INFO replication | grep -E "role|master_host|master_port|master_link_status|master_sync_in_progress"

echo ""
echo "=== 同步完成，断开主从关系 ==="
redis_slave SLAVEOF NO ONE
echo "Slave 已恢复为独立节点"
