const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');

function page(name, fetchImpl, search = '') {
  const redirects = [];
  const mounted = [];
  const sandbox = {
    fetch: fetchImpl,
    window: { location: { search, replace: url => redirects.push(url) }, confirm: () => true },
    URLSearchParams, AbortController, TextDecoder, Uint8Array,
    requestAnimationFrame: () => {},
    Vue: {
      ref: value => ({ value }), computed: fn => ({ get value() { return fn(); } }),
      watch: () => {}, nextTick: () => {}, onMounted: fn => mounted.push(fn),
      createApp: () => ({ mount: () => {} }),
    },
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(readFileSync(resolve(__dirname, '../view/shared.js'), 'utf8'), context);
  if (name) {
    const html = readFileSync(resolve(__dirname, '../view/' + name + '.html'), 'utf8');
    const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
      .map(match => match[1]).filter(value => value.trim()).join('\n');
    vm.runInContext(script, context);
  }
  return { context, redirects, mounted,
    setup: () => vm.runInContext(name === 'login' ? 'LoginScreen.setup()' : 'ChatInterface.setup()', context) };
}

function response(status, data) {
  return { status, ok: status >= 200 && status < 300, json: async () => data };
}

test('401 redirects once and prevents subsequent anonymous requests', async () => {
  let calls = 0;
  const p = page(null, async () => { calls++; return response(401, {}); });
  await assert.rejects(vm.runInContext("apiFetch('/api/sessions')", p.context), /登录/);
  await assert.rejects(vm.runInContext("apiFetch('/api/sessions')", p.context), /登录/);
  assert.equal(calls, 1);
  assert.deepEqual(p.redirects, ['/login?expired=1']);
});

test('concurrent 401 responses only trigger one navigation', async () => {
  const p = page(null, async () => response(401, {}));
  await Promise.allSettled([
    vm.runInContext("apiFetch('/api/sessions')", p.context),
    vm.runInContext("apiFetch('/api/auth/me')", p.context),
  ]);
  assert.deepEqual(p.redirects, ['/login?expired=1']);
});

test('login sends credentials and redirects after success', async () => {
  let request;
  const p = page('login', async (url, options) => { request = { url, options }; return response(200, {}); });
  const ui = p.setup();
  ui.username.value = 'alice'; ui.password.value = 'alice-password';
  await ui.handleSubmit({ preventDefault() {} });
  assert.equal(request.url, '/api/auth/login');
  assert.deepEqual(JSON.parse(request.options.body), { username: 'alice', password: 'alice-password' });
  assert.deepEqual(p.redirects, ['/']);
});

test('login displays failure and expired notice', async () => {
  const p = page('login', async () => response(401, { detail: '用户名或密码错误' }), '?expired=1');
  const ui = p.setup();
  ui.username.value = 'alice'; ui.password.value = 'wrong';
  await ui.handleSubmit({ preventDefault() {} });
  assert.equal(ui.errorMessage.value, '用户名或密码错误');
  assert.match(ui.notice.value, /过期/);
  assert.equal(ui.isSubmitting.value, false);
  assert.deepEqual(p.redirects, []);
});

test('chat loads current user before sessions and logs out', async () => {
  const calls = [];
  const p = page('chat', async (url, options) => {
    calls.push([url, options?.method || 'GET']);
    return response(200, url === '/api/auth/me' ? { id: 'alice-id', username: 'alice' } : []);
  });
  const ui = p.setup();
  await p.mounted[0]();
  assert.equal(ui.currentUser.value.username, 'alice');
  assert.deepEqual(calls.slice(0, 2).map(c => c[0]), ['/api/auth/me', '/api/sessions']);
  await ui.handleLogout();
  assert.deepEqual(calls[2], ['/api/auth/logout', 'POST']);
  assert.deepEqual(p.redirects, ['/login']);
  assert.equal(ui.currentUser.value, null);
  await assert.rejects(vm.runInContext("apiFetch('/api/sessions')", p.context), /登录/);
  assert.equal(calls.length, 3);
});

test('expired initialization does not load sessions', async () => {
  const calls = [];
  const p = page('chat', async url => { calls.push(url); return response(401, {}); });
  p.setup();
  await p.mounted[0]();
  assert.deepEqual(calls, ['/api/auth/me']);
  assert.deepEqual(p.redirects, ['/login?expired=1']);
});

test('memory opens, edits and saves with the original revision', async () => {
  const calls = [];
  const saved = { content: '原记忆', revision: 'version-1', updated_at: null };
  const p = page('chat', async (url, options) => {
    calls.push([url, options]);
    if (options?.method === 'PUT') {
      return response(200, { ...saved, content: '新记忆', revision: 'version-2' });
    }
    return response(200, saved);
  });
  const ui = p.setup();
  await ui.openMemory();
  assert.equal(ui.memoryVisible.value, true);
  assert.equal(ui.memoryDraft.value, '原记忆');
  ui.editMemory();
  ui.memoryDraft.value = '新记忆';
  await ui.saveMemory();
  assert.equal(calls[1][0], '/api/memory');
  assert.deepEqual(JSON.parse(calls[1][1].body), { content: '新记忆', revision: 'version-1' });
  assert.equal(ui.memoryContent.value, '新记忆');
  assert.equal(ui.memoryEditing.value, false);
  assert.match(ui.memoryNotice.value, /保存/);
});

test('cancel and closing a dirty memory editor never write', async () => {
  const calls = [];
  const p = page('chat', async (url, options) => {
    calls.push(options?.method || 'GET');
    return response(200, { content: '原记忆', revision: 'v1' });
  });
  const ui = p.setup();
  await ui.openMemory();
  ui.editMemory(); ui.memoryDraft.value = '草稿';
  ui.cancelMemoryEdit();
  assert.equal(ui.memoryDraft.value, '原记忆');
  ui.editMemory(); ui.memoryDraft.value = '新草稿';
  p.context.window.confirm = () => false;
  ui.closeMemory();
  assert.equal(ui.memoryVisible.value, true);
  assert.equal(ui.memoryDraft.value, '新草稿');
  p.context.window.confirm = () => true;
  ui.closeMemory();
  assert.equal(ui.memoryVisible.value, false);
  assert.deepEqual(calls, ['GET']);
});

test('memory save conflict retains draft and reload asks before discarding', async () => {
  let reads = 0;
  const p = page('chat', async (url, options) => {
    if (options?.method === 'PUT') return response(409, { detail: '记忆已被其他操作更新' });
    reads++;
    return response(200, { content: '服务器记忆', revision: 'v1' });
  });
  const ui = p.setup();
  await ui.openMemory(); ui.editMemory(); ui.memoryDraft.value = '保留我的草稿';
  await ui.saveMemory();
  assert.equal(ui.memoryDraft.value, '保留我的草稿');
  assert.equal(ui.memoryEditing.value, true);
  assert.match(ui.memoryError.value, /更新/);
  p.context.window.confirm = () => false;
  await ui.loadMemory();
  assert.equal(reads, 1);
  assert.equal(ui.memoryDraft.value, '保留我的草稿');
});

test('memory clear requires confirmation and restores returned template', async () => {
  const calls = [];
  const p = page('chat', async (url, options) => {
    calls.push(options?.method || 'GET');
    return response(200, { content: options?.method === 'DELETE' ? '# 用户记忆\n' : '原记忆', revision: 'v1' });
  });
  const ui = p.setup(); await ui.openMemory();
  p.context.window.confirm = () => false;
  await ui.clearMemory();
  assert.deepEqual(calls, ['GET']);
  p.context.window.confirm = () => true;
  await ui.clearMemory();
  assert.deepEqual(calls, ['GET', 'DELETE']);
  assert.equal(ui.memoryContent.value, '# 用户记忆\n');
  assert.match(ui.memoryNotice.value, /清空/);
});

test('memory cannot be edited or saved after initial load fails', async () => {
  let calls = 0;
  const p = page('chat', async () => { calls++; return response(503, { detail: '加载失败' }); });
  const ui = p.setup(); await ui.openMemory(); ui.editMemory(); await ui.saveMemory();
  assert.equal(calls, 1);
  assert.equal(ui.memoryEditing.value, false);
  assert.match(ui.memoryError.value, /加载失败/);
});

test('memory stays open and keeps the draft while a save is in progress', async () => {
  let finishSave;
  let writes = 0;
  const p = page('chat', async (url, options) => {
    if (options?.method === 'PUT') {
      writes++;
      return new Promise(resolve => { finishSave = resolve; });
    }
    return response(200, { content: '原记忆', revision: 'v1' });
  });
  const ui = p.setup(); await ui.openMemory(); ui.editMemory(); ui.memoryDraft.value = '草稿';
  const saving = ui.saveMemory();
  assert.equal(ui.memorySaving.value, true);
  ui.closeMemory(); ui.cancelMemoryEdit(); await ui.saveMemory();
  assert.equal(ui.memoryVisible.value, true);
  assert.equal(ui.memoryDraft.value, '草稿');
  assert.equal(writes, 1);
  finishSave(response(503, { detail: '保存失败，请重试' }));
  await saving;
  assert.equal(ui.memorySaving.value, false);
  assert.equal(ui.memoryDraft.value, '草稿');
  assert.match(ui.memoryError.value, /失败/);
});
