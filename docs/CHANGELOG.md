## 2026-01-11

### ✨ 功能增强
- 增加 LLM 单次返回的结构化意图 `INTENT_JSON` 协议，支持提问/操作/混合三态，并预留 `send_message`/`write_run_code`/`file_control` 等扩展动作。
- 后端将意图透传给前端，用于本地展示与调试。
- 新增 Python 后端 `backend_py/`（FastAPI + python-socketio），按现有 Socket.IO 协议对齐，作为阶段2替换骨架。
- Python 后端依赖收敛到 Python 3.11 可稳定安装的一组版本（见 `backend_py/requirements.txt`）。
- `.gitignore` 新增忽略 `backend/.env` 与 `backend/.env.example`，避免误提交本地敏感配置。
- 启动脚本 `start.sh` 不再依赖 `backend/.env.example`，缺失时会生成最小的 `backend/.env` 模板。

### 🔧 问题修复
- 修复新请求开始时因前端 `stopAllRef` 短暂置位导致的音频块丢弃（表现为“开头内容没念”）。
- 修复 Python 千问 TTS 在 `websockets==14.1` 下 `extra_headers` 参数不兼容导致的运行时错误，恢复服务端音频输出。
- 前端语音输入切换为“录音直传后端 + 千问 Audio 语音识别”，并在停止录音时加入缓冲，降低短句截断概率。

### 📝 修改的文件
- `backend/services/llm.js`
- `backend/controllers/conversationController.js`
- `backend/services/toolRouter.js`
- `backend/utils/safety.js`
- `frontend/src/hooks/useSocket.ts`
- `frontend/src/hooks/useVoice.ts`
- `frontend/src/utils/socket.ts`
- `frontend/src/components/MessageList.tsx`
- `backend_py/main.py`
- `backend_py/controllers/conversation_controller.py`
- `backend_py/services/llm_service.py`
- `backend_py/services/tts_service.py`
- `backend_py/services/qwen_tts_ws.py`
- `backend_py/services/asr_service.py`
- `backend_py/services/tool_router.py`
- `backend_py/services/system_controller.py`
- `backend_py/services/music_controller.py`
- `backend_py/services/file_writer.py`
- `backend_py/session_store.py`
- `backend_py/safety.py`
- `backend_py/utils/wav.py`
- `backend_py/requirements.txt`
- `requirements.txt`
- `start.sh`
