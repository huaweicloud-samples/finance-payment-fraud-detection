#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载 anomaly-detector 的 Python 依赖 wheel 到 $TEMP/wheels/
目标: cp310 / manylinux2014_x86_64 (与镜像 base python:3.10-slim 完全一致)
- torch: 必须用 CPU 版 (download.pytorch.org/whl/cpu)
- esdk-obs-python: 仅 sdist 发布, 用 --no-binary 下载
- 其余: 优先 binary wheel, 缺则 sdist
"""
import os, sys, subprocess, glob, shutil

def get_pip():
    # 使用与镜像一致的解释器: 直接参数化 cp310 兼容 wheel
    return [sys.executable, '-m', 'pip']

def main():
    wheels_dir = os.path.join(os.environ['TEMP'], 'wheels')
    os.makedirs(wheels_dir, exist_ok=True)

    req = r"D:\workbuddy\huaweicloud\finance-payment-fraud-detection\src\app\anomaly-detector\requirements.txt"
    with open(req, encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#') and not l.startswith('--')]
    pkgs = [l for l in lines if '==' in l or '>=' in l]
    # 去掉 torch 的额外 index 行(已分离处理), 保留其余包

    print(f'下载 {len(pkgs)} 个依赖到 {wheels_dir}')
    print('  ' + ' '.join(pkgs))

    # 1) 普通 wheel 包 (排除 torch 与 esdk, 单独处理)
    normal = [p for p in pkgs
              if not p.lower().startswith('torch') and 'esdk-obs-python' not in p]
    if normal:
        cmd = [sys.executable, '-m', 'pip', 'download', '--dest', wheels_dir,
               '--only-binary', ':all:', '--platform', 'manylinux2014_x86_64',
               '--python-version', '310', '--implementation', 'cp',
               '--no-deps'] + normal
        print('RUN:', ' '.join(cmd))
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print('[warn] binary-only 下载失败, 尝试 sdist 回退')
            # 找出失败的包: 逐个下载
            for p in normal:
                cmd2 = [sys.executable, '-m', 'pip', 'download', '--dest', wheels_dir,
                        '--no-deps', p]
                subprocess.run(cmd2)

    # 2) torch CPU 版 (extra-index 方式, 不带 --platform 限制因为 torch 轮子命名特殊)
    torch_pkgs = [p for p in pkgs if p.lower().startswith('torch')]
    for p in torch_pkgs:
        cmd = [sys.executable, '-m', 'pip', 'download', '--dest', wheels_dir,
               '--extra-index-url', 'https://download.pytorch.org/whl/cpu',
               '--only-binary', ':all:', '--platform', 'manylinux2014_x86_64',
               '--python-version', '310', '--implementation', 'cp',
               '--no-deps', p]
        print('RUN:', ' '.join(cmd))
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f'[warn] torch 下载失败: {p}')

    # 3) esdk-obs-python 只有 sdist
    esdk = [p for p in pkgs if 'esdk-obs-python' in p]
    for p in esdk:
        cmd = [sys.executable, '-m', 'pip', 'download', '--dest', wheels_dir,
               '--no-binary', ':all:', '--no-deps', p]
        print('RUN:', ' '.join(cmd))
        subprocess.run(cmd)

    # 汇总
    files = glob.glob(os.path.join(wheels_dir, '*'))
    print(f'\n=== 下载完成: {len(files)} 个文件 ===')
    total = sum(os.path.getsize(f) for f in files) / 1024 / 1024
    print(f'总大小: {total:.1f} MB')
    for f in sorted(files):
        print(f'  {os.path.basename(f)} ({os.path.getsize(f)/1024/1024:.1f} MB)')

if __name__ == '__main__':
    main()