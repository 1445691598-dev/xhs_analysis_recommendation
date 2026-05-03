# -*- coding: utf-8 -*-
import json
import csv
import time
import random
import re
import os
from DrissionPage import ChromiumPage, ChromiumOptions

# ================= 配置区域 =================
# 替换为你自己的主页链接 (请确保格式类似下方)
TARGET_PROFILE_URL = "https://www.xiaohongshu.com/user/profile/60cf49e50000000001001c7e"
# 想要抓取的最大笔记数量
TARGET_COUNT = 100
# 导出的文件名
CSV_FILENAME = "my_xhs_data.csv"
# ============================================

def random_sleep(min_s=2.0, max_s=4.0):
    """模拟人类操作的随机停顿"""
    time.sleep(random.uniform(min_s, max_s))

def find_notes_in_json(obj):
    """
    黑科技：无论小红书怎么改变 JSON 结构，直接递归搜索包含笔记特征的字典。
    这比写死路径 (如 data.user.notes) 稳定 100 倍。
    """
    found = []
    if isinstance(obj, dict):
        # 只要同时包含 note_id 和 interact_info，我们就认为它是一个完整的笔记节点
        if ('note_id' in obj or 'id' in obj) and 'interact_info' in obj and 'display_title' in obj:
            found.append(obj)
        else:
            for v in obj.values():
                found.extend(find_notes_in_json(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_notes_in_json(item))
    return found

def parse_and_clean_notes(raw_notes_list):
    """清洗提取到的原始笔记节点，只保留我们需要的数据"""
    clean_data = []
    for note in raw_notes_list:
        note_id = note.get('note_id') or note.get('id', '')
        title = note.get('display_title') or note.get('title', '（无标题）')
        
        interact = note.get('interact_info', {})
        likes = interact.get('liked_count', 0)
        collects = interact.get('collected_count', 0)
        comments = interact.get('comment_count', 0)
        
        # 提取封面 URL
        cover_url = ""
        cover_info = note.get('cover') or note.get('note_cover', {})
        if isinstance(cover_info, dict):
            cover_url = cover_info.get('url_default') or cover_info.get('url', '')
            
        clean_data.append({
            '笔记ID': str(note_id),
            '标题': str(title).strip(),
            '点赞': int(likes) if str(likes).isdigit() else 0,
            '收藏': int(collects) if str(collects).isdigit() else 0,
            '评论': int(comments) if str(comments).isdigit() else 0,
            '封面链接': str(cover_url)
        })
    return clean_data

def export_to_csv(data_list, filename):
    """导出为 CSV 文件"""
    if not data_list:
        print("\n❌ 没有抓取到任何数据，无法生成 CSV。")
        return
        
    headers = list(data_list[0].keys())
    try:
        with open(filename, mode='w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(data_list)
        print(f"\n✅ 成功！已将 {len(data_list)} 条数据保存至: {os.path.abspath(filename)}")
    except Exception as e:
        print(f"\n❌ 保存 CSV 失败: {e}")

def main():
    print(f"🚀 启动全新的小红书抓取程序...")
    
    # 初始化浏览器配置
    co = ChromiumOptions()
    # 注释掉了原代码中写死的浏览器路径，DrissionPage 会自动寻找你电脑里的 Chrome/Edge
    co.headless(False) # 保持显示窗口，方便扫码登录
    
    try:
        page = ChromiumPage(co)
    except Exception as e:
        print(f"❌ 浏览器启动失败，请检查是否安装了 Chrome 浏览器。错误信息: {e}")
        return

    all_notes = {}  # 用字典去重，键为笔记ID

    # 开始监听网络请求（小红书加载下一页的 API）
    page.listen.start('sns/web/v1/user_posted')
    
    print(f"🌐 正在打开主页: {TARGET_PROFILE_URL}")
    page.get(TARGET_PROFILE_URL)
    
    print("⏳ 等待 15 秒，请在弹出的浏览器中确认页面加载。如果需要登录，请立即扫码...")
    time.sleep(15)

    # 1. 抓取首屏数据：小红书第一页数据藏在网页 HTML 的 window.__INITIAL_STATE__ 变量里
    print("📦 正在解析首屏数据...")
    html = page.html
    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(.*?)</script>', html, re.S)
    if match:
        raw_json_str = match.group(1).strip().replace('undefined', 'null')
        try:
            initial_state = json.loads(raw_json_str)
            raw_initial_notes = find_notes_in_json(initial_state)
            clean_initial_notes = parse_and_clean_notes(raw_initial_notes)
            for note in clean_initial_notes:
                all_notes[note['笔记ID']] = note
            print(f"✔️ 首屏提取到 {len(clean_initial_notes)} 条笔记。")
        except json.JSONDecodeError:
            print("⚠️ 首屏 JSON 解析失败，可能是页面结构有大变动。")

    # 2. 模拟人类滚动，抓取后续数据
    print("\n🚶‍♂️ 开始模拟滚动，加载更多数据...")
    max_scrolls = 30 # 最大滚动次数，防止死循环
    empty_scroll_count = 0 
    
    for i in range(max_scrolls):
        if len(all_notes) >= TARGET_COUNT:
            print(f"🎯 已达到目标抓取数量 ({TARGET_COUNT})，停止滚动。")
            break
            
        print(f"向下滚动第 {i+1} 次...")
        page.scroll.down(800)
        
        # 等待拦截数据包
        packet = page.listen.wait(timeout=3.0)
        if packet:
            try:
                api_data = packet.response.body
                raw_api_notes = find_notes_in_json(api_data)
                clean_api_notes = parse_and_clean_notes(raw_api_notes)
                
                new_count = 0
                for note in clean_api_notes:
                    if note['笔记ID'] not in all_notes:
                        all_notes[note['笔记ID']] = note
                        new_count += 1
                
                print(f"✔️ 截获 API 数据包，新增 {new_count} 条笔记。当前总计: {len(all_notes)} 条。")
                empty_scroll_count = 0 # 重置空滑计数
            except Exception as e:
                print(f"⚠️ 解析数据包出错: {e}")
        else:
            empty_scroll_count += 1
            if empty_scroll_count >= 3:
                print("🛑 连续 3 次滚动没有加载出新数据，可能已经到底了。")
                break
                
        random_sleep(2.0, 4.0) # 模拟人看完之后的停顿

    # 退出浏览器
    try:
        page.listen.stop()
        page.quit()
    except:
        pass

    # 3. 排序并导出
    final_list = list(all_notes.values())
    # 按点赞数从高到低排序
    final_list.sort(key=lambda x: x['点赞'], reverse=True)
    
    export_to_csv(final_list, CSV_FILENAME)

if __name__ == "__main__":
    main()