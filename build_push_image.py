#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用 OCI 镜像组装 + 推送工具（无 Docker 环境）
用法: python build_push_image.py <repo> <tag> <layers_dir> <app_dir> [--entrypoint '...'] [--env A=B --env C=D]
- <layers_dir>: 含 base 基础层目录 (config.json + layerN.tar.gz + manifest.json 或 meta.json)
- <app_dir>: 应用内容目录, 会打成新层追加
流程: 读取基础层 -> 追加应用层 -> 构造 config (合并 Env/Cmd) -> push blobs -> push manifest
"""
import os, sys, json, hashlib, tarfile, io, gzip, requests, argparse, glob

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
        digest = 'sha256:' + hashlib.sha256(data).hexdigest()
        h = self.headers(repo)
        r = self.s.head(f'https://{self.host}/v2/{repo}/blobs/{digest}', headers=h, timeout=15)
        if r.status_code == 200:
            print(f'  [skip] {name} exists ({len(data)} bytes)')
            return digest, len(data)
        r = self.s.post(f'https://{self.host}/v2/{repo}/blobs/uploads/', headers=h, timeout=15)
        if r.status_code not in (201, 202):
            raise RuntimeError(f'upload start failed: {r.status_code} {r.text[:300]}')
        loc = r.headers.get('Location', '')
        if not loc.startswith('http'):
            loc = 'https://' + self.host + loc
        loc_base, _, qs = loc.partition('?')
        put_url = f'{loc_base}?{qs}&digest={digest}'
        r2 = self.s.put(put_url, data=data, headers=h, timeout=600)
        if r2.status_code not in (201, 202):
            raise RuntimeError(f'upload data failed: {r2.status_code} {r2.text[:300]}')
        print(f'  [push] {name} {len(data)/1024/1024:.1f}MB -> {digest[:20]}...')
        return digest, len(data)

    def push_manifest(self, repo, ref, manifest):
        h = self.headers(repo)
        h['Content-Type'] = 'application/vnd.oci.image.manifest.v1+json'
        url = f'https://{self.host}/v2/{repo}/manifests/{ref}'
        r = self.s.put(url, data=json.dumps(manifest).encode(), headers=h, timeout=30)
        if r.status_code not in (201, 202, 200):
            raise RuntimeError(f'manifest push failed: {r.status_code} {r.text[:300]}')
        print(f'  [push] manifest {repo}:{ref} OK')
        return r

def tar_gz_bytes(entries):
    """entries: list of (arcname, data_bytes, mode). 返回 (tar_bytes, gz_bytes, diff_id, digest)"""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as tf:
        for name, data, mode in entries:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = mode
            ti.mtime = 0
            if name.endswith('/'):
                ti.type = tarfile.DIRTYPE
                ti.size = 0
            tf.addfile(ti, io.BytesIO(data) if not name.endswith('/') else None)
    raw_tar = raw.getvalue()
    diff_id = 'sha256:' + hashlib.sha256(raw_tar).hexdigest()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        gz.write(raw_tar)
    gz_data = buf.getvalue()
    digest = 'sha256:' + hashlib.sha256(gz_data).hexdigest()
    return raw_tar, gz_data, diff_id, digest

def dir_to_entries(app_dir, prefix=''):
    """把目录递归读成 tar 条目 (arcname, data, mode). prefix 是目标前缀如 '/usr/src/app'"""
    entries = []
    for root, dirs, files in os.walk(app_dir):
        for d in sorted(dirs):
            full = os.path.join(root, d)
            rel = os.path.relpath(full, app_dir).replace('\\', '/')
            # 目录条目
            entries.append((f'{prefix}/{rel}/', b'', 0o755))
        for f in sorted(files):
            full = os.path.join(root, f)
            rel = os.path.relpath(full, app_dir).replace('\\', '/')
            with open(full, 'rb') as fh:
                data = fh.read()
            st = os.stat(full)
            entries.append((f'{prefix}/{rel}', data, st.st_mode & 0o777))
    if not entries:
        # 空目录也生成根
        entries.append((f'{prefix}/', b'', 0o755))
    return entries

def read_base_config(layers_dir):
    """返回 (base_config_dict, abs_paths_of_layers)"""
    cfg_path = os.path.join(layers_dir, 'config.json')
    with open(cfg_path, encoding='utf-8') as f:
        cfg = json.load(f)
    layers = sorted(glob.glob(os.path.join(layers_dir, 'layer*.tar.gz')),
                    key=lambda p: int(p.rsplit('layer',1)[1].split('.')[0]))
    return cfg, layers

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo', help='如 spf/data-collector')
    ap.add_argument('tag', default='latest', nargs='?')
    ap.add_argument('--base-dir', required=True, help='基础层目录 (config.json + layerN.tar.gz)')
    ap.add_argument('--app-dir', help='应用目录, 追加为最后一层')
    ap.add_argument('--app-prefix', default='/app', help='应用层内目标路径前缀')
    ap.add_argument('--entrypoint', help='Entrypoint JSON 数组字符串, 如 ["java","-jar","/app/runner.jar"]')
    ap.add_argument('--cmd', help='Cmd JSON 数组字符串')
    ap.add_argument('--env', action='append', default=[], help='环境变量 KEY=VALUE (可重复)')
    ap.add_argument('--workdir', default=None)
    ap.add_argument('--auth', default=os.path.join(os.environ.get('TEMP','/tmp'),'swr_auth.txt'))
    ap.add_argument('--host', default='swr.cn-north-4.myhuaweicloud.com')
    args = ap.parse_args()

    # 1. 读基础层
    base_cfg, base_layers = read_base_config(args.base_dir)
    print(f'基础层: {len(base_layers)} 个, config os={base_cfg.get("os")} arch={base_cfg.get("architecture")}')
    entries = []
    for p in base_layers:
        with open(p,'rb') as f:
            entries.append((os.path.basename(p), f.read()))

    # 2. 追加应用层
    app_layer = None
    if args.app_dir:
        print(f'构建应用层: {args.app_dir} -> {args.app_prefix}')
        tars = dir_to_entries(args.app_dir, args.app_prefix)
        raw, gz, diff, dgst = tar_gz_bytes(tars)
        app_layer = (gz, diff, dgst)
        print(f'  应用层: {len(gz)/1024/1024:.1f}MB diff={diff[:20]}')

    # 3. 构造 config
    cfg = dict(base_cfg)
    cfg['rootfs'] = {'type':'layers', 'diff_ids': []}
    for p in base_layers:
        # 计算基础层 diff_id: 解压 tar 的 sha256
        with gzip.open(p,'rb') as f:
            raw_tar = f.read()
        cfg['rootfs']['diff_ids'].append('sha256:' + hashlib.sha256(raw_tar).hexdigest())
    if app_layer:
        cfg['rootfs']['diff_ids'].append(app_layer[1])
    c = cfg.get('config', {})
    if args.env:
        env = c.get('Env', [])
        env = env + args.env
        c['Env'] = env
    if args.entrypoint:
        c['Entrypoint'] = json.loads(args.entrypoint)
    if args.cmd:
        c['Cmd'] = json.loads(args.cmd)
    if args.workdir:
        c['WorkingDir'] = args.workdir
    cfg['config'] = c

    cfg_bytes = json.dumps(cfg).encode()
    cfg_digest = 'sha256:' + hashlib.sha256(cfg_bytes).hexdigest()

    # 4. push
    pusher = SwrPusher(args.auth, args.host)
    layers_manifest = []
    for p in base_layers:
        with open(p,'rb') as f:
            data = f.read()
        dg, size = pusher.push_layer(args.repo, os.path.basename(p), data)
        layers_manifest.append({'mediaType':'application/vnd.oci.image.layer.v1.tar+gzip','digest':dg,'size':size,'annotations':{'org.opencontainers.image.title':os.path.basename(p)}})
    if app_layer:
        dg, size = pusher.push_layer(args.repo, 'app-layer.tar.gz', app_layer[0])
        layers_manifest.append({'mediaType':'application/vnd.oci.image.layer.v1.tar+gzip','digest':dg,'size':size})
    pusher.push_layer(args.repo, 'config.json', cfg_bytes)

    manifest = {
        'schemaVersion': 2,
        'mediaType': 'application/vnd.oci.image.manifest.v1+json',
        'config': {'mediaType':'application/vnd.oci.image.config.v1+json','digest':cfg_digest,'size':len(cfg_bytes)},
        'layers': layers_manifest,
    }
    pusher.push_manifest(args.repo, args.tag, manifest)
    print(f'DONE: {args.repo}:{args.tag}')

if __name__ == '__main__':
    main()