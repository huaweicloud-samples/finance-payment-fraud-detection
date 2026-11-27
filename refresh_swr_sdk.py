# -*- coding: utf-8 -*-
"""
Refresh SWR docker credentials and update K8s default-secret.
使用官方 huaweicloudsdkswr SDK 的 create_authorization_token 接口
（IAM 新平面增强型登录指令），全程 AK/SK 签名，无需控制台/CLI。

用法:
    python refresh_swr_sdk.py --ak <AK> --sk <SK> [--project-id <ID>] [--kubeconfig <path>]
"""
import argparse
import base64
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ak', required=True)
    ap.add_argument('--sk', required=True)
    ap.add_argument('--project-id', default='0e6d048c10c0404a98c958566361c20d')
    ap.add_argument('--project-name', default='cn-north-4')
    ap.add_argument('--region', default='cn-north-4')
    ap.add_argument('--namespace', default='spf-app')
    ap.add_argument('--secret', default='default-secret')
    ap.add_argument('--kubeconfig', default=None,
                    help='kubeconfig 路径，默认取 infra/kubeconfig.yaml')
    args = ap.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    if not args.kubeconfig:
        args.kubeconfig = os.path.join(base, 'infra', 'kubeconfig.yaml')

    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkcore.exceptions.exceptions import ClientRequestException
    from huaweicloudsdkcore.region.region import Region
    from huaweicloudsdkswr.v2.region.swr_region import SwrRegion
    from huaweicloudsdkswr.v2.swr_client import SwrClient
    from huaweicloudsdkswr.v2.model.create_authorization_token_request \
        import CreateAuthorizationTokenRequest

    print('[*] 构建 SWR 客户端 (region=%s, project=%s)' % (args.region, args.project_id))

    # 1) 直接构造 Region（避免 Region singleton 缺 endpoint 的问题）
    endpoint = 'https://%s.%s.myhuaweicloud.com' % ('swr', args.region)
    print('[*] SWR endpoint: %s' % endpoint)
    custom_region = Region(args.region, endpoint)

    creds = BasicCredentials(args.ak, args.sk, project_id=args.project_id)
    client = SwrClient.new_builder() \
        .with_credentials(creds) \
        .with_region(custom_region) \
        .build()

    # 2) 调用增强型登录指令接口
    print('[*] 调用 create_authorization_token ...')
    req = CreateAuthorizationTokenRequest()
    resp = client.create_authorization_token(req)

    auths = resp.auths  # dict[str, AuthInfo]
    x_dockerlogin = resp.x_swr_dockerlogin
    x_expire = resp.x_swr_expireat

    print('[+] 返回 auths keys: %s' % list(auths.keys()) if auths else '[!] auths 为空')
    if x_dockerlogin:
        print('[+] X-Swr-Dockerlogin: %s' % x_dockerlogin)

    # 3) 组装 dockerconfigjson
    #    响应体 auths 形如 {"swr.cn-north-4.myhuaweicloud.com": {"auth": "<b64>"}}
    #    另需包含内网地址 100.125.0.78:20202（CCE 节点默认走内网镜像仓库）
    registry_host = 'swr.%s.myhuaweicloud.com' % args.region
    dockerconfig = {'auths': {}}

    if auths:
        for host_key, info in auths.items():
            dockerconfig['auths'][host_key] = {'auth': info.auth}
            print('[+] 已收录 host: %s' % host_key)

    # 确保主 registry host 存在
    if registry_host not in dockerconfig['auths'] and x_dockerlogin:
        # 若 X-Swr-Dockerlogin 形如 'docker login -u xxx -p xxx host' 则解析
        parts = x_dockerlogin.split()
        user = pw = None
        for i, p in enumerate(parts):
            if p == '-u':
                user = parts[i + 1]
            elif p == '-p':
                pw = parts[i + 1]
            elif p == '--username':
                user = parts[i + 1]
            elif p == '--password':
                pw = parts[i + 1]
            elif i == len(parts) - 1 and p.startswith('swr.'):
                registry_host = p
        if user and pw:
            dockerconfig['auths'][registry_host] = {
                'auth': base64.b64encode(('%s:%s' % (user, pw)).encode()).decode()
            }
            print('[+] 补录 host(来自dockerlogin): %s' % registry_host)

    if registry_host not in dockerconfig['auths']:
        print('[!] 警告: 未获得主 registry 的 auth 条目，尝试 X-Swr-Dockerlogin 解析')
        # 再尝试解析 dockerlogin 字符串中的 auth
        if x_dockerlogin:
            # 形如 docker login -u <user> -p <token> swr.cn-north-4.myhuaweicloud.com
            tokens = x_dockerlogin.replace('docker login', '').split()
            user = pw = None
            for i, t in enumerate(tokens):
                if t in ('-u', '--username'):
                    user = tokens[i + 1]
                elif t in ('-p', '--password'):
                    pw = tokens[i + 1]
                elif t.startswith('swr.'):
                    registry_host = t
            if user and pw:
                dockerconfig['auths'][registry_host] = {
                    'auth': base64.b64encode(('%s:%s' % (user, pw)).encode()).decode()
                }
                print('[+] 解析 dockerlogin 得到 auth for %s' % registry_host)

    if registry_host not in dockerconfig['auths']:
        print('[-] 未能获得任何可用认证信息，退出')
        sys.exit(1)

    # 内网地址条目
    dockerconfig['auths']['100.125.0.78:20202'] = dockerconfig['auths'][registry_host]

    dockerconfig_json = json.dumps(dockerconfig)
    print('[+] dockerconfigjson 生成，包含 %d 个 host' % len(dockerconfig['auths']))

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

    # 4) kubectl apply
    print('[*] 应用 Secret 到集群 ...')
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

    print('[+] 完成! Secret %s/%s 已更新' % (args.namespace, args.secret))
    print('[+] 有效期至: %s' % x_expire)

if __name__ == '__main__':
    main()