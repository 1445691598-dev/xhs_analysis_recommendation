"""使用 DeepSeek（OpenAI 兼容接口）生成选题与画像分析。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent / ".env")


def _config(name: str, default: str) -> str:
    v = os.getenv(name, "").strip()
    if v:
        return v
    try:
        import streamlit as st

        if name in st.secrets:
            s = str(st.secrets[name]).strip()
            if s:
                return s
    except (FileNotFoundError, KeyError, RuntimeError, TypeError):
        pass
    return default


def deepseek_chat(system: str, user: str, temperature: float = 0.6) -> str:
    key = _config("DEEPSEEK_API_KEY", "")
    if not key:
        raise ValueError(
            "未配置 DeepSeek：本地请在 .env 填写 DEEPSEEK_API_KEY；"
            "Streamlit Cloud 请在 App Settings → Secrets 中填写。"
        )
    base = _config("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = _config("DEEPSEEK_MODEL", "deepseek-chat")
    client = OpenAI(api_key=key, base_url=base)
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    choice = resp.choices[0]
    content = choice.message.content
    return (content or "").strip()
