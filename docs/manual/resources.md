<a name="resources-设计环境数据集"></a>
# 设计环境数据集

环境根包含 catalog/、persona.toml 和 config.toml。catalog/ 只有 manifest.json、app-catalog.jsonl、
callbacks.jsonl、content-catalog.jsonl、location.jsonl、merchant-catalog.jsonl。
每行是一项完整 JSON 对象，使用 UTF-8 无 BOM 与 LF；空 callbacks.jsonl 表示没有回调。
字段、显示名、绑定与发布摘要合同见[资源引擎](../design/03-resource-engine.md)。

地点逐项声明，不使用 count 扩张或生成地址。给本人住处声明 is_home，为一个实际地点声明 accepts_parcels，
同名分店使用 branch_label。地图从唯一运行种子和全部地点在内存生成，不把 map.json 放进发布环境。
需要营业限制时声明 weekly 与按 ISO 日期完整替换的 exceptions；省略 opens 表示全天营业。

app 区分内容平台和功能 app。功能 app 绑定具体动作，由引擎选择；内容平台供人物 interact。
商品只声明零食、食品、饮料、商品四类价格与地点类型或内容 app 绑定。
内容用 app_ids 或 location_kinds 显式绑定，填写实际时长、体力档位、两项心理缓解档位和 pushable。
不要添加内容分类、manner、动作菜单、消息、聊天对象或商家账户。

```mermaid
flowchart LR
    Locations[显式地点] --> Bindings[内容与商品绑定]
    Apps[内容与功能 app] --> Bindings
    Bindings --> Publish[刷新 manifest 原字节摘要]
    Callbacks[可选回调声明] --> Publish
    Publish --> Validate[完整环境验证]
```

修改目录后使用仓库的发布身份工具刷新 manifest 的文件摘要与目录内容摘要，再执行完整验证。
验证失败按返回的所属路径与英文原因修正，不能删除未知字段错误或靠旧格式混读运行。
操作命令见[运行手册](runtime.md)，作者说明放在环境包外。
