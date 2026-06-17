#!/bin/bash

# 检查 iplist.txt 文件是否存在
if [ ! -f iplist.txt ]; then
  echo "iplist.txt 文件不存在"
  exit 1
fi

# 读取 iplist.txt 中的每个 IP 地址，并使用 ssh-keyscan 收集主机密钥
while read -r ip; do
  # 确保行不为空
  if [ -n "$ip" ]; then
    echo "正在处理 IP: $ip"
    # 收集主机密钥并追加到 known_hosts 文件
    ssh-keyscan -H "$ip" >> ~/.ssh/known_hosts 2>/dev/null
  fi
done < iplist.txt

# 去重 known_hosts 文件中的条目
sort -u -o ~/.ssh/known_hosts ~/.ssh/known_hosts

echo "已完成所有 IP 的处理"