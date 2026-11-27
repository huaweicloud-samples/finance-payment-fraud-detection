#!/usr/bin/env python3
"""
Refresh SWR credentials using Huawei Cloud API V4 signing.
Directly calls SWR API to create long-term credential, then updates K8s secret.
"""

import json
import base64
import os
import time
import urllib.request
from datetime import datetime, timezone
from hmac import new as hmac_new
from hashlib import sha256, md5

AK = 'HPUAVZP83K9HGXWGFWWA'
SK = '8OLXdPtkSPn5GDrVKgLkrWNmiVjzGkk1wxJ3t4P5'
REGION = 'cn-north-4'
SWR_HOST = f'swr.{REGION}.myhuaweicloud.com'
SWR_INSTANCE_ID = 'spf'  # SWR organization name = instance_id


def _hmac(key, message):
    """HMAC-SHA256"""
    if isinstance(key, str): key = key.encode()
    if isinstance(message, str): message = message.encode()
    return hmac_new(key, message, sha256).digest()


def _sign_sk(key, datestamp, region, service):
    """Derive signing key from secret key"""
    k_date = _hmac(key, datestamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_final = _hmac(k_service, 'swr_request')
    return k_final


def _build_iam_token(ak, sk, project_id):
    """Get IAM auth token using API V4 signing"""
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    datestamp = timestamp[:8]
    host = f'iam.{REGION}.myhuaweicloud.com'
    path = '/v3/auth/tokens'

    body = json.dumps({
        'auth': {
            'identity': {
                'methods': ['password'],
                'password': {
                    'user': {
                        'name': 'GT-dgcmy',
                        'password': sk,
                        'domain': {'id': 'default'}
                    }
                }
            },
            'scope': {'project': {'id': project_id}}
        }
    }).encode()

    ct = 'application/json'
    body_md5 = md5(body).hexdigest()

    # Canonical headers (note: x-swa-security-token is empty for first call)
    canonical_headers = (
        f'content-type:{ct}\n'
        f'host:{host}\n'
        f'x-swa-timestamp:{timestamp}\n'
        f'x-swa-security-token:\n'
    )
    signed_headers = 'content-type;host;x-swa-timestamp;x-swa-security-token'
    canonical_request = f'POST\n{path}\n\n{canonical_headers}\n{signed_headers}\n{body_md5}'

    scope = f'{datestamp}/iam/iam_request'
    string_to_sign = (
        f'HMAC-SHA256\n'
        f'{timestamp}\n'
        f'{scope}\n'
        f'{sha256(canonical_request.encode()).hexdigest()}'
    )

    signing_key = _sign_sk(sk, datestamp, REGION, 'iam')
    signature = hmac_new(signing_key, string_to_sign.encode(), sha256).hexdigest()

    headers = {
        'Authorization': f'HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers}, Signature={signature}',
        'x-swa-timestamp': timestamp,
        'Content-Type': ct,
    }

    url = f'https://{host}{path}'
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    resp = urllib.request.urlopen(req, timeout=15)
    data = json.loads(resp.read())
    return data['token']['id'], data['token']['project']['id']


def _call_swr_api(method, path, body=None, iam_token=None):
    """Call SWR API with V4 signing"""
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    datestamp = timestamp[:8]
    host = SWR_HOST

    ct = 'application/json' if body else ''
    body_bytes = body.encode() if body else b''
    body_md5 = md5(body_bytes).hexdigest() if body_bytes else ''

    canonical_headers = (
        f'content-type:{ct}\n'
        f'host:{host}\n'
        f'x-swa-timestamp:{timestamp}\n'
    )
    signed_headers = 'content-type;host;x-swa-timestamp'

    if iam_token:
        canonical_headers += 'x-swa-security-token:\n'
        signed_headers += ';x-swa-security-token'

    canonical_request = f'{method}\n{path}\n\n{canonical_headers}\n{signed_headers}\n{body_md5}'

    scope = f'{datestamp}/swr/swr_request'
    string_to_sign = (
        f'HMAC-SHA256\n'
        f'{timestamp}\n'
        f'{scope}\n'
        f'{sha256(canonical_request.encode()).hexdigest()}'
    )

    signing_key = _sign_sk(SK, datestamp, REGION, 'swr')
    signature = hmac_new(signing_key, string_to_sign.encode(), sha256).hexdigest()

    headers = {
        'Authorization': f'HMAC-SHA256 Credential={AK}/{scope}, SignedHeaders={signed_headers}, Signature={signature}',
        'x-swa-timestamp': timestamp,
        'Content-Type': ct,
    }

    if iam_token:
        headers['x-swa-security-token'] = iam_token

    url = f'https://{host}{path}'
    req = urllib.request.Request(url, data=body_bytes if body_bytes else None, headers=headers, method=method)
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def main():
    # Step 1: Get IAM token + project ID
    print('Step 1: Getting IAM token...')
    try:
        iam_token, project_id = _build_iam_token(AK, SK, '0e6d048c10c0404a98c958566361c20d')
    except Exception:
        # Try to get project ID from user info
        print('  Trying alternative project ID lookup...')
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        datestamp = timestamp[:8]
        host = f'iam.{REGION}.myhuaweicloud.com'
        ct = ''
        body_md5 = ''
        canonical_headers = f'content-type:{ct}\nhost:{host}\nx-swa-timestamp:{timestamp}\nx-swa-security-token:\n'
        signed_headers = 'content-type;host;x-swa-timestamp;x-swa-security-token'
        canonical_request = f'GET\n/v3/users\n\n{canonical_headers}\n{signed_headers}\n{body_md5}'
        scope = f'{datestamp}/iam/iam_request'
        string_to_sign = f'HMAC-SHA256\n{timestamp}\n{scope}\n{sha256(canonical_request.encode()).hexdigest()}'
        signing_key = _sign_sk(SK, datestamp, REGION, 'iam')
        signature = hmac_new(signing_key, string_to_sign.encode(), sha256).hexdigest()

        headers = {
            'Authorization': f'HMAC-SHA256 Credential={AK}/{scope}, SignedHeaders={signed_headers}, Signature={signature}',
            'x-swa-timestamp': timestamp,
            'X-Iam-User-Name': 'GT-dgcmy',
        }
        req = urllib.request.Request(f'https://{host}/v3/users', headers=headers, method='GET')
        resp = urllib.request.urlopen(req, timeout=10)
        user_data = json.loads(resp.read())
        project_id = user_data['user']['project_id']
        print(f'  Project ID: {project_id}')

        # Now get IAM token
        iam_body = json.dumps({
            'auth': {
                'identity': {
                    'methods': ['password'],
                    'password': {
                        'user': {
                            'name': 'GT-dgcmy',
                            'password': SK,
                            'domain': {'id': 'default'}
                        }
                    }
                },
                'scope': {'project': {'id': project_id}}
            }
        }).encode()

        ct2 = 'application/json'
        body_md5_2 = md5(iam_body).hexdigest()
        canonical_headers_2 = (
            f'content-type:{ct2}\nhost:{host}\nx-swa-timestamp:{timestamp}\nx-swa-security-token:\n'
        )
        signed_headers_2 = 'content-type;host;x-swa-timestamp;x-swa-security-token'
        canonical_request_2 = f'POST\n/v3/auth/tokens\n\n{canonical_headers_2}\n{signed_headers_2}\n{body_md5_2}'
        scope_2 = f'{datestamp}/iam/iam_request'
        string_to_sign_2 = f'HMAC-SHA256\n{timestamp}\n{scope_2}\n{sha256(canonical_request_2.encode()).hexdigest()}'
        signature_2 = hmac_new(signing_key, string_to_sign_2.encode(), sha256).hexdigest()

        headers_2 = {
            'Authorization': f'HMAC-SHA256 Credential={AK}/{scope_2}, SignedHeaders={signed_headers_2}, Signature={signature_2}',
            'x-swa-timestamp': timestamp,
            'Content-Type': ct2,
        }
        req = urllib.request.Request(f'https://{host}/v3/auth/tokens', data=iam_body, headers=headers_2, method='POST')
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        iam_token = data['token']['id']
        project_id = data['token']['project']['id']

    print(f'  OK IAM token: {iam_token[:30]}...')
    print(f'  OK Project ID: {project_id}')

    # Step 2: Get SWR long-term credential
    print('Step 2: Getting SWR long-term credential...')
    try:
        swr_body = json.dumps({'validity_days': 1})
        swr_resp = _call_swr_api(
            'POST', '/v2/credentials',
            body=swr_body, iam_token=iam_token
        )
        token = swr_resp.get('token', '')
        print(f'  OK token: {token[:40]}...')
    except Exception as e:
        print(f'  FAIL: {e}')
        # Try with instance_id in path
        try:
            swr_body = json.dumps({'validity_days': 1})
            swr_resp = _call_swr_api(
                'POST', f'/v2/instances/{SWR_INSTANCE_ID}/credentials',
                body=swr_body, iam_token=iam_token
            )
            token = swr_resp.get('token', '')
            print(f'  OK (instance path) token: {token[:40]}...')
        except Exception as e2:
            print(f'  Also failed: {e2}')
            raise

    # Step 3: Get docker login credentials
    print('Step 3: Getting docker login...')
    login_url = f'https://{SWR_HOST}/v2/token?account=GT-dgcmy&token={token}'
    resp = urllib.request.urlopen(urllib.request.Request(login_url, headers={'Accept': 'application/json'}), timeout=10)
    login_data = json.loads(resp.read())
    docker_user = login_data.get('username', 'GT-dgcmy')
    docker_password = login_data.get('password', token)
    print(f'  OK docker user: {docker_user}')

    # Step 4: Update K8s secret
    print('Step 4: Updating K8s default-secret...')
    docker_config = {
        'auths': {
            SWR_HOST: {
                'username': docker_user,
                'password': docker_password,
                'auth': base64.b64encode(f'{docker_user}:{docker_password}'.encode()).decode()
            },
            '100.125.0.78:20202': {
                'auth': base64.b64encode(f'{docker_user}:{docker_password}'.encode()).decode()
            }
        }
    }
    dockerconfigjson_b64 = base64.b64encode(json.dumps(docker_config).encode()).decode()

    kubeconfig = r'D:\workbuddy\huaweicloud\finance-payment-fraud-detection\infra\kubeconfig.yaml'
    tmp_file = r'D:\workbuddy\huaweicloud\finance-payment-fraud-detection\tmp_secret.yaml'

    with open(tmp_file, 'w', encoding='utf-8') as f:
        f.write(f"""apiVersion: v1
kind: Secret
metadata:
  name: default-secret
  namespace: spf-app
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: {dockerconfigjson_b64}
""")

    os.system(f'kubectl --kubeconfig "{kubeconfig}" -n spf-app apply -f "{tmp_file}"')
    os.remove(tmp_file)

    print('\n=== DONE ===')
    print('SWR credentials refreshed and K8s secret updated!')
    print()
    print('Next steps (run in PowerShell):')
    print('  $k = "D:\\workbuddy\\huaweicloud\\finance-payment-fraud-detection\\infra\\kubeconfig.yaml"')
    print('  kubectl --kubeconfig $k -n spf-app rollout restart statefulset/postgres')
    print('  kubectl --kubeconfig $k -n spf-app rollout restart deployment/anomaly-detector')
    print('  kubectl --kubeconfig $k -n spf-app rollout restart cronjob/data-collector')


if __name__ == '__main__':
    main()
