# -*- coding: utf-8 -*-
import json
import csv
import time
import random
import re
import os
import datetime
from DrissionPage import ChromiumPage, ChromiumOptions

# ================= 配置区域 =================
# 填入你想要分析的笔记链接（支持一个或多个）
TARGET_NOTE_URLS = [
    "https://www.xiaohongshu.com/explore/69da471d000000001e00cd67?xsec_token=ABsAec4krRPs2KcqyJkpoWQmOlqXmuRMieBX3TFZG5r2w=&xsec_source=pc_user",
    "https://www.xiaohongshu.com/explore/69db6c2b000000001f007ab6?xsec_token=ABc0oSriyIbYTRsQPHYDJuuhXzl2egDF_B5P_a5qhkRzs=&xsec_source=pc_user"
]
CSV_FILENAME = "single_note_analysis.csv"
# ============================================

def random_sleep(min_s=2.0, max_s=4.0):
    time.sleep(random.uniform(min_s, max_s))

def find_detail_in_json(obj):
    """雷达函数：全量扫描详情数据块"""
    if isinstance(obj, dict):
        if 'desc' in obj and 'title' in obj and 'interactInfo' in obj:
            return obj
        for v in obj.values():
            res = find_detail_in_json(v)
            if res: return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_detail_in_json(item)
            if res: return res
    return None

def check_security_wall(page):
    """处理验证码和登录墙"""
    if "当前笔记暂时无法浏览" in page.html or "扫码查看" in page.html:
        print("\n🚨 请在浏览器中完成【扫码登录】，程序会自动等待...")
        while "当前笔记暂时无法浏览" in page.html or "扫码查看" in page.html:
            time.sleep(2)
        print("✔️ 登录成功！")
    
    if "验证" in page.html and ("滑动" in page.html or "安全中心" in page.html):
        print("\n🚨 请手动完成【滑块验证】...")
        while "验证" in page.html and ("滑动" in page.html or "安全中心" in page.html):
            time.sleep(2)
        print("✔️ 验证通过！")

def scrape_single_note(page, url):
    """抓取单篇笔记的完整特征"""
    note_id = url.split('/')[-1].split('?')[0]
    page.get(url)
    time.sleep(3) # 给足加载时间
    check_security_wall(page)
    
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(.*?)</script>', page.html, re.S)
    if not match:
        return None
        
    try:
        raw_json = match.group(1).strip().replace('undefined', 'null')
        if raw_json.endswith(';'): raw_json = raw_json[:-1]
        state = json.loads(raw_json)
        
        # 优先路径寻找，失败则启动雷达
        note_detail = None
        try:
            note_detail = state['note']['noteDetailMap'][note_id]['note']
        except:
            note_detail = find_detail_in_json(state)
        
        if not note_detail: return None

        # 特征提取
        title = note_detail.get('title', '（无标题）')
        desc = note_detail.get('desc', '')
        clean_desc = str(desc).replace('\n', ' | ').replace('\r', '').strip()
        
        ts = note_detail.get('time') or note_detail.get('lastUpdateTime')
        pub_time = datetime.datetime.fromtimestamp(int(ts)/1000).strftime('%Y-%m-%d %H:%M:%S') if ts else "未知"
        
        interact = note_detail.get('interactInfo', {})
        tags = [t['name'] for t in note_detail.get('tagList', []) if isinstance(t, dict)]

        return {
            '笔记ID': note_id,
            '标题': title.strip(),
            '正文内容': clean_desc,
            '发布时间': pub_time,
            '点赞': interact.get('likedCount', 0),
            '收藏': interact.get('collectCount', 0) or interact.get('collectedCount', 0),
            '评论': interact.get('commentCount', 0),
            '分享': interact.get('shareCount', 0),
            'tag标签': " ".join(tags),
            '笔记链接': url
        }
    except Exception as e:
        print(f"解析失败 {url}: {e}")
        return None

def main():
    co = ChromiumOptions()
    co.headless(False)
    co.set_user_data_path(r'./xhs_browser_data') # 记住登录状态
    page = ChromiumPage(co)

    final_data = []
    print(f"🚀 开始抓取指定的 {len(TARGET_NOTE_URLS)} 篇稿件...")

    for url in TARGET_NOTE_URLS:
        print(f"🔎 正在分析: {url} ...", end="", flush=True)
        res = scrape_single_note(page, url)
        if res:
            final_data.append(res)
            print(" ✅ 成功")
        else:
            print(" ❌ 失败")
        random_sleep()

    if final_data:
        headers = final_data[0].keys()
        with open(CSV_FILENAME, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(final_data)
        print(f"\n📊 分析完成！数据已保存至: {CSV_FILENAME}")
    
    page.quit()

if __name__ == "__main__":
    main()