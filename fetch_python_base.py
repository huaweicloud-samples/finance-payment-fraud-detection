#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 daocloud 镜像加速站拉取 python:3.10-slim 基础镜像的所有层到本地
输出: {TEMP}/py310_layers/layer{i}.tar.gz
"""
import os, requests, json, sys

PROXIES = {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}
MIRROR = 'https://docker.m.daocloud.io'
AUTH = 'https://m.daocloud.io/auth/token'

def main():
    s = requests.Session()
    # 1. token
    r0 = s.get(f'{AUTH}?service=docker.m.daocloud.io&scope=repository:library/python:pull', proxies=PROXIES, timeout=20)
    r0.raise_for_status()
    tok = r0.json()['token']
    h = {'Authorization': f'Bearer {tok}',
         'Accept': 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'}

    # 2. manifest (可能返回 index, 需要 amd64)
    r = s.get(f'{MIRROR}/v2/library/python/manifests/3.10-slim', headers=h, proxies=PROXIES, timeout=20)
    r.raise_for_status()
    m = r.json()

    if m.get('mediaType') == 'application/vnd.oci.image.index.v1+json':
        # 多架构 index, 选 amd64
        amd = [x for x in m['manifests'] if x.get('platform', {}).get('architecture') == 'amd64'][0]
        r = s.get(f'{MIRROR}/v2/library/python/manifests/{amd["digest"]}', headers=h, proxies=PROXIES, timeout=20)
        r.raise_for_status()
        m = r.json()

    layers = m.get('layers', [])
    outdir = os.path.join(os.environ.get('TEMP', '/tmp'), 'py310_layers')
    os.makedirs(outdir, exist_ok=True)

    for i, l in enumerate(layers):
        fn = os.path.join(outdir, f'layer{i}.tar.gz')
        if os.path.exists(fn) and os.path.getsize(fn) == l['size']:
            print(f'[skip] layer{i} exists ({l["size"]/1e6:.1f}MB)')
            continue
        dg = l['digest']
        r = s.get(f'{MIRROR}/v2/library/python/blobs/{dg}', headers=h, proxies=PROXIES, timeout=600, stream=True)
        if r.status_code != 200:
            print(f'[ERR] layer{i} HTTP {r.status_code}')
            continue
        with open(fn, 'wb') as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        print(f'[down] layer{i} {l["size"]/1e6:.1f}MB')

    # 保存 manifest 信息（含 config digest）
    cfg = m.get('config', {})
    info = {'layers': [{'digest': l['digest'], 'size': l['size']} for l in layers],
            'config_digest': cfg.get('digest'), 'config_size': cfg.get('size')}
    with open(os.path.join(outdir, 'manifest.json'), 'w') as f:
        json.dump(info, f)
    print('manifest saved. layers:', len(layers))

if __name__ == '__main__':
    main()