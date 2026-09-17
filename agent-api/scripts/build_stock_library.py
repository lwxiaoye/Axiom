# -*- coding: utf-8 -*-
"""内置精选图库建库/扩库脚本（docker exec agent-api python -u scripts/build_stock_library.py）。

按「做 PPT 常用主题」分类从 Pexels 拉图（API key 读 settings.WEB_SEARCH_PEXELS_KEY），
落 data/stock_images/ + index.json。幂等：已存在的文件跳过下载，索引整体重写。
授权：Pexels License 免费商用；本库仅作内部平台的小规模精选缓存，非镜像分发。
"""
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402

LIB = Path("/app/data/stock_images")
PER_QUERY = 8

# (英文查询, 中文标题, [检索标签…含中英文])
TAXONOMY = [
    ("technology abstract background", "科技抽象", ["科技", "抽象", "数字化", "technology", "tech"]),
    ("artificial intelligence robot", "人工智能", ["人工智能", "AI", "机器人", "智能", "robot", "ai"]),
    ("data center server room", "数据中心", ["数据中心", "服务器", "机房", "算力", "data", "server"]),
    ("circuit board macro", "芯片电路", ["芯片", "电路", "半导体", "集成电路", "chip", "circuit"]),
    ("programmer coding screen", "编程开发", ["编程", "程序员", "代码", "开发", "软件", "coding"]),
    ("smart city night skyline", "智慧城市", ["智慧城市", "夜景", "都市", "5g", "city"]),
    ("business meeting office", "商务会议", ["会议", "商务", "办公", "汇报", "洽谈", "meeting", "business"]),
    ("teamwork collaboration hands", "团队协作", ["协作", "团队", "合作", "共赢", "teamwork"]),
    ("handshake partnership", "合作签约", ["握手", "签约", "伙伴", "达成", "handshake"]),
    ("city skyline skyscraper", "城市天际线", ["天际线", "摩天楼", "cbd", "地标", "skyline"]),
    ("startup planning whiteboard", "创业规划", ["创业", "规划", "白板", "路演", "startup"]),
    ("finance stock market chart", "金融走势", ["金融", "股票", "走势", "投资", "理财", "行情", "finance", "stock"]),
    ("coins savings growth", "储蓄增长", ["货币", "储蓄", "成本", "增长", "收益", "money"]),
    ("solar panels field", "光伏电站", ["太阳能", "光伏", "新能源", "节能", "solar"]),
    ("wind turbines landscape", "风力发电", ["风电", "风力", "风机", "wind"]),
    ("green forest sunlight", "绿色森林", ["森林", "绿色", "自然", "环保", "生态", "forest", "green"]),
    ("ocean waves aerial", "海洋", ["海洋", "大海", "波浪", "海岸", "ocean", "sea"]),
    ("mountain landscape", "山川", ["山", "山脉", "远山", "户外", "mountain"]),
    ("earth from space", "地球太空", ["地球", "太空", "全球", "宇宙", "earth", "space"]),
    ("recycling waste sorting", "回收分类", ["回收", "垃圾分类", "减排", "循环", "低碳", "recycling"]),
    ("electric car charging", "电动汽车", ["电动车", "充电", "新能源汽车", "ev"]),
    ("classroom students learning", "课堂教学", ["课堂", "学生", "教育", "教学", "上课", "classroom", "education"]),
    ("library books reading", "图书阅读", ["图书馆", "书", "阅读", "书籍", "书香", "library", "book"]),
    ("science laboratory research", "科研实验", ["实验室", "科研", "实验", "研究", "laboratory", "lab"]),
    ("microscope biology", "显微观察", ["显微镜", "生物", "检测", "microscope"]),
    ("doctor hospital care", "医疗诊疗", ["医生", "医院", "医疗", "诊疗", "健康", "doctor", "hospital", "medical"]),
    ("medicine pills pharmacy", "药品", ["药", "药品", "用药", "制药", "medicine"]),
    ("elderly care nurse", "养老关怀", ["护理", "养老", "关怀", "老人", "care"]),
    ("coffee shop cozy", "咖啡时光", ["咖啡", "咖啡馆", "休闲", "下午茶", "coffee"]),
    ("healthy food vegetables", "健康饮食", ["美食", "健康饮食", "蔬菜", "餐饮", "营养", "food"]),
    ("chef cooking kitchen", "烹饪厨房", ["烹饪", "厨房", "厨师", "料理", "cooking"]),
    ("happy family home", "家庭生活", ["家庭", "亲子", "居家", "陪伴", "family"]),
    ("running sport fitness", "运动健身", ["跑步", "运动", "健身", "体育", "锻炼", "running", "sport", "fitness"]),
    ("yoga meditation calm", "瑜伽冥想", ["瑜伽", "冥想", "康养", "放松", "yoga"]),
    ("travel airport airplane", "航空出行", ["旅行", "飞机", "出行", "机场", "航空", "travel", "airplane"]),
    ("high speed train railway", "轨道交通", ["火车", "高铁", "铁路", "轨道", "train"]),
    ("drinking water glass", "饮水健康", ["喝水", "饮水", "水杯", "补水", "water"]),
    ("factory manufacturing industry", "工业制造", ["工厂", "制造", "生产", "工业", "车间", "factory", "manufacturing"]),
    ("construction site crane", "建筑施工", ["工地", "建筑", "施工", "基建", "工程", "construction"]),
    ("engineer blueprint design", "工程设计", ["工程师", "图纸", "设计", "测绘", "engineer", "blueprint"]),
    ("robotic arm automation", "智能制造", ["机械臂", "自动化", "智能制造", "产线", "automation"]),
    ("agriculture farm field", "现代农业", ["农业", "农田", "粮食", "乡村", "丰收", "农产", "agriculture", "farm"]),
    ("logistics warehouse delivery", "物流仓储", ["物流", "仓储", "供应链", "快递", "配送", "logistics"]),
    ("museum art gallery", "艺术展览", ["博物馆", "艺术", "展览", "美术馆", "museum", "art"]),
    ("concert stage lights", "舞台演出", ["音乐会", "舞台", "演出", "晚会", "灯光", "concert", "stage"]),
    ("chinese traditional architecture", "中式古建", ["古建筑", "中式", "国风", "传统", "古镇", "chinese"]),
    ("red lantern festival", "节庆灯笼", ["灯笼", "节日", "春节", "庆典", "喜庆", "lantern", "festival"]),
    ("calligraphy ink brush", "书法水墨", ["书法", "水墨", "文化", "笔墨", "calligraphy"]),
    ("gradient abstract minimal", "简约渐变", ["渐变", "抽象背景", "简约", "gradient", "minimal"]),
    ("white paper texture", "纸张质感", ["纸", "纹理", "纸张", "质感", "留白", "paper", "texture"]),
    ("geometric architecture pattern", "几何结构", ["几何", "线条", "结构", "极简", "geometric"]),
]


async def main() -> int:
    key = str(getattr(settings, "WEB_SEARCH_PEXELS_KEY", "") or "").strip()
    if not key:
        print("缺 WEB_SEARCH_PEXELS_KEY")
        return 1
    LIB.mkdir(parents=True, exist_ok=True)
    index, seen_files = [], set()
    ok = fail = skip = 0
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=30, headers={"Authorization": key}) as client:
        for qi, (query, title_zh, tags) in enumerate(TAXONOMY, 1):
            try:
                resp = await client.get(
                    "https://api.pexels.com/v1/search",
                    params={"query": query, "per_page": PER_QUERY, "orientation": "landscape"},
                )
                photos = resp.json().get("photos") or []
            except Exception as e:  # noqa: BLE001
                print(f"[{qi:02d}] {query}: 搜索失败 {e}")
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:40]

            async def grab(p, slug=slug, title_zh=title_zh, tags=tags):
                nonlocal ok, fail, skip
                pid = p.get("id")
                src = (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")
                if not pid or not src:
                    return
                fname = f"{slug}-{pid}.jpg"
                entry = {
                    "file": fname, "title": title_zh,
                    "alt": str(p.get("alt") or "")[:200], "tags": tags,
                    "credit": f"Pexels / {p.get('photographer') or ''}".strip(" /"),
                }
                fpath = LIB / fname
                if fpath.exists() and fpath.stat().st_size > 10000:
                    skip += 1
                    index.append(entry); seen_files.add(fname)
                    return
                async with sem:
                    try:
                        r = await client.get(src)
                        if r.status_code == 200 and len(r.content) > 10000:
                            fpath.write_bytes(r.content)
                            ok += 1
                            index.append(entry); seen_files.add(fname)
                        else:
                            fail += 1
                    except Exception:  # noqa: BLE001
                        fail += 1

            await asyncio.gather(*(grab(p) for p in photos))
            print(f"[{qi:02d}/{len(TAXONOMY)}] {title_zh}: 累计 下载{ok} 复用{skip} 失败{fail}")
    (LIB / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    total_mb = sum(f.stat().st_size for f in LIB.glob("*.jpg")) / 1024 / 1024
    print(f"完成：索引 {len(index)} 条，磁盘 {total_mb:.0f}MB，目录 {LIB}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
