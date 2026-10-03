// 动效录制：CDP screencast 抓帧 → 落盘 PNG 序列（供 gif.py 合成）
// 用法: node record.mjs <scenario> <outdir> [baseUrl]
//   scenario = citations | stamp
// 依赖：Chrome 已带 --remote-debugging-port=9222 起着。
import { writeFileSync, mkdirSync } from "node:fs";

const scenario = process.argv[2];
const outdir = process.argv[3];
const BASE = process.argv[4] ?? "http://localhost:3212";
mkdirSync(outdir, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ver = await (await fetch("http://127.0.0.1:9222/json/version")).json();
const ws = new WebSocket(ver.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
let frames = [];
const events = [];
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id);
    pending.delete(m.id);
    m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
  } else if (m.method === "Page.screencastFrame") {
    events.push({ t: Date.now(), data: m.params.data, sid: m.params.sessionId });
  }
});
await new Promise((r) => ws.addEventListener("open", r));
const send = (method, params = {}, sessionId) =>
  new Promise((resolve, reject) => {
    const msg = { id: ++id, method, params };
    if (sessionId) msg.sessionId = sessionId;
    pending.set(msg.id, { resolve, reject });
    ws.send(JSON.stringify(msg));
  });

const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
await send("Page.enable", {}, sessionId);
await send("Runtime.enable", {}, sessionId);
await send("Emulation.setFocusEmulationEnabled", { enabled: true }, sessionId);
await send("Emulation.setDeviceMetricsOverride",
  { width: 1440, height: 900, deviceScaleFactor: 2, mobile: false }, sessionId);
await send("Page.bringToFront", {}, sessionId);
await send("Page.navigate", { url: BASE + "/" }, sessionId);
await sleep(4000);

const evalJs = async (fn) => {
  const r = await send("Runtime.evaluate", { expression: `(${fn})()`, awaitPromise: true, returnByValue: true }, sessionId);
  return r.result?.value;
};
const has = (text) => evalJs(`() => document.body.innerText.includes(${JSON.stringify(text)})`);

// 登录（学生账号，用于真实办理链路）
await evalJs(`async () => {
  await fetch('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ email:'review@qtu.edu.cn', password:'reviewpass123' }) });
  return 'ok';
}`);
await send("Page.navigate", { url: BASE + "/" }, sessionId);
await sleep(4500);

// 等 composer 就绪
for (let i = 0; i < 30; i++) {
  const ready = await evalJs(`() => { const t = document.getElementById('composer-input'); return !!t && !!t.placeholder && !t.placeholder.startsWith('正在准备'); }`);
  if (ready) break;
  await sleep(500);
}

const typeSend = (text) => evalJs(`() => {
  const t = document.getElementById('composer-input');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(t, ${JSON.stringify(text)});
  t.dispatchEvent(new Event('input', { bubbles: true }));
  t.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  return 'sent';
}`);

const startCast = () => send("Page.startScreencast", { format: "jpeg", quality: 85, maxWidth: 1200, everyNthFrame: 1 }, sessionId);

// screencastFrame 需要 ack 才会继续推帧
const ackLoop = setInterval(async () => {
  const batch = events.splice(0, events.length);
  for (const f of batch) {
    frames.push(f);
    send("Page.screencastFrameAck", { sessionId: f.sid }, sessionId).catch(() => {});
  }
}, 40);

async function run() {
  if (scenario.startsWith("lab-")) {
    const which = scenario.slice(4); // stamp | citations | dot
    await send("Page.navigate", { url: BASE + "/motion-lab" }, sessionId);
    await sleep(2500);
    await evalJs(`() => { const el = document.querySelector('[data-motion="${which}"]'); el && el.scrollIntoView({block:'center'}); return !!el; }`);
    await sleep(600);
    await startCast();
    const clicked = await evalJs(`() => { const el = document.querySelector('[data-motion="${which}"]'); const b = el && [...el.querySelectorAll('button')].find(x=>x.textContent.trim()==='重播'); if (b) b.click(); return !!b; }`);
    console.log("lab click:", which, clicked);
    await sleep(2000);
  } else if (scenario === "live-trace") {
    // 活体轨迹：必须在提问前开录（轨迹出现那一刻动画已在跑）
    await startCast();
    await typeSend("我想转专业，同时在准备保研，课程绩点认定、学分替代和时间安排上分别要注意什么？");
    let mark = null;
    for (let i = 0; i < 400; i++) {
      if (await has("正在研究")) { mark = Date.now(); break; }
      await sleep(120);
    }
    console.log("live-trace mark:", mark ? "HIT" : "MISS");
    await sleep(6000);
  } else if (scenario === "citations") {
    await startCast();
    await typeSend("请假超过 7 天需要什么审批流程？");
    // 等「出处」出现 = 答案完成、溯源开始落定
    let mark = null;
    for (let i = 0; i < 200; i++) {
      if (await has("出处")) { mark = Date.now(); break; }
      await sleep(150);
    }
    console.log("citations mark at", mark ? "hit" : "MISS");
    await sleep(2200);
  } else if (scenario === "stamp") {
    await typeSend("帮我预约明天晚上的羽毛球馆，班级比赛用");
    // 等确认卡出现
    let found = false;
    for (let i = 0; i < 200; i++) {
      if (await has("待确认")) { found = true; break; }
      await sleep(150);
    }
    console.log("confirm card:", found);
    if (!found) console.log("BODY:", (await evalJs("() => document.body.innerText.slice(-400)")));
    await sleep(400);
    await startCast();
    await evalJs(`() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='确认办理'); if(b) b.click(); return !!b; }`);
    let mark = null;
    for (let i = 0; i < 400; i++) {
      if (await has("凭证号")) { mark = Date.now(); break; }
      await sleep(120);
    }
    console.log("receipt mark:", mark ? "HIT" : "MISS", "frames so far:", frames.length);
    if (!mark) console.log("BODY:", (await evalJs("() => document.body.innerText.slice(-400)")));
    await sleep(3000);
    console.log("after tail sleep, frames:", frames.length);
  }
}

await run();
clearInterval(ackLoop);
// 收尾：把剩余帧 flush 出来
await sleep(300);
const rest = events.splice(0, events.length);
for (const f of rest) frames.push(f);
await send("Page.stopScreencast", {}, sessionId).catch(() => {});

const t0 = frames.length ? frames[0].t : Date.now();
frames.forEach((f, i) => {
  writeFileSync(`${outdir}/f${String(i).padStart(4, "0")}.jpg`, Buffer.from(f.data, "base64"));
});
writeFileSync(`${outdir}/timeline.json`, JSON.stringify({ t0, frames: frames.map((f, i) => ({ i, dt: f.t - t0 })) }));
console.log(`saved ${frames.length} frames to ${outdir}`);
await send("Target.closeTarget", { targetId });
ws.close();
process.exit(0);
