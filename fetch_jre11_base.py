#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 docker.m.daocloud.io 下载 eclipse-temurin:11-jre 基础层
与 fetch_python_base.py 同款成功模式:
- token: m.daocloud.io/auth/token (daocloud 自带)
- blob:  docker.m.daocloud.io/v2/...
先试直连; 失败再走代理 7897
输出到 $TEMP/jre11_layers/
"""
import os, json, requests

TEMP = os.environ['TEMP']
OUT = os.path.join(TEMP, 'jre11_layers')
os.makedirs(OUT, exist_ok=True)
MIRROR = 'https://docker.m.daocloud.io'
AUTH = 'https://m.daocloud.io/auth/token'
PROXIES = {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}
REPO = 'library/eclipse-temurin'

def make_session(with_proxy):
    s = requests.Session()
    s.trust_env = False
    if with_proxy:
        s.proxies = PROXIES
    return s

def main():
    # 探测直连是否可用
    direct_ok = False
    try:
        s = make_session(False)
        r0 = s.get(f'{AUTH}?service=docker.m.daocloud.io&scope=repository:{REPO}:pull', timeout=15)
        r0.raise_for_status()
        direct_ok = True
        print('[mode] 直连 daocloud 可用')
    except Exception as e:
        print(f'[mode] 直连失败 ({e.__class__.__name__}), 尝试代理')
        s = make_session(True)
        r0 = s.get(f'{AUTH}?service=docker.m.daocloud.io&scope=repository:{REPO}:pull', timeout=20)
        r0.raise_for_status()
        print('[mode] 代理可用')

    tok = r0.json()['token']
    h = {'Authorization': f'Bearer {tok}',
         'Accept': 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'}

    # manifest (可能为 index)
    r = s.get(f'{MIRROR}/v2/{REPO}/manifests/11-jre', headers=h, timeout=30)
    r.raise_for_status()
    m = r.json()
    if m.get('mediaType', '').endswith('index.v1+json'):
        amd = [x for x in m['manifests'] if x.get('platform', {}).get('architecture') == 'amd64'][0]
        r = s.get(f'{MIRROR}/v2/{REPO}/manifests/{amd["digest"]}', headers=h, timeout=30)
        r.raise_for_status()
        m = r.json()
    layers = m.get('layers', [])
    print(f'layers: {len(layers)}')

    for i, l in enumerate(layers):
        fn = os.path.join(OUT, f'layer{i}.tar.gz')
        if os.path.exists(fn) and os.path.getsize(fn) == l['size']:
            print(f'[skip] layer{i} exists')
            continue
        dg = l['digest']
        url = f'{MIRROR}/v2/{REPO}/blobs/{dg}'
        try:
            rr = s.get(url, headers=h, timeout=600, stream=True)
            rr.raise_for_status()
        except Exception:
            # 切代理重试
            s2 = make_session(True)
            rr = s2.get(url, headers=h, timeout=600, stream=True)
            rr.raise_for_status()
        with open(fn, 'wb') as f:
            for chunk in rr.iter_content(1 << 20):
                f.write(chunk)
        print(f'[down] layer{i} {l["size"]/1e6:.1f}MB -> {fn}')

    # config
    cfg = m.get('config', {})
    r3 = s.get(f'{MIRROR}/v2/{REPO}/blobs/{cfg["digest"]}', headers=h, timeout=60)
    r3.raise_for_status()
    with open(os.path.join(OUT, 'config.json'), 'wb') as f:
        f.write(r3.content)

    info = {'layers': [{'file': f'layer{i}.tar.gz', 'digest': l['digest'], 'size': l['size']} for i, l in enumerate(layers)],
            'config_digest': cfg.get('digest'), 'config_size': cfg.get('size')}
    with open(os.path.join(OUT, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, indent=2)
    print('DONE. 输出到', OUT)

if __name__ == '__main__':
    main()