'use strict';

/** @brief 按现行人物提示格式解析归档请求，所有展示文字保留输入原文。 */
const ViewerContext = {};
{
  const LIFE_TEMPLATE = `你不是AI助手，也不是在等待指令的程序。你是{name}，一个有自己性格、需要和处境的人，
正过着自己的日子：会饿、会困、会心烦，也会在意身边的人和没做完的事。
人物卡片写的就是你这个人：你是谁、日子过得怎么样、身边有谁、
平时喜欢什么、压力大了靠什么缓一缓。
场景段落是你此刻的真实处境：什么时间、在哪儿、身边有谁、身体感觉如何、
手头正有什么事。
事实清单是此刻对你有用的信息。
凭你自己的性子和眼下的处境拿主意，像一个真实的人那样过日子。

怎样过一天：
你的一天由你自己安排。时间只在你做着某件事时流逝，所以每次想好之后都要落到一件具体的事上，哪怕只是歇一会儿或去睡觉。
一件事做完了，先给它收个尾，说说做得怎样、有什么想记住的，再开始下一件。
你手上最多同时挂两件事，暂停的也算；暂停的事只能回到原来的地方接着做，在别处可以直接收尾。
要出门，先把手上的事收尾或暂停；交谈和通话不能暂停，只能先结束。路上想听点什么、打个电话，出发时一并开始，到了再收尾，到了地方还可以接着听。
有的事一时做不了，世界会告诉你原因和该先做什么，照着调整就好。
困了就去睡。想在某个时刻起来，就在当天的目标里写上提醒时间；要到明天才起的，睡前收尾时说清几点起。睡着时只有提醒或睡够了才会醒。
工作要在要求的地方做，留意截止时间和已经做了多少；来不及做完时，想清楚先做哪部分。
你会饿、会困、会累。力气来自吃下去的东西，饿着或困着做事会慢，走路比坐车更费力。饿了就趁早去弄吃的，别拖到饿得发慌。
感到饿，或到了你的用餐时段，就把吃饭和手头的事一起考虑：先看手边能吃的和已经在路上的餐食；手边没有时，看看列出的餐食，需要时浏览商品。当前地点没有卖吃的，不代表不能送到这里。下单后要等送到，拿到手才能吃。
花钱量力而行；有人找你时，按你们的关系和当时的情况回应。
说打算做、说已经做过，都不等于真的做了，只有实际开始并完成的事才算数。你可以暂缓一件事，下次有机会时再随身体和处境重新权衡。`;
  const PRELUDE_SUFFIX = '\n\n请用一至三句第一人称中文，说出你此刻的感受和打算，最多二百汉字。';
  const BUDGET_MARKER = '\n\nAuthoritative ReAct model-turn budget:\n';
  const LOW_BUDGET = '\n\n剩余回合已经不多：当前阶段是before_action，请尽快提交本阶段需要的动作或结束。';
  const SCENE_START = new RegExp('^现在是 \\d{4} 年 \\d{1,2} 月 \\d{1,2} 日星期[一二三四五六日] '
    + '\\d{2}:\\d{2}(?:（Unix epoch：当前 \\d+）)?。');
  const MARKERS = {
    verified_experiences: '已核验经历：\n',
    subjective_summary: '主观归纳：\n',
    recent_subjective_episodes: '最近的人物自述：\n',
    decision_statement: '你刚才心里想的是：',
  };
  const LABELS = {
    system: '身份规则', persona: '人物卡片', scene: '此刻场景', verified_experiences: '已核验经历',
    subjective_summary: '主观归纳', recent_subjective_episodes: '最近人物自述', facts: '事实清单',
    decision_statement: '刚形成的自述', tools: '工具定义', message: '完整消息',
  };
  const rules = {
    system: {
      label: '身份规则', source: 'custom/agents/life_agent.py · _LIFE_INSTRUCTIONS、_model_request',
      fill: '人物名字填入固定生活规则；行动请求末尾追加当前决策的模型回合预算。',
      steps: [
        ['每轮复用', '生活规则文本复用，不从历史对话累计。'],
        ['阶段变化', '自述阶段要求一至三句、最多二百汉字；行动阶段追加实际预算。'],
        ['预算减少', '同一决策内每次模型调用消耗回合，剩余不超过两回合时增加一句提醒。'],
      ],
    },
    persona: {
      label: '人物卡片', source: 'custom/agents/life_agent.py · _persona_card_text；src/worldmock/runtime.py',
      fill: '从人物档案中的 persona_card 读取编译后身份文本；生成时限制六百汉字。',
      steps: [
        ['初始化', '由人物档案生成卡片，恢复运行时保留已保存的档案。'],
        ['每轮复用', '同一人物的卡片进入自述与行动请求，不自动吸收当天经历。'],
        ['保持分离', '持续变化的事实与主观记忆分别进入其他内容块。'],
      ],
    },
    scene: {
      label: '此刻场景', source: 'src/worldmock/life/projection.py、narration.py · render_scene',
      fill: '本轮冻结观察转换成时间、地点、体感、活动、目标、消息头、待办、物品与餐食的中文句子。',
      steps: [
        ['观察冻结', '自述与行动请求使用同一决策的观察；下一决策重新观察并重建场景。'],
        ['即时更新', '库存与活动进度来自本轮状态；餐食句优先描述手边食物，其次最早在途餐食。'],
        ['同刻窗口', '同一秒的后续决策可见上一轮本人结果的“刚才”；每秒首轮为空。'],
        ['消息头', '场景只放最新三条消息头，不带发件人和时间；读取状态不负责移除旧请求。'],
      ],
    },
    verified_experiences: {
      label: '已核验经历', source: 'src/worldmock/footprints/experiences.py · recent_verified_experiences',
      fill: '从已提交的本人活动结算投影最近五条事实，转成不含内部身份的中文句子。',
      steps: [
        ['实际结算', '仅到达、等待、食用、播放会产生此类经历，订单、消息与工作不在此窗口内。'],
        ['窗口前移', '新事实加入后保留最近五条；退出窗口不删除归档证据。'],
        ['阶段位置', '自述阶段单列句子，行动阶段同一窗口位于 facts.experiences。'],
      ],
    },
    subjective_summary: {
      label: '主观归纳', source: 'custom/agents/life_memory.py · _consolidation_due、_consolidate_subjective',
      fill: '归纳请求接收人物卡片、旧主观归纳和最多三十条尚未归纳的主观记忆。',
      steps: [
        ['触发条件', '默认未归纳的非反思条目达到十条，或重要度合计达到 3.0；睡醒也检查归纳。'],
        ['生成与替换', '有未归纳条目才调用模型；新归纳截取前四千字符并替换旧归纳。'],
        ['持久保存', '推进归纳游标，原记忆条目仍然保存；归纳始终是未经核验的主观内容。'],
      ],
    },
    recent_subjective_episodes: {
      label: '最近人物自述', source: 'custom/agents/life_memory.py；agentsociety2/agent/memory.py',
      fill: '默认取持久记忆按追加顺序的最后八条，提示只放带 [subjective] 标记的正文。',
      steps: [
        ['活动收尾', 'conclude_activity 可登记最多八条记忆、每条最多一千字符；在动作结算前写入。'],
        ['相似合并', '同类型、二十四小时内且相似度至少 0.6 时合并，更新时刻与次数，不改原文和位置。'],
        ['窗口前移', '新条目让较早条目退出默认八条窗口；持久记录仍可通过记忆工具查询。'],
        ['主观性质', '模型写入的感受或认定不能当作订单、工作提交等动作已经成功的证据。'],
      ],
    },
    facts: {
      label: '事实清单', source: 'src/worldmock/life/projection.py · _facts_projection；narration.py · render_facts',
      fill: '行动阶段把本轮公开事实渲染成单行紧凑 JSON，包括进度、消息、库存、商品、工具对象和目标。',
      steps: [
        ['每轮重建', '以当前冻结观察重新投影，没有把此前所有请求或购买回执拼接进来。'],
        ['各自窗口', '经历、动作结果、供给可见期与活动历史采用各自保留规则，并非统一长期记忆。'],
        ['公开字段', '内部身份和完整在途订单未直接进入此清单；在途餐食可能只出现在场景句子。'],
      ],
    },
    decision_statement: {
      label: '刚形成的自述', source: 'custom/agents/life_agent.py · prelude_decision_statement_request、_life_user_sections',
      fill: '本次自述模型的已校验正文，以“你刚才心里想的是”追加到行动输入末尾。',
      steps: [
        ['先形成自述', '自述可见人物、场景、已核验经历和记忆，不接收完整事实清单或工具定义。'],
        ['再选择行动', '行动请求加入完整事实清单与工具，并引用这次自述。'],
        ['下一决策', '清空并重新生成自述；不会自动作为历史聊天永久追加。'],
      ],
    },
    tools: {
      label: '工具定义', source: 'custom/agents/life_agent.py · build_react_tool_schemas、_budgeted_tool_models',
      fill: '独立 tools 字段写入完整工具描述、参数结构和本轮可交互对象枚举。',
      steps: [
        ['阶段筛选', '按当前活动阶段、可见对象和前置条件生成本轮可用动作。'],
        ['回合预算', '只剩登记动作所需的最后回合时移除记忆查询工具。'],
        ['计量口径', '未提供 tools 与显式 tools: [] 分别保留；工具参数和描述均可查看。'],
      ],
    },
    message: {
      label: '本轮追加消息', source: 'agentsociety2/agent/person.py；custom/agents/life_agent.py · _run_react_turn',
      fill: '同一决策内的 assistant 工具调用、记忆查询结果与纠错消息按实际发送顺序保留。',
      steps: [
        ['同轮追加', '记忆查询及协议纠错会增加消息，随后模型请求携带这些记录。'],
        ['动作登记', '登记一次生活动作即结束本次决策，执行结果在后续观察进入。'],
        ['下一决策', '重建 system 与 user，不直接携带上一决策的整段聊天记录。'],
        ['未知格式', '非当前模板或有歧义的消息不做语义分段，完整正文仍可查看。'],
      ],
    },
    inbox: {
      label: '收件箱', source: 'src/worldmock/life/projection.py · _public_inbox、_inbox_rows',
      fill: '场景取最新三条消息头；事实清单还保留发件人、创建时刻与已读状态。',
      steps: [
        ['收到消息', '当前直接消息头固定截取正文前四十字符，没有全文读取入口。'],
        ['持续出现', '旧消息可以持续可见；已读不等于请求已完成。'],
        ['与订单分离', '消息没有在提示中自动与后续订单、收货人和交付状态绑定。'],
      ],
    },
    recent_results: {
      label: '最近动作结果', source: 'src/worldmock/life/observe.py · _recent_action_results',
      fill: '读取上次进度请求之后的新动作结果，放入 facts.progress.results。',
      steps: [
        ['执行结算', '结果包含成功或失败、原因以及公开购买明细等。'],
        ['请求前移', '下一次进度请求推进截点，更早的购买回执不再持续出现在这个窗口。'],
        ['证据仍存', '退出本轮提示不删除事件归档，也不等于订单或交付消失。'],
      ],
    },
    progress: {
      label: '活动进度', source: 'src/worldmock/life/projection.py · _progress_projection',
      fill: '按地点停留或路途会话组织活动历史，带客观进度、本人自述、当前结果与提醒。',
      steps: [
        ['随活动增长', '新的会话和活动累积进入进度层，主观进度明确标注未经核验。'],
        ['状态变化', '进行、暂停、待收尾、已收尾与截止结束分别按当前状态投影。'],
        ['容量约束', '请求过大时只浓缩已收尾活动历史，活动当前状态、结果和提醒保持完整。'],
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
        ['提示分开取值', '生活提示直接组装人物、经历与主观记忆，不把整个文件再次灌入。'],
        ['范围区别', '文件四千字符预算和主观归纳四千字符限制都不是整个请求的容量。'],
      ],
    },
    capacity: {
      label: '请求容量', source: 'src/worldmock/life/prompt_capacity.py · fit_request',
      fill: '计量所有实际消息、工具与最大输出预留；展示的字符数量不是 token 数。',
      steps: [
        ['整体计量', '达到代码配置上下文容量的 85% 时，开始浓缩活动历史。'],
        ['按会话浓缩', '先合并较旧会话的已收尾活动，重新计量后再考虑合并全部已收尾历史。'],
        ['保留当前', '进行中、暂停、待收尾活动以及当前结果和提醒不被浓缩。'],
        ['明确失败', '仍超限则报错；自述阶段没有 facts 可供此步骤压缩，也不会静默截掉整份请求。'],
      ],
    },
  };

  /** @brief 取得精确匹配当前生活系统规则之后的阶段文字。 */
  function lifeSuffix(request) {
    const first = request?.messages?.[0];
    if (first?.role !== 'system' || typeof first.content !== 'string') return null;
    const [prefix, body] = LIFE_TEMPLATE.split('{name}');
    if (!first.content.startsWith(prefix)) return null;
    const bodyIndex = first.content.indexOf(body, prefix.length);
    if (bodyIndex < 0 || !first.content.slice(prefix.length, bodyIndex).trim()) return null;
    if (first.content.slice(prefix.length, bodyIndex).includes('\n')) return null;
    return first.content.slice(bodyIndex + body.length);
  }

  /** @brief 检查行动阶段的明确预算后缀，不凭工具名称猜测其他模型调用。 */
  function isActionSuffix(suffix) {
    if (suffix === null) return false;
    const text = suffix.startsWith(LOW_BUDGET) ? suffix.slice(LOW_BUDGET.length) : suffix;
    if (!text.startsWith(BUDGET_MARKER)) return false;
    try {
      const budget = JSON.parse(text.slice(BUDGET_MARKER.length));
      return budget !== null && !Array.isArray(budget) && budget.phase === 'before_action';
    } catch (error) {
      console.error('Context action budget is invalid:', error.message);
      return false;
    }
  }

  /** @brief 只按实际系统模板、阶段声明和工具字段区分自述、行动及其他调用。 */
  function classify(request, purpose) {
    const suffix = lifeSuffix(request);
    const hasTools = Object.prototype.hasOwnProperty.call(request || {}, 'tools');
    if (suffix === PRELUDE_SUFFIX && !hasTools) return {stage: 'prelude', label: '形成自述'};
    if (hasTools && Array.isArray(request.tools) && isActionSuffix(suffix)) {
      return {stage: 'action', label: '选择动作'};
    }
    return {stage: 'other', label: purpose ? `其他调用 · ${purpose}` : '其他调用'};
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

  /** @brief 为归档原文块生成稳定身份和统一计量，不声明业务填充来源已经核验。 */
  function makeBlock(key, text, messageIndex, role, label = LABELS[key]) {
    const id = key === 'tools' ? 'tools' : `message:${messageIndex}:${key}`;
    return {id, key, label, role, messageIndex, text, chars: charCount(text), rule: key, interpretation: 'recorded'};
  }

  /** @brief 收集空行边界并保留其原始下标，切片时把分隔符计入随后内容块。 */
  function boundaries(text) {
    return Array.from(text.matchAll(/\n\n/g), match => ({start: match.index, body: match.index + 2}));
  }

  /** @brief 识别当前模板的明确段标题，事实清单只接受以花括号起始的候选段。 */
  function markerKey(text) {
    for (const [key, marker] of Object.entries(MARKERS)) {
      if (text.startsWith(marker)) return key;
    }
    return text.startsWith('{') ? 'facts' : null;
  }

  /** @brief 按现行阶段合同验证内容块的唯一性及先后顺序。 */
  function validOrder(parts, stage) {
    const allowed = ['persona', 'scene', 'verified_experiences', 'subjective_summary',
      'recent_subjective_episodes', 'facts', 'decision_statement'];
    const keys = parts.map(part => part.key);
    if (new Set(keys).size !== keys.length) return false;
    if (keys.some((key, index) => index > 0 && allowed.indexOf(key) < allowed.indexOf(keys[index - 1]))) {
      return false;
    }
    if (stage === 'prelude') return !keys.includes('facts') && !keys.includes('decision_statement');
    return keys.includes('facts') && !keys.includes('verified_experiences');
  }

  /** @brief 校验候选事实段符合模板的一行对象 JSON 格式，不能据此核验填充来源。 */
  function validFacts(text) {
    if (text.includes('\n') || text.includes('\r')) return false;
    try {
      const facts = JSON.parse(text);
      return facts !== null && !Array.isArray(facts) && typeof facts === 'object';
    } catch (error) {
      console.error('Context facts JSON is invalid:', error.message);
      return false;
    }
  }

  /** @brief 验证明确标题有正文，最近记忆符合当前人物自述的起始标记。 */
  function validPart(part, text) {
    if (part.key === 'facts') return validFacts(text);
    const marker = MARKERS[part.key];
    if (!marker) return text.trim().length > 0;
    const body = text.slice(marker.length);
    if (!body.trim()) return false;
    return part.key !== 'recent_subjective_episodes' || body.startsWith('- [subjective] ');
  }

  /** @brief 场景时间边界唯一且候选段有序时取无损切片；归档未保存来源边界，分段只是模板推断。 */
  function splitUser(text, stage) {
    const lines = boundaries(text);
    const scenes = lines.filter(item => SCENE_START.test(text.slice(item.body)));
    if (scenes.length !== 1 || scenes[0].start === 0) return null;
    const scene = scenes[0];
    const parts = [{key: 'persona', start: 0}, {key: 'scene', start: scene.start}];
    for (const line of lines) {
      if (line.start <= scene.start) continue;
      const key = markerKey(text.slice(line.body));
      if (key) parts.push({key, start: line.start});
    }
    if (!validOrder(parts, stage)) return null;
    const slices = parts.map((part, index) => ({
      ...part, text: text.slice(part.start, parts[index + 1]?.start ?? text.length),
    }));
    if (!slices.every((part, index) => validPart(part, index ? part.text.slice(2) : part.text))) return null;
    return slices;
  }

  /** @brief 为未解析消息添加明确状态，并以英文记录解析失败原因。 */
  function unparsed(message, index, warnings, reason) {
    warnings.push(`消息 ${index + 1} 未解析：${reason}；完整正文已保留。`);
    console.error(`Context message ${index} was not segmented: template or unique boundaries were not matched.`);
    return [makeBlock('message', messageText(message), index, message?.role, '未解析 · 完整消息')];
  }

  /** @brief 首条简单 user 消息可按模板推断分段，所有候选块明确标记来源未核验。 */
  function parseMessage(message, index, context) {
    const role = message?.role;
    if (index !== context.firstUser) {
      const key = role === 'system' ? 'system' : 'message';
      const label = role === 'system' ? LABELS.system : `${role || '未知角色'} · 完整消息`;
      const block = makeBlock(key, messageText(message), index, role, label);
      if (context.stage === 'other' || (role === 'system' && index !== 0)) block.rule = null;
      if (role === 'system' && block.rule === null) block.label = '系统消息';
      return [block];
    }
    if (context.stage === 'other') {
      const blocks = unparsed(message, index, context.warnings, '不属于当前生活人物的两阶段模板');
      blocks[0].rule = null;
      return blocks;
    }
    const hasExtraFields = Object.keys(message).some(key => !['role', 'content'].includes(key));
    if (typeof message.content !== 'string' || hasExtraFields) {
      return unparsed(message, index, context.warnings, '消息含结构化内容或附加字段');
    }
    const parts = splitUser(message.content, context.stage);
    if (!parts) return unparsed(message, index, context.warnings, '内容边界缺失、重复或不符合当前顺序');
    return parts.map(part => ({...makeBlock(part.key, part.text, index, role), interpretation: 'template'}));
  }

  /** @brief 解析一个原始 model_request 事件；缺少请求消息时保留可见归档载荷。 */
  function parseRequest(event) {
    const request = event?.data?.request;
    const classification = classify(request, event?.data?.purpose);
    const warnings = [];
    const blocks = [];
    if (!Array.isArray(request?.messages)) {
      warnings.push('请求没有有效的 messages 数组，归档载荷按原始 JSON 展示。');
      console.error('Context request messages are missing or invalid.');
      blocks.push(makeBlock('message', JSON.stringify(event), -1, 'request', '未解析 · 请求载荷'));
    } else {
      const firstUser = request.messages.findIndex(message => message?.role === 'user');
      const context = {firstUser, stage: classification.stage, warnings};
      request.messages.forEach((message, index) => blocks.push(...parseMessage(message, index, context)));
    }
    if (request && Object.prototype.hasOwnProperty.call(request, 'tools')) {
      const block = makeBlock('tools', JSON.stringify(request.tools), -1, 'tools');
      if (classification.stage === 'other') block.rule = null;
      blocks.push(block);
    }
    const messageChars = blocks.filter(block => block.key !== 'tools').reduce((sum, block) => sum + block.chars, 0);
    const toolChars = blocks.filter(block => block.key === 'tools').reduce((sum, block) => sum + block.chars, 0);
    return {stage: classification.stage, stageLabel: classification.label, blocks, messageChars, toolChars, warnings};
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

  Object.assign(ViewerContext, {parseRequest, classify, compare, rules});
}
