# -*- coding: utf-8 -*-
"""
小红书用户主页笔记抓取（DrissionPage）。
仅供学习研究，请遵守小红书用户协议与 robots 规则，勿高频爬取。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


def _human_pause(min_sec: float = 1.0, max_sec: float = 3.0) -> None:
    """模拟人工操作的随机停顿（秒）。"""
    lo, hi = (min_sec, max_sec) if min_sec <= max_sec else (max_sec, min_sec)
    time.sleep(random.uniform(lo, hi))


def _safe_print(*objects: Any, sep: str = " ", end: str = "\n", file: Any = None) -> None:
    """向 stdout 打印；Windows 控制台可能对 flush/编码抛 OSError 22，此处吞掉异常不中断爬虫。"""
    try:
        if file is not None:
            print(*objects, sep=sep, end=end, file=file)
        else:
            print(*objects, sep=sep, end=end)
    except OSError:
        pass


# 与小红书接口里 model_type 一致：非笔记卡片不合并
_SKIP_NOTE_MODEL_TYPES = frozenset(
    {
        "rec_query",
        "hot_query",
        "banner",
        "ads",
        "ad",
        "hot_query_rec",
    }
)


def find_notes_in_json(obj: Any) -> List[Dict[str, Any]]:
    """
    递归搜索含「笔记 id + 互动块 + 标题字段」的字典节点，不依赖固定 JSON 路径。
    在 purchased 逻辑基础上兼容 noteId / displayTitle / interactInfo / title。
    """
    found: List[Dict[str, Any]] = []
    if isinstance(obj, dict):
        has_ref = any(k in obj for k in ("note_id", "noteId", "id"))
        inter = obj.get("interact_info")
        if inter is None:
            inter = obj.get("interactInfo")
        has_interact = isinstance(inter, dict) and bool(inter)
        has_title_field = any(
            k in obj for k in ("display_title", "displayTitle", "title")
        )
        if has_ref and has_interact and has_title_field:
            found.append(obj)
        else:
            for v in obj.values():
                found.extend(find_notes_in_json(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_notes_in_json(item))
    return found


def _skip_model_note(n: Dict[str, Any]) -> bool:
    mt = str(n.get("model_type") or n.get("modelType") or "").lower()
    return mt in _SKIP_NOTE_MODEL_TYPES


# 新脚本同款：非贪婪到最近的 </script>，利于首屏整块截取
_RE_INITIAL_STATE_SCRIPT = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_initial_state_object(html: str) -> Optional[Any]:
    """
    解析 window.__INITIAL_STATE__ 为 Python 对象。
    优先使用与 purchased 一致的正则；失败则回退到旧版截取逻辑。
    """
    if not html or "__INITIAL_STATE__" not in html:
        return None
    raw_js: Optional[str] = None
    m = _RE_INITIAL_STATE_SCRIPT.search(html)
    if m:
        raw_js = m.group(1).strip()
        if raw_js.endswith(";"):
            raw_js = raw_js[:-1]
    if not raw_js:
        raw_js = _extract_initial_state_raw(html)
    if not raw_js:
        return None
    fixed = (
        raw_js.replace(":undefined", ":null")
        .replace("undefined", "null")
        .replace(":void 0", ":null")
    )
    try:
        return json.loads(fixed, strict=False)
    except json.JSONDecodeError:
        alt = _extract_initial_state_raw(html)
        if not alt or alt.strip() == (raw_js or "").strip():
            return None
        fixed2 = (
            alt.replace(":undefined", ":null")
            .replace("undefined", "null")
            .replace(":void 0", ":null")
        )
        try:
            return json.loads(fixed2, strict=False)
        except json.JSONDecodeError:
            return None


def _extract_initial_state_raw(html: str) -> Optional[str]:
    """从 HTML 中截取 window.__INITIAL_STATE__ 的 JSON 片段（与页面脚本格式对齐）。"""
    if not html or "__INITIAL_STATE__" not in html:
        return None
    m = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*(.+?)<\/script>",
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*(.+)</script>",
            html,
            re.DOTALL | re.IGNORECASE,
        )
    if not m:
        return None
    raw = m.group(1).strip()
    if raw.endswith(";"):
        raw = raw[:-1]
    return raw


def normalize_profile_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith("http"):
        u = "https://www.xiaohongshu.com/user/profile/" + u.lstrip("/")
    p = urlparse(u)
    if p.netloc and "xiaohongshu" not in p.netloc and "rednote" not in p.netloc:
        return u
    return u


def put_merged_row(store: Dict[str, Dict[str, Any]], row: Dict[str, Any]) -> None:
    """
    按 _dedupe 去重；同一笔记合并 API 与 DOM 时，对点赞/收藏/评论取较大值（强制把 DOM 抠出的数补进 JSON 为 0 的项）。
    """
    if not row:
        return
    key = str(row.get("_dedupe", "")).strip()
    if not key:
        return
    if key not in store:
        store[key] = {k: v for k, v in row.items()}
        return
    a = store[key]
    ta = (str(a.get("标题") or "")).strip()
    tb = (str(row.get("标题") or "")).strip()
    if ta in ("", "（无标题）") and tb:
        title = tb
    elif tb in ("", "（无标题）") and ta:
        title = ta
    else:
        title = ta if len(ta) >= len(tb) else tb
    cover = (str(a.get("封面") or "")).strip() or (str(row.get("封面") or "")).strip()
    store[key] = {
        "_dedupe": key,
        "标题": title or "（无标题）",
        "封面": cover,
        "点赞": max(int(a.get("点赞") or 0), int(row.get("点赞") or 0)),
        "收藏": max(int(a.get("收藏") or 0), int(row.get("收藏") or 0)),
        "评论": max(int(a.get("评论") or 0), int(row.get("评论") or 0)),
    }


def _looks_like_interact_dict(x: Dict[str, Any]) -> bool:
    """判断子 dict 是否像互动统计块（含 note_card 外其它层级的嵌套）。"""
    if not isinstance(x, dict) or not x:
        return False
    joined = " ".join(str(k).lower() for k in x)
    return any(
        t in joined
        for t in (
            "liked",
            "collect",
            "comment",
            "share",
            "count",
            "fav",
            "bookmark",
        )
    )


def _deep_scan_interact_candidates(
    obj: Any,
    depth: int = 0,
    max_depth: int = 4,
    out: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """在整条笔记对象内深度查找可能含点赞/收藏/评论的子 dict（不限于 note_card）。"""
    if out is None:
        out = []
    if depth > max_depth or obj is None:
        return out
    if isinstance(obj, dict):
        for _k, v in obj.items():
            if isinstance(v, dict):
                if _looks_like_interact_dict(v) and len(v) <= 48:
                    out.append(v)
                _deep_scan_interact_candidates(v, depth + 1, max_depth, out)
            elif isinstance(v, list):
                for it in v[:30]:
                    if isinstance(it, dict):
                        _deep_scan_interact_candidates(it, depth + 1, max_depth, out)
    elif isinstance(obj, list):
        for it in obj[:30]:
            if isinstance(it, dict):
                _deep_scan_interact_candidates(it, depth + 1, max_depth, out)
    return out


def _extract_interact_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    从扁平或嵌套结构选取「最像互动数据」的 dict（兼容 note_card.interact_info），
    并在 note_card 外对整条对象做浅层深度扫描以兜底其它层级字段。
    """
    if not isinstance(d, dict):
        return {}
    candidates: List[Dict[str, Any]] = []
    for key in ("interact_info", "interactInfo", "interact"):
        v = d.get(key)
        if isinstance(v, dict) and v:
            candidates.append(v)
    nc = d.get("note_card") or d.get("noteCard")
    if isinstance(nc, dict):
        for key in ("interact_info", "interactInfo", "interact"):
            v = nc.get(key)
            if isinstance(v, dict) and v:
                candidates.append(v)
    for key in ("interact_data", "engagement", "stats", "count_info", "counter"):
        v = d.get(key)
        if isinstance(v, dict) and v and _looks_like_interact_dict(v):
            candidates.append(v)
    seen_ids = {id(c) for c in candidates}
    for sub in _deep_scan_interact_candidates(d, 0, 4, []):
        sid = id(sub)
        if sid not in seen_ids:
            seen_ids.add(sid)
            candidates.append(sub)
    if not candidates:
        return {}
    best: Dict[str, Any] = {}
    best_score = -1
    for v in candidates:
        keys = " ".join(str(k).lower() for k in v)
        score = sum(
            1
            for kw in (
                "count",
                "liked",
                "collect",
                "comment",
                "share",
                "fav",
            )
            if kw in keys
        )
        if score > best_score:
            best_score = score
            best = v
    return best


def _print_interact_debug(
    raw: Dict[str, Any],
    interact: Dict[str, Any],
    nid: str,
    title: str,
    likes: int,
    collects: int,
    comments: int,
) -> None:
    """收藏/评论仍为 0 时，在终端打印原始 JSON 片段便于核对字段名。"""
    if collects > 0 and comments > 0:
        return
    has_nc = isinstance(raw.get("note_card") or raw.get("noteCard"), dict)
    if not interact and not has_nc and likes == 0:
        return
    snippet: Dict[str, Any] = {
        "hint_note_id": nid,
        "hint_title": (title or "")[:80],
        "interact_info_keys": list(interact.keys()) if isinstance(interact, dict) else [],
        "interact_info": interact if isinstance(interact, dict) else {},
    }
    nc = raw.get("note_card") or raw.get("noteCard")
    if isinstance(nc, dict):
        snippet["note_card_keys_sample"] = list(nc.keys())[:40]
        for k in ("interact_info", "interactInfo", "interact"):
            if k in nc and isinstance(nc[k], dict):
                snippet[f"note_card.{k}"] = nc[k]
    try:
        s = json.dumps(snippet, ensure_ascii=False, default=str)
    except Exception:
        s = str(snippet)
    logging.getLogger(__name__).debug(
        "interact_debug snippet: %s%s",
        s[:3500],
        " ... (truncated)" if len(s) > 3500 else "",
    )
    _safe_print(f"interact_debug note={nid[:24]!r} len={len(s)}")


def _canonical_dedupe_key(note_id: str, title: str, cover: str) -> str:
    """唯一键：优先笔记 id；否则 标题 + 封面 URL（去 query）。"""
    nid = (note_id or "").strip()
    if nid and nid not in ("undefined", "null", "0") and len(nid) >= 16:
        return f"id:{nid}"
    t = (title or "").strip()
    c = (cover or "").strip().split("?", 1)[0].strip()
    return f"tc:{t}|{c}"[:512]


def _collect_count_fields(interact: Dict[str, Any], field: str) -> int:
    """从 interact_info 中取点赞/收藏/评论，兼容多种字段命名。"""
    if not isinstance(interact, dict):
        return 0
    if field == "like":
        keys = (
            "liked_count",
            "likedCount",
            "like_count",
            "likeCount",
            "likes",
        )
    elif field == "collect":
        keys = (
            "collected_count",
            "collectedCount",
            "collect_count",
            "collectCount",
            "collection_count",
            "collectionCount",
            "favorite_count",
            "favoriteCount",
            "bookmark_count",
            "bookmarkCount",
            "save_count",
            "saveCount",
        )
    else:
        keys = (
            "comment_count",
            "commentCount",
            "comments_count",
            "commentsCount",
            "chat_count",
            "chatCount",
            "reply_count",
            "replyCount",
            "msg_count",
            "msgCount",
        )
    for k in keys:
        if k in interact:
            return _parse_count(interact.get(k))
    return 0


def _parse_count(val: Any) -> int:
    if val is None:
        return 0
    if isinstance(val, bool):
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().replace(",", "").replace("，", "")
    if not s:
        return 0
    if "万" in s:
        num = float(re.sub(r"[^\d.]", "", s) or 0)
        return int(num * 10000)
    m = re.search(r"([\d.]+)", s)
    if m:
        return int(float(m.group(1)))
    return 0


def _cover_from_note(d: Dict[str, Any]) -> str:
    cov = d.get("cover") or d.get("note_cover") or {}
    if isinstance(cov, str) and cov.startswith("http"):
        return cov
    if isinstance(cov, dict):
        u = (
            cov.get("url_default")
            or cov.get("url")
            or cov.get("url_pre")
        )
        if u:
            return str(u)
        info_list = cov.get("info_list") or cov.get("infoList") or []
        if info_list and isinstance(info_list[0], dict):
            u2 = info_list[0].get("url") or info_list[0].get("url_default")
            if u2:
                return str(u2)
    for key in ("images_list", "image_list", "imageList"):
        imgs = d.get(key)
        if imgs and isinstance(imgs, list) and imgs and isinstance(imgs[0], dict):
            u = imgs[0].get("url_default") or imgs[0].get("url") or imgs[0].get("info_list", [{}])[0].get("url")
            if u:
                return str(u)
    return ""


def normalize_note_dict(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    card = raw.get("note_card") or raw.get("noteCard")
    if isinstance(card, dict):
        raw = {**raw, **card}
    nid = str(
        raw.get("note_id")
        or raw.get("noteId")
        or raw.get("id")
        or ""
    )
    title = (
        raw.get("display_title")
        or raw.get("displayTitle")
        or raw.get("title")
        or raw.get("desc")
        or ""
    )
    title = str(title).strip()
    interact = _extract_interact_dict(raw)
    likes = _collect_count_fields(interact, "like")
    collects = _collect_count_fields(interact, "collect")
    comments = _collect_count_fields(interact, "comment")

    cover = _cover_from_note(raw)
    if not nid and not title and not cover:
        return None
    _print_interact_debug(raw, interact, nid, title, likes, collects, comments)
    uid = _canonical_dedupe_key(nid, title, cover)
    return {
        "_dedupe": uid,
        "封面": cover,
        "标题": title or "（无标题）",
        "点赞": likes,
        "收藏": collects,
        "评论": comments,
    }


def _looks_like_note_card(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    if any(k in d for k in ("note_card", "noteCard")):
        return True
    has_id = any(k in d for k in ("note_id", "noteId", "id"))
    has_title = any(
        k in d for k in ("title", "display_title", "displayTitle", "desc")
    )
    has_extra = any(
        k in d
        for k in ("interact_info", "interactInfo", "cover", "images_list", "image_list")
    )
    return bool(has_id and (has_title or has_extra))


def _deep_find_note_lists(obj: Any, depth: int = 0) -> List[List[Dict[str, Any]]]:
    found: List[List[Dict[str, Any]]] = []
    if depth > 14:
        return found
    if isinstance(obj, dict):
        for _k, v in obj.items():
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                if _looks_like_note_card(v[0]):
                    found.append(v)  # type: ignore[arg-type]
            found.extend(_deep_find_note_lists(v, depth + 1))
    elif isinstance(obj, list):
        for it in obj:
            found.extend(_deep_find_note_lists(it, depth + 1))
    return found


def _rows_from_initial_state_dict(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从已解析的 __INITIAL_STATE__ 根对象产出笔记行：先递归 find_notes，再走列表兜底。"""
    merged: Dict[str, Dict[str, Any]] = {}
    for n in find_notes_in_json(state):
        if not isinstance(n, dict) or _skip_model_note(n):
            continue
        row = normalize_note_dict(n)
        if row:
            put_merged_row(merged, row)

    if merged:
        return list(merged.values())

    lists_to_merge: List[List[Dict[str, Any]]] = []
    user = state.get("user") if isinstance(state.get("user"), dict) else {}
    if isinstance(user, dict):
        for key in ("notes", "postedNotes", "noteList"):
            v = user.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                lists_to_merge.append(v)  # type: ignore[arg-type]
        upd = user.get("userPageData") or user.get("user_page_data")
        if isinstance(upd, dict):
            for key in ("notes", "noteList", "postedNotes"):
                v = upd.get(key)
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    lists_to_merge.append(v)  # type: ignore[arg-type]

    lists_to_merge.extend(_deep_find_note_lists(state))

    for lst in lists_to_merge:
        for item in lst:
            row = normalize_note_dict(item)
            if row:
                put_merged_row(merged, row)

    return list(merged.values())


def parse_initial_state_html(html: str) -> List[Dict[str, Any]]:
    """首屏 / 收尾：HTML → __INITIAL_STATE__ → find_notes_in_json（含路径兜底）。"""
    state = _parse_initial_state_object(html)
    if not isinstance(state, dict):
        return []
    return _rows_from_initial_state_dict(state)


def _body_to_text(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes):
        try:
            return body.decode("utf-8")
        except Exception:
            return body.decode("utf-8", errors="ignore")
    return str(body)


def parse_response_json_notes(body: Any) -> List[Dict[str, Any]]:
    """
    解析监听到的 JSON 响应：整包递归 find_notes_in_json，再 normalize。
    若递归未命中，再深度遍历 notes / noteList 等列表（旧逻辑兜底）。
    """
    text = _body_to_text(body)
    if not text.strip():
        return []
    try:
        root: Any = json.loads(text)
    except json.JSONDecodeError:
        return []

    merged: Dict[str, Dict[str, Any]] = {}
    for n in find_notes_in_json(root):
        if not isinstance(n, dict) or _skip_model_note(n):
            continue
        row = normalize_note_dict(n)
        if row:
            put_merged_row(merged, row)

    if merged:
        return list(merged.values())

    list_keys = ("notes", "noteList", "note_list", "items", "list")

    def consider_list(lst: Any) -> None:
        if not isinstance(lst, list) or not lst:
            return
        for item in lst:
            if not isinstance(item, dict):
                continue
            row = normalize_note_dict(item)
            if row:
                put_merged_row(merged, row)

    def walk(o: Any, depth: int) -> None:
        if depth > 14:
            return
        if isinstance(o, dict):
            for k in list_keys:
                if k in o:
                    consider_list(o.get(k))
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            if o and isinstance(o[0], dict):
                if any(
                    _looks_like_note_card(x) or normalize_note_dict(x)
                    for x in o
                    if isinstance(x, dict)
                ):
                    consider_list(o)
            for it in o:
                walk(it, depth + 1)

    walk(root, 0)
    if not merged and isinstance(root, list) and root and isinstance(root[0], dict):
        consider_list(root)
    return list(merged.values())


def try_parse_user_posted_body(body: Any) -> List[Dict[str, Any]]:
    """兼容旧名：与 parse_response_json_notes 相同。"""
    return parse_response_json_notes(body)


def try_parse_notes_response_flexible(body: Any) -> List[Dict[str, Any]]:
    """兼容旧名：与 parse_response_json_notes 相同。"""
    return parse_response_json_notes(body)


def collect_packets_notes(
    page: Any,
    rounds: int = 6,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """
    从已开启的 listen 队列中取包并解析笔记。
    返回：(笔记行列表, 捕获到的数据包数量, 其中包含笔记列表的包数量)。
    """
    rows: Dict[str, Dict[str, Any]] = {}
    packets_seen = 0
    packets_with_notes = 0
    for _ in range(rounds):
        pkt = page.listen.wait(timeout=2.5, fit_count=False)
        if pkt is False or pkt is None:
            break
        packets_seen += 1
        body = None
        try:
            body = pkt.response.body if pkt.response else None
        except Exception:
            body = None
        parsed = parse_response_json_notes(body)
        if parsed:
            packets_with_notes += 1
        for row in parsed:
            put_merged_row(rows, row)
    return list(rows.values()), packets_seen, packets_with_notes


def _extract_note_id_from_blob(blob: str) -> str:
    """从链接或内嵌 JSON 片段中提取 24 位十六进制笔记 id。"""
    if not blob:
        return ""
    for pat in (
        r"/explore/([0-9a-f]{24})",
        r"/discovery/item/([0-9a-f]{24})",
        r'"note_id"\s*:\s*"([0-9a-f]{24})"',
        r'"noteId"\s*:\s*"([0-9a-f]{24})"',
    ):
        m = re.search(pat, blob, re.I)
        if m:
            return m.group(1).lower()
    return ""


def _try_read_sibling_count_text(icon_node: Any) -> str:
    """读取图标节点附近兄弟节点的纯数字文案（赞/收藏/评论）。"""
    xps = (
        "./following-sibling::*[1]",
        "./following-sibling::*[2]",
        "./following-sibling::span[1]",
        "./parent::*/span",
        "./ancestor::div[1]//span[contains(@class,'count') or contains(@class,'Count')][1]",
    )
    for xp in xps:
        try:
            sib = icon_node.ele(f"xpath:{xp}", timeout=0.12)
            if not sib:
                continue
            t = (getattr(sib, "text", None) or "").strip()
            if re.fullmatch(r"[\d.]+万?", t) or (t.isdigit() and len(t) <= 12):
                return t
        except Exception:
            continue
    return ""


def _dom_interact_from_collect_chat_icons(ele: Any) -> Tuple[int, int, int]:
    """类名含 like / collect / chat 等图标的兄弟节点数字（赞、收藏、评论）。"""
    likes = collects = comments = 0
    for kw in ("like", "Like", "zan", "Zan", "love", "good", "heart"):
        try:
            nodes = ele.eles(f'xpath:.//*[contains(@class,"{kw}")]')
        except Exception:
            nodes = []
        for node in nodes or []:
            t = _try_read_sibling_count_text(node)
            if t:
                likes = max(likes, _parse_count(t))
    for kw in ("collect", "Collect", "collection", "star", "bookmark"):
        try:
            nodes = ele.eles(f'xpath:.//*[contains(@class,"{kw}")]')
        except Exception:
            nodes = []
        for node in nodes or []:
            t = _try_read_sibling_count_text(node)
            if t:
                collects = max(collects, _parse_count(t))
    for kw in ("chat", "Chat", "comment", "Comment", "reply"):
        try:
            nodes = ele.eles(f'xpath:.//*[contains(@class,"{kw}")]')
        except Exception:
            nodes = []
        for node in nodes or []:
            t = _try_read_sibling_count_text(node)
            if t:
                comments = max(comments, _parse_count(t))
    return likes, collects, comments


def _dom_element_to_note_row(ele: Any) -> Optional[Dict[str, Any]]:
    """从单个卡片 DOM 节点抽取一行笔记（启发式）。"""
    try:
        text_all = (getattr(ele, "text", None) or "").strip()
    except Exception:
        text_all = ""

    cover = ""
    try:
        imgs = ele.eles("tag:img")
        for img in imgs or []:
            src = (
                img.attr("src")
                or img.attr("data-src")
                or img.attr("data-original")
                or ""
            )
            src = (src or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            if "http" in src or src.startswith("data:"):
                cover = src
                break
    except Exception:
        pass

    title = ""
    try:
        t_el = ele.ele('xpath:.//*[contains(@class,"title")]', timeout=0.35)
        if t_el:
            title = (getattr(t_el, "text", None) or "").strip()
    except Exception:
        pass

    if not title:
        lines = [ln.strip() for ln in text_all.splitlines() if ln.strip()]
        for ln in lines:
            if len(ln) <= 1:
                continue
            if re.fullmatch(r"[\d.,]+万?", ln):
                continue
            if re.fullmatch(r"\d{1,4}", ln) and len(ln) <= 3:
                continue
            if len(ln) > len(title) and len(ln) < 200:
                title = ln

    likes = collects = comments = 0
    html_snip = ""
    try:
        html_snip = str(getattr(ele, "html", "") or "")
    except Exception:
        html_snip = ""
    blob = (text_all + "\n" + html_snip)[:12000]

    zm = re.search(r"(?:赞|点赞)[^\d]*([\d.]+万?)", blob)
    if zm:
        likes = _parse_count(zm.group(1))
    cm = re.search(r"收藏[^\d]*([\d.]+万?)", blob)
    if cm:
        collects = _parse_count(cm.group(1))
    mm = re.search(r"评论[^\d]*([\d.]+万?)", blob)
    if mm:
        comments = _parse_count(mm.group(1))

    # 方案 B：class 含 count 的 span / 通用数字块（卡片内常见竖排：赞 / 收藏 / 评论）
    span_nums: List[int] = []
    try:
        spans = ele.eles(
            'xpath:.//span[contains(@class,"count") or contains(@class,"Count")]'
        )
        for sp in spans or []:
            t = (getattr(sp, "text", None) or "").strip()
            if re.fullmatch(r"[\d.]+万?", t) or re.fullmatch(r"\d+", t):
                span_nums.append(_parse_count(t))
    except Exception:
        pass
    if len(span_nums) >= 1 and likes == 0:
        likes = span_nums[0]
    if len(span_nums) >= 2 and collects == 0:
        collects = span_nums[1]
    if len(span_nums) >= 3 and comments == 0:
        comments = span_nums[2]

    ic_like, ic_collect, ic_comment = _dom_interact_from_collect_chat_icons(ele)
    likes = max(likes, ic_like)
    collects = max(collects, ic_collect)
    comments = max(comments, ic_comment)

    if not title and not cover:
        return None
    nid = _extract_note_id_from_blob(blob)
    uid = _canonical_dedupe_key(nid, title, cover)
    return {
        "_dedupe": uid,
        "封面": cover,
        "标题": title or "（无标题）",
        "点赞": likes,
        "收藏": collects,
        "评论": comments,
    }


def parse_notes_from_dom(page: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    保底 DOM：匹配常见笔记卡片 class，从节点文本与 img 抽取信息。
    """
    diagnostics: List[str] = []
    merged: Dict[str, Dict[str, Any]] = {}
    selectors: Tuple[Tuple[str, str], ...] = (
        ('xpath://div[contains(@class,"note-item")]', "div[class*=note-item]"),
        ('xpath://div[contains(@class,"feed-card")]', "div[class*=feed-card]"),
        ('xpath://div[contains(@class,"note-card")]', "div[class*=note-card]"),
        ('xpath://div[contains(@class,"NoteFeed")]', "div[class*=NoteFeed]"),
        ('xpath://section[contains(@class,"note")]', "section[class*=note]"),
        ('xpath://div[contains(@class,"mask")][contains(@class,"note")]', "div.note+mask"),
    )
    for sel, label in selectors:
        eles = None
        try:
            eles = page.eles(sel, timeout=2)
        except Exception as ex:
            diagnostics.append(f"• DOM「{label}」查询异常：{type(ex).__name__}: {ex}")
            continue
        if not eles:
            diagnostics.append(f"• DOM「{label}」：0 个匹配节点。")
            continue
        diagnostics.append(f"• DOM「{label}」：匹配 {len(eles)} 个节点。")
        for ele in eles:
            row = _dom_element_to_note_row(ele)
            if row:
                put_merged_row(merged, row)
    return list(merged.values()), diagnostics


def _diagnose_html_snippet(html: str) -> List[str]:
    """根据 HTML 片段给出简短风控/登录线索（仅启发式）。"""
    hints: List[str] = []
    if not html or len(html) < 200:
        hints.append("• 页面 HTML 过短，可能未正常打开目标页。")
        return hints
    low = html.lower()
    for kw, msg in (
        ("扫码", "• 页面文案中出现「扫码」，可能仍在登录流程。"),
        ("请登录", "• 页面文案中出现「请登录」。"),
        ("登录后", "• 页面文案中出现「登录后」。"),
        ("验证", "• 页面文案中出现「验证」，可能存在风控验证。"),
        ("访问异常", "• 页面文案中出现「访问异常」。"),
        ("滑块", "• 页面文案中出现「滑块」验证。"),
        ("captcha", "• 页面中出现 captcha 相关文本。"),
    ):
        if kw.lower() in low or kw in html:
            hints.append(msg)
    return hints[:6]


def scrape_profile_notes(
    profile_url: str,
    *,
    headless: bool = False,
    login_wait_seconds: float = 10.0,
    target_note_count: int = 52,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    使用 Chromium 打开用户主页：监听 sns/web/v1/user_posted，首屏从 HTML 解析
    window.__INITIAL_STATE__（递归 find_notes_in_json），滚动阶段继续拉包 + DOM。
    默认显示真实浏览器窗口（headless=False），便于扫码登录。
    强制滚动：原生 JS scrollBy(0,800)，每轮随机等待 3–5 秒并拉包 + DOM 回血；最多 100 轮或达到 target_note_count。
    返回 (笔记行列表, 错误信息)。错误信息为 None 表示成功解析到至少一条笔记。
    """
    from DrissionPage import ChromiumPage, ChromiumOptions

    url = normalize_profile_url(profile_url)
    if not url or "user/profile" not in url:
        return [], "请输入有效的小红书用户主页链接（需包含 /user/profile/）。"

    co = ChromiumOptions()
    co.headless(headless)
    co.set_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    page = ChromiumPage(co)
    merged: Dict[str, Dict[str, Any]] = {}
    packet_round1_seen = packet_round1_with = 0
    packet_scroll_seen = packet_scroll_with = 0
    scroll_iters = 0
    dom_diag: List[str] = []

    try:
        page.listen.start("sns/web/v1/user_posted")
        page.get(url)
        page.wait.doc_loaded()
        # 预留扫码登录时间（固定等待）
        time.sleep(float(login_wait_seconds))
        _human_pause(1.0, 3.0)

        # 首屏：登录等待结束后立即从 HTML 解析 __INITIAL_STATE__（不依赖滚动）
        html_boot = page.html
        for row in parse_initial_state_html(html_boot):
            put_merged_row(merged, row)

        first_batch, packet_round1_seen, packet_round1_with = collect_packets_notes(
            page, rounds=28
        )
        for row in first_batch:
            put_merged_row(merged, row)

        tgt = max(1, int(target_note_count))
        max_scroll_limit = 100

        for y in range(1, max_scroll_limit + 1):
            if len(merged) >= tgt:
                _safe_print(f"Progress: {len(merged)} / {tgt} done")
                scroll_iters = y - 1
                break
            _safe_print(f"Progress: {len(merged)} / {tgt} scroll {y}")
            try:
                page.run_js("window.scrollBy(0, 800);")
            except Exception:
                pass

            time.sleep(random.uniform(3.0, 5.0))

            sbatch, ps, pw = collect_packets_notes(page, rounds=22)
            packet_scroll_seen += ps
            packet_scroll_with += pw
            for row in sbatch:
                put_merged_row(merged, row)

            try:
                dom_rows_round, _ = parse_notes_from_dom(page)
                for dr in dom_rows_round:
                    put_merged_row(merged, dr)
            except Exception:
                pass

            scroll_iters = y
            if y == max_scroll_limit and len(merged) < tgt:
                _safe_print(
                    f"Progress: {len(merged)} / {tgt} scroll_limit {max_scroll_limit}"
                )

        try:
            page.listen.stop()
        except Exception:
            pass

        html = page.html
        state_rows = parse_initial_state_html(html)
        for row in state_rows:
            put_merged_row(merged, row)

        js_err: Optional[str] = None
        try:
            js = (
                "try{return JSON.stringify(window.__INITIAL_STATE__);}"
                "catch(e){return null;}"
            )
            raw = page.run_js(js)
            if raw and isinstance(raw, str) and len(raw) > 10:
                state = json.loads(raw, strict=False)
                if isinstance(state, dict):
                    for row in _rows_from_initial_state_dict(state):
                        put_merged_row(merged, row)
        except Exception as e:
            js_err = f"{type(e).__name__}: {e}"

        # 保底：直接从 DOM 笔记卡片解析（不依赖 __INITIAL_STATE__）
        try:
            dom_rows, dom_diag = parse_notes_from_dom(page)
        except Exception as ex:
            dom_rows, dom_diag = [], [
                f"• DOM 保底整体异常：{type(ex).__name__}: {ex}"
            ]
        for row in dom_rows:
            put_merged_row(merged, row)

        rows = list(merged.values())
        if not rows:
            logging.getLogger(__name__).warning(
                "未能解析到任何笔记（已不向终端输出 HTML）；"
                "滚动轮数约 %s，详见返回的诊断文本。",
                scroll_iters,
            )
            lines: List[str] = [
                "【未能解析到任何笔记】请对照下方排查信息检查页面状态（如需扫码登录，请确保已在弹窗浏览器中完成登录后再试）。",
                f"• 滚动策略：JS window.scrollBy(0,800)，每轮随机等待 3–5 秒，强制 collect_packets + DOM 回血；"
                f"最多 {max_scroll_limit} 轮或达到 {tgt} 条。本次实际滚动轮数约 {scroll_iters}。",
                "",
                "—— 网络监听 ——",
                "• 监听目标 URL 子串：「sns/web/v1/user_posted」；"
                "响应体先递归 find_notes_in_json，未命中时再深度遍历 notes / noteList / items 等列表。",
                f"• 首屏捕获 {packet_round1_seen} 个数据包，其中 {packet_round1_with} 个解析出笔记；"
                f"滚动阶段累计 {packet_scroll_seen} 个包，{packet_scroll_with} 个解析出笔记。",
            ]
            raw_block = _extract_initial_state_raw(html)
            if not raw_block:
                lines.append("• 页面 HTML 中未提取到 window.__INITIAL_STATE__（常见于未登录、验证页或模板变更）。")
            else:
                lines.append("• 已提取到 __INITIAL_STATE__ 脚本块。")
                fixed = (
                    raw_block.replace(":undefined", ":null")
                    .replace("undefined", "null")
                    .replace(":void 0", ":null")
                )
                try:
                    json.loads(fixed, strict=False)
                    lines.append(
                        "• __INITIAL_STATE__ 可被 JSON 解析，但未从中解析出笔记条目（字段结构可能与当前解析逻辑不一致）。"
                    )
                except json.JSONDecodeError as je:
                    lines.append(f"• __INITIAL_STATE__ JSON 解析失败：{je}")

            lines.append("")
            lines.append("—— 页面线索 ——")
            lines.extend(_diagnose_html_snippet(html))

            if js_err:
                lines.append("")
                lines.append("—— 脚本诊断 ——")
                lines.append(f"• 执行 window.__INITIAL_STATE__ 序列化时异常：{js_err}")

            lines.append("")
            lines.append("—— DOM 保底解析 ——")
            lines.extend(
                dom_diag
                if dom_diag
                else ["• DOM 保底未返回明细（可能未进入解析分支）。"]
            )

            return [], "\n".join(lines)

        rows.sort(key=lambda x: (x.get("点赞") or 0), reverse=True)
        return rows, None
    except Exception as e:
        logging.getLogger(__name__).exception(
            "抓取过程异常（已不向终端输出页面 HTML）"
        )
        tb = traceback.format_exc()
        msg = (
            f"【抓取过程抛出异常】{type(e).__name__}: {e}\n\n"
            f"—— 完整堆栈 ——\n{tb}"
        )
        return [], msg
    finally:
        try:
            page.quit()
        except Exception:
            pass


def dataframe_ready_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """去掉内部字段，供 Streamlit 表格展示。"""
    out = []
    for r in rows:
        out.append(
            {
                "封面": r.get("封面") or "",
                "标题": r.get("标题") or "",
                "点赞": r.get("点赞") if r.get("点赞") is not None else 0,
                "收藏": r.get("收藏") if r.get("收藏") is not None else 0,
                "评论": r.get("评论") if r.get("评论") is not None else 0,
            }
        )
    return out
