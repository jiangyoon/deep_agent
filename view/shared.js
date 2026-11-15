// view/shared.js
// 登录页、对话页共用的认证请求与图标组件。

const { createApp, ref, computed, watch, nextTick, onMounted } = Vue;

let authenticationLost = false;

function redirectToLogin(expired) {
  if (authenticationLost) return;
  authenticationLost = true;
  window.location.replace(expired ? '/login?expired=1' : '/login');
}

async function apiFetch(url, options) {
  if (authenticationLost) {
    throw new Error('登录已过期，请重新登录');
  }
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  if (response.status === 401) {
    redirectToLogin(true);
    throw new Error('登录已过期，请重新登录');
  }
  return response;
}

// ------------------------------------------------------------
// 图标：不要声明名为 class 的 prop（保留字）。
// 外层写 class="w-5 h-5" 时，Vue 会透传到根节点 <svg>。
// ------------------------------------------------------------
function makeIcon(paths) {
  return {
    template: `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
           fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round">
        ${paths}
      </svg>
    `,
  };
}

const IconBrain = makeIcon(`
  <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/>
  <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/>
  <path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/>
  <path d="M17.599 6.5a3 3 0 0 0 .399-1.375"/>
  <path d="M6.003 5.125A3 3 0 0 0 6.401 6.5"/>
  <path d="M3.077 10.993A4 4 0 0 1 3 10"/>
  <path d="M21 10a4 4 0 0 1-.077.993"/>
  <path d="M12 20v2"/>
`);
const IconLoader = makeIcon(`<path d="M21 12a9 9 0 1 1-6.219-8.56"/>`);
const IconPlus = makeIcon(`<path d="M5 12h14"/><path d="M12 5v14"/>`);
const IconMessage = makeIcon(`
  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
`);
const IconDatabase = makeIcon(`
  <ellipse cx="12" cy="5" rx="9" ry="3"/>
  <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
  <path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3"/>
`);
const IconLogOut = makeIcon(`
  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
  <polyline points="16 17 21 12 16 7"/>
  <line x1="21" x2="9" y1="12" y2="12"/>
`);
const IconUser = makeIcon(`
  <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/>
  <circle cx="12" cy="7" r="4"/>
`);
const IconChevronDown = makeIcon(`<path d="m6 9 6 6 6-6"/>`);
const IconChevronRight = makeIcon(`<path d="m9 18 6-6-6-6"/>`);
const IconSend = makeIcon(`
  <path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>
`);
const IconSquare = makeIcon(`<rect width="18" height="18" x="3" y="3" rx="2"/>`);
const IconTrash = makeIcon(`
  <path d="M3 6h18"/>
  <path d="M8 6V4h8v2"/>
  <path d="M19 6l-1 14H6L5 6"/>
  <path d="M10 11v5"/>
  <path d="M14 11v5"/>
`);
