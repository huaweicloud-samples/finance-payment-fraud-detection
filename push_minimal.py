#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无 Docker 环境下向华为云 SWR push OCI 镜像的完整实现
流程: 构造分层tar -> PUT blob (monolithic) -> PUT manifest -> tag
"""
import os, sys, json, hashlib, tarfile, io, requests, base64

def no_proxy_session():
    for k in [k for k in os.environ if 'proxy' in k.lower()]:
        os.environ.pop(k)
    s = requests.Session()
    s.trust_env = False
    return s

class SwrPusher:
    def __init__(self, auth_file, host='swr.cn-north-4.myhuaweicloud.com'):
        with open(auth_file, encoding='utf-8') as f:
            self.user, self.passwd = f.read().strip().split(':', 1)
        self.host = host
        self.s = no_proxy_session()
        self.tokens = {}

    def get_token(self, repo, scope='pull,push'):
        key = f'{repo}:{scope}'
        if key not in self.tokens:
            url = (f'https://{self.host}/swr/auth/v2/registry/auth/'
                   f'?service=dockyard&scope=repository:{repo}:{scope}')
            r = self.s.get(url, auth=(self.user, self.passwd), timeout=15)
            r.raise_for_status()
            self.tokens[key] = r.json()['token']
        return self.tokens[key]

    def headers(self, repo, scope='pull,push'):
        return {
            'Authorization': f'Bearer {self.get_token(repo, scope)}',
            'Content-Type': 'application/octet-stream',
        }

    def push_layer(self, repo, name, data):
        """push 一个 blob, 返回 (digest, size)"""
        digest = 'sha256:' + hashlib.sha256(data).hexdigest()
        h = self.headers(repo)
        # HEAD 检查是否已存在
        r = self.s.head(f'https://{self.host}/v2/{repo}/blobs/{digest}', headers=h, timeout=15)
        if r.status_code == 200:
            print(f'  [skip] blob {name} already exists ({len(data)} bytes)')
            return digest, len(data)
        # 上传
        r = self.s.post(f'https://{self.host}/v2/{repo}/blobs/uploads/', headers=h, timeout=15)
        if r.status_code not in (201, 202):
            raise RuntimeError(f'upload start failed: {r.status_code} {r.text[:200]}')
        loc = r.headers.get('Location', '')
        if not loc.startswith('http'):
            loc = 'https://' + self.host + loc
        # 分离 query
        loc_base, _, qs = loc.partition('?')
        put_url = f'{loc_base}?{qs}&digest={digest}'
        r2 = self.s.put(put_url, data=data, headers=h, timeout=300)
        if r2.status_code not in (201, 202):
            raise RuntimeError(f'upload data failed: {r2.status_code} {r2.text[:300]}')
        print(f'  [push] blob {name} {len(data)} bytes -> {digest[:20]}...')
        return digest, len(data)

    def push_manifest(self, repo, ref, manifest):
        """push manifest (ref = tag 或 digest)"""
        h = self.headers(repo)
        h['Content-Type'] = 'application/vnd.oci.image.manifest.v1+json'
        url = f'https://{self.host}/v2/{repo}/manifests/{ref}'
        r = self.s.put(url, data=json.dumps(manifest).encode(), headers=h, timeout=30)
        if r.status_code not in (201, 202, 200):
            raise RuntimeError(f'manifest push failed: {r.status_code} {r.text[:300]}')
        print(f'  [push] manifest {repo}:{ref} OK')
        return r

def make_single_layer_oci(image_cmd, entries):
    """构造 OCI 镜像: 单层 tar.gz, 包含 entries (list of (name, data, mode))
       diff_id = 解压后 tar 的 sha256 (OCI 规范); layer digest = gzip 后的 sha256
    """
    # 1. 构造未压缩 tar -> diff_id
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as tf:
        for name, data, mode in entries:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = mode
            tf.addfile(ti, io.BytesIO(data))
    raw_tar = raw.getvalue()
    diff_id = 'sha256:' + hashlib.sha256(raw_tar).hexdigest()

    # 2. gzip 压缩 -> layer digest
    buf = io.BytesIO()
    import gzip
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        gz.write(raw_tar)
    layer = buf.getvalue()
    digest = 'sha256:' + hashlib.sha256(layer).hexdigest()

    config = {
        'architecture': 'amd64',
        'os': 'linux',
        'config': {
            'Entrypoint': image_cmd,
            'Env': ['PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'],
        },
        'rootfs': {'type': 'layers', 'diff_ids': [diff_id]},
    }
    config_bytes = json.dumps(config).encode()
    config_digest = 'sha256:' + hashlib.sha256(config_bytes).hexdigest()
    manifest = {
        'schemaVersion': 2,
        'mediaType': 'application/vnd.oci.image.manifest.v1+json',
        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json',
                   'digest': config_digest, 'size': len(config_bytes)},
        'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip',
                    'digest': digest, 'size': len(layer)}],
    }
    return layer, config_bytes, manifest

if __name__ == '__main__':
    auth_file = os.path.join(os.environ.get('TEMP', '/tmp'), 'swr_auth.txt')
    repo = 'spf/anomaly-detector'
    pusher = SwrPusher(auth_file)

    # 构造最小镜像: hello.sh
    hello = b'#!/bin/sh\necho "SPF minimal image OK"\n'
    layer, cfg, manifest = make_single_layer_oci(
        ['/hello.sh'], [('hello.sh', hello, 0o755)])
    print('== push layer ==')
    pusher.push_layer(repo, 'minimal-layer', layer)
    print('== push config ==')
    pusher.push_layer(repo, 'config.json', cfg)
    print('== push manifest (latest) ==')
    pusher.push_manifest(repo, 'latest', manifest)
    print('DONE: pushed to %s' % repo)