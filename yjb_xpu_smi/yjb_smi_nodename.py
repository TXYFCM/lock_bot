#!/usr/bin/env python3

# nohup python3 report.py &> tmp/report.log & echo $! > tmp/report.pid

import socket
import subprocess
from datetime import datetime
import requests
import re
import os
import json
import base64
import binascii
import six
import hashlib

from flask import request, Flask, abort
# pip install pycrypto
# Windows No module named Crypto.Cipher问题，https://www.jianshu.com/p/09a14a61b454
from Crypto.Cipher import AES

# 全局配置
WEBHOOK_URL = ''
TOKEN = ''
AESKEY = ''
HEADERS = {"Content-Type": "application/json"}
PORT = "8777"
# 全局配置
ROOT_DIR = "/path/to/"  # 请修改为实际路径


HOSTNAME_MAP = {
}
HOSTNAME_ORDER = [
]
HOSTNAME_ORDER_ORDER_MAP = {hostname: idx for idx, hostname in enumerate(HOSTNAME_ORDER)}


def run_smi():
    try:
        env = os.environ.copy()
        env["IP"] = f"{ROOT_DIR}/iplist.txt"
        output = subprocess.check_output(["bash", f"{ROOT_DIR}/my_xpu_smi.sh"], env=env)
        return output.decode("utf-8").strip()
    except Exception as e:
        return "[ERROR] my_xpu_smi.sh failed: {}".format(str(e))

def strip_ansi_codes(text):
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)

def parse_output_to_markdown(output, max_rows_per_msg=12):
    output = strip_ansi_codes(output)
    lines = output.strip().split('\n')
    rows = []
    for line in lines:
        # 匹配可达主机的正常输出
        m1 = re.match(
            r'^([\d\.]+):\s+(\w+)\s+\|\s+Mem:\s+([^|]+)\|\s+Util:\s+([^%]+%)\s+\|\s+Container:\s*(.*)',
            line)
        if m1:
            ip, status, mem, util, container = m1.groups()
            color = "green" if status == "FREE" else "red"
            hostname = HOSTNAME_MAP.get(ip, ip)
            rows.append({
                "ip": ip,
                "hostname": hostname,
                "status": status,
                "mem": mem.strip(),
                "util": util.strip(),
                "container": container.strip() or "-"
            })
            continue
        # 匹配 SSH 不可达
        m2 = re.match(r'^\[([\d\.]+)\] SSH unreachable\. Skipped\.', line)
        if m2:
            ip = m2.group(1)
            hostname = HOSTNAME_MAP.get(ip, ip)
            rows.append({
                "ip": ip,
                "hostname": hostname,
                "status": "SSH无法连接",
                "mem": "-",
                "util": "-",
                "container": "-"
            })
            continue

        # 匹配 ping 不可达
        m3 = re.match(r'^\[([\d\.]+)\] Unreachable \(ping failed\)\. Skipped\.', line)
        if m3:
            ip = m3.group(1)
            hostname = HOSTNAME_MAP.get(ip, ip)
            rows.append({
                "ip": ip,
                "hostname": hostname,
                "status": "网络不通",
                "mem": "-",
                "util": "-",
                "container": "-"
            })
            continue

        # 匹配执行超时
        m4 = re.match(r'^\[([\d\.]+)\] Execution exceeded', line)
        if m4:
            ip = m4.group(1)
            hostname = HOSTNAME_MAP.get(ip, ip)
            rows.append({
                "ip": ip,
                "hostname": hostname,
                "status": "SSH连接超时",
                "mem": "-",
                "util": "-",
                "container": "-"
            })
            continue

        # 匹配其它错误
        m5 = re.match(r'^\[([\d\.]+)\] Error', line)
        if m5:
            ip = m5.group(1)
            hostname = HOSTNAME_MAP.get(ip, ip)
            rows.append({
                "ip": ip,
                "hostname": hostname,
                "status": "SSH连接超时",
                "mem": "-",
                "util": "-",
                "container": "-"
            })
            continue


    if not rows:
        return ["暂无可解析的节点信息。"]

    
    rows.sort(
        key=lambda r: HOSTNAME_ORDER_ORDER_MAP.get(r["hostname"], 999)
    )

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    header_table = (
        f"| IP地址 | 状态 | 平均显存 | 平均利用率 | 容器 |\n"
        "|--------|------|------|----------------|------|"
    )

    total_parts = (len(rows) + max_rows_per_msg - 1) // max_rows_per_msg
    chunks = []
    for i in range(0, len(rows), max_rows_per_msg):
        part_num = (i // max_rows_per_msg) + 1
        chunk_rows = rows[i:i+max_rows_per_msg]

        rendered_rows = []
        for r in chunk_rows:
            color = "green" if r["status"] == "FREE" else "red"
            rendered_rows.append(
                "| {} | <font color=\"{}\">{}</font> | {} | {} | {} |".format(
                    r["hostname"],
                    color,
                    r["status"],
                    r["mem"],
                    r["util"],
                    r["container"]
                )
            )

        if total_parts == 1:
            title = f"##### 空闲状态报告（{now_str}）"
        else:
            title = f"##### 空闲状态报告（{now_str}） - 第 {part_num}/{total_parts} 部分"

        chunks.append(title + "\n" + header_table + "\n" + "\n".join(rendered_rows))

    return chunks

def build_msg(content_md):
    return {
        "message": {
            "header": {},
            "body": [
                {"type": "MD", "content": content_md}
            ]
        }
    }

def run_once(toid=None):
    result = run_smi()
    content_md_list = parse_output_to_markdown(result)
    for content_md in content_md_list:
        payload = build_msg(content_md)
        if toid:
            payload['message']['header']['toid'] = toid
        try:
            r = requests.post(WEBHOOK_URL, json=payload, headers=HEADERS)
            print("[INFO] Sent MD message, status={}, response={}".format(r.status_code, r.text))
        except Exception as e:
            print("[ERROR] Failed to send message: {}".format(str(e)))


def base64_urlsafe_decode(s):
    """
    base64 解码(urlsafe兼容模式)
    :return:
    """
    # 系统的urlsafe_b64decode方法不支持补'='
    s = s.replace('-', '+').replace('_', '/') + '=' * (len(s) % 4)
    return base64.b64decode(s)


def check_signature(signature, rn, timestamp, TOKEN):
    md5 = hashlib.md5()
    md5.update(f"{rn}{timestamp}{TOKEN}".encode("utf-8"))
    return md5.hexdigest() == signature


class AESCipher(object):
    """
    AES加解密类
    """

    def __init__(self, key, mode=AES.MODE_ECB, padding='PKCS7', encode='base64', **kwargs):
        """
         初始化
        :param key:
        :param mode:
        :param padding: 数据填充方式 PKCS7、ZERO
        :param encode: 数据编码方式 raw、base64、hex
        """
        self.key = key
        self.mode = mode
        self.padding = padding
        self.encode = encode
        self.kwargs = kwargs

        self.bs = AES.block_size

        self.IV = self.kwargs.get('IV', None)
        if self.IV and self.mode in (AES.MODE_ECB, AES.MODE_CTR):
            raise TypeError("ECB and CTR mode does not use IV")

    def _aes(self):
        return AES.new(self.key, self.mode, **self.kwargs)

    def encrypt(self, plaintext):
        """
        加密
        :param plaintext:
        :return: py3返回 byte string, py2返回str
        """
        # padding https://en.wikipedia.org/wiki/Padding_(cryptography)#PKCS#5_and_PKCS#7
        if self.padding == 'PKCS7':
            def pad(s): return s + (self.bs - len(s) % self.bs) \
                * chr(self.bs - len(s) % self.bs).encode('utf-8')
        else:
            def pad(s): return s + (self.bs - len(s) % self.bs) \
                * '\x00'
        # 统一为字节类型
        if isinstance(plaintext, six.text_type):
            plaintext = plaintext.encode('utf-8')

        # 注意：加密、解密需单独实例化
        raw = self._aes().encrypt(pad(plaintext))

        if self.encode == 'hex':
            return binascii.hexlify(raw)
        if self.encode == 'base64':
            return base64.b64encode(raw)
        return raw

    def decrypt(self, ciphertext):
        """
        解密
        :param ciphertext:
        :return: py3返回 byte string, py2返回str
        """
        if not ciphertext:
            return None

        if self.padding == 'PKCS7':
            if six.PY3:
                def unpad(s): return s[0:-s[-1]]
            else:
                def unpad(s): return s[0:-ord(s[-1])]
        else:
            def unpad(s): return s.rstrip('\x00')

        # 统一为文本字符类型
        if isinstance(ciphertext, six.binary_type) and self.encode != 'raw':
            ciphertext = ciphertext.decode('utf-8')
        if self.encode == 'hex':
            ciphertext = binascii.unhexlify(ciphertext)
        if self.encode == 'base64':
            ciphertext = base64_urlsafe_decode(ciphertext)

        return unpad(self._aes().decrypt(ciphertext))


APP = Flask(__name__)
@APP.route('/', methods=['post'])
def serve():
    # 注意：仅回调配置的时候有此参数，需要指定缺省值
    echostr = request.form.get('echostr', None)
    signature = request.form.get('signature', None)
    rn = request.form.get('rn', None)
    timestamp = request.form.get('timestamp', None)

    global HEADERS, WEBHOOK_URL
    # 配置回调地址时应回调服务就绪，配置回调地址时会调用配置地址，需要回显才能校验通过
    if echostr:
        if check_signature(signature, rn, timestamp, TOKEN):
            return echostr
        else:
            return 'check signature fail', 401
    else:
        # 正常请求里, 鉴权参数在URL查询参数里
        signature = request.args.get('signature', None)
        rn = request.args.get('rn', None)
        timestamp = request.args.get('timestamp', None)

        if not check_signature(signature, rn, timestamp, TOKEN):
            return 'check signature fail', 401

        # 获取request raw body, Django可使用request.body
        msg_base64 = request.get_data()
        try:
            encrypter = AESCipher(base64_urlsafe_decode(AESKEY))
            # 通过AES解密后得到回调消息数据
            decrypted = encrypter.decrypt(msg_base64)
            msg_data = json.loads(decrypted)
        except:
            try:
                msg_data = json.loads(msg_base64)
            except:
                abort(404)

    toid = msg_data['message']['header'].get('toid', None)
    run_once(toid)
    return 'command succeed'

@APP.errorhandler(404)
def page_not_found(_):
    return "404 - Page not found", 404


if __name__ == '__main__':
    APP.run(host='0.0.0.0', port=PORT)
