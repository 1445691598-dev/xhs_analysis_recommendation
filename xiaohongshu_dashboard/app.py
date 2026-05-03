"""
小红书数据看板 — 支持在线抓取或上传 CSV 展示笔记数据。
"""

from __future__ import annotations

import importlib.util
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from llm import deepseek_chat
from xhs_scraper import dataframe_ready_rows, scrape_profile_notes

if "show_analysis" not in st.session_state:
    st.session_state["show_analysis"] = False
if "last_profile" not in st.session_state:
    st.session_state["last_profile"] = ""
if "notes_rows" not in st.session_state:
    st.session_state["notes_rows"] = []
if "scrape_error" not in st.session_state:
    st.session_state["scrape_error"] = None
if "topic_llm_result" not in st.session_state:
    st.session_state["topic_llm_result"] = ""

TOPIC_SYSTEM = """你是一位熟悉小红书的内容运营顾问。用户会提供若干条笔记的标题与互动数据（可能不全），以及可选的补充说明。
请用 Markdown 输出，语气清晰可执行；不要编造无法从材料推出的精确粉丝比例；信息不足时请写明「推断依据有限」。
必须包含以下小节（标题用中文）：
1. 内容画像（主题、调性、标题习惯等）
2. 潜在受众画像（合理推断，并标注「推断」）
3. 同赛道参考（说明如何在小红书用搜索词/话题找对标类型；勿捏造未经核实的具体博主昵称）
4. 推荐选题方向（8–12 条，每条含方向标题 + 一句话创作要点）
"""

st.set_page_config(
    page_title="小红书数据看板",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

def _has_browser_scraper() -> bool:
    """云端部署（requirements.txt）不装 DrissionPage，仅本地完整环境可抓取。"""
    return importlib.util.find_spec("DrissionPage") is not None


def _strip_col(name: str) -> str:
    return str(name).strip()


def _find_column(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    """在 DataFrame 列名中按候选名（忽略大小写、首尾空格）匹配第一个存在的列。"""
    norm = {_strip_col(c).lower(): c for c in df.columns}
    for cand in candidates:
        key = _strip_col(cand).lower()
        if key in norm:
            return norm[key]
    return None


def rows_from_uploaded_csv(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    将上传 CSV 转为与 dataframe_ready_rows 输出一致的行结构：
    封面、标题、点赞、收藏、评论（均为展示用字段）。
    """
    if df.empty:
        return []

    c_title = _find_column(df, "标题", "title", "display_title")
    c_cover = _find_column(df, "封面", "封面链接", "cover", "cover_url", "url_default")
    c_like = _find_column(df, "点赞", "likes", "liked_count", "like_count")
    c_collect = _find_column(df, "收藏", "collects", "collected_count", "collect_count")
    c_comment = _find_column(df, "评论", "comments", "comment_count")

    if not c_title and not c_cover:
        raise ValueError(
            "CSV 中未找到「标题」或「封面/封面链接」列，无法对齐展示字段。"
        )

    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        title = ""
        if c_title:
            title = str(row[c_title]).strip() if pd.notna(row[c_title]) else ""
        cover = ""
        if c_cover:
            cover = str(row[c_cover]).strip() if pd.notna(row[c_cover]) else ""

        def num(col: Optional[str]) -> int:
            if not col:
                return 0
            v = row[col]
            if pd.isna(v):
                return 0
            if isinstance(v, (int, float)):
                return int(v)
            s = str(v).strip().replace(",", "").replace("，", "")
            if not s:
                return 0
            try:
                return int(float(s))
            except ValueError:
                return 0

        out.append(
            {
                "封面": cover,
                "标题": title or "（无标题）",
                "点赞": num(c_like),
                "收藏": num(c_collect),
                "评论": num(c_comment),
            }
        )
    return out


def _notes_context_for_llm(rows: List[Dict[str, Any]], max_notes: int = 60) -> str:
    lines: List[str] = []
    for i, r in enumerate(rows[:max_notes]):
        title = str(r.get("标题", "")).strip()
        t = title[:120] + ("…" if len(title) > 120 else "")
        lines.append(
            f"{i + 1}. 标题={t} | 点赞={r.get('点赞', 0)} | 收藏={r.get('收藏', 0)} | 评论={r.get('评论', 0)}"
        )
    return "\n".join(lines) if lines else "（暂无笔记数据）"


def main():
    st.title("📊 小红书数据看板")
    st.caption(
        "支持在线抓取主页笔记（仅本机完整环境），或直接上传已导出的 CSV。"
        "仅供学习研究，请遵守平台规则并控制访问频率。"
    )
    if not _has_browser_scraper():
        st.info(
            "**当前为精简 / 云端部署模式**：未安装浏览器自动化组件，**「开始分析」抓取已关闭**；"
            "访客可上传 CSV 体验表格与 **DeepSeek 选题建议**。在本机若要使用抓取，请执行："
            "`pip install -r requirements-local.txt`"
        )

    col_left, col_right = st.columns([1.55, 1], gap="large")

    with col_left:
        st.subheader("主页链接与抓取")
        st.caption(
            "若已有 CSV，可直接看下方「数据文件」上传，无需填写链接。"
        )
        if _has_browser_scraper():
            profile_url = st.text_input(
                "粘贴小红书主页链接",
                placeholder="例如：https://www.xiaohongshu.com/user/profile/xxxxxxxx",
                label_visibility="collapsed",
            )
            headless = st.toggle(
                "无头模式（不显示浏览器窗口，不推荐：不利于扫码登录）",
                value=False,
                help="关闭此项（默认）会弹出 Chrome 窗口，可在其中扫码登录小红书。",
            )

            analyze = st.button("开始分析", type="primary", use_container_width=True)

            if analyze:
                if not profile_url or not profile_url.strip():
                    st.warning("请先输入小红书主页链接，再点击「开始分析」。")
                else:
                    st.session_state["last_profile"] = profile_url.strip()
                    st.session_state["scrape_error"] = None
                    st.session_state["notes_rows"] = []
                    with st.spinner(
                        "正在启动浏览器并打开主页… 登录后将用 JS 强制滚动（scrollBy 800px），"
                        "每轮随机等待 3–5 秒并拉包 + DOM 补互动，最多 100 轮或满 52 条，"
                        "终端会打印进度，请耐心等待、勿关浏览器。"
                    ):
                        rows, err = scrape_profile_notes(
                            profile_url.strip(),
                            headless=headless,
                            login_wait_seconds=10.0,
                            target_note_count=52,
                        )
                    if err:
                        st.session_state["scrape_error"] = err
                        st.session_state["show_analysis"] = False
                    else:
                        st.session_state["notes_rows"] = dataframe_ready_rows(rows)
                        st.session_state["show_analysis"] = True
                        st.session_state["scrape_error"] = None
        else:
            st.warning(
                "浏览器抓取在本环境不可用。请使用下方 **上传 CSV**，或在本地安装 "
                "`requirements-local.txt` 后重新运行。"
            )

        st.subheader("数据文件")
        uploaded = st.file_uploader(
            "上传抓取结果 CSV（如 my_xhs_data.csv）",
            type=["csv"],
            help="上传后将直接展示表格与封面列，不会启动浏览器抓取。",
        )
        if uploaded is not None:
            try:
                try:
                    raw_df = pd.read_csv(uploaded, encoding="utf-8-sig")
                except UnicodeDecodeError:
                    uploaded.seek(0)
                    raw_df = pd.read_csv(uploaded, encoding="utf-8")
                notes = rows_from_uploaded_csv(raw_df)
                if not notes:
                    st.warning("CSV 中没有有效数据行。")
                    st.session_state["notes_rows"] = []
                    st.session_state["show_analysis"] = False
                else:
                    st.session_state["notes_rows"] = notes
                    st.session_state["show_analysis"] = True
                    st.session_state["scrape_error"] = None
                    st.session_state["last_profile"] = f"本地上传：{uploaded.name}"
            except Exception as e:
                st.session_state["show_analysis"] = False
                st.session_state["scrape_error"] = f"读取 CSV 失败：{e}"
                st.session_state["notes_rows"] = []

        err_msg = st.session_state.get("scrape_error")
        if err_msg:
            st.error("出现问题，详细信息如下：")
            with st.expander("查看完整错误与排查信息", expanded=True):
                st.text(err_msg)

        show = st.session_state.get("show_analysis")
        raw_rows = st.session_state.get("notes_rows") or []
        if show and raw_rows:
            prof = st.session_state.get("last_profile") or "（数据来源：上传或抓取）"
            prof_short = prof if len(prof) <= 72 else prof[:72] + "…"
            st.success(f"当前数据：`{prof_short}`")
            st.divider()
            st.subheader("笔记数据")

            df = pd.DataFrame(raw_rows)
            for col in ("点赞", "收藏", "评论"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
            if "封面" in df.columns:
                df["封面"] = df["封面"].apply(
                    lambda u: u
                    if (isinstance(u, str) and u.startswith("http"))
                    else None
                )
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                height=min(520, 48 + len(df) * 38),
                column_config={
                    "封面": st.column_config.ImageColumn("封面", width="small"),
                    "标题": st.column_config.TextColumn("标题", width="large"),
                    "点赞": st.column_config.NumberColumn("点赞", format="%d"),
                    "收藏": st.column_config.NumberColumn("收藏", format="%d"),
                    "评论": st.column_config.NumberColumn("评论", format="%d"),
                },
            )
            st.caption(f"共 {len(df)} 条笔记（去重后）。")

    with col_right:
        st.subheader("选题建议（DeepSeek）")
        st.caption(
            "本地：使用 `.env` 中的 `DEEPSEEK_API_KEY`；"
            "Streamlit Cloud：在应用 Settings → Secrets 中配置同名变量。"
            "仅在你点击按钮时向 DeepSeek 发送数据。"
        )

        raw_rows = st.session_state.get("notes_rows") or []
        show = st.session_state.get("show_analysis")
        if not (show and raw_rows):
            st.info("请先在左侧完成「开始分析」或上传 CSV，加载笔记数据后再生成 AI 选题建议。")
        else:
            extra = st.text_area(
                "补充说明（可选：赛道、目标人群、想避开的风格等）",
                height=88,
                placeholder="例：职场干货 / 面向应届生 / 想加强系列连载感 …",
                key="topic_extra_llm",
            )
            prof = st.session_state.get("last_profile") or "（未知来源）"
            if st.button("调用 DeepSeek 生成画像与选题", type="primary", use_container_width=True):
                user_blob = (
                    f"【数据来源说明】\n{prof}\n\n"
                    f"【用户补充】\n{(extra or '').strip() or '（无）'}\n\n"
                    f"【笔记列表（标题与互动）】\n{_notes_context_for_llm(raw_rows)}"
                )
                with st.spinner("正在请求 DeepSeek，请稍候…"):
                    try:
                        st.session_state["topic_llm_result"] = deepseek_chat(TOPIC_SYSTEM, user_blob)
                    except Exception as e:
                        st.session_state["topic_llm_result"] = ""
                        st.error(f"调用失败：{e}")

            result = st.session_state.get("topic_llm_result") or ""
            if result:
                st.markdown("---")
                st.markdown(result)

        with st.expander("几条通用运营提示（非 AI）"):
            for tip in (
                "清单体 + 标题带数字，往往更容易被点开。",
                "封面大字号 + 单一主体，信息不要太杂。",
                "文末抛具体问题，有助于提升评论量。",
            ):
                st.markdown(f"- {tip}")


if __name__ == "__main__":
    main()
