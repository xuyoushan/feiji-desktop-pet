"""
DeepSeek API 客户端封装
"""

import os
import requests
from llm_prompt import FEIJI_SYSTEM_PROMPT
from app_paths import APP_ROOT


def _load_dotenv():
    """从 .env 文件加载环境变量（不覆盖已有值）"""
    env_path = APP_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-chat"
MAX_HISTORY      = 20
REQUEST_TIMEOUT  = 25

_NO_KEY_RESPONSE = (
    '{"text": "啾啾！请先配置 DEEPSEEK_API_KEY，我才能和你聊天哦。", '
    '"emotion": "normal", "action": "idle", "animation": "idle_anim", "tts": true}'
)


class DeepSeekError(Exception):
    """API 调用失败时抛出"""
    pass


def send_message(user_text: str, history: list = None) -> str:
    """
    发送消息给 DeepSeek，返回原始模型输出文本。
    history 格式：[{"role": "user"/"assistant", "content": "..."}]
    失败时抛出 DeepSeekError。
    """
    if not DEEPSEEK_API_KEY:
        return _NO_KEY_RESPONSE
    raw = _call_api(user_text, history, force_json=True)
    return raw


def _call_api(user_text: str, history: list, force_json: bool) -> str:
    messages = [{"role": "system", "content": FEIJI_SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-MAX_HISTORY:])
    messages.append({"role": "user", "content": user_text})

    body = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": 500,
        "temperature": 0.9,
    }
    if force_json:
        body["response_format"] = {"type": "json_object"}

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 422 and force_json:
            return _call_api(user_text, history, force_json=False)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.Timeout:
        raise DeepSeekError("请求超时，肥鸡暂时飞走了一下……")
    except requests.exceptions.ConnectionError:
        raise DeepSeekError("网络连接失败，肥鸡找不到信号……")
    except requests.exceptions.HTTPError as e:
        raise DeepSeekError(f"API 错误：{e.response.status_code}")
    except (KeyError, IndexError):
        raise DeepSeekError("返回格式异常")
    except DeepSeekError:
        raise
    except Exception as e:
        raise DeepSeekError(str(e))
