import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { Worker } from 'node:worker_threads';
import { createServer as createSocket } from 'node:net';
import { once } from 'node:events';
import { mkdirSync, writeFileSync } from 'node:fs';

const mode = process.argv[2] || '--all';
assert.ok(['--self-test','--geometry','--scroll','--navigation','--all'].includes(mode), 'Unknown mode');
const port = 3100;
const session = `viewport-${process.pid}`;
const output = `/tmp/knowledge-viewport-${process.pid}`;
mkdirSync(output, {recursive:true});
let child;
let backend;
let browserOpened = false;
const requests = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const ab = (...args) => execFileSync('agent-browser', ['--session',session,...args], {encoding:'utf8',timeout:30000});
const evaluate = js => ab('eval','-b',Buffer.from(js).toString('base64'));
const probe = js => JSON.parse(JSON.parse(evaluate(`JSON.stringify(${js})`).trim()));
const json = (response,status,value) => {response.writeHead(status, {'Content-Type':'application/json'});response.end(JSON.stringify(value));};
async function available() {
  const socket = createSocket();
  await new Promise((resolve,reject) => {socket.once('error',reject);socket.listen(port,'127.0.0.1',resolve);});
  await new Promise(resolve => socket.close(resolve));
}
const user = {id:'viewport-qa',username:'qa',display_name:'Viewport QA',role:'admin',active:true,preferred_locale:'zh-CN'};
try {
  await available();
  backend = new Worker(`
    const { parentPort } = require('node:worker_threads');
    const { createServer } = require('node:http');
    const requests = [];
    const user = ${JSON.stringify(user)};
    const json = ${json.toString()};
    const server = createServer((request,response) => {
    const path = new URL(request.url,'http://qa').pathname;
    requests.push({method:request.method,path});
    if (request.method === 'PATCH' && path === '/api/v1/account/preferences') {
      let body=''; request.on('data',chunk=>body+=chunk); request.on('end',()=>{
        try {const value=JSON.parse(body).preferred_locale;if(!['en','zh-CN'].includes(value))return json(response,400,{});user.preferred_locale=value;return json(response,200,user);}catch{return json(response,400,{});}
      });return;
    }
    if (request.method !== 'GET') return json(response,503,{detail:'QA mutation blocked'});
    if (path === '/api/v1/auth/me') return json(response,200,user);
    if (path === '/api/v1/knowledge-bases') return json(response,200,[{id:'qa',name:'隔离验收资料库',description:'',active:true}]);
    if (path === '/api/v1/conversations') return json(response,200,{items:[],has_more:false});
    if (path === '/health') return json(response,200,{status:'ok',dependencies:{}});
    return json(response,503,{detail:'QA unknown route blocked'});
  });
    server.listen(0,'127.0.0.1', () => parentPort.postMessage(server.address().port));
  `, {eval:true});
  const [mockPort] = await once(backend,'message');
  const mockBase = `http://127.0.0.1:${mockPort}`;
  assert.equal((await fetch(`${mockBase}/unknown`)).status,503);
  assert.equal((await fetch(`${mockBase}/api/v1/auth/me`,{method:'POST'})).status,503);
  // Dev is isolated: Next evaluates rewrites against the mock backend, never 8080.
  child = spawn('node',['node_modules/next/dist/bin/next','dev','--hostname','127.0.0.1','--port',String(port)],{cwd:'apps/web',env:{...process.env,RAG_API_BASE_URL:mockBase},stdio:['ignore','pipe','pipe'],detached:true});
  let logs = '';
  child.stdout.on('data',chunk => {logs += chunk;});
  child.stderr.on('data',chunk => {logs += chunk;});
  const deadline = Date.now()+90000;
  let ready = false;
  while(Date.now()<deadline) {
    if(child.exitCode !== null) throw new Error(`QA server exited ${child.exitCode}: ${logs.slice(-1500)}`);
    try {if ((await fetch(`http://127.0.0.1:${port}/rag-api/api/v1/auth/me`,{signal:AbortSignal.timeout(3000)})).status===200) {ready=true;break;}} catch {}
    await sleep(250);
  }
  assert.ok(ready,'QA readiness deadline exceeded');
  if(mode === '--self-test') {
    const failed = spawn(process.execPath,['-e','require("node:assert/strict").equal(1,2)'],{stdio:'ignore'});
    assert.notEqual((await once(failed,'exit'))[0],0,'Assertions must fail closed');
  } else {
    ab('open',`http://127.0.0.1:${port}`);browserOpened=true;
    ab('wait','.conversation-panel');
    if(mode==='--geometry'||mode==='--all') {
      for (const locale of ['zh-CN','en']) {
        if (probe('document.documentElement.lang') !== locale) {
          ab('click','.language-switch');ab('wait','--fn',`document.documentElement.lang==='${locale}'`);
        }
      for(const [width,height] of [[1440,900],[1280,800],[1001,800],[1000,800],[768,1024],[390,844],[360,800]]) {
        ab('set','viewport',String(width),String(height));
        ab('wait','--fn','!document.querySelector(".topbar")');
        ab('wait','--fn',`(() => {const node=document.querySelector('.viewport-workspace');const h=parseFloat(node.style.getPropertyValue('--workspace-height'));return Math.abs(h-(visualViewport.height+visualViewport.offsetTop-node.getBoundingClientRect().top))<2;})()`);
        const box = probe(`(() => {const r=document.querySelector('.conversation-panel').getBoundingClientRect(); const mast=document.querySelector('.site-masthead').getBoundingClientRect();return {top:r.top,bottom:r.bottom,mastBottom:mast.bottom,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth};})()`);
        assert.ok(Math.abs(box.top-box.mastBottom)<=2,JSON.stringify(box));
        assert.ok(Math.abs(box.bottom-box.height)<=2,JSON.stringify(box));
        assert.equal(box.overflow,false);
        const clipped = probe(`Array.from(document.querySelectorAll('.conversation-heading,.composer-zone,.composer,#question')).map(n=>({class:n.className,left:n.getBoundingClientRect().left,right:n.getBoundingClientRect().right})).filter(r=>r.left< -1||r.right>innerWidth+1)`);
        assert.deepEqual(clipped,[],'Conversation controls must not be clipped');
        ab('screenshot',`${output}/${locale}-${width}.png`);
      }
      }
    }
    if(mode==='--scroll'||mode==='--all') {
      ab('set','viewport','1280','800');
      ab('click','.workspace-show-evidence');
      ab('wait','--fn',"Math.abs(document.querySelector('.workspace-main').scrollTop-document.querySelector('.workspace-main').clientHeight)<3");
      ab('click','.workspace-return-chat');
      ab('wait','--fn',"document.querySelector('.workspace-main').scrollTop<3");
    }
    if(mode==='--navigation'||mode==='--all') {
      ab('set','viewport','360','800');
      const overlap = probe(`(() => {const a=document.querySelector('.workspace-nav-trigger').getBoundingClientRect(), b=document.querySelector('#question').getBoundingClientRect();return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;})()`);
      assert.equal(overlap,false,'Navigation overlaps composer');
      ab('click','.workspace-nav-trigger');ab('wait','dialog[open]');ab('press','Escape');
      assert.equal(probe(`!!document.querySelector('dialog[open]')`),false);
    }
    ab('screenshot',`${output}/workspace.png`);
    if (mode === '--all') execFileSync(process.execPath,['apps/web/tests/workspace-navigation.qa.mjs','--all'],{encoding:'utf8',timeout:90000,stdio:'pipe'});
  }
  writeFileSync(`${output}/report.json`,JSON.stringify({mode,requests},null,2));
  console.log(JSON.stringify({passed:true,mode,output,productionWrites:0}));
} finally {
  if(browserOpened) {try {ab('close');} catch {}}
  if(child) {
    try {process.kill(-child.pid,'SIGTERM');} catch {}
    if(child.exitCode===null) await Promise.race([once(child,'exit'),sleep(3000)]);
    try {process.kill(-child.pid,'SIGKILL');} catch {}
  }
  if(backend) await backend.terminate();
}
