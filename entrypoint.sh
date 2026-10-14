#!/bin/bash
set -e

# 以 root 修复卷挂载目录的权限（构建时无法改变运行时挂载卷的 owner）
chown -R appuser:appuser /data /videos

# 降权为 appuser 再启动应用
exec su -s /bin/sh appuser -c "python3 -m uvicorn app.main:app --host 0.0.0.0 --port 7860"
