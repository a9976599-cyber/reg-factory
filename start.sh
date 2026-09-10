#!/usr/bin/env bash
# 启动 reg-factory 控制面板 (mac/linux)
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[错误] 还没安装。请先运行 ./install.sh"
  exit 1
fi

PORT="${REG_FACTORY_PORT:-8799}"

echo "启动 reg-factory 控制面板 ..."
echo "面板地址: http://127.0.0.1:${PORT}"
echo "Codex K12: 由主面板自动管理,内嵌入口位于左侧导航"
echo "Ctrl+C 停止服务。"

# 后台等 2 秒打开浏览器(mac=open / linux=xdg-open)
( sleep 2; (command -v open >/dev/null && open "http://127.0.0.1:${PORT}") || (command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:${PORT}") ) &

.venv/bin/python -m uvicorn webui.server:app --host 127.0.0.1 --port "$PORT"
