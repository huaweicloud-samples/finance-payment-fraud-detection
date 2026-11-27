#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
anomaly-detector 依赖安装到 $TEMP/sitepkgs_DIR
目标平台: cp310 manylinux2014_x86_64 (与 python:3.10-slim 一致)
- torch 用 CPU 源
- esdk-obs-python 只有 sdist, 单独 --no-deps 安装(纯 python)
- 其余包 pip install --target 全依赖树解析
"""
import os, sys, subprocess, glob, shutil

PY = sys.executable
TEMP = os.environ['TEMP']
SITE = os.path.join(TEMP, 'sitepkgs_ad')
REQ = r"D:\workbuddy\huaweicloud\finance-payment-fraud-detection\src\app\anomaly-detector\requirements.txt"

os.makedirs(SITE, exist_ok=True)

with open(REQ, encoding='utf-8') as f:
    lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
pkgs = [l for l in lines if not l.startswith('--')]

# 1) 除 torch / esdk 外的全部包 (含传递依赖)
normal = [p for p in pkgs
          if not p.lower().startswith('torch') and 'esdk-obs-python' not in p]
print('== 1/3 安装普通依赖 (target=%s) ==' % SITE)
if normal:
    cmd = [PY, '-m', 'pip', 'install', '--target', SITE,
           '--platform', 'manylinux2014_x86_64',
           '--python-version', '310', '--implementation', 'cp',
           '--only-binary', ':all:',
           '--extra-index-url', 'https://download.pytorch.org/whl/cpu',
           ] + normal
    print('RUN: %s' % ' '.join(cmd[:16]) + ' ...')
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print('[FATAL] 普通依赖安装失败 rc=%d' % r.returncode)
        sys.exit(r.returncode)

# 2) torch CPU 版 (torch==2.4.1, 从 CPU 源单独)
torch_pkgs = [p for p in pkgs if p.lower().startswith('torch')]
print('== 2/3 安装 torch CPU 版 ==')
for p in torch_pkgs:
    cmd = [PY, '-m', 'pip', 'install', '--target', SITE,
           '--platform', 'manylinux2014_x86_64',
           '--python-version', '310', '--implementation', 'cp',
           '--only-binary', ':all:',
           '--index-url', 'https://download.pytorch.org/whl/cpu',
           '--no-deps', p]
    print('RUN: %s' % ' '.join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print('[FATAL] torch 安装失败 rc=%d (%s)' % (r.returncode, p))
        sys.exit(r.returncode)

# 3) esdk-obs-python sdist (纯 python, --no-deps 安装到 target)
esdk = [p for p in pkgs if 'esdk-obs-python' in p]
print('== 3/3 安装 esdk-obs-python sdist ==')
for p in esdk:
    cmd = [PY, '-m', 'pip', 'install', '--target', SITE,
           '--no-deps', p]
    print('RUN: %s' % ' '.join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print('[FATAL] esdk 安装失败 rc=%d (%s)' % (r.returncode, p))
        sys.exit(r.returncode)

# 汇总
files = glob.glob(os.path.join(SITE, '*'))
total = sum(os.path.getsize(f) for f in files if os.path.isfile(f)) / 1024/1024
print('\n=== 全部完成 ===')
print('site-packages: %s (%d 项, %.0f MB)' % (SITE, len(files), total))
# 关键包确认
for k in ['torch', 'sentence_transformers', 'obs', 'psycopg', 'sklearn', 'kubernetes', 'pandas']:
    hit = [f for f in os.listdir(SITE) if f.lower().startswith(k)]
    print('  [%s] %s' % (k, hit if hit else 'MISSING!'))