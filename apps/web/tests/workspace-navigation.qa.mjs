import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
let session = `knowledge-sidebar-${process.pid}`;
const dir = '/tmp/knowledge-sidebar-qa';
mkdirSync(dir, { recursive: true });
function ab(...args) { return execFileSync('agent-browser', ['--session', session, ...args], { encoding: 'utf8', timeout: 35000 }); }
function evaluate(js) { return ab('eval', '-b', Buffer.from(js).toString('base64')); }
function probe(js) { const output = evaluate(`JSON.stringify(${js})`); return JSON.parse(JSON.parse(output.trim())); }
const base = process.env.QA_BASE_URL || 'http://127.0.0.1:3100';
assert.match(base, /^http:\/\/127\.0\.0\.1:3100$/);
const results = [];
function checkAccountDismissal(menu, outside) {
  ab('click', `${menu} summary`);
  assert.equal(probe(`document.querySelector(${JSON.stringify(menu)}).open`), true);
  evaluate(`document.querySelector(${JSON.stringify(`${menu} .workspace-account-options`)}).dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, pointerType:'touch'}))`);
  assert.equal(probe(`document.querySelector(${JSON.stringify(menu)}).open`), true, 'An inside pointer must not dismiss the account menu');
  ab('click', outside);
  assert.equal(probe(`document.querySelector(${JSON.stringify(menu)}).open`), false, 'An outside pointer must dismiss the account menu');
}
try {
  ab('open', 'about:blank');
  const mock = (path, body) => ab('network', 'route', `**/rag-api${path}`, '--body', JSON.stringify(body));
  mock('/api/v1/auth/me', { id:'qa-sidebar', username:'qa', display_name:'Navigation QA', role:'admin', active:true, preferred_locale:'zh-CN' });
  mock('/api/v1/knowledge-bases', [{id:'qa',name:'测试资料库',description:'',active:true},{id:'qa2',name:'另一资料库',description:'',active:true}]);
  mock('/health', {status:'ok',dependencies:{}});
  mock('/api/v1/conversations/qa-history', {conversation:{id:'qa-history',knowledge_base_id:'qa',title:'隔离验收会话',turn_count:2},turns:[]});
  mock('/api/v1/conversations?*', {items:[{id:'qa-history',knowledge_base_id:'qa',title:'隔离验收会话',turn_count:2}],has_more:false});
  // Block any other API request: these tests must never write production data.
  await Promise.resolve();
  ab('open', base);
  ab('wait', '.workspace-sidebar');
  assert.equal(probe(`document.querySelectorAll('.service-pill').length`),1);
  evaluate(`window.fetch = ((original) => async (input, init) => { const url = String(input); if (url.includes('/rag-api/') && init?.method && init.method !== 'GET') return new Response(JSON.stringify({detail:'QA blocked mutation'}), {status:503,headers:{'Content-Type':'application/json'}}); return original(input,init); })(window.fetch.bind(window));`);
  for (const width of [1440, 1280, 768, 360, 1000, 1001]) {
    ab('set','viewport',String(width),'900');
    const state = probe(`({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,sidebar:getComputedStyle(document.querySelector('.workspace-sidebar')).display,trigger:getComputedStyle(document.querySelector('.workspace-nav-trigger')).display,columns:getComputedStyle(document.querySelector('.workspace-layout')).gridTemplateColumns})`);
    assert.equal(state.overflow, false);
    assert.equal(state.sidebar === 'none', width <= 1000);
    assert.equal(state.trigger === 'none', width > 1000);
    if (width <= 1000) assert.equal(probe(`getComputedStyle(document.querySelector('.workspace-nav-trigger')).position`),'fixed');
    results.push(state);
  }
  ab('set','viewport','1280','900');
  ab('wait','.workspace-sidebar .workspace-nav-history li button');
  const row = probe(`(() => {const b=document.querySelector('.workspace-sidebar .workspace-nav-history li button');const rect=b.getBoundingClientRect();const parts=[...b.children].map(n=>n.getBoundingClientRect());return {height:rect.height,aligned:parts.every(r=>Math.abs((r.top+r.bottom)/2-(rect.top+rect.bottom)/2)<3)};})()`);
  assert.ok(row.height <= 44 && row.aligned,'History icon, title and count must share one compact row');
  checkAccountDismissal('.workspace-sidebar .workspace-account-menu', '.conversation-heading h1');
  ab('set','viewport','360','800');
  ab('click','.workspace-nav-trigger');
  ab('wait','dialog[open]');
  assert.equal(probe(`document.activeElement.classList.contains('workspace-nav-close')`),true);
  ab('press','Shift+Tab');
  // Native Chromium dialogs may move reverse-Tab to browser chrome (BODY).
  // Background page controls must never receive focus; Tab must return inside.
  assert.equal(probe(`document.activeElement === document.body || !!document.activeElement.closest('dialog[open]')`),true);
  evaluate(`document.querySelector('.workspace-nav-trigger').focus()`);
  assert.equal(probe(`document.activeElement.classList.contains('workspace-nav-trigger')`),false);
  ab('press','Tab');
  assert.equal(probe(`document.activeElement.classList.contains('workspace-nav-close')`),true);
  ab('press','Escape');
  assert.equal(probe(`document.querySelector('dialog[open]') === null`),true);
  assert.equal(probe(`document.activeElement.classList.contains('workspace-nav-trigger')`),true);
  ab('click','.workspace-nav-trigger');
  ab('wait', '--fn', "document.getAnimations().every(animation => animation.playState === 'finished')");
  ab('screenshot',`${dir}/mobile.png`);
  checkAccountDismissal('dialog .workspace-account-menu', 'dialog .workspace-nav-heading h2');
  assert.equal(probe(`!!document.querySelector('dialog[open]')`), true, 'Dismissing the account menu must not close the navigation drawer');
  ab('click','dialog .workspace-nav-history li button');
  ab('wait','--fn', "!document.querySelector('dialog[open]')");
  assert.ok(ab('get','url').includes('conversation=qa-history'));
  ab('click','.workspace-nav-trigger');
  ab('click','dialog .workspace-nav-new');
  assert.ok(!ab('get','url').includes('conversation='));
  ab('click','.workspace-nav-trigger');
  ab('set','viewport','1280','900');
  ab('wait','--fn', "!document.querySelector('dialog[open]')");
  ab('screenshot',`${dir}/desktop.png`);
  const labels=probe(`Array.from(document.querySelectorAll('.workspace-sidebar a')).map(a=>a.getAttribute('href'))`);
  assert.ok(labels.includes('/account') && labels.includes('/admin/documents'));
  ab('close');
  session = `knowledge-sidebar-en-${process.pid}`;
  ab('open','about:blank');
  mock('/api/v1/auth/me', {id:'qa-user',username:'qa',display_name:'QA User',role:'user',active:true,preferred_locale:'en'});
  mock('/api/v1/knowledge-bases', [{id:'qa',name:'QA knowledge base',description:'',active:true}]);
  mock('/api/v1/conversations?*', {items:[],has_more:false});
  mock('/health', {status:'ok',dependencies:{}});
  ab('open',base);
  ab('wait','--fn', "document.documentElement.lang === 'en' && !!document.querySelector('.workspace-sidebar')");
  for (const width of [1440, 1280, 1001, 1000, 768, 360]) {
    ab('set','viewport',String(width),'900');
    assert.equal(probe(`document.documentElement.scrollWidth > innerWidth`),false);
    assert.equal(probe(`getComputedStyle(document.querySelector('.workspace-sidebar')).display === 'none'`),width <= 1000);
  }
  ab('set','viewport','1280','900');
  assert.equal(probe(`document.querySelectorAll('.service-pill').length`),0);
  assert.equal(probe(`document.querySelectorAll('.workspace-sidebar .workspace-nav-admin').length`),0);
  assert.equal(probe(`document.querySelector('.workspace-sidebar nav').getAttribute('aria-label')`),'Workspace navigation');
  evaluate(`window.fetch = async () => new Response(JSON.stringify({detail:'QA blocked mutation'}), {status:503,headers:{'Content-Type':'application/json'}})`);
  ab('click','.workspace-sidebar .workspace-account-menu summary');
  assert.equal(probe(`!!document.querySelector('.workspace-sidebar .workspace-account-menu[open]')`),true);
  ab('screenshot',`${dir}/account-menu.png`);
  ab('click','.workspace-sidebar .workspace-nav-account button');
  ab('wait','--fn', "!!document.querySelector('.workspace-nav-account [role=alert]')");
  assert.ok(probe(`document.querySelector('.workspace-nav-account [role=alert]').textContent`).includes('Sign-out failed'));
  console.log(JSON.stringify({passed:true,mode:process.argv[2]||'--all',results,drawerFocusAndEscape:true,artifacts:dir}));
  writeFileSync(`${dir}/report.json`,JSON.stringify({results,drawerFocusAndEscape:true},null,2));
} finally { ab('close'); }
