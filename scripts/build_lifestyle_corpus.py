"""Build corpora/lifestyle/items.json — the lifestyle activity catalog (unit LA).

Sources combined per the frozen task spec:
  1. hf:alperugurcan/Hobbies (MIT, 117 rows, synthetic) → hobby/sport/outdoor/
     volunteer/learning/wellness backbone. Rows are deduped by hobby name and
     joined 1:1 with the translation table below (script fails on drift).
  2. hf:Martjn/calisthenics_exercises (Apache-2.0, 170 exercises) → 7 aggregated
     sport activities, one per exercise category (push/pull/legs/core/skill/
     explosive/mobility).
  3. hf:berdaniera/meditation (CC-BY-4.0, 255 scripts, 17 themes) → 8 wellness
     meditation activities keyed to real themes.
  4. hf:verdantdavid/gsv3_upcoming_events → 10 outdoor culture activity types
     (concert / theatre / comedy / festival / exhibition / cinema / market /
     workshop / pub quiz / karaoke), grounded by keyword presence in the csv.
  5. Self-authored vacuum categories: household / pet / social / learning /
     volunteer / wellness morning-care / common sports / fills.

MET provenance policy (fail-closed, no fabricated code numbers):
  - Exact 2011 Compendium codes are cited ONLY where widely reproduced:
    12030 running 5 mph=8.3, 01010 bicycling leisure <10mph=4.0,
    03020 calisthenics light-moderate=3.8, 03030 calisthenics vigorous=8.0.
  - Every other entry cites "Compendium 2011 nearest: <descriptor>, MET x.x
    (assumption)" — value calibrated to the compendium category family.
Durations are typical values (assumption; Eurostat time-use reference).

HF raw files are NOT frozen into the repo (spec: frozen artifact ≤5MB excluding
HF downloads). This generator validates grounding against raw files in --hf-dir
(default /tmp/la_hf) and fails closed when they are missing or drifted.

v2 schema: every item additionally carries location_kinds (closed set),
time_window, needs_partner and effects (entertainment/social/health weights),
merged fail-closed from corpora/lifestyle/enrichment.json.

Usage:
  python3 scripts/build_lifestyle_corpus.py            # write items.json
  python3 scripts/build_lifestyle_corpus.py --check    # regenerate, compare bytes
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "corpora/lifestyle/items.json"
ENRICHMENT = ROOT / "corpora/lifestyle/enrichment.json"
HF_DIR = Path("/tmp/la_hf")

#: location_kinds 封闭集（与 enrichment.json 的声明保持一致，双端断言防漂移）。
LOCATION_KINDS = ("home", "office", "outdoor", "fitness", "venue", "anywhere")

CATEGORIES = ("sport", "hobby", "learning", "household", "social",
              "wellness", "pet", "volunteer", "outdoor")

# Hobbies rows dropped as concept duplicates of a kept row / other group.
HOBBY_DROPS = {
    "Pottery Making",       # dup of Pottery
    "Carpentry",            # dup of Woodworking
    "Whittling",            # dup of Wood Carving
    "Watercolor Painting",  # dup of Painting
    "Mindfulness Meditation",  # covered by berdaniera/meditation group
    "Soapmaking",              # alternate spelling of Soap Making
}


def m_code(code: str, desc: str) -> str:
    return f"Compendium 2011 code {code} ({desc})"


def m_near(desc: str, value: float) -> str:
    return f"Compendium 2011 nearest: {desc}, MET {value} (assumption)"


C_RUN = m_code("12030", "running 5 mph, 8.3")
C_BIKE = m_code("01010", "bicycling leisure <10 mph, 4.0")
C_CAL_M = m_code("03020", "calisthenics light-to-moderate effort, 3.8")
C_CAL_V = m_code("03030", "calisthenics vigorous effort, 8.0")

DUR = "duration: typical value (assumption; Eurostat time-use reference)"


def src_hobby(row: str, met: str) -> str:
    return (f"hf:alperugurcan/Hobbies (MIT) row '{row}'; met: {met}; {DUR}")


# (csv_row_name, slug, name_zh, category, minutes, met, met_cite, description_zh)
HOBBIES: list[tuple[str, str, str, str, int, float, str, str]] = [
    ("Reading", "reading", "阅读", "hobby", 30, 1.3, m_near("sitting, reading", 1.3), "安静地读完一本书，小说或非虚构皆可，沉浸式消遣。"),
    ("Jogging", "jogging", "慢跑", "sport", 30, 7.0, m_near("running/jogging family, easy pace", 7.0), "以轻松能说话的配速在户外或跑步机上慢跑，锻炼心肺。"),
    ("Photography", "photography", "摄影", "hobby", 90, 2.3, m_near("walking, light load carrying", 2.3), "带着相机出门扫街或采风，寻找光线与构图并按下快门。"),
    ("Yoga", "yoga", "瑜伽", "sport", 45, 2.5, m_near("Hatha yoga", 2.5), "在垫上完成一套体式与呼吸练习，拉伸放松身心。"),
    ("Knitting", "knitting", "编织", "hobby", 60, 1.3, m_near("sitting, handcraft", 1.3), "用毛线和棒针慢慢织围巾、帽子或毛衣等织物。"),
    ("Woodworking", "woodworking", "木工", "hobby", 90, 3.0, m_near("woodworking, general", 3.0), "在工作室里锯、刨、打磨木料，制作小件家具或器物。"),
    ("Mountain Biking", "mountain-biking", "山地骑行", "sport", 90, 8.0, m_near("bicycling, vigorous", 8.0), "骑山地车在林道或土路上爬坡冲刺，享受颠簸与速度。"),
    ("Gardening", "gardening", "园艺种植", "outdoor", 60, 3.8, C_CAL_M, "在阳台或小院松土、播种、移栽和修剪，打理一片绿意。"),
    ("Board Games", "board-games", "桌游", "social", 90, 1.5, m_near("sitting, playing games", 1.5), "和朋友们围桌开一局策略或聚会桌游，边玩边聊。"),
    ("Cooking", "cooking-research", "烹饪研究", "hobby", 75, 2.5, m_near("cooking, moderate effort", 2.5), "照新菜谱研究食材搭配与火候，尝试做一道没做过的菜。"),
    ("Calligraphy", "calligraphy", "书法", "hobby", 45, 1.8, m_near("standing, light effort art activity", 1.8), "研墨铺纸，一笔一画临帖练字，静心养性。"),
    ("Hiking", "hiking", "徒步登山", "outdoor", 180, 6.0, m_near("backpacking/hiking on trails", 6.0), "背上轻装走进山野，沿步道徒步登高，边走边看风景。"),
    ("Sculpting", "sculpting", "雕塑", "hobby", 90, 2.0, m_near("standing, moderate art activity", 2.0), "用泥塑或刻刀塑造立体形象，慢慢打磨一件作品。"),
    ("Swimming", "swimming", "游泳", "sport", 45, 6.0, m_near("swimming, leisurely general", 6.0), "在泳池里游几个来回，换气划水，全身放松地锻炼。"),
    ("Origami", "origami", "折纸", "hobby", 30, 1.3, m_near("sitting, handcraft", 1.3), "跟着教程把彩纸折成纸鹤、玫瑰或小盒子等造型。"),
    ("Singing", "singing-practice", "唱歌练声", "hobby", 30, 2.0, m_near("singing, standing/sitting", 2.0), "开着伴奏练唱喜欢的歌，顺带做几组发声练习。"),
    ("Kayaking", "kayaking", "皮划艇", "sport", 90, 5.0, m_near("kayaking, moderate", 5.0), "划着皮划艇沿水面前行，用腰腹和手臂力量控制方向。"),
    ("Scrapbooking", "scrapbooking", "手账剪贴", "hobby", 60, 1.5, m_near("sitting, handcraft", 1.5), "把票根、照片和贴纸整理进手账本，记录生活的碎片。"),
    ("Archery", "archery", "射箭", "sport", 60, 3.5, m_near("archery, non-competitive", 3.5), "站在靶位前搭箭开弓，瞄准黄心，体会稳与准的乐趣。"),
    ("Chess", "chess", "国际象棋", "hobby", 60, 1.5, m_near("sitting, playing chess", 1.5), "与对手在线或面对面下一盘慢棋，琢磨开局与残局。"),
    ("Fishing", "fishing", "钓鱼", "outdoor", 180, 2.5, m_near("fishing, sitting", 2.5), "在水边守着竿等鱼上钩，享受安静等待中的期待感。"),
    ("Model Building", "model-building", "模型制作", "hobby", 90, 1.5, m_near("sitting, light handcraft", 1.5), "拼装上色一款模型，从零件到成品慢慢打磨细节。"),
    ("Weightlifting", "gym-weightlifting", "器械力量训练", "sport", 60, 6.0, m_near("resistance training, vigorous", 6.0), "在健身房用杠铃哑铃按计划练深蹲、卧推等力量动作。"),
    ("Birdwatching", "birdwatching", "观鸟", "outdoor", 120, 2.3, m_near("walking, slow pace with stops", 2.3), "带上望远镜去公园或湿地找鸟，辨认羽色与鸣叫。"),
    ("Pottery", "pottery", "陶艺", "hobby", 90, 2.0, m_near("standing, moderate handcraft", 2.0), "在转盘上拉坯塑形，亲手做一只碗或一只杯子。"),
    ("Rock Climbing", "rock-climbing", "攀岩", "sport", 90, 8.0, m_near("rock climbing, moderate to vigorous", 8.0), "在岩馆或自然岩壁上攀爬线路，靠指尖与核心向上。"),
    ("Bowling", "bowling", "保龄球", "sport", 60, 3.8, C_CAL_M, "和朋友打几局保龄球，助跑挥臂把球轰向球瓶。"),
    ("Running", "running-training", "跑步训练", "sport", 40, 8.3, C_RUN, "按课表完成一次跑步训练，可能是节奏跑或长距离。"),
    ("Scrabble", "scrabble", "拼词游戏", "hobby", 45, 1.5, m_near("sitting, playing word games", 1.5), "和朋友拼一局字母拼词，比谁的词汇和布局更精。"),
    ("Painting", "painting", "绘画", "hobby", 90, 1.8, m_near("standing/sitting, painting", 1.8), "支起画架用丙烯或油彩作画，把眼前的样子画下来。"),
    ("Martial Arts", "martial-arts", "武术搏击训练", "sport", 60, 6.0, m_near("martial arts, moderate", 6.0), "去道馆练习拳法、腿法或套路，流汗又练反应。"),
    ("Dancing", "dancing", "舞蹈", "sport", 60, 5.0, m_near("dancing, general", 5.0), "跟着音乐跳一支舞，练身体协调也释放情绪。"),
    ("Surfing", "surfing", "冲浪", "sport", 90, 6.0, m_near("surfing, moderate", 6.0), "划水出海等一道浪，起身站板顺着浪壁滑行。"),
    ("Journaling", "journaling", "写日记", "wellness", 20, 1.3, m_near("sitting, writing", 1.3), "睡前写下今天的收获与情绪，梳理一整天的心情。"),
    ("Astrophotography", "astrophotography", "天文摄影", "hobby", 120, 1.8, m_near("standing, light activity with equipment", 1.8), "在晴夜架起相机对准星空，长曝光拍月亮、星云或银河。"),
    ("Geocaching", "geocaching", "地理寻宝", "outdoor", 120, 4.0, m_near("walking, moderate pace outdoors", 4.0), "按 GPS 坐标在城市或郊野寻找别人藏下的宝藏点。"),
    ("Drone Flying", "drone-flying", "无人机飞行", "hobby", 60, 1.8, m_near("standing, light effort", 1.8), "找块空地起飞无人机，练习航线并航拍一段风景。"),
    ("Amateur Astronomy", "amateur-astronomy", "天文观测", "outdoor", 120, 1.5, m_near("standing/sitting, quiet observation", 1.5), "支好望远镜认星座、看行星，记录今晚的星空。"),
    ("Leatherworking", "leatherworking", "皮具制作", "hobby", 90, 2.0, m_near("standing/sitting, moderate handcraft", 2.0), "裁皮、打斩、手缝，做一只卡包或一条皮带。"),
    ("3D Printing", "3d-printing", "3D打印制作", "hobby", 90, 1.8, m_near("standing, light technical work", 1.8), "建模、切片、打印并做后处理，做出一个实体小物件。"),
    ("Stand-up Comedy", "standup-comedy-performing", "脱口秀开放麦表演", "hobby", 30, 2.0, m_near("standing, performing", 2.0), "把自己写的段子带上开放麦舞台，试着逗笑全场。"),
    ("Metal Detecting", "metal-detecting", "金属探测", "outdoor", 120, 3.0, m_near("walking outdoors with equipment", 3.0), "提着探测器在公园或滩涂扫来扫去，挖出地下的旧物。"),
    ("Acroyoga", "acroyoga", "双人杂技瑜伽", "sport", 60, 4.0, m_near("gymnastics/acroyoga, moderate", 4.0), "和搭档配合完成托举与平衡动作，练信任与核心。"),
    ("Baking", "baking", "烘焙", "hobby", 90, 2.3, m_near("cooking/baking, moderate", 2.3), "称料、揉面、送进烤箱，等一炉面包或蛋糕出炉。"),
    ("Soap Making", "soap-making", "手工皂制作", "hobby", 60, 2.0, m_near("standing, moderate handcraft", 2.0), "融碱、调香、入模，亲手做一批手工皂。"),
    ("Amateur Radio", "amateur-radio", "业余无线电", "hobby", 60, 1.5, m_near("sitting, operating equipment", 1.5), "守在电台前调频通联，和远方的火腿友互报信号。"),
    ("Crossword Puzzles", "crossword-puzzles", "填字游戏", "hobby", 30, 1.3, m_near("sitting, puzzle solving", 1.3), "用一支笔和一张格子图较劲，把横竖线索全部填上。"),
    ("Fitness Gaming", "fitness-gaming", "体感游戏健身", "sport", 45, 3.8, C_CAL_M, "跟着体感游戏跳舞或挥拍，把锻炼变成闯关。"),
    ("Quilting", "quilting", "拼布缝纫", "hobby", 90, 1.5, m_near("sitting, sewing", 1.5), "把碎布拼成图案，一针一线缝成一床拼布。"),
    ("Volunteering", "community-volunteering", "社区志愿服务", "volunteer", 120, 3.0, m_near("volunteer work, moderate", 3.0), "参加社区组织的志愿服务，帮邻里做点实事。"),
    ("Beekeeping", "beekeeping", "养蜂", "outdoor", 60, 3.0, m_near("beekeeping, moderate outdoor work", 3.0), "开箱检查蜂群、取蜜换脾，照料一群勤劳的蜜蜂。"),
    ("Coin Collecting", "coin-collecting", "钱币收藏", "hobby", 60, 1.3, m_near("sitting, collecting/sorting", 1.3), "整理和鉴赏各国钱币，查资料补全藏品的版别故事。"),
    ("Genealogy", "genealogy", "家谱研究", "learning", 90, 1.3, m_near("sitting, research/reading", 1.3), "翻旧照片和老档案，梳理家族世系，还原长辈的故事。"),
    ("Model Railroading", "model-railroading", "铁道模型", "hobby", 120, 1.5, m_near("standing/sitting, layout work", 1.5), "铺设沙盘轨道、布置场景，让模型列车在房间里跑起来。"),
    ("Slacklining", "slacklining", "走扁带", "sport", 45, 4.0, m_near("balance training, moderate", 4.0), "在两棵树间绷一条扁带，赤脚练习行走与平衡。"),
    ("Skateboarding", "skateboarding", "滑板", "sport", 60, 5.0, m_near("skateboarding, moderate", 5.0), "在广场或滑板场练荡板和招式，摔了再爬起来。"),
    ("Kite Flying", "kite-flying", "放风筝", "outdoor", 60, 2.5, m_near("walking/standing outdoors, light-moderate", 2.5), "趁起风去空地放风筝，收放线让风筝越飞越高。"),
    ("Ultimate Frisbee", "ultimate-frisbee", "飞盘", "sport", 90, 6.0, m_near("ultimate frisbee, casual", 6.0), "和朋友在草坪上打一场飞盘，跑动接盘传配合。"),
    ("Glassblowing", "glassblowing", "玻璃吹制", "hobby", 90, 2.5, m_near("standing, moderate craft work", 2.5), "用吹杆挑起熔融玻璃，边转边吹出一只器皿的形状。"),
    ("Virtual Reality Gaming", "vr-gaming", "VR游戏", "hobby", 45, 2.0, m_near("standing, light gaming motion", 2.0), "戴上头显进入虚拟世界，动手动脚玩一局沉浸游戏。"),
    ("Podcasting", "podcasting", "播客录制", "hobby", 90, 1.8, m_near("standing/sitting, speaking/recording", 1.8), "选题、录音频、剪节目，更新一期自己的播客。"),
    ("Parkour", "parkour", "跑酷", "sport", 90, 8.0, C_CAL_V, "在城市障碍间跑、跳、翻越，训练身体的爆发与控制。"),
    ("Juggling", "juggling", "杂耍抛接", "hobby", 30, 3.0, m_near("juggling, moderate", 3.0), "从三个球开始练抛接，挑战不掉落的连续纪录。"),
    ("Fly Tying", "fly-tying", "毛钩绑制", "hobby", 60, 1.5, m_near("sitting, fine handcraft", 1.5), "用钩针、羽毛和丝线绑出拟饵毛钩，为钓鱼做准备。"),
    ("CrossFit", "crossfit", "综合体能训练", "sport", 60, 7.0, m_near("vigorous mixed conditioning", 7.0), "跟着 WOD 完成举重、体操与有氧混编的高强度训练。"),
    ("Cycling", "cycling", "骑行", "sport", 60, 5.8, m_near("bicycling, moderate 12-14 mph", 5.8), "沿街或绿道骑一圈公路车，匀速踏频出点汗。"),
    ("Hand Lettering", "hand-lettering", "手写字艺术", "hobby", 45, 1.5, m_near("sitting, drawing letterforms", 1.5), "用笔尖描摹字母的衬线与弧度，写一幅装饰字体。"),
    ("Jewelry Making", "jewelry-making", "首饰制作", "hobby", 90, 1.8, m_near("sitting, fine handcraft", 1.8), "串珠、绕线或镶蜡雕蜡，做一件可以戴出门的首饰。"),
    ("Tennis", "tennis", "网球", "sport", 90, 7.3, m_near("tennis, general", 7.3), "约球友打一场网球，底线对拉再伺机上网。"),
    ("Wine Tasting", "wine-tasting", "品酒", "social", 90, 1.5, m_near("sitting, tasting", 1.5), "和朋友开几瓶酒，摇杯闻香，聊聊产区与口感。"),
    ("Violin", "violin-practice", "小提琴练习", "hobby", 45, 2.0, m_near("playing instrument, standing/sitting", 2.0), "架琴夹弓练音阶与曲目，磨一段越来越顺的旋律。"),
    ("Ukulele", "ukulele-practice", "尤克里里练习", "hobby", 30, 2.0, m_near("playing instrument, sitting", 2.0), "抱着小琴按和弦扫弦，弹唱一首简单的歌。"),
    ("DJing", "djing", "DJ打碟", "hobby", 120, 2.5, m_near("standing, moderate arm activity", 2.5), "在对拍和混音台前选歌接歌，攒一期歌单或小样。"),
    ("Home Brewing", "home-brewing", "自酿饮品", "hobby", 120, 2.3, m_near("standing, kitchen work", 2.3), "备料、糖化、发酵，酿一批自己的啤酒或果酒。"),
    ("Digital Art", "digital-art", "数字绘画", "hobby", 90, 1.5, m_near("sitting, drawing tablet work", 1.5), "用数位板在软件里起稿、上色，画一张插画。"),
    ("Virtual Reality Fitness", "vr-fitness", "VR健身", "sport", 45, 4.0, m_near("VR exergame, moderate", 4.0), "戴上头显挥拳、躲避、下蹲，玩着游戏把汗流了。"),
    ("Rock Collecting", "rock-collecting", "岩石矿物收集", "hobby", 90, 2.5, m_near("walking outdoors, collecting", 2.5), "野外捡石头回家洗净编号，对照图鉴辨认矿物。"),
    ("RC Car Racing", "rc-car-racing", "遥控车竞速", "hobby", 60, 3.0, m_near("standing, light-moderate control activity", 3.0), "带着遥控车下场跑计时圈，调校设定刷新成绩。"),
    ("Golf", "golf", "高尔夫", "sport", 150, 4.5, m_near("golf, walking course", 4.5), "背着球包走完十八洞，一场球里挥杆推杆兼徒步。"),
    ("Writing Short Stories", "writing-short-stories", "短篇小说写作", "hobby", 90, 1.5, m_near("sitting, writing", 1.5), "构思人物与情节，把一个短故事从开头写到结尾。"),
    ("Sketching", "sketching", "速写", "hobby", 45, 1.8, m_near("standing/sitting, drawing", 1.8), "带上小本子在现场快速勾画人物与街景。"),
    ("Amateur Theater", "amateur-theater", "业余话剧排演", "hobby", 150, 2.5, m_near("rehearsing, standing/moving", 2.5), "和剧社同伴排一幕戏，走位对词准备演出。"),
    ("Scuba Diving", "scuba-diving", "潜水", "sport", 90, 7.0, m_near("scuba diving, moderate", 7.0), "背着气瓶下潜，在水下悠闲地看鱼看珊瑚。"),
    ("Table Tennis", "table-tennis", "乒乓球", "sport", 60, 4.0, m_near("table tennis, casual", 4.0), "在球台前打几局乒乓，正手攻反手拨地来回。"),
    ("Car Restoration", "car-restoration", "汽车翻新修复", "hobby", 180, 3.0, m_near("auto repair, moderate", 3.0), "在车库里拆检、除锈、喷漆，一点点救回一台老车。"),
    ("Sailing", "sailing", "帆船航行", "sport", 150, 3.0, m_near("sailing, moderate", 3.0), "升帆出港，迎风调帆压舷，在水面上驭风前行。"),
    ("Flower Arranging", "flower-arranging", "插花", "hobby", 60, 2.0, m_near("standing, light-moderate arrangement", 2.0), "修剪花材、搭配高低，插一瓶有层次的鲜花。"),
    ("Rope Climbing", "rope-climbing", "绳索攀爬", "sport", 60, 8.0, C_CAL_V, "抓绳靠臂力一节节向上爬，练抓握与核心。"),
    ("Wood Carving", "wood-carving", "木雕", "hobby", 90, 2.0, m_near("sitting, moderate handcraft", 2.0), "用刻刀在木料上细细雕出花纹或小动物。"),
    ("Motorcycle Restoration", "motorcycle-restoration", "摩托车翻新", "hobby", 180, 3.0, m_near("vehicle repair, moderate", 3.0), "把一台旧摩托拆开清洗检修，复刻它当年的样子。"),
    ("Drawing", "drawing", "素描", "hobby", 60, 1.8, m_near("sitting/standing, drawing", 1.8), "用铅笔排线打形，安静地画完一组静物或石膏像。"),
    ("Home Improvement", "home-improvement-diy", "家居改造DIY", "household", 180, 3.5, m_near("home repair/DIY, moderate", 3.5), "刷墙、装搁架、改造一个角落，亲手翻新家里的样子。"),
    ("Backpacking", "backpacking-trek", "重装徒步露营", "outdoor", 480, 7.0, m_near("backpacking with load", 7.0), "背上帐篷与干粮走一条两三天的线，晚上扎营山间。"),
    ("Racing Drones", "racing-drones", "竞速无人机", "hobby", 60, 2.5, m_near("standing, moderate attention activity", 2.5), "戴上飞行眼镜操纵穿越机，绕桩竞速刷圈。"),
    ("Fencing", "fencing", "击剑", "sport", 60, 6.0, m_near("fencing, moderate", 6.0), "穿上护面上剑道，练步伐进攻与防守还击。"),
    ("Embroidery", "embroidery", "刺绣", "hobby", 60, 1.5, m_near("sitting, fine needlework", 1.5), "穿针引线在绷布上绣出一小片花卉或图案。"),
    ("Skydiving", "skydiving", "跳伞", "sport", 30, 3.5, m_near("parachuting incl. preparation", 3.5), "登机爬升到高空，跃出舱门自由落体再开伞降落。"),
]

# Aggregated from Martjn/calisthenics_exercises categories.
# (category, slug, name_zh, minutes, met, met_cite, description_zh)
CALISTHENICS = [
    ("push", "calisthenics-push", "自重推力训练", 45, 3.8, C_CAL_M, "俯卧撑与双杠臂屈伸等推类动作组合，练胸肩与手臂推力。"),
    ("pull", "calisthenics-pull", "自重拉力训练", 45, 3.8, C_CAL_M, "引体向上与澳式划船等拉类动作组合，强化背部与肱二头。"),
    ("legs", "calisthenics-legs", "下肢自重训练", 45, 3.8, C_CAL_M, "深蹲、弓步与单腿蹲变式组合，把腿部与臀部练扎实。"),
    ("core", "calisthenics-core", "核心训练", 30, 3.8, C_CAL_M, "平板支撑、举腿等核心动作组合，练稳定有力的躯干。"),
    ("skill", "calisthenics-skill", "技巧动作练习", 45, 3.8, C_CAL_M, "练人体旗、前水平等街健技巧动作的进阶阶梯。"),
    ("explosive", "calisthenics-explosive", "爆发力训练", 30, 8.0, C_CAL_V, "爆发式俯卧撑、跳箱与短冲刺组合，提升爆发力。"),
    ("mobility", "calisthenics-mobility", "活动度与拉伸", 30, 2.3, m_near("stretching, general", 2.3), "关节活动度与静态拉伸流程，训练后放松全身。"),
]

# Selected from berdaniera/meditation themes.
# (theme, slug, name_zh, minutes, description_zh)
MEDITATION = [
    ("reduce stress", "meditation-reduce-stress", "减压冥想", 15, "闭眼坐正，跟随引导觉察呼吸，让紧绷的身体一点点松下来。"),
    ("improve focus", "meditation-improve-focus", "专注力冥想", 15, "把注意力拴在一呼一吸上，走神就轻轻拉回来，练心的定力。"),
    ("improve sleep", "meditation-improve-sleep", "睡前冥想", 15, "躺下后从脚到头逐段放松，让思绪落地，慢慢滑向睡眠。"),
    ("manage anxiety", "meditation-manage-anxiety", "缓解焦虑冥想", 15, "给焦虑的感受命名并允许它存在，用呼吸把心拉回当下。"),
    ("increase mindfulness", "meditation-mindfulness", "正念觉察冥想", 20, "不加评判地觉察念头与情绪的来去，练习纯粹的旁观。"),
    ("develop compassion", "meditation-compassion", "慈悲心冥想", 15, "在心中对自己和身边的人默送善意与祝福，软化内心的棱角。"),
    ("cultivate gratitude", "meditation-gratitude", "感恩冥想", 10, "回想今天值得感谢的三件小事，让心里升起暖意。"),
    ("build resilience", "meditation-resilience", "心理韧性冥想", 15, "在想象中直面压力情境并稳住呼吸，一点点扩展承受力。"),
]
MED_MET = 1.1
MED_MET_CITE = m_near("sitting, meditation/quiet", 1.1)

# Event types from verdantdavid/gsv3_upcoming_events (grounding keyword, minutes, met, cite).
# (slug, name_zh, keyword, minutes, met, met_cite, description_zh)
EVENTS = [
    ("event-concert", "看音乐会", "concert", 150, 1.5, m_near("sitting, concert", 1.5), "去现场听一场演唱会或音乐会，跟着旋律挥荧光棒。"),
    ("event-music-festival", "逛音乐节", "festival", 240, 3.5, m_near("walking/standing at festival grounds", 3.5), "在音乐节场地里赶场看演出，顺便逛市集吃吃喝喝。"),
    ("event-theatre", "看话剧", "theatre", 150, 1.5, m_near("sitting, watching performance", 1.5), "进剧场看一部话剧，跟着舞台上的故事起伏。"),
    ("event-comedy", "看脱口秀", "comedy", 90, 1.5, m_near("sitting, watching performance", 1.5), "坐在小剧场里听脱口秀专场，笑到脸酸。"),
    ("event-exhibition", "看艺术展览", "exhibit", 90, 2.0, m_near("walking, slow pace (gallery)", 2.0), "在美术馆或画廊慢慢看一个展览，在喜欢的作品前多站一会儿。"),
    ("event-cinema", "影院观影", "movie", 120, 1.3, m_near("sitting, watching movie", 1.3), "买桶爆米花在影院看一部新片，享受大银幕与音效。"),
    ("event-market", "逛市集", "market", 90, 2.5, m_near("walking, shopping", 2.5), "在创意市集或周末集市里闲逛，淘点小玩意儿。"),
    ("event-workshop", "参加体验工作坊", "workshop", 90, 2.0, m_near("standing/sitting, light hands-on activity", 2.0), "报名一次线下体验课，跟着老师动手做点什么。"),
    ("event-pub-quiz", "知识问答之夜", "trivia", 120, 1.5, m_near("sitting, social games", 1.5), "组队参加酒吧的问答之夜，抢答冷知识赢一轮酒水。"),
    ("event-karaoke", "唱K", "karaoke", 120, 2.0, m_near("singing, standing/sitting", 2.0), "约上几个人包间唱K，轮番点歌唱到嗓子冒烟。"),
]

# Self-authored vacuum items: (slug, name_zh, category, minutes, met, met_cite, description_zh)
SELF: list[tuple[str, str, str, int, float, str, str]] = [
    # 常见运动补集（跑步/骑行/游泳/网球等已由 HF 行覆盖）
    ("sport-brisk-walking", "快走", "sport", 40, 4.3, m_near("walking, brisk 3.5 mph", 4.3), "迈开步子快走四十分钟，微微出汗的最低门槛有氧。"),
    ("sport-badminton", "打羽毛球", "sport", 90, 4.5, m_near("badminton, casual singles/doubles", 4.5), "约人在球馆打羽毛球，高远球吊网前地来回奔跑。"),
    ("sport-basketball", "打篮球", "sport", 90, 8.0, m_near("basketball, game", 8.0), "组队打半场篮球，突破分球抢篮板，出一身透汗。"),
    ("sport-soccer", "踢足球", "sport", 90, 7.0, m_near("soccer, casual", 7.0), "约一场友谊赛踢足球，传接跑位，过足球瘾。"),
    ("sport-jump-rope", "跳绳", "sport", 15, 10.0, m_near("jumping rope, moderate", 10.0), "抽十五分钟跳绳，双摇间歇地把心率拉上去。"),
    ("sport-aerobics-class", "有氧操课", "sport", 60, 6.0, m_near("aerobics, general", 6.0), "跟着团课音乐跳一节有氧操，蹦跳组合练全身。"),
    ("sport-stretching", "拉伸放松", "sport", 20, 2.3, m_near("stretching, general", 2.3), "训练后或睡前做一套全身拉伸，把紧张的肌肉拉开。"),
    # 家务
    ("housework-cleaning", "打扫房间", "household", 45, 3.3, m_near("cleaning house, general", 3.3), "吸地拖地擦桌子，把里里外外收拾干净。"),
    ("housework-laundry", "洗衣服", "household", 45, 2.0, m_near("laundry, washing/folding", 2.0), "分类丢进洗衣机，洗完晾晒或叠好归位。"),
    ("housework-cooking", "家常做饭", "household", 50, 2.5, m_near("cooking, moderate effort", 2.5), "淘米切菜开火，给一家人做一顿家常晚饭。"),
    ("housework-tidying", "整理房间", "household", 30, 2.3, m_near("light housework, organizing", 2.3), "把散落的东西归位，桌面床铺收拾利落。"),
    ("housework-dishes", "洗碗", "household", 20, 2.3, m_near("washing dishes, standing", 2.3), "饭后把碗筷洗净擦干，厨房恢复整洁。"),
    ("housework-trash", "倒垃圾", "household", 10, 2.5, m_near("walking, carrying light load", 2.5), "把分类好的垃圾拎下楼扔进桶里。"),
    ("housework-change-sheets", "换床单", "household", 20, 3.0, m_near("making bed/changing linens", 3.0), "拆下旧床单被套换上干净的，铺平四角。"),
    ("housework-windows", "擦窗户", "household", 40, 3.0, m_near("window cleaning, moderate", 3.0), "里外两面把玻璃擦透亮，顺带抹净窗台。"),
    ("housework-repair", "小家电维修", "household", 60, 3.0, m_near("home repair, moderate", 3.0), "拧开外壳查线路换零件，修好一件罢工的小家电。"),
    ("housework-watering", "浇花", "household", 10, 2.3, m_near("walking, light gardening", 2.3), "给阳台上的绿植逐盆浇水，顺手摘掉黄叶。"),
    ("housework-parcel", "取快递", "household", 10, 2.5, m_near("walking, errand", 2.5), "下楼到驿站取快递，抱一堆纸箱回家拆。"),
    ("housework-waste-sorting", "垃圾分类", "household", 15, 2.3, m_near("light housework, sorting", 2.3), "把干湿可回收分清楚，分别打包投放。"),
    ("housework-grocery", "超市采购", "household", 45, 2.8, m_near("walking, grocery shopping", 2.8), "推着购物车把一周的食材日用品补齐。"),
    ("housework-wardrobe", "整理衣柜", "household", 45, 2.3, m_near("light housework, organizing", 2.3), "换季整理衣柜，该挂的挂该收的收。"),
    # 宠物
    ("pet-walk-dog", "遛狗", "pet", 30, 3.0, m_near("walking the dog", 3.0), "牵上狗子出门遛弯，让它撒欢儿也让自己动动。"),
    ("pet-feed-cat", "喂猫", "pet", 10, 1.5, m_near("standing, light care activity", 1.5), "给猫添粮换水，顺便拌点它爱吃的小食。"),
    ("pet-litter-box", "清理猫砂", "pet", 10, 2.5, m_near("squatting, light cleaning", 2.5), "铲掉结块补上新砂，给猫一个干净的厕所。"),
    ("pet-bath", "给宠物洗澡", "pet", 40, 3.5, m_near("bathing pet, moderate", 3.5), "烧水兑温给狗搓澡冲净，再吹干梳顺毛。"),
    ("pet-play", "陪宠物玩", "pet", 30, 2.5, m_near("standing, light active play", 2.5), "用逗猫棒或飞盘陪宠物疯玩一阵，人宠都开心。"),
    ("pet-vet", "带宠物看兽医", "pet", 60, 2.0, m_near("walking/standing, errand with pet", 2.0), "装进航空箱带宠物去宠物医院体检或看病。"),
    ("pet-grooming", "给宠物梳毛", "pet", 20, 2.3, m_near("grooming pet, light", 2.3), "用针梳给猫狗顺毛，梳掉浮毛防止打结。"),
    # 社交
    ("social-phone-call", "打电话给朋友", "social", 20, 1.3, m_near("sitting, talking on phone", 1.3), "给老朋友打个电话，隔着听筒闲聊近况。"),
    ("social-video-call", "视频聊天", "social", 30, 1.3, m_near("sitting, video conversation", 1.3), "和家人朋友视频连线，边聊边看看彼此。"),
    ("social-wechat-chat", "微信聊天", "social", 20, 1.3, m_near("sitting, texting", 1.3), "在微信上和几个朋友有一搭没一搭地聊。"),
    ("social-dinner-friends", "约朋友吃饭", "social", 90, 1.5, m_near("sitting, eating and talking", 1.5), "订个馆子约朋友聚一餐，边吃边聊近况。"),
    ("social-family-dinner", "家庭聚餐", "social", 90, 1.5, m_near("sitting, eating and talking", 1.5), "在家或饭店张罗一桌家庭聚餐，热闹地吃顿饭。"),
    ("social-date", "约会", "social", 150, 2.0, m_near("walking/sitting, social outing", 2.0), "和喜欢的人约个会，看电影逛街或只是散步聊天。"),
    ("social-party", "参加聚会", "social", 180, 2.5, m_near("standing/moving at party", 2.5), "去朋友家或酒吧参加聚会，认识新朋友聊到深夜。"),
    ("social-visit-parents", "探望父母", "social", 120, 1.5, m_near("sitting, family visit", 1.5), "回家看看爸妈，陪他们说说话吃顿饭。"),
    ("social-neighbor-chat", "和邻居聊天", "social", 15, 1.5, m_near("standing, conversation", 1.5), "在楼道或小区里碰见邻居，站着聊几句家常。"),
    # 学习
    ("learning-online-course", "上网课", "learning", 60, 1.3, m_near("sitting, class/lecture", 1.3), "打开电脑上一节在线课程，边听边记要点。"),
    ("learning-professional-book", "读专业书", "learning", 45, 1.3, m_near("sitting, reading", 1.3), "啃几章专业书，把和工作相关的知识补上。"),
    ("learning-language", "学外语", "learning", 30, 1.3, m_near("sitting, studying", 1.3), "背单词练听力，每天把外语学习打卡完成。"),
    ("learning-ted-talks", "看TED演讲", "learning", 20, 1.3, m_near("sitting, watching lecture", 1.3), "看一两个 TED 演讲，听个有意思的新想法。"),
    ("learning-notes", "写学习笔记", "learning", 20, 1.3, m_near("sitting, writing", 1.3), "把今天学到的内容整理成笔记，理清要点。"),
    ("learning-coding", "练习编程", "learning", 90, 1.5, m_near("sitting, computer work", 1.5), "写点代码做个小项目，顺手学一门新技术。"),
    ("learning-library", "图书馆自习", "learning", 120, 1.3, m_near("sitting, studying", 1.3), "去图书馆占个座，安静地自习一上午或一下午。"),
    # 志愿
    ("volunteer-environmental", "环保公益活动", "volunteer", 150, 4.0, m_near("conservation volunteer work", 4.0), "参加捡拾垃圾或植树一类的环保公益活动。"),
    ("volunteer-donation", "公益捐赠整理", "volunteer", 40, 2.5, m_near("standing, sorting/packing goods", 2.5), "把衣物书籍整理打包，送到捐赠点或寄给公益项目。"),
    ("volunteer-elder-care", "社区助老服务", "volunteer", 120, 2.5, m_near("volunteer care work, moderate", 2.5), "去社区帮独居老人跑腿陪聊，做一顿热饭。"),
    # 晨间护理与养生
    ("wellness-morning-wash", "晨间洗漱", "wellness", 15, 2.0, m_near("standing, washing/grooming", 2.0), "起床后刷牙洗脸清醒过来，开始新的一天。"),
    ("wellness-skincare", "护肤保养", "wellness", 10, 1.5, m_near("standing/sitting, grooming", 1.5), "按步骤涂水乳精华防晒，把皮肤照顾好。"),
    ("wellness-grooming", "整理仪容", "wellness", 15, 2.0, m_near("standing, grooming/dressing", 2.0), "吹头发剃须挑衣服，把自己收拾得体出门。"),
    ("wellness-tea", "泡茶", "wellness", 10, 2.0, m_near("standing, light kitchen task", 2.0), "烧水温杯泡一壶茶，趁热慢慢喝完。"),
    ("wellness-foot-soak", "泡脚", "wellness", 20, 1.5, m_near("sitting, bathing/soaking", 1.5), "睡前接盆热水泡脚，泡到微微出汗。"),
    ("wellness-morning-stretch", "晨间伸展", "wellness", 10, 2.3, m_near("stretching/light calisthenics", 2.3), "起床后伸伸懒腰转转脖子，唤醒僵硬的身体。"),
    ("wellness-hot-spring", "泡温泉", "wellness", 60, 1.5, m_near("sitting, bathing", 1.5), "去泡一池温泉，让热水把疲惫都化开。"),
    # 户外补充
    ("outdoor-park-walk", "公园散步", "outdoor", 40, 3.0, m_near("walking, slow/leisure pace", 3.0), "晚饭后去公园遛个弯，绕着湖走两圈吹吹风。"),
    ("outdoor-camping", "露营", "outdoor", 1200, 2.5, m_near("camping, mixed light activities", 2.5), "去营地搭帐篷过夜，烧烤看星星再钻进睡袋。"),
    # 爱好补充
    ("hobby-jigsaw-puzzle", "拼图", "hobby", 45, 1.3, m_near("sitting, puzzle", 1.3), "摊开一副千片拼图，按色块把边缘先拼出来。"),
    ("hobby-rubiks-cube", "玩魔方", "hobby", 25, 1.3, m_near("sitting, puzzle", 1.3), "打乱魔方按公式复原，练手速也练记公式。"),
    ("hobby-pour-over", "手冲咖啡", "hobby", 25, 2.0, m_near("standing, light kitchen task", 2.0), "称豆磨粉闷蒸注水，给自己冲一杯手冲。"),
    ("hobby-bookstore", "逛书店", "hobby", 60, 2.0, m_near("walking, slow pace (store)", 2.0), "在书店里翻翻新书，挑一本带回家。"),
    ("hobby-photo-organizing", "整理相册", "hobby", 60, 1.5, m_near("sitting, light organizing", 1.5), "把手机和硬盘里的照片筛一遍，做成小相册。"),
]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_enrichment() -> dict:
    """@brief 读取 v2 字段补齐表并校验形状与封闭集。"""
    document = load_json_file(ENRICHMENT)
    entries = document["entries"]
    assert tuple(document["location_kind_closed_set"]) == LOCATION_KINDS, \
        "enrichment closed set drifted from generator constant"
    for activity_id, record in entries.items():
        assert record["location_kinds"] and set(record["location_kinds"]) <= set(LOCATION_KINDS), \
            f"{activity_id}: bad location_kinds"
        window = record["time_window"]
        assert 0 <= window["start_hour"] < window["end_hour"] <= 24, \
            f"{activity_id}: bad time_window"
        assert isinstance(record["needs_partner"], bool), f"{activity_id}: bad needs_partner"
        effects = record["effects"]
        assert set(effects) == {"entertainment", "social", "health"}, \
            f"{activity_id}: bad effects keys"
        assert all(0.0 <= float(v) <= 1.0 for v in effects.values()), \
            f"{activity_id}: effects out of [0,1]"
    return entries


def load_json_file(path: Path):
    """@brief 读取 UTF-8 JSON 文件。"""
    return json.loads(path.read_text(encoding="utf-8"))


def verify_grounding() -> dict[str, str]:
    """@brief 校验 HF 原始文件在场且表格与真实数据吻合，返回文件哈希。"""
    paths = {
        "hobbies": HF_DIR / "hobbies.csv",
        "calisthenics": HF_DIR / "exercises.jsonl",
        "meditation": HF_DIR / "meditation-dataset.json",
        "events": HF_DIR / "events.csv",
    }
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        raise SystemExit(f"missing HF raw files (download per header URLs): {missing}")

    with paths["hobbies"].open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    names = {r["hobby"] for r in rows}
    table_names = {row[0] for row in HOBBIES}
    unhandled = names - table_names - HOBBY_DROPS
    unknown = table_names - names
    if unhandled or unknown:
        raise SystemExit(f"hobby table drift: unhandled={sorted(unhandled)} unknown={sorted(unknown)}")

    categories: dict[str, int] = {}
    for line in paths["calisthenics"].open(encoding="utf-8"):
        entry = json.loads(line)
        categories[entry["category"]] = categories.get(entry["category"], 0) + 1
    if set(categories) != {row[0] for row in CALISTHENICS}:
        raise SystemExit(f"calisthenics categories drifted: {sorted(categories)}")
    if sum(categories.values()) != 170:
        raise SystemExit(f"calisthenics exercise count drifted: {sum(categories.values())}")

    themes = {entry["theme"] for entry in json.load(paths["meditation"].open(encoding="utf-8"))}
    missing_themes = [row[0] for row in MEDITATION if row[0] not in themes]
    if missing_themes:
        raise SystemExit(f"meditation themes drifted: {missing_themes}")

    sample_limit = 12000
    with paths["events"].open(encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        text = " ".join((row["event"] or "").lower() for i, row in zip(range(sample_limit), reader))
    missing_kw = [row[2] for row in EVENTS if row[2] not in text]
    if missing_kw:
        raise SystemExit(f"event keywords not grounded: {missing_kw}")

    return {key: sha256_of(path) for key, path in paths.items()}


def apply_enrichment(records: list[dict]) -> None:
    """@brief 把 v2 补齐字段就地合并进条目（覆盖集漂移即失败）。"""
    enrichment = load_enrichment()
    record_ids = {record["activity_id"] for record in records}
    assert record_ids == set(enrichment), \
        f"enrichment coverage drift: {sorted(record_ids ^ set(enrichment))[:8]}"
    for record in records:
        extra = enrichment[record["activity_id"]]
        record["location_kinds"] = extra["location_kinds"]
        record["time_window"] = extra["time_window"]
        record["needs_partner"] = extra["needs_partner"]
        record["effects"] = extra["effects"]


def build_items() -> list[dict]:
    """@brief 组装全部生活活动条目并做封闭校验。"""
    items: list[dict] = []

    for row_name, slug, name, category, minutes, met, cite, desc in HOBBIES:
        items.append((slug, name, category, minutes, met, src_hobby(row_name, cite), desc))
    for category, slug, name, minutes, met, cite, desc in CALISTHENICS:
        items.append((slug, name, "sport", minutes, met,
                      f"hf:Martjn/calisthenics_exercises (Apache-2.0) category '{category}' "
                      f"aggregated; met: {cite}; {DUR}", desc))
    for theme, slug, name, minutes, desc in MEDITATION:
        items.append((slug, name, "wellness", minutes, MED_MET,
                      f"hf:berdaniera/meditation (CC-BY-4.0) theme '{theme}'; "
                      f"met: {MED_MET_CITE}; {DUR}", desc))
    for slug, name, keyword, minutes, met, cite, desc in EVENTS:
        items.append((slug, name, "outdoor", minutes, met,
                      f"hf:verdantdavid/gsv3_upcoming_events keyword '{keyword}'; "
                      f"met: {cite}; {DUR}", desc))
    for slug, name, category, minutes, met, cite, desc in SELF:
        items.append((slug, name, category, minutes, met,
                      f"self:authored for WorldMock lifestyle corpus; met: {cite}; {DUR}", desc))

    records = [dict(activity_id=slug, name_zh=name, category=category,
                    typical_duration_minutes=minutes, description_zh=desc,
                    met_value=met, source=source)
               for slug, name, category, minutes, met, source, desc in items]

    apply_enrichment(records)

    ids = [r["activity_id"] for r in records]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise SystemExit(f"duplicate activity ids: {dupes}")
    names = [r["name_zh"] for r in records]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise SystemExit(f"duplicate name_zh: {dupes}")
    for record in records:
        if record["category"] not in CATEGORIES:
            raise SystemExit(f"bad category: {record}")
        if not 5 <= record["typical_duration_minutes"] <= 1440:
            raise SystemExit(f"bad duration: {record['activity_id']}")
        if not 1.0 <= record["met_value"] <= 16.0:
            raise SystemExit(f"bad met: {record['activity_id']}")
        if "Compendium 2011" not in record["source"]:
            raise SystemExit(f"missing MET provenance: {record['activity_id']}")
    if not 180 <= len(records) <= 200:
        raise SystemExit(f"catalog size {len(records)} outside target range 180-200")
    records.sort(key=lambda r: (r["category"], r["activity_id"]))
    return records


def render(records: list[dict]) -> str:
    header = {
        "schema_version": 2,
        "corpus_id": "lifestyle-activities-2026-09-16",
        "description": "WorldMock 生活活动目录：基础活动类型语料（不进 content_catalog，"
                       "由 start_basic_activity(activity_type='lifestyle:<activity_id>') 提交）。",
        "categories": list(CATEGORIES),
        "count": len(records),
    }
    head = json.dumps(header, ensure_ascii=False, indent=2)
    body = json.dumps(records, ensure_ascii=False, indent=2)
    return head[:-1] + ',\n  "items": ' + body.replace("\n", "\n  ") + "\n}\n"


def main(argv: list[str]) -> int:
    digests = verify_grounding()
    records = build_items()
    content = render(records)
    check_only = "--check" in argv
    if check_only:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else None
        if current != content:
            print(f"DRIFT {OUT}")
            return 1
        print(f"OK {OUT} (byte-identical, {len(records)} items)")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8")
    by_cat: dict[str, int] = {}
    for record in records:
        by_cat[record["category"]] = by_cat.get(record["category"], 0) + 1
    print(f"WROTE {OUT} ({len(records)} items)")
    for cat in CATEGORIES:
        print(f"  {cat}: {by_cat.get(cat, 0)}")
    for key, digest in sorted(digests.items()):
        print(f"  hf-sha256 {key}: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
