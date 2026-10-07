# 人工最小测试环境

这个声明的人造世界验证 WorldMock 登记、加载、固定规则、状态、记录与导出。地点、人物、内容和数值为人工事实，app 名允许真实名称；正式来源、数量与重建验收由 WorldMock-Dataset 管理。

| 输入 | 人工语义 |
| --- | --- |
| 地点 | 家、公司、一心面馆、二味小炒、三杯咖啡、四季便利、五里公园，共七个 |
| app | 8 个：支付宝、高德地图、哔哩哔哩、抖音、饿了么、Keep、美团、微信 |
| 商品 | 6 个：牛肉面、薯片、柠檬茶、扑克牌、饭团、纸巾 |
| 动作 | 7 个：刷推荐、办公、安静休息、跑步、聊天、学课程、语音通话 |
| 内容 | 十五条，覆盖七动作及每个动作与 app 的组合；摘要声明 synthetic 与合成 notice，没有内容分类 |
| 地图 | 种子 7、七节点；地点与地图发布字节保持不变 |
| 场景 | 结构 8 的单人、双人、双人四小时入口；工作内容与工作安排按人物分配 |

目录格式 2，规则版本 2；清单绑定 apps、locations、goods、actions、contents、map 六个 JSON 文件。环境根只有 catalog 与 scenarios，没有语料目录或规则副本。场景 environment 钉住清单标识与版本，真实加载核对字节与跨对象引用；已选店铺有 merchant 账户。

```bash
worldmock environment generate-map --locations tests/fixtures/environment/catalog/locations.json \
  --seed 7 --output tests/fixtures/environment/catalog/map.json --check
worldmock validate --environment tests/fixtures/environment
```

登记与纯加载证明输入有效，完整动作、关闭恢复、自然日与认证导出分别要求当前版本真实回执。
