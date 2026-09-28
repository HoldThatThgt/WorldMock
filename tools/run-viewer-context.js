'use strict';

/** @brief 按七层请求合同解析归档模型请求，所有展示文字保留输入原文。 */
const ViewerContext = {};
{
  const LIFE_ROLE = '你是模拟世界中的一个人物，依据人设层选择下一步行动；世界的时间、状态与结算由环境负责。';
  const USER_LAYERS = [['tool', '工具层'], ['persona', '人设层'], ['memory', '记忆层'], ['todo', '待办层'],
    ['environment', '环境层'], ['progress', '进度层']];
  const LABELS = {system: '系统层', tools: '工具定义', message: '完整消息', ...Object.fromEntries(USER_LAYERS)};
  const UNVERIFIED = '未经核验';
  const rules = {
    system: {
      label: '系统层', source: 'custom/agents/life_prompt.py · LIFE_SYSTEM_LAYER、system_message',
      fill: '固定交互规则按紧凑 JSON 写入 system 消息；整次运行逐字节相同，不追加预算或阶段文字。',
      steps: [
        ['每次复用', '同一运行的每个行动请求使用同一份系统层。'],
        ['规则唯一', '机制只写在系统层，工具描述只说明用途。'],
        ['识别依据', '查看器按 layer=系统层 与人物角色原文识别行动请求。'],
      ],
    },
    tool: {
      label: '工具层', source: 'src/worldmock/life/tool_layer.py · tool_layer、availability',
      fill: '环境按本次观察列出可操作目标，逐个动作与工具给出 available 与 reason。',
      steps: [
        ['单一来源', '可用性与环境受理使用同一组拒绝检查，列出即可提交。'],
        ['不可用项', 'available 为 false 的动作仍然列出，reason 与被拒时的原因相同。'],
        ['每次重建', '每次观察重新构建，不累积上一决策的目标。'],
      ],
    },
    persona: {
      label: '人设层', source: 'custom/agents/life_persona.py · persona_layer',
      fill: '宿主从人物档案与观察中的看法偏好构建身份、关系与偏好；当前阶段只有姓名与场景联系人。',
      steps: [
        ['无卡片路径', '年龄、作息、口味等未知字段为 null，不补造。'],
        ['关系', '每位场景联系人一项，按场景顺序列出。'],
        ['看法偏好', '人物自己写下的看法与偏好未经核验。'],
      ],
    },
    memory: {
      label: '记忆层', source: 'src/worldmock/life/observe.py · memory_layer',
      fill: '当前阶段为空结构：earlier_days 与 earlier_today 都是空列表。',
      steps: [
        ['当前阶段', '记忆层尚未派生条目，保持空列表而不是省略。'],
        ['记忆工具', '需要旧事时通过 memory_recent、memory_search、memory_time_range 查询。'],
        ['核验区分', 'settled 是环境核验的事实，self_notes 是人物自己写下的内容。'],
      ],
    },
    todo: {
      label: '待办层', source: 'src/worldmock/life/todo_layer.py · todo_layer',
      fill: '暂停的活动按暂停时刻排列并带恢复与收尾工具；今日计划列出目标、是否推进与提醒时刻。',
      steps: [
        ['暂停项', '恢复只能回到原地点，在别处恢复标为不可用并写明地点。'],
        ['今日计划', '零点计划之前或当日结束之后为 null。'],
        ['目标上限', '最多八项目标，按计划顺序排列。'],
      ],
    },
    environment: {
      label: '环境层', source: 'src/worldmock/life/environment_layer.py · environment_layer',
      fill: '观察时刻、身体感受文字与所在地点（停留或路程、地点类型、固定规则）。',
      steps: [
        ['身体感受', '只给感受文字，不给内部数值。'],
        ['所在地点', '路程中名称为「去某地的路上」，类型为步行或驾车。'],
        ['固定规则', '地点菜单排除的动作写成「这里不能……」。'],
      ],
    },
    progress: {
      label: '进度层', source: 'src/worldmock/life/progress_layer.py · progress_view',
      fill: '本段停留或路程中的调用与结果、消息全文、配送、截止与提醒，按发生时刻排列。',
      steps: [
        ['段落边界', '运行开始、出发与到达时重新开始，结果保留到本段结束。'],
        ['self_note', '提交世界请求那次回复的非空正文作为 self_note 附在调用上，未经核验。'],
        ['容量折叠', '超过容量时先去掉未受保护记录的 self_note，再折叠为 condensed；受保护记录不变。'],
      ],
    },
    tools: {
      label: '工具定义', source: 'custom/agents/life_agent.py · 原生工具；custom/agents/life_prompt.py · TOOL_DESCRIPTIONS',
      fill: '八个世界请求工具与三个记忆查询工具，严格参数结构，整次运行字节相同。',
      steps: [
        ['稳定工具面', '不再按观察绑定枚举，名称在宿主本地按私有绑定校验。'],
        ['最后回合', '只剩最后一个回合时不提供记忆查询工具。'],
        ['计量口径', '未提供 tools 与显式 tools: [] 分别保留；工具参数和描述均可查看。'],
      ],
    },
    message: {
      label: '本轮追加消息', source: 'agentsociety2/agent/person.py；custom/agents/life_agent.py · _run_react_turn',
      fill: '同一决策内的 assistant 调用、记忆查询结果与协议纠错消息按实际发送顺序保留。',
      steps: [
        ['记忆查询', '查询结果作为一条紧凑 JSON 工具消息追加。'],
        ['世界请求', '登记世界请求即结束本次决策，不再追加工具消息。'],
        ['多出消息', '首个请求多于两条消息时，多出的消息按完整消息显示。'],
        ['未知格式', '不符合七层合同的消息不做分段，完整正文仍可查看。'],
      ],
    },
    continuation: {
      label: '同秒续接',
      source: 'src/worldmock/life/continuation.py · same_second_chain；custom/agents/life_continuation.py · continuation_messages',
      fill: '同一秒的后续决策原样续接上一决策末次请求的全部消息，再追加上一决策的 assistant 与一条 tool 消息 {result, changes, events}。',
      steps: [
        ['识别', '一次决策的首个请求多于两条消息即判为续接。'],
        ['来源决策', '前序 assistant 按 tool_call 身份匹配到产生它的决策。'],
        ['回传思考', '前序 assistant 的 reasoning_content 单列显示；时间推进后请求不再带它。'],
      ],
    },
    reflection: {
      label: '睡醒反思', source: 'custom/agents/life_memory.py · after_step；agentsociety2/agent/memory_runtime.py',
      fill: '睡眠完成后的睡醒边界触发，输入当前时刻与最近三十条记忆，不含人物卡片。',
      steps: [
        ['睡醒边界', '先检查主观归纳，再进行反思；没有已有记忆时不调用反思模型。'],
        ['新增条目', '生成一至五条带原记忆引用的主观反思，重要度为 0.8，进入持久记忆。'],
        ['后续归纳', '反思不计入归纳触发阈值，但属于后续归纳可读取的未归纳内容。'],
      ],
    },
    memory_view: {
      label: '工作区记忆视图', source: 'custom/agents/life_memory.py · render_memory_view、build_context',
      fill: '工作区 MEMORY.md 由身份、五条已核验经历和主观归纳组合，最多四千字符。',
      steps: [
        ['每步重写', '根据当前身份、事实窗口和最新主观归纳重新生成视图。'],
        ['提示分开取值', '生活请求不读取这个文件；七层由环境与宿主分别构建。'],
        ['范围区别', '文件四千字符预算和主观归纳四千字符限制都不是整个请求的容量。'],
      ],
    },
    capacity: {
      label: '请求容量', source: 'src/worldmock/life/prompt_capacity.py · fit_request',
      fill: '计量系统层、工具、各用户层、追加消息与最大输出预留；展示的字符数量不是 token 数。',
      steps: [
        ['整体计量', '达到模型上下文容量的 85% 时开始处理进度层。'],
        ['逐步处理', '从旧到新去掉未受保护记录的 self_note，再把连续的未受保护记录折叠为 condensed。'],
        ['保留当前', '受保护记录，以及工具层、人设层、待办层、环境层都不被压缩。'],
        ['明确失败', '仍超限则按层记录 token 数并报错，不静默截掉请求。'],
      ],
    },
  };

  /** @brief 判断值是否为非数组对象。 */
  function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  /** @brief 解析 JSON 文本；无法解析返回 undefined，由调用方决定是否记为未解析。 */
  function tryJson(text) {
    try {
      return JSON.parse(text);
    } catch (error) {
      return undefined;
    }
  }

  /** @brief system 消息是生活决策系统层 JSON：layer 为系统层且角色原文相同。 */
  function lifeSystem(request) {
    const first = request?.messages?.[0];
    if (first?.role !== 'system' || typeof first.content !== 'string' || !first.content.startsWith('{')) return false;
    const layer = tryJson(first.content);
    return isObject(layer) && layer.layer === '系统层' && layer.role === LIFE_ROLE;
  }

  /** @brief 只按系统层 JSON 与工具字段区分行动请求；旧运行的两阶段请求归入其他调用并显示未解析。 */
  function classify(request, purpose) {
    if (lifeSystem(request) && Array.isArray(request.tools)) return {stage: 'action', label: '选择动作'};
    return {stage: 'other', label: purpose ? `其他调用 · ${purpose}` : '其他调用 · 未解析'};
  }

  /** @brief 统一按 Unicode 码点统计字符，不把代理对拆成两个字符。 */
  function charCount(text) {
    return Array.from(text).length;
  }

  /** @brief 附带字段或非字符串内容使用完整消息 JSON，避免丢失工具调用等记录。 */
  function messageText(message) {
    const keys = Object.keys(message || {});
    const onlyContent = keys.every(key => key === 'role' || key === 'content');
    return onlyContent && typeof message?.content === 'string' ? message.content : JSON.stringify(message);
  }

  /** @brief 为归档原文块生成稳定身份和统一计量。 */
  function makeBlock(key, text, messageIndex, role, label = LABELS[key]) {
    const id = key === 'tools' ? 'tools' : `message:${messageIndex}:${key}`;
    return {id, key, label, role, messageIndex, text, chars: charCount(text), rule: key, interpretation: 'recorded'};
  }

  /** @brief 找出顶层数组各元素的起点（开括号或逗号下标）；结构不是单个顶层数组时返回 null。 */
  function elementStarts(text) {
    if (!text.startsWith('[') || !text.endsWith(']')) return null;
    const starts = [0];
    let depth = 0, inString = false, escaped = false;
    for (let index = 0; index < text.length; index += 1) {
      const char = text[index];
      if (inString) {
        if (escaped) escaped = false;
        else if (char === '\\') escaped = true;
        else if (char === '"') inString = false;
      } else if (char === '"') {
        inString = true;
      } else if (char === '[' || char === '{') {
        depth += 1;
      } else if (char === ']' || char === '}') {
        depth -= 1;
        if (depth === 0 && index !== text.length - 1) return null;
      } else if (char === ',' && depth === 1) {
        starts.push(index);
      }
    }
    return depth === 0 && !inString ? starts : null;
  }

  /** @brief 把首个 user 按顶层数组无损切片：分隔符计入随后切片，每片可解析且层序符合七层合同才分段。 */
  function splitLayers(text) {
    const starts = elementStarts(text);
    if (!starts || starts.length !== USER_LAYERS.length) return null;
    const layers = [];
    for (const [index, start] of starts.entries()) {
      const end = starts[index + 1] ?? text.length;
      const value = tryJson(text.slice(start + 1, index + 1 < starts.length ? end : end - 1));
      const [key, name] = USER_LAYERS[index];
      if (!isObject(value) || value.layer !== name) return null;
      layers.push({key, text: text.slice(start, end), value});
    }
    return layers;
  }

  /** @brief 为未解析消息添加明确状态，并以英文记录解析失败原因。 */
  function unparsed(message, index, warnings, reason) {
    warnings.push(`消息 ${index + 1} 未解析：${reason}；完整正文已保留。`);
    console.error(`Context message ${index} was not segmented: the seven-layer contract was not matched.`);
    return [makeBlock('message', messageText(message), index, message?.role, '未解析 · 完整消息')];
  }

  /** @brief 首个 user 之外的消息整条展示；行动请求的首条 system 对应系统层规则。 */
  function wholeMessage(message, index, stage) {
    const role = message?.role;
    const key = role === 'system' ? 'system' : 'message';
    const label = role === 'system' ? LABELS.system : `${role || '未知角色'} · 完整消息`;
    const block = makeBlock(key, messageText(message), index, role, label);
    if (stage === 'other' || (role === 'system' && index !== 0)) block.rule = null;
    if (role === 'system' && block.rule === null) block.label = '系统消息';
    return block;
  }

  /** @brief 续接前缀中的一条消息：整条保留，assistant 按 tool_call 身份标出来源决策，回传的思考另列一块（不重复计量）。 */
  function continuationMessage(message, index, sources) {
    const role = message?.role;
    const calls = Array.isArray(message?.tool_calls) ? message.tool_calls : [];
    const origin = calls.map(call => sources[call?.id]).find(Boolean) ?? '未匹配';
    const label = role === 'assistant' ? `续接 · assistant · 来源决策 ${origin}` : `续接 · ${role || '未知角色'} · 完整消息`;
    const block = {...makeBlock('message', messageText(message), index, role, label), rule: 'continuation'};
    const reasoning = role === 'assistant' && typeof message.reasoning_content === 'string' ? message.reasoning_content : '';
    if (!reasoning) return [block];
    return [block, {...makeBlock('reasoning', reasoning, index, role, `回传思考 · 来源决策 ${origin}`),
      rule: 'continuation', interpretation: 'reasoning'}];
  }

  /** @brief 续接前缀中 assistant 的全部 tool_call 身份（首个 user 之后的消息），供查看器匹配来源决策。 */
  function prefixCallIds(request) {
    const messages = Array.isArray(request?.messages) ? request.messages : [];
    const firstUser = messages.findIndex(message => message?.role === 'user');
    return messages.slice(firstUser + 1).filter(message => message?.role === 'assistant')
      .flatMap(message => (Array.isArray(message.tool_calls) ? message.tool_calls : []).map(call => call?.id))
      .filter(id => typeof id === 'string');
  }

  /** @brief 行动请求的首个 user 按六层切片；其他调用或不符合合同的输入整条保留为未解析；续接前缀按来源决策标注。 */
  function parseMessage(message, index, context) {
    if (context.sources && index > context.firstUser) return continuationMessage(message, index, context.sources);
    if (index !== context.firstUser) return [wholeMessage(message, index, context.stage)];
    if (context.stage === 'other') {
      const blocks = unparsed(message, index, context.warnings, '不属于七层行动请求');
      blocks[0].rule = null;
      return blocks;
    }
    const hasExtraFields = Object.keys(message).some(key => !['role', 'content'].includes(key));
    if (typeof message.content !== 'string' || hasExtraFields) {
      return unparsed(message, index, context.warnings, '消息含结构化内容或附加字段');
    }
    const layers = splitLayers(message.content);
    if (!layers) return unparsed(message, index, context.warnings, '顶层数组无法无损切片、某层不可解析或层序不符');
    return layers.map(layer => ({...makeBlock(layer.key, layer.text, index, message.role),
      interpretation: 'layer', value: layer.value}));
  }

  /** @brief 解析一个原始 model_request 事件；缺少请求消息时保留可见归档载荷。
   *
   * continuation 为 {sources: {tool_call 身份: 来源决策}} 时表示它是一次决策的首个请求：行动请求多于两条消息即判为续接，
   * 前序消息按来源决策标注、回传思考单列；回传思考块是 assistant 消息的一部分，不另计字符。 */
  function parseRequest(event, continuation = null) {
    const request = event?.data?.request;
    const classification = classify(request, event?.data?.purpose);
    const warnings = [];
    const blocks = [];
    let sources = null;
    if (!Array.isArray(request?.messages)) {
      warnings.push('请求没有有效的 messages 数组，归档载荷按原始 JSON 展示。');
      console.error('Context request messages are missing or invalid.');
      blocks.push(makeBlock('message', JSON.stringify(event), -1, 'request', '未解析 · 请求载荷'));
    } else {
      const firstUser = request.messages.findIndex(message => message?.role === 'user');
      const continued = classification.stage === 'action' && continuation && request.messages.length > 2;
      sources = continued ? continuation.sources : null;
      const context = {firstUser, stage: classification.stage, warnings, sources};
      request.messages.forEach((message, index) => blocks.push(...parseMessage(message, index, context)));
    }
    if (request && Object.prototype.hasOwnProperty.call(request, 'tools')) {
      const block = makeBlock('tools', JSON.stringify(request.tools), -1, 'tools');
      if (classification.stage === 'other') block.rule = null;
      blocks.push(block);
    }
    const messageChars = blocks.filter(block => block.key !== 'tools' && block.key !== 'reasoning')
      .reduce((sum, block) => sum + block.chars, 0);
    const toolChars = blocks.filter(block => block.key === 'tools').reduce((sum, block) => sum + block.chars, 0);
    return {stage: classification.stage, stageLabel: classification.label, blocks, messageChars, toolChars, warnings,
      continuation: sources !== null};
  }

  /** @brief 可用性短语：可用，或「不可用：」加环境给出的原因原文。 */
  function availability(row) {
    return row?.available ? '可用' : `不可用：${row?.reason ?? ''}`;
  }

  /** @brief 工具层下钻：每个目标一项，其后逐个列出该目标的动作与工具及可用性。 */
  function toolItems(layer) {
    const items = [];
    for (const target of Array.isArray(layer?.targets) ? layer.targets : []) {
      items.push({label: `${target.name}（${target.kind}）`, text: JSON.stringify(target, null, 2)});
      for (const row of Array.isArray(target.actions) ? target.actions : []) {
        items.push({label: `${target.name} · ${row.action}：${row.way} · ${availability(row)}`,
          text: JSON.stringify(row, null, 2)});
      }
      for (const row of Array.isArray(target.tools) ? target.tools : []) {
        items.push({label: `${target.name} · ${row.tool} · ${availability(row)}`, text: JSON.stringify(row, null, 2)});
      }
    }
    return items;
  }

  /** @brief 进度层下钻：每条记录一项；带 self_note 的记录在标签与正文中标明未经核验。 */
  function progressItems(layer) {
    return (Array.isArray(layer?.events) ? layer.events : []).map(record => {
      const subject = record.tool ?? record.sender ?? record.order ?? record.task ?? record.goal ?? '';
      const hasNote = typeof record.self_note === 'string';
      const note = hasNote ? `self_note（${UNVERIFIED}）：${record.self_note}\n\n` : '';
      return {label: `${record.occurred_at} · ${record.type}${subject ? ' · ' + subject : ''}` +
        (hasNote ? ` · self_note（${UNVERIFIED}）` : ''), text: note + JSON.stringify(record, null, 2)};
    });
  }

  /** @brief 按层的真实字段给出下钻条目；只有工具层与进度层可下钻。 */
  function drillItems(block) {
    if (block?.interpretation !== 'layer') return [];
    if (block.key === 'tool') return toolItems(block.value);
    if (block.key === 'progress') return progressItems(block.value);
    return [];
  }

  /** @brief 比较稳定身份相同的完整正文，字符数量相同也不能代替内容相等。 */
  function comparison(before, after) {
    const block = after || before;
    let status = before ? 'removed' : 'added';
    if (before && after) status = before.text === after.text ? 'unchanged' : 'changed';
    return {id: block.id, key: block.key, label: block.label, status, before, after,
      delta: (after?.chars || 0) - (before?.chars || 0)};
  }

  /** @brief 按当前请求顺序显示对照，再列出已退出的内容块，保留前后完整切片。 */
  function compare(currentBlocks, previousBlocks) {
    const previous = new Map(previousBlocks.map(block => [block.id, block]));
    const currentIds = new Set(currentBlocks.map(block => block.id));
    const changes = currentBlocks.map(block => comparison(previous.get(block.id) || null, block));
    for (const block of previousBlocks) {
      if (!currentIds.has(block.id)) changes.push(comparison(block, null));
    }
    return changes;
  }

  Object.assign(ViewerContext, {parseRequest, prefixCallIds, classify, compare, drillItems, rules});
}
