# -*- coding: utf-8 -*-
"""
Refresh SWR docker credentials and update K8s default-secret.
【修复】绕过本地系统代理(127.0.0.1:7897 Clash)劫持:
    requests 在 Windows 会读注册表系统代理，NO_PROXY 环境变量不可靠。
    通过 requests.Session.trust_env = False 全局禁用代理，直连华为云。

调用链（全部走官方 SDK, AK/SK 签名，无需控制台/CLI）:
  1) SwrClient.create_authorization_token (POST /v2/manage/utils/authorizationtoken)
     -> 响应头 X-Swr-Dockerlogin + 响应体 auths{host:{auth: b64(user:pass)}}
  2) 构造 dockerconfigjson (公网 host + 内网 100.125.0.78:20202)
  3) kubectl apply 更新 default-secret
  4) 触发 workload 滚动重启 (可选)

用法:
    python refresh_swr_noproxy.py --ak <AK> --sk <SK> [--restart]
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys

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

    # ============ 0) 禁用系统代理（关键修复） ============
    import requests
    requests.Session.trust_env = False          # requests 全局忽略环境/注册表代理
    for k in [k for k in os.environ if 'proxy' in k.lower()]:
        os.environ.pop(k)

    base = os.path.dirname(os.path.abspath(__file__))
    if not args.kubeconfig:
        args.kubeconfig = os.path.join(base, 'infra', 'kubeconfig.yaml')

    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkcore.region.region import Region
    from huaweicloudsdkswr.v2.swr_client import SwrClient
    from huaweicloudsdkswr.v2.model.create_authorization_token_request \
        import CreateAuthorizationTokenRequest

    endpoint = 'https://swr-api.%s.myhuaweicloud.com' % args.region
    print('[*] SWR 管理面 endpoint: %s (proxy disabled)' % endpoint)

    creds = BasicCredentials(args.ak, args.sk, project_id=args.project_id)
    client = SwrClient.new_builder() \
        .with_credentials(creds) \
        .with_region(Region(args.region, endpoint)) \
        .build()

    # ============ 1) 生成临时(增强型)登录指令 ============
    print('[*] 调用 create_authorization_token ...')
    resp = client.create_authorization_token(CreateAuthorizationTokenRequest())

    auths = resp.auths or {}
    dockerlogin = resp.x_swr_dockerlogin
    expireat = resp.x_swr_expireat
    print('[+] X-Swr-Dockerlogin: %s' % dockerlogin)
    print('[+] 有效期至: %s' % expireat)
    print('[+] auths hosts: %s' % list(auths.keys()))

    dockerconfig = {'auths': {}}
    for host, info in auths.items():
        dockerconfig['auths'][host] = {'auth': info.auth}
        print('[+] 收录: %s' % host)

    registry_host = 'swr.%s.myhuaweicloud.com' % args.region
    if registry_host not in dockerconfig['auths'] and dockerlogin:
        # 兜底: 解析 'docker login -u xxx -p yyy host'
        m_user = re.search(r'-u\s+(\S+)', dockerlogin)
        m_pass = re.search(r'-p\s+(\S+)', dockerlogin)
        if m_user and m_pass:
            auth_b64 = base64.b64encode(
                ('%s:%s' % (m_user.group(1), m_pass.group(1))).encode()).decode()
            dockerconfig['auths'][registry_host] = {'auth': auth_b64}
            print('[+] 兜底解析 dockerlogin 得到 %s 的 auth' % registry_host)

    if registry_host not in dockerconfig['auths']:
        print('[-] 未能获得主 registry auth，退出')
        sys.exit(1)

    # 内网地址（CCE 节点默认走内网镜像仓库）
    dockerconfig['auths'].setdefault(
        '100.125.0.78:20202', dockerconfig['auths'][registry_host])

    dockerconfig_json = json.dumps(dockerconfig)
    print('[+] dockerconfigjson hosts: %s' % list(dockerconfig['auths'].keys()))

    b64 = base64.b64encode(dockerconfig_json.encode()).decode()
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

    # ============ 2) 更新 K8s Secret ============
    print('[*] 更新 K8s Secret %s/%s ...' % (args.namespace, args.secret))
    r = subprocess.run(
        ['kubectl', '--kubeconfig', args.kubeconfig, '-n', args.namespace,
         'apply', '-f', secret_file],
        capture_output=True, text=True, timeout=120)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        print('[-] kubectl apply 失败')
        sys.exit(1)

    try:
        os.remove(secret_file)
    except OSError:
        pass

    # 验证 secret 回读一致
    r2 = subprocess.run(
        ['kubectl', '--kubeconfig', args.kubeconfig, '-n', args.namespace,
         'get', 'secret', args.secret, '-o', 'jsonpath={.data..dockerconfigjson}'],
        capture_output=True, text=True, timeout=60)
    if r2.returncode == 0 and r2.stdout:
        cur = base64.b64decode(r2.stdout).decode()
        same = '一致' if json.loads(cur) == json.loads(dockerconfig_json) else '不一致!'
        print('[+] 回读验证: %s (%d bytes)' % (same, len(cur)))

    # ============ 3) 滚动重启 workloads (可选) ============
    if args.restart:
        for wl in ['statefulset/postgres', 'deployment/anomaly-detector',
                   'cronjob/data-collector']:
            print('[*] 重启 %s ...' % wl)
            r3 = subprocess.run(
                ['kubectl', '--kubeconfig', args.kubeconfig, '-n', args.namespace,
                 'rollout', 'restart', wl],
                capture_output=True, text=True, timeout=60)
            print('   ', (r3.stdout or r3.stderr).strip())

    print('[+] 完成! 有效期至: %s' % expireat)

if __name__ == '__main__':
    main()