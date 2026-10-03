#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端每日共识清洗（GitHub Actions 运行）

设计原理：
- 用户实际收视网络为「东莞移动」。GitHub 云端（美国）测速视角与之严重不符，
  云端**不做速度测速、不做速度排序**。
- 云端职责双轨：
  ① 种子保护——现有 live.m3u（本机东莞实测精修成果）中的线路原样保留；
  ② 共识补缺——从多个「国内清洗库」（各自已在境内完成测速清洗）抓取候选，
     按「多库共识度」为缺失/不足的频道补充线路。

产物：live.m3u（x-tvg-url 头指向 epg_lite.xml；每频道 ≤2 条）
"""
import re
import os
import sys
import time

import requests

# ═══════════ 配置 ═══════════

# 共识库（国内清洗视角的聚合输出）
SOURCES = [
    "https://live.zbds.top/tv/iptv4.m3u",                                              # vbskycn 每日清洗
    "https://live.fanmingming.cn/tv/m3u/itv.m3u",                                      # fanmingming 央卫
    "https://raw.githubusercontent.com/Guovin/TV/gd/output/result.m3u",                # Guovin 每日聚合
    "https://raw.githubusercontent.com/suxuang/myIPTV/main/ipv4.m3u",                  # myIPTV 全网通
    "https://raw.githubusercontent.com/sammy0101/hk-iptv-auto/main/hk_live.m3u",       # 香港自动聚合
    "https://raw.githubusercontent.com/kimwang1978/collect-txt/main/work/%E6%B8%AF%E6%BE%B3%E5%8F%B0.txt",  # kim 港澳台
    "https://raw.githubusercontent.com/kimwang1978/collect-txt/main/work/%E7%94%B5%E5%BD%B1.txt",          # kim 电影
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/hk.m3u",           # iptv-org 香港
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/tw.m3u",           # iptv-org 台湾
]

# 目标频道（名称, 分组）
CHANNELS = [
    ("CCTV1", "央视频道"), ("CCTV3", "央视频道"), ("CCTV5", "央视频道"),
    ("CCTV6", "央视频道"), ("CCTV9", "央视频道"),
    ("湖南卫视", "卫视频道"), ("浙江卫视", "卫视频道"),
    ("东方卫视", "卫视频道"), ("江苏卫视", "卫视频道"),
    ("翡翠台", "港·澳·台"), ("明珠台", "港·澳·台"), ("无线新闻台", "港·澳·台"),
    ("TVB Plus", "港·澳·台"), ("ViuTV", "港·澳·台"), ("HOY TV", "港·澳·台"),
    ("港台电视31", "港·澳·台"), ("港台电视32", "港·澳·台"),
    ("凤凰中文", "港·澳·台"), ("凤凰资讯", "港·澳·台"), ("凤凰香港", "港·澳·台"),
    ("澳视澳门", "港·澳·台"), ("澳门卫视", "港·澳·台"),
    ("美亚电影台", "港·澳·台"), ("天映频道", "港·澳·台"), ("天映经典", "港·澳·台"),
    ("东森电影", "港·澳·台"), ("东森洋片", "港·澳·台"), ("龙华电影", "港·澳·台"),
]

# 频道名别名（辅助匹配各库的不同叫法）
ALIASES = {
    "明珠台": ["明珠", "TVB明珠"],
    "无线新闻台": ["無綫新聞台", "無綫新聞", "无线新闻", "TVB新闻台", "无线新闻台HD"],
    "翡翠台": ["翡翠", "TVB翡翠台", "翡翠台HD"],
    "凤凰中文": ["凤凰卫视中文台", "鳳凰衛視中文台"],
    "凤凰资讯": ["凤凰卫视资讯台", "鳳凰衛視資訊台"],
    "凤凰香港": ["凤凰卫视香港台", "鳳凰衛視香港台"],
    "澳视澳门": ["澳視澳門", "澳门澳视", "澳视澳门HD"],
    "澳门卫视": ["澳門衛視"],
    "美亚电影台": ["美亚电影", "美亞電影台", "美亞電影HD", "美亚电影台HD"],
    "天映频道": ["天映經典頻道", "天映经典频道", "天映頻道"],
    "天映经典": ["天映經典頻道", "天映经典频道", "天映頻道", "天映经典电影"],
    "东森电影": ["東森電影台", "东森电影台"],
    "东森洋片": ["東森洋片台", "东森洋片台"],
    "龙华电影": ["龍華電影", "龍華電影台", "龙华电影台"],
    "港台电视31": ["港台電視31", "RTHK31", "港台31"],
    "港台电视32": ["港台電視32", "RTHK32", "港台32"],
}

# 候选黑名单（本机实测验出的假源/死站，云端补缺时剔除）
CAND_BLACKLIST = [
    "Meroser.mp4", "epg.pw/stream/", "8.138.7.223", "mag.trexlive.me",
    "4666888.xyz", "cdn.qd.je", "jdshipin", "hklive.tv",
    "ye23.win", "3a.ink", "pi.0472.org", "52sf.ga", "live.264788.xyz",
    "hmysapp.cn",  # "当前频道被外星人偷走了"占位页（2026-10-03 抓帧验出）
]

EPG_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/wfxllb/IPTV/main/epg_lite.xml"
LOGO = "https://gcore.jsdelivr.net/gh/yuanzl77/TVlogo@master/png/{}.png"

MAX_PER_CHANNEL = 2   # 每频道上限（条）


# ═══════════ 频道名归一化（繁体→简体、剥离画质后缀）═══════════

_T2S = {
    "無": "无", "綫": "线", "線": "线", "視": "视", "電": "电", "臺": "台", "衛": "卫",
    "鳳": "凤", "訊": "讯", "財": "财", "經": "经", "開": "开", "東": "东", "龍": "龙",
    "亞": "亚", "華": "华", "劇": "剧", "娛": "娱", "體": "体", "綜": "综", "藝": "艺",
    "聞": "闻", "際": "际", "國": "国", "語": "语", "灣": "湾", "產": "产",
    "廣": "广", "場": "场", "紀": "纪", "實": "实", "觀": "观", "賞": "赏", "頻": "频",
    "節": "节", "樂": "乐", "號": "号", "總": "总", "雙": "双",
    "專": "专", "題": "题", "詞": "词", "錄": "录", "風": "风", "雲": "云",
    "麗": "丽", "寶": "宝", "業": "业", "戰": "战", "動": "动", "畫": "画", "夢": "梦",
    "銀": "银", "錢": "钱", "馬": "马", "車": "车", "澳門": "澳门",
}


def normalize(name: str) -> str:
    s = name.strip()
    s = "".join(_T2S.get(ch, ch) for ch in s)
    s = re.sub(r'[（(【\[][^）)】\]]*[）)】\]]', '', s)
    s = re.sub(r'\s*(?:50\s*FPS|HEVC|H\.?265)$', '', s, flags=re.IGNORECASE)
    while re.search(r'(高清版|超清版|频道|卫视|高清|超清|HD|台)$', s):
        s = re.sub(r'(高清版|超清版|频道|卫视|高清|超清|HD|台)$', '', s)
    s = s.replace('-', '').replace(' ', '')
    return s.lower()


def build_target_index():
    """返回 {归一化名: 目标频道}"""
    idx = {}
    for ch, _grp in CHANNELS:
        idx[normalize(ch)] = ch
        for a in ALIASES.get(ch, []):
            idx[normalize(a)] = ch
    return idx


# ═══════════ 解析 ═══════════

def parse_entries(text: str):
    """解析 m3u / txt，返回 [(频道名, 线路URL)]"""
    rows = []
    lines = text.splitlines()
    is_m3u = any("#EXTINF" in l for l in lines[:50])
    if is_m3u:
        name = None
        for l in lines:
            l = l.strip()
            if l.startswith("#EXTINF"):
                m = re.search(r",(.*)$", l)
                name = m.group(1).strip() if m else ""
            elif l and not l.startswith("#") and name:
                rows.append((name, l.strip()))
                name = None
    else:
        for l in lines:
            l = l.strip()
            if not l or l.startswith("#"):
                continue
            m = re.match(r"^(.*?),(https?://.*)$", l)
            if m:
                rows.append((m.group(1).strip(), m.group(2).strip()))
    return rows


def fetch(url: str, retries: int = 2) -> str:
    for i in range(retries + 1):
        try:
            r = requests.get(url, timeout=35, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as e:
            if i < retries:
                time.sleep(3)
                continue
            print(f"  [跳过] {url[:80]}: {type(e).__name__}")
    return ""


def light_check(urls, timeout=12):
    """并发轻验证：2xx/3xx=ok；4xx/5xx=dead；超时/连接错误=unknown（跨境不代表本地不可达）"""
    from concurrent.futures import ThreadPoolExecutor

    def probe(u):
        try:
            r = requests.get(u, timeout=timeout, stream=True, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            for _ in r.iter_content(1024):
                break
            r.close()
            if 200 <= r.status_code < 400:
                return u, "ok"
            return u, "dead"
        except requests.exceptions.Timeout:
            return u, "unknown"
        except Exception:
            return u, "unknown"

    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for u, st in ex.map(probe, urls):
            out[u] = st
    return out


# ═══════════ 主流程 ═══════════

def read_seed(path: str):
    """解析现有 live.m3u -> {频道: [urls]}（按出现顺序）"""
    seeds = {}
    if not os.path.exists(path):
        print("  [提示] 无现有 live.m3u，全部从共识源构建")
        return seeds
    text = open(path, encoding="utf-8").read()
    name = None
    for l in text.splitlines():
        l = l.strip()
        if l.startswith("#EXTINF"):
            m = re.search(r'tvg-name="([^"]+)"', l)
            if not m:
                m = re.search(r",(.*)$", l)
            name = m.group(1).strip() if m else None
        elif l.startswith("http") and name:
            url = l.split("$")[0].strip()
            seeds.setdefault(name, [])
            if url not in seeds[name]:
                seeds[name].append(url)
            name = None
    return seeds


def main():
    print("=" * 52)
    print("云端共识清洗开始")

    target_idx = build_target_index()

    # ① 读种子（本机精修成果）
    seeds = read_seed("live.m3u")
    print(f"种子加载: {len(seeds)} 频道 / {sum(len(v) for v in seeds.values())} 条线路")

    # ② 抓共识库
    consensus = {}   # (频道, url) -> 计数（多少个库推了这条）
    lib_ok = 0
    for src in SOURCES:
        text = fetch(src)
        if not text:
            continue
        lib_ok += 1
        seen_this_lib = set()
        cnt = 0
        for name, url in parse_entries(text):
            url = url.split("$")[0].strip()
            if not url.startswith("http"):
                continue
            if any(b in url for b in CAND_BLACKLIST):
                continue
            ch = target_idx.get(normalize(name))
            if not ch:
                continue
            key = (ch, url)
            if key in seen_this_lib:
                continue
            seen_this_lib.add(key)
            consensus[key] = consensus.get(key, 0) + 1
            cnt += 1
        print(f"  ✓ {src.split('/')[2][:36]:<38} {cnt} 条候选")
    print(f"库可用: {lib_ok}/{len(SOURCES)}")

    if lib_ok == 0:
        print("!! 所有共识库不可达，中止（保持现有 live.m3u 不动）")
        sys.exit(1)

    # ③ 合并：种子保护 + 验证池补缺 + 共识兜底（含轻验证）
    # 读本机验证池（东莞移动实测通过的候选）
    vpool = {}
    vp_path = "verified_candidates.json"
    if os.path.exists(vp_path):
        try:
            vp = json.load(open(vp_path, encoding="utf-8"))
            vpool = vp.get("channels", {})
            nvp = sum(len(v) for v in vpool.values())
            print(f"验证池加载: {nvp} 条（本机实测通过）")
        except Exception as e:
            print(f"验证池读取失败（忽略）: {e}")

    final = {}
    need_check = {}
    stats = {"seed": 0, "vpool": 0, "cons_ok": 0, "cons_unknown": 0, "empty": []}
    for ch, _grp in CHANNELS:
        seed_urls = [u for u in seeds.get(ch, []) if u]
        urls = list(seed_urls[:MAX_PER_CHANNEL])
        stats["seed"] += len(urls)
        # P2：验证池（本机实测通过，含速度）
        if len(urls) < MAX_PER_CHANNEL:
            for item in vpool.get(ch, []):
                if len(urls) >= MAX_PER_CHANNEL:
                    break
                u = item["url"] if isinstance(item, dict) else item
                if u in urls:
                    continue
                urls.append(u)
                stats["vpool"] += 1
        # P3：共识候选（稍后轻验证）
        if len(urls) < MAX_PER_CHANNEL:
            cands = sorted([(u, cnt) for (c, u), cnt in consensus.items() if c == ch],
                           key=lambda x: -x[1])
            need_check[ch] = [u for u, _ in cands if u not in urls][:6]
        else:
            need_check[ch] = []
        final[ch] = urls

    # 轻验证（仅对仍不足的频道、top 候选）
    to_check = []
    for ch, urls in need_check.items():
        to_check.extend(urls)
    check_result = {}
    if to_check:
        print(f"轻验证 {len(to_check)} 条候选（GET 探测）...")
        check_result = light_check(to_check)
        n_ok = sum(1 for v in check_result.values() if v == "ok")
        n_dead = sum(1 for v in check_result.values() if v == "dead")
        print(f"  通过 {n_ok} / 明确死 {n_dead} / 未知 {len(check_result)-n_ok-n_dead}")

    for ch, cands in need_check.items():
        cur = final[ch]
        # 第一轮：验证通过者
        for u in cands:
            if len(cur) >= MAX_PER_CHANNEL:
                break
            if check_result.get(u) == "ok" and u not in cur:
                cur.append(u)
                stats["cons_ok"] += 1
        # 第二轮：未知者兜底（跨境超时不代表本地不可达）
        for u in cands:
            if len(cur) >= MAX_PER_CHANNEL:
                break
            if check_result.get(u, "unknown") != "dead" and u not in cur:
                cur.append(u)
                stats["cons_unknown"] += 1
        if not cur:
            stats["empty"].append(ch)

    # ④ 写出
    lines_out = [f'#EXTM3U x-tvg-url="{EPG_URL}"']
    for ch, grp in CHANNELS:
        for u in final.get(ch, []):
            lines_out.append(
                f'#EXTINF:-1 tvg-id="{ch}" tvg-name="{ch}" '
                f'tvg-logo="{LOGO.format(ch)}" group-title="{grp}",{ch}')
            lines_out.append(u)
    content = "\n".join(lines_out) + "\n"

    old = open("live.m3u", encoding="utf-8").read() if os.path.exists("live.m3u") else ""
    if content == old:
        print("内容无变化，不写文件")
        return
    with open("live.m3u", "w", encoding="utf-8") as f:
        f.write(content)

    n_ch = sum(1 for ch, _ in CHANNELS if final.get(ch))
    n_line = sum(len(v) for v in final.values())
    print(f"\n完成: {n_ch} 频道 / {n_line} 线路")
    print(f"  种子保留 {stats['seed']} 条 / 验证池补入 {stats['vpool']} 条 / "
          f"共识补入 {stats['cons_ok']} 条（轻验证通过）+ {stats['cons_unknown']} 条（跨境未知兜底）")
    if stats["empty"]:
        print(f"  仍无源频道 ({len(stats['empty'])}): {stats['empty']}")
    print("=" * 52)


if __name__ == "__main__":
    main()
