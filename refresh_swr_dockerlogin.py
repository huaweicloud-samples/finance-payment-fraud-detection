# -*- coding: utf-8 -*-
"""
Refresh SWR docker credentials and update K8s default-secret.
使用官方 huaweicloudsdkswr SDK:
  1) create_secret -> 响应头 X-Swr-Dockerlogin 得到 docker login 认证串
  2) 解析出 username/password，构造 dockerconfigjson（公网 + 内网 100.125.0.78:20202）
  3) 更新 K8s secret 并触发 workload 滚动重启

用法:
    python refresh_swr_dockerlogin.py --ak <AK> --sk <SK> [--kubeconfig <path>]
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys

def parse_dockerlogin(raw):
    """解析 'docker login -u xxx -p yyy swr.cn-north-4.myhuaweicloud.com' 类似的串"""
    # 形如: docker login -u swr-cloud-user -p <token> swr.cn-north-4.myhuaweicloud.com
    # 或  : docker login --username ... --password ... host
    m = re.search(r'-u\s+(\S+)', raw)
    p = re.search(r'-p\s+(\S+)', raw)
    h = re.search(r'(swr\.[\w.\-]+(?:\.myhuaweicloud\.com)?)', raw)
    if m and p:
        return m.group(1), p.group(1), (h.group(1) if h else None)
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ak', required=True)
    ap.add_argument('--sk', required=True)
    ap.add_argument('--project-id', default='0e6d048c10c0404a98c958566361c20d')
    ap.add_argument('--project-name', default='cn-north-4')
    ap.add_argument('--region', default='cn-north-4')
    ap.add_argument('--namespace', default='spf-app')
    ap.add_argument('--secret', default='default-secret')
    ap.add_argument('--kubeconfig', default=None)
    ap.add_argument('--restart', action='store_true', help='刷新后自动重启 workloads')
    args = ap.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    if not args.kubeconfig:
        args.kubeconfig = os.path.join(base, 'infra', 'kubeconfig.yaml')

    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkcore.region.region import Region
    from huaweicloudsdkswr.v2.swr_client import SwrClient
    from huaweicloudsdkswr.v2.model.create_secret_request import CreateSecretRequest

    endpoint = 'https://swr.%s.myhuaweicloud.com' % args.region
    print('[*] SWR endpoint: %s' % endpoint)

    creds = BasicCredentials(args.ak, args.sk, project_id=args.project_id)
    client = SwrClient.new_builder() \
        .with_credentials(creds) \
        .with_region(Region(args.region, endpoint)) \
        .build()

    print('[*] 调用 create_secret (生成临时登录指令) ...')
    req = CreateSecretRequest(projectname=args.project_name)
    resp = client.create_secret(req)

    dockerlogin = resp.x_swr_dockerlogin
    expireat = resp.x_swr_expireat
    print('[+] X-Swr-Dockerlogin: %s' % dockerlogin)
    print('[+] 有效期至: %s' % expireat)

    parsed = parse_dockerlogin(dockerlogin)
    if not parsed:
        print('[-] 无法解析 X-Swr-Dockerlogin，请检查输出')
        sys.exit(1)
    user, pw, host = parsed
    print('[+] 解析结果 user=%s host=%s' % (user, host))
    if not host:
        host = 'swr.%s.myhuaweicloud.com' % args.region

    auth_b64 = base64.b64encode(('%s:%s' % (user, pw)).encode()).decode()
    dockerconfig = {
        'auths': {
            host: {'auth': auth_b64},
            # CCE 节点默认走内网镜像仓库地址
            '100.125.0.78:20202': {'auth': auth_b64},
        }
    }
    print('[+] dockerconfigjson hosts: %s' % list(dockerconfig['auths'].keys()))

    b64 = base64.b64encode(json.dumps(dockerconfig).encode()).decode()
    secret_yaml = '''apiVersion: v1
kind: Secret
metadata:
  name: %s
  namespace: %s
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: %s
''' % (args.secret, args.namespace, b64)

    secret_file = os.path.join(base, 'tmp_secret.yaml')
    with open(secret_file, 'w', encoding='utf-8') as f:
        f.write(secret_yaml)

    print('[*] 更新 K8s Secret ...')
    r = subprocess.run(
        ['kubectl', '--kubeconfig', args.kubeconfig, '-n', args.namespace,
         'apply', '-f', secret_file],
        capture_output=True, text=True, timeout=120)
    print(r.stdout)
    if r.returncode != 0:
        print('[-] kubectl apply 失败:', r.stderr)
        sys.exit(1)

    try:
        os.remove(secret_file)
    except OSError:
        pass

    # 验证 secret 已生效
    r2 = subprocess.run(
        ['kubectl', '--kubeconfig', args.kubeconfig, '-n', args.namespace,
         'get', 'secret', args.secret, '-o', 'jsonpath={.data..dockerconfigjson}'],
        capture_output=True, text=True, timeout=60)
    if r2.returncode == 0:
        decoded = base64.b64decode(r2.stdout).decode()
        n = len(decoded)
        print('[+] 验证成功: secret %s/%s 已更新 (%d bytes: %s...)' % (
            args.namespace, args.secret, n, decoded[:80].replace('\n', ' ')))

    if args.restart:
        for wl in [
            'statefulset/postgres',
            'deployment/anomaly-detector',
            'cronjob/data-collector',
        ]:
            print('[*] 重启 %s ...' % wl)
            r3 = subprocess.run(
                ['kubectl', '--kubeconfig', args.kubeconfig, '-n', args.namespace,
                 'rollout', 'restart', wl],
                capture_output=True, text=True, timeout=60)
            print('   ', (r3.stdout or r3.stderr).strip())

    print('[+] 完成!')

if __name__ == '__main__':
    main()