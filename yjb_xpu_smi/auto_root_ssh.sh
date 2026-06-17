#!/bin/bash

mapfile -t HOSTS < iplist.txt   # 全量读取到数组

PUBKEY=$(sudo cat /root/.ssh/id_rsa.pub)
if [ -z "$PUBKEY" ]; then
    echo "❌ 本机没有 id_rsa.pub，请先 ssh-keygen"
    exit 1
fi

for IP in "${HOSTS[@]}"; do
    echo ">>> 配置 $IP 上的 root SSH 免密"

    ssh -o StrictHostKeyChecking=no $USER@$IP "
        sudo su - root bash -c '
            mkdir -p /root/.ssh &&
            chmod 700 /root/.ssh &&
            grep -qxF \"$PUBKEY\" /root/.ssh/authorized_keys 2>/dev/null || echo \"$PUBKEY\" >> /root/.ssh/authorized_keys &&
            chmod 600 /root/.ssh/authorized_keys &&
            sed -i \"s/^#*PermitRootLogin.*/PermitRootLogin yes/\" /etc/ssh/sshd_config &&
            sed -i \"s/^#*PubkeyAuthentication.*/PubkeyAuthentication yes/\" /etc/ssh/sshd_config &&
            systemctl restart sshd || systemctl restart ssh
        '
    "

    echo "🍺 完成 $IP"
done

echo "🎯 全部完成，试试： ssh root@<ip>"
