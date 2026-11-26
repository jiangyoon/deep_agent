// 流式事件和历史事件共用这套规则。manualOpen 只由用户操作修改。
function applyChatEvent(blocks, event, now = null) {
  const data = event.data;
  const last = blocks[blocks.length - 1];
  const thought = blocks[0]?.type === 'thought' ? blocks[0] : null;
  if (event.name === 'reasoning_delta') {
    if (!data.text) return;
    if (last?.type === 'text') last.type = 'messageStep';
    if (!thought) {
      blocks.unshift({ key: 'thought', type: 'thought', status: 'running', content: data.text });
    } else {
      if (thought.separateNextThought || thought.status !== 'running') thought.content += '\n\n';
      thought.separateNextThought = false;
      // 新一轮思考开始：清掉上一轮留下的手动开合标记，恢复自动展开/折叠。
      if (thought.status !== 'running') thought.manualOpen = null;
      thought.status = 'running';
      thought.content += data.text;
    }
    return;
  }
  if (event.name === 'answer_delta') {
    if (!data.text) return;
    if (thought?.status === 'running' && data.text.trim()) thought.status = 'completed';
    if (last?.type === 'text') last.content += data.text;
    else blocks.push({ key: 'text-' + blocks.length, type: 'text', content: data.text });
    return;
  }
  // 工具事件只分隔前后两段思考，保持思考框当前的展开状态。
  if (thought) thought.separateNextThought = true;
  if (last?.type === 'text') last.type = 'messageStep';
  if (event.name === 'ask_user') {
    const id = data.tool_call_id || data.id;
    const call = blocks.find(b => b.type === 'tool' && b.id === id);
    if (call) {
      call.status = 'waiting';
    } else {
      blocks.push({ key: 'call-' + blocks.length, type: 'tool', id, name: 'ask_user',
        args: { question: data.question, options: data.options }, status: 'waiting' });
    }
    // 弹出确认卡片时，用户正看到的思考内容保持展开：收尾会把 running 思考
    // 折叠，这里固定住当前展开状态（用户手动收起过的则尊重其选择）。
    if (thought && thought.status === 'running' && thought.manualOpen == null) thought.manualOpen = true;
    finishChatBlocks(blocks, 'done');
  } else if (event.name === 'tool_call') {
    blocks.push({ key: 'call-' + blocks.length, type: 'tool', id: data.id, name: data.name,
      args: data.args, status: 'running', startedAt: now });
  } else if (event.name === 'tool_result') {
    // LangGraph 恢复时可能重新发出同批工具结果，按调用 ID 更新原卡片。
    let call = blocks.find(b => b.type === 'tool' &&
      (data.id ? b.id === data.id : b.name === data.name && b.status === 'running'));
    if (!call) {
      call = { key: 'call-' + blocks.length, type: 'tool', name: data.name, id: data.id };
      blocks.push(call);
    }
    call.status = data.status === 'error' ? 'error' : 'completed';
    if (data.askReply) {
      call.status = data.askReply.status;
      call.askReply = data.askReply;
    }
    call.result = data.result;
    if (now != null && call.startedAt != null && call.ms == null && !data.askReply) call.ms = now - call.startedAt;
  }
}

function finishChatBlocks(blocks, status = 'interrupted') {
  for (const block of blocks) {
    if (block.status !== 'running') continue;
    if (status === 'done') block.status = block.type === 'thought' ? 'completed' : 'unknown';
    else block.status = status;
  }
}

const toolTitles = {
  lookup_certificate_mapping: '查证书职称对应', lookup_certificate_reward: '查证书奖励标准',
  lookup_application_schedule: '查申报时间安排', find_policy_document: '定位政策文件',
  list_notice_attachments: '查通知附件清单', search_policy_sections: '检索政策原文',
  read_file: '读取文件', write_file: '写入文件', edit_file: '编辑文件',
  ls: '列出目录', glob: '查找文件', grep: '搜索内容', ask_user: '向你确认',
};

const ActivityCard = {
  components: { IconLoader, IconChevronDown, IconChevronRight },
  props: ['block'],
  emits: ['inspect', 'open-ask'],
  setup(props, { emit }) {
    const panel = ref(null);
    let followThought = true;
    const open = computed(() => props.block.manualOpen ??
      (props.block.type === 'thought' && props.block.status === 'running'));
    const title = computed(() => {
      const b = props.block;
      if (b.type === 'thought') return b.status === 'running' ? '正在思考' : '模型思考';
      if (b.type === 'messageStep') return '过程说明';
      const skill = b.name === 'read_file' && /^\/skills\/(.+)\/SKILL\.md$/.exec(b.args?.file_path || '');
      return skill ? '读取 Skill · ' + skill[1] : toolTitles[b.name] || b.name || '工具调用';
    });
    const statusText = computed(() => {
      const b = props.block;
      if (b.type === 'messageStep') return '';
      const label = { running: '进行中', completed: '已完成', error: '失败', interrupted: '已中断',
        unknown: '未记录结果', waiting: '等待你回答', answered: '已回答', skipped: '已跳过', deferred: '稍后确认' }[b.status];
      return b.ms == null ? label : label + ' · ' + (b.ms < 1000 ? b.ms + ' ms' : (b.ms / 1000).toFixed(1) + ' s');
    });
    function toggle(event) {
      if (props.block.status === 'waiting') {
        emit('open-ask');
        return;
      }
      emit('inspect', event.currentTarget);
      props.block.manualOpen = !open.value;
    }
    function onPanelScroll() {
      const el = panel.value;
      if (el) followThought = el.scrollHeight - el.scrollTop - el.clientHeight <= 4;
    }
    function followThoughtOutput() {
      if (props.block.type === 'thought' && panel.value && followThought && open.value) {
        panel.value.scrollTop = panel.value.scrollHeight;
      }
    }
    watch(() => props.block.content, followThoughtOutput, { flush: 'post' });
    onMounted(followThoughtOutput);
    return { panel, open, title, statusText, toggle, onPanelScroll };
  },
  template: `
    <section class="activity-card">
      <button type="button" class="activity-heading" :aria-expanded="block.status === 'waiting' ? undefined : open"
              :aria-haspopup="block.status === 'waiting' ? 'dialog' : undefined" @click="toggle">
        <icon-loader v-if="block.status === 'running'" class="w-4 h-4 shrink-0 text-violet-500 animate-spin"/>
        <span v-else class="step-dot" :class="{ failed: block.status === 'error' || block.status === 'interrupted' }"></span>
        <span class="min-w-0 flex-1 truncate text-left">{{ title }}</span>
        <span class="text-xs text-gray-500">{{ statusText }}</span>
        <component :is="open ? 'icon-chevron-down' : 'icon-chevron-right'" class="w-4 h-4 shrink-0"/>
      </button>
      <div v-if="open" ref="panel" @scroll.passive="onPanelScroll" tabindex="0" :aria-label="title"
           class="activity-detail" :class="{ 'thought-detail': block.type === 'thought' }">
        <template v-if="block.askReply && block.args?.question">
          <div class="whitespace-pre-wrap">{{ block.args.question }}</div>
          <div v-if="block.askReply.status === 'answered'" class="mt-2 whitespace-pre-wrap text-gray-600">{{
            block.args.options?.find(option => option.value === block.askReply.value)?.label ?? block.askReply.value
          }}</div>
        </template>
        <template v-else-if="block.type === 'tool'">
          <div class="text-gray-500 text-xs">{{ block.name }}</div>
          <pre v-if="block.args != null">{{ JSON.stringify(block.args, null, 2) }}</pre>
          <pre v-if="block.result">{{ block.result }}</pre>
        </template>
        <div v-else class="whitespace-pre-wrap">{{ block.content }}</div>
      </div>
    </section>`,
};

const AgentBlocks = {
  components: { ActivityCard },
  props: ['blocks', 'isGenerating', 'renderAnswer', 'markdownReady', 'blueNames'],
  emits: ['inspect', 'open-ask'],
  computed: {
    waiting() {
      return this.isGenerating && !this.blocks.some(b => b.status === 'running') &&
        this.blocks[this.blocks.length - 1]?.type !== 'text';
    },
  },
  template: `
    <div class="w-full min-w-0 max-w-2xl space-y-2">
      <template v-for="block in blocks" :key="block.key">
        <div v-if="block.type === 'text'" class="px-6 py-4 rounded-[1.5rem] rounded-tl-sm shadow-sm bg-white border border-gray-200 text-gray-800">
          <div v-if="markdownReady" class="md-answer leading-relaxed text-[15px]" v-html="renderAnswer(block.content, blueNames)"></div>
          <div v-else class="whitespace-pre-wrap leading-relaxed text-[15px]">{{ block.content }}</div>
        </div>
        <activity-card v-else :block="block" @inspect="$emit('inspect', $event)" @open-ask="$emit('open-ask')"/>
      </template>
      <div v-if="waiting" role="status" class="text-sm text-gray-500 animate-pulse">正在处理…</div>
    </div>`,
};
