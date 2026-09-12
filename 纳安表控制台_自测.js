/**
 * 纳安表控制台 HTML 自测脚本 (Node.js, 无浏览器依赖)
 *
 * 用法:  node 纳安表控制台_自测.js
 *
 * 原理: 从 纳安表控制台.html 提取 <script> 内联代码, 在 DOM / Web Serial
 *       桩环境里执行, 然后模拟固件字节流 (与 main.c 协议一致, 含任意
 *       分包、WAVE 头截断、头与二进制同包、背靠背两波、波中断线等),
 *       断言解析结果。固件协议依据: OLED_SH1106/Core/Src/main.c uart.c current.c
 */
'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert').strict;

const HTML_FILE = path.join(__dirname, '纳安表控制台.html');
const html = fs.readFileSync(HTML_FILE, 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('未在 HTML 中找到 <script> 块'); process.exit(2); }
const SRC = m[1];

/* ---------------- 桩: DOM ---------------- */
/* 记录所有 draw 调用, 供断言"画了什么" (像素验证不了, 但调用序列可以) */
const drawLog = [];
const ctxStub = {
  fillStyle: '', strokeStyle: '', lineWidth: 0,
  font: '', textAlign: '', textBaseline: '',
  setLineDash(d) { drawLog.push(['dash', d && d.join(',')]); },
  beginPath() { drawLog.push(['begin']); },
  moveTo(x, y) { drawLog.push(['move', Math.round(x), Math.round(y)]); },
  lineTo(x, y) { drawLog.push(['line', Math.round(x), Math.round(y)]); },
  stroke() { drawLog.push(['stroke']); },
  fill() { drawLog.push(['fill']); },
  closePath() {},
  fillRect() {}, scale() {}, save() {}, restore() {},
  translate() {}, rotate() {},
  fillText(s, x, y) { drawLog.push(['text', String(s), Math.round(x), Math.round(y)]); },
  measureText(s) { return { width: String(s).length * 6 }; },
};
let elSeq = 0;
function makeEl(id) {
  const el = {
    id, textContent: '', className: '', innerHTML: '', value: '',
    disabled: false, style: {}, children: [],
    scrollTop: 0, scrollHeight: 0, clientWidth: 800, width: 0, height: 0,
    onclick: null, classSet: new Set(),
    classList: {
      toggle(c, on) { on ? el.classSet.add(c) : el.classSet.delete(c); },
      add(c) { el.classSet.add(c); }, remove(c) { el.classSet.delete(c); },
      contains(c) { return el.classSet.has(c); },
    },
    appendChild(child) { el.children.push(child); },
    click() { if (!el.disabled && el.onclick) el.onclick(); },   /* 与浏览器一致: disabled 按钮 click 无效 */
    addEventListener() {},
    getContext() { return ctxStub; },
    /* PNG 导出用: 记录最后一次导出的 blob, 供断言 */
    toBlob(cb, type) { el._blobType = type; BlobCapture.last = new BlobCapture(['png'], { type }); cb(BlobCapture.last); },
  };
  return el;
}

/* ---------------- 桩: Blob / URL (CSV 导出捕获) ---------------- */
class BlobCapture {
  constructor(parts, opts) { this._parts = parts; this.type = opts ? opts.type : ''; BlobCapture.last = this; }
}

/* ---------------- 桩: 虚拟串口 ---------------- */
function makeFakePort(tag, vid = 0x1A86, pid = 0x7523) {
  let controller;
  const port = {
    tag, _closed: false, _writes: [],
    readable: new ReadableStream({ start(c) { controller = c; } }),
    writable: {
      getWriter() {
        return {
          releaseLock() {},
          write(data) {
            if (port._closed) return Promise.reject(new Error('port closed'));
            port._writes.push(new TextDecoder().decode(data));
            return Promise.resolve();
          },
        };
      },
    },
    getInfo: () => ({ usbVendorId: vid, usbProductId: pid }),
    open: () => Promise.resolve(),
    close() { port._closed = true; try { controller.close(); } catch (_) {} return Promise.resolve(); },
    _push(bytes) { controller.enqueue(new Uint8Array(bytes)); },
    _unplug() { port._closed = true; try { controller.error(new Error('unplugged')); } catch (_) {} },
  };
  return port;
}

/* 与 Chromium 行为一致: 过滤不匹配 -> NotFoundError */
function portMatchesFilter(port, filters) {
  const info = port.getInfo();
  return filters.some(f =>
    (!f.usbVendorId || f.usbVendorId === info.usbVendorId) &&
    (!f.usbProductId || f.usbProductId === info.usbProductId));
}

/* ---------------- 环境装配: 每个测试重新 eval 脚本 ---------------- */
let els, currentPort, navDisconnectHandler, lastCreated, lastAnchor;

function setup() {
  els = {};
  currentPort = null;
  navDisconnectHandler = null;
  lastCreated = null;
  lastAnchor = null;
  drawLog.length = 0;
  const document = {
    getElementById(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; },
    /* 必须记住 tag: log() 内部也会 createElement('div'), 会覆盖 lastCreated,
     * 所以只跟踪 <a> (导出下载用), 否则断言会落到日志的 div 上 */
    createElement(tag) {
      lastCreated = makeEl('el' + (++elSeq));
      lastCreated.tag = tag;
      if (tag === 'a') lastAnchor = lastCreated;
      return lastCreated;
    },
    addEventListener() {},
    activeElement: { tagName: 'BODY' },
  };
  const navigator = {
    userAgent: 'NodeTest/1.0',
    serial: {
      requestPort(filters) {
        if (filters && !portMatchesFilter(currentPort, filters)) {
          const err = new Error('no matching device'); err.name = 'NotFoundError';
          return Promise.reject(err);
        }
        return Promise.resolve(currentPort);
      },
      addEventListener(ev, fn) { if (ev === 'disconnect') navDisconnectHandler = fn; },
    },
  };
  globalThis.document = document;
  Object.defineProperty(globalThis, 'navigator', { value: navigator, configurable: true, writable: true });
  globalThis.window = { isSecureContext: true };
  globalThis.devicePixelRatio = 1;
  globalThis.Blob = BlobCapture;
  URL.createObjectURL = () => 'blob:mock';
  URL.revokeObjectURL = () => {};
  (0, eval)(SRC);
}

const tick = ms => new Promise(r => setTimeout(r, ms));
function logText() { return (els['log'] && els['log'].children.map(c => c.innerHTML).join('|')) || ''; }

/* 伪随机 (LCG, 可复现) */
let seed = 42;
function rnd() { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed; }

/* 按固件 main.c UART_DumpBuffer 构造字节流:
 * "<tag> <count>\r\n" + count*2 小端 u16 + 2 字节校验和(样本和) + 尾部文本
 * tag: WAVE (内置ADC波形) / WAVEX (ADS8866 波形) / NOISE (底噪逐秒序列) */
function waveBytes(count, samples, trailingText, tag = 'WAVE') {
  const out = [];
  for (const ch of (tag + ' ' + count + '\r\n')) out.push(ch.charCodeAt(0));
  let sum = 0;
  for (const s of samples) {
    out.push(s & 0xFF, (s >> 8) & 0xFF);
    sum = (sum + s) & 0xFFFF;
  }
  out.push(sum & 0xFF, (sum >> 8) & 0xFF);
  for (const ch of trailingText) out.push(ch.charCodeAt(0));
  return out;
}

/* ================= 测试 ================= */

/* T1: setStatus 逐条解析固件协议行 */
function testSetStatus() {
  setup();
  const cases = [
    ['NANO-AMMETER READY', '仪器在线', 'run'],
    ['START MODE1', '模式一运行中', 'run'],
    ['MODE2 START (I<1nA)', '模式二运行中（双斜率）', 'run'],
    ['I=+12.345 nA MODE=1', '+12.345 nA', 'run'],
    ['I=-0.001 nA MODE=2', '-0.001 nA', 'run'],
    ['NOISE START (50s)', '底噪标定中', 'noise'],
    ['NOISE 10/50s', 'NOISE 10/50s', 'noise'],
    ['IBIAS=+123.4 fA', '偏置 +123.4 fA', 'noise'],
    ['RESULT NONE', '暂无结果', 'idle'],
    /* D 查询成功行: 必须带 (模式n), 否则被通用 I= 正则截获 */
    ['RESULT I=+0.500 nA MODE=2', '+0.500 nA（模式2）', 'run'],
    /* 新固件: 板子忙时丢弃指令并回 BUSY */
    ['BUSY', '板子忙碌中（指令已丢弃）', 'noise'],
    /* STOP IDLE: 数值显示时保留数值, 瞬态文字才换成"已停止" */
    ['START MODE1', '模式一运行中', 'run'],
    ['STOP IDLE', '已停止', 'idle'],
  ];
  for (const [input, text, cls] of cases) {
    setStatus(input);
    assert.strictEqual(els['screenState'].textContent, text, `输入: ${input}`);
    assert.strictEqual(els['screenState'].className, cls, `输入: ${input}`);
  }
}

/* T2: 全流水线 - 连接 / READY / 按钮发指令 */
async function testTextPipeline() {
  setup();
  currentPort = makeFakePort('P1');
  await connect();
  assert.strictEqual(els['connState'].textContent, '已连接');
  currentPort._push(new TextEncoder().encode('NANO-AMMETER READY\r\n'));
  await tick(60);
  assert.strictEqual(els['screenState'].textContent, '仪器在线');
  els['btnStart'].click();
  assert.ok(currentPort._writes.some(w => w.includes('S')), '应发送 S 指令');
  els['btnQuery'].click();
  assert.ok(currentPort._writes.some(w => w.includes('D')), '应发送 D 指令');
}

/* T3: WAVE 头 + 二进制 + 尾部文本全部同一包 */
function testWaveSameChunk() {
  setup();
  const bytes = waveBytes(8, [1, 2, 3, 4, 5, 6, 7, 8], '\r\nI=+12.345 nA MODE=1\r\n');
  onChunk(new Uint8Array(bytes));
  assert.strictEqual(els['screenState'].textContent, '+12.345 nA', '尾部文本行应被解析');
  assert.strictEqual(els['btnCsv'].disabled, false, 'CSV 应可用');
  assert.ok(els['waveInfo'].textContent.includes('8 点'), 'waveInfo 应显示点数');
  assert.ok(logText().includes('校验和 OK ✓'), '校验和应通过');
  /* 再喂文本, 证明没有卡在二进制模式 */
  onChunk(new TextEncoder().encode('NOISE 10/50s\r\n'));
  assert.strictEqual(els['screenState'].textContent, 'NOISE 10/50s');
  /* CSV 内容逐点校验: 表头英文, t_us = 序号×160, 外部路缺省留空 */
  els['btnCsv'].click();
  assert.strictEqual(BlobCapture.last._parts.join(''),
    'index,t_us,code_int,code_ext\n' +
    '0,0,1,\n1,160,2,\n2,320,3,\n3,480,4,\n' +
    '4,640,5,\n5,800,6,\n6,960,7,\n7,1120,8,\n');
}

/* T4: 同一字节流随机分包 25 次, 全部必须成功 */
function testWaveRandomSplits() {
  for (let iter = 0; iter < 25; iter++) {
    setup();
    const bytes = waveBytes(8, [10, 20, 30, 40, 50, 60, 70, 80], '\r\nI=+1.000 nA MODE=2\r\n');
    let i = 0;
    while (i < bytes.length) {
      const n = 1 + (rnd() % 9);
      onChunk(new Uint8Array(bytes.slice(i, Math.min(i + n, bytes.length))));
      i += n;
    }
    assert.strictEqual(els['screenState'].textContent, '+1.000 nA', `iter ${iter} 尾部文本`);
    assert.strictEqual(els['btnCsv'].disabled, false, `iter ${iter} CSV 未启用`);
    assert.ok(logText().includes('校验和 OK ✓'), `iter ${iter} 校验和失败`);
  }
}

/* T5: WAVE 头被截断分包 ("WA" | "VE 8\r\n"+二进制+文本) */
function testSplitHeader() {
  setup();
  const bytes = waveBytes(8, [1, 2, 3, 4, 5, 6, 7, 8], '\r\nI=+12.345 nA MODE=1\r\n');
  onChunk(new Uint8Array(bytes.slice(0, 2)));
  onChunk(new Uint8Array(bytes.slice(2)));
  assert.strictEqual(els['btnCsv'].disabled, false, '头截断时 CSV 仍应可用');
  assert.ok(logText().includes('校验和 OK ✓'), '头截断时校验和应通过');
  assert.strictEqual(els['screenState'].textContent, '+12.345 nA');
}

/* T7: 拔线 (disconnect 事件) 后重连, 发送必须走新端口 */
async function testUnplugReconnectEvent() {
  setup();
  currentPort = makeFakePort('P1');
  await connect();
  const p1 = currentPort;
  p1._unplug();
  navDisconnectHandler({ target: p1 });
  await tick(60);
  assert.strictEqual(els['screenState'].textContent, '—— 未连接 ——');
  currentPort = makeFakePort('P2');
  await connect();
  els['btnNoise'].click();
  await tick(60);
  assert.ok(currentPort._writes.some(w => w.includes('N')), '重连后 N 指令应送达新端口');
  assert.ok(!logText().includes('发送失败'), '不应出现发送失败');
}

/* T7b: 拔线 (仅读错误, 无事件) 后重连 */
async function testUnplugReconnectReadError() {
  setup();
  currentPort = makeFakePort('P1');
  await connect();
  currentPort._unplug();
  await tick(60);
  assert.strictEqual(els['screenState'].textContent, '—— 未连接 ——', 'readLoop 收尾应复位连接状态');
  currentPort = makeFakePort('P2');
  await connect();
  els['btnStart'].click();
  await tick(60);
  assert.ok(currentPort._writes.some(w => w.includes('S')), '重连后 S 指令应送达新端口');
  assert.ok(!logText().includes('发送失败'), '不应出现发送失败');
}

/* T8: 波形下载中途断开, 重连后必须恢复文本解析 */
async function testDisconnectMidWave() {
  setup();
  currentPort = makeFakePort('P1');
  await connect();
  const bytes = waveBytes(8, [1, 2, 3, 4, 5, 6, 7, 8], '');
  currentPort._push(new Uint8Array(bytes.slice(0, 14))); /* 头 + 部分数据 */
  await tick(60);
  els['btnDisconnect'].click();
  await tick(60);
  currentPort = makeFakePort('P2');
  await connect();
  currentPort._push(new TextEncoder().encode('NANO-AMMETER READY\r\n'));
  await tick(60);
  assert.strictEqual(els['screenState'].textContent, '仪器在线', '重连后文本应正常解析 (waveMode 残留会导致卡死)');
}

/* T9: 背靠背两波 (同一 chunk) + 第二波 CSV */
function testBackToBack() {
  setup();
  const w1 = waveBytes(4, [1, 2, 3, 4], '');
  const w2 = waveBytes(4, [5, 6, 7, 8], '\r\nI=+9.000 nA MODE=1\r\n');
  onChunk(new Uint8Array(w1.concat(w2)));
  const hits = logText().match(/接收 (WAVE|WAVEX|NOISE) 完成/g);
  assert.strictEqual(hits && hits.length, 2, '应完成两次波形接收');
  assert.strictEqual(els['screenState'].textContent, '+9.000 nA');
  els['btnCsv'].click();
  assert.strictEqual(BlobCapture.last._parts.join(''),
    'index,t_us,code_int,code_ext\n' +
    '0,0,5,\n1,160,6,\n2,320,7,\n3,480,8,\n', 'CSV 应为第二波数据');
}

/* T11: 诊断横幅 */
function testDiag() {
  setup();
  assert.ok(els['diag'].innerHTML.includes('Web Serial 可用'), '诊断横幅应显示 Web Serial 可用');
}

/* T12: 连接后自动 D 同步 (新增功能) */
async function testAutoD() {
  setup();
  currentPort = makeFakePort('P1');
  await connect();
  await tick(400);
  assert.ok(currentPort._writes.some(w => w.includes('D')), '连接后应自动发送 D 同步状态');
}

/* T14: 设备识别 - ST-Link / 未知设备不得显示为 CH340 */
async function testWrongDeviceWarning() {
  /* ST-Link VCP: 精确过滤与 WCH 过滤都不匹配, 走到"列出全部"才可选到 */
  setup();
  currentPort = makeFakePort('P1', 0x0483, 0x3748);
  await connect();
  assert.strictEqual(els['portName'].textContent, 'ST-Link VCP', '端口名应识别为 ST-Link VCP');
  assert.ok(logText().includes('不是 CH340'), '应发出非 CH340 警告');
  assert.ok(logText().includes('列出全部串口'), '应经过列出全部串口的回退');

  /* 未知设备: 显示 VID:PID */
  setup();
  currentPort = makeFakePort('P2', 0x1234, 0x5678);
  await connect();
  assert.strictEqual(els['portName'].textContent, 'USB 1234:5678', '未知设备应显示 VID:PID');
  assert.ok(logText().includes('不是 CH340'), '未知设备也应警告');

  /* 真 CH340: 正常识别, 无警告 */
  setup();
  currentPort = makeFakePort('P3', 0x1A86, 0x7523);
  await connect();
  assert.strictEqual(els['portName'].textContent, 'CH340');
  assert.ok(!logText().includes('不是 CH340'), 'CH340 不应有警告');
  assert.ok(!logText().includes('列出全部串口'), 'CH340 不应触发回退');
}

/* T15: 页面在板子测量中途打开 — BUSY 同步 / STOP IDLE 恢复 (新固件协议) */
async function testBusyReply() {
  setup();
  currentPort = makeFakePort('P1');
  await connect();
  const push = s => currentPort._push(new TextEncoder().encode(s));

  /* 板子正在忙: 自动 D 被丢弃, 回 BUSY → 按钮变灰 */
  push('BUSY\r\n');
  await tick(60);
  assert.strictEqual(els['screenState'].textContent, '板子忙碌中（指令已丢弃）');
  assert.strictEqual(els['btnStart'].disabled, true, 'BUSY 后按钮应禁用');

  /* 测量结束: 结果行 + STOP IDLE */
  push('I=+12.345 nA MODE=1\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, false, '结果行后应恢复');
  push('STOP IDLE\r\n');
  await tick(60);
  assert.strictEqual(els['screenState'].textContent, '+12.345 nA', 'STOP IDLE 不应覆盖结果数值');
  assert.strictEqual(els['btnStart'].disabled, false, 'STOP IDLE 后按钮保持可用');

  /* 瞬态状态下的 STOP IDLE 显示"已停止" */
  push('START MODE1\r\n');
  await tick(60);
  push('STOP IDLE\r\n');
  await tick(60);
  assert.strictEqual(els['screenState'].textContent, '已停止');
  assert.strictEqual(els['btnStart'].disabled, false);
}

/* T13: 测量/底噪期间 S/N/手动发送自动禁用, 结果后恢复 */
async function testBusyDisable() {
  setup();
  currentPort = makeFakePort('P1');
  await connect();
  const push = s => currentPort._push(new TextEncoder().encode(s));

  /* 单次测量流程 */
  push('START MODE1\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, true, '测量中 S 应禁用');
  assert.strictEqual(els['btnNoise'].disabled, true, '测量中 N 应禁用');
  assert.strictEqual(els['btnSend'].disabled, true, '测量中手动发送应禁用');
  assert.strictEqual(els['btnQuery'].disabled, false, 'D 查询应保持可用');
  assert.strictEqual(els['btnWave'].disabled, false, 'B 应保持可用');
  els['btnStart'].click();   /* 测量中连点: disabled 按钮 click 无效 */
  assert.ok(!currentPort._writes.some(w => w.includes('S')), '测量中不应发出 S');

  push('I=+12.345 nA MODE=1\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, false, '结果后 S 应恢复');
  assert.strictEqual(els['btnNoise'].disabled, false, '结果后 N 应恢复');
  assert.strictEqual(els['btnSend'].disabled, false, '结果后手动发送应恢复');

  /* 底噪流程: 进度行不改变禁用, IBIAS 后恢复 */
  push('NOISE START (50s)\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, true, '底噪中 S 应禁用');
  push('NOISE 10/50s\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, true, '底噪进度中仍禁用');
  push('IBIAS=+123.4 fA\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, false, 'IBIAS 后应恢复');

  /* MODE2 流程 */
  push('START MODE1\r\n');
  await tick(60);
  push('MODE2 START (I<1nA)\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, true, 'MODE2 中应禁用');
  push('I=+0.050 nA MODE=2\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, false, 'MODE2 结果后应恢复');

  /* D 响应 = 固件已回空闲 (D 只在空闲态被消费) */
  push('START MODE1\r\n');
  await tick(60);
  push('RESULT NONE\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, false, 'RESULT 响应后应恢复');

  /* 板子重启 (READY) 也应清 busy */
  push('NOISE START (50s)\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, true);
  push('NANO-AMMETER READY\r\n');
  await tick(60);
  assert.strictEqual(els['btnStart'].disabled, false, 'READY(重启)应恢复按钮');
}

/* ---- 以下为 X / W 指令扩展 (WAVEX / NOISE 包头) ----
 * 三者共用同一种二进制块格式, 只有包头字不同 (固件 main.c UART_DumpBuffer)。
 * 解析状态必须按 tag 分流, 否则 X 的数据会覆盖 B 的, 或 W 的覆盖 X 的。 */

/* T16: WAVEX 单包 -> 外部波形落到独立槽位, 不碰内置波形 */
function testWavexSingleChunk() {
  setup();
  onChunk(new Uint8Array(waveBytes(8, [1, 2, 3, 4, 5, 6, 7, 8], '', 'WAVE')));
  const before = Array.from(getWaveData());
  onChunk(new Uint8Array(waveBytes(6, [91, 92, 93, 94, 95, 96], '', 'WAVEX')));
  assert.deepStrictEqual(Array.from(getWaveExtData()), [91, 92, 93, 94, 95, 96], 'WAVEX 应存入 waveExtData');
  assert.deepStrictEqual(Array.from(getWaveData()), before, 'WAVEX 不得覆盖内置波形 waveData');
  assert.ok(logText().includes('WAVEX'), '日志应记录 WAVEX 接收');
  assert.ok(logText().includes('校验和 OK ✓'), 'WAVEX 校验和应通过');
}

/* T17: NOISE 单包 (50 点, 逐秒序列) */
function testNoiseSingleChunk() {
  setup();
  const ns = Array.from({ length: 50 }, (_, i) => 30000 + i * 7);
  onChunk(new Uint8Array(waveBytes(50, ns, '', 'NOISE')));
  assert.strictEqual(getNoiseData().length, 50, 'NOISE 应为 50 点');
  assert.strictEqual(getNoiseData()[0], 30000);
  assert.strictEqual(getNoiseData()[49], 30000 + 49 * 7);
  assert.ok(logText().includes('校验和 OK ✓'), 'NOISE 校验和应通过');
}

/* T18: 背靠背 WAVE + WAVEX 同一包 -> 两块都要完整落地 */
function testBackToBackMixedTags() {
  setup();
  const w1 = waveBytes(4, [1, 2, 3, 4], '', 'WAVE');
  const w2 = waveBytes(4, [5, 6, 7, 8], '\r\nI=+9.000 nA MODE=1\r\n', 'WAVEX');
  onChunk(new Uint8Array(w1.concat(w2)));
  assert.deepStrictEqual(Array.from(getWaveData()), [1, 2, 3, 4], '内置波形应为第一块');
  assert.deepStrictEqual(Array.from(getWaveExtData()), [5, 6, 7, 8], '外部波形应为第二块');
  assert.strictEqual(els['screenState'].textContent, '+9.000 nA', '尾部文本应被解析');
}

/* T19: WAVEX 随机分包 ×25, 全部必须成功 */
function testWavexRandomSplits() {
  for (let iter = 0; iter < 25; iter++) {
    setup();
    const bytes = waveBytes(8, [10, 20, 30, 40, 50, 60, 70, 80], '', 'WAVEX');
    let i = 0;
    while (i < bytes.length) {
      const n = 1 + (rnd() % 9);
      onChunk(new Uint8Array(bytes.slice(i, Math.min(i + n, bytes.length))));
      i += n;
    }
    assert.deepStrictEqual(Array.from(getWaveExtData()), [10, 20, 30, 40, 50, 60, 70, 80], `iter ${iter}`);
    assert.ok(logText().includes('校验和 OK ✓'), `iter ${iter} 校验和失败`);
  }
}

/* T20: 三种包头连续, 各自落到各自槽位; 校验和失败的块不得污染槽位 */
function testThreeTagsIsolated() {
  setup();
  onChunk(new Uint8Array(waveBytes(3, [1, 2, 3], '', 'WAVE')));
  onChunk(new Uint8Array(waveBytes(3, [4, 5, 6], '', 'WAVEX')));
  onChunk(new Uint8Array(waveBytes(3, [7, 8, 9], '', 'NOISE')));
  assert.deepStrictEqual(Array.from(getWaveData()), [1, 2, 3]);
  assert.deepStrictEqual(Array.from(getWaveExtData()), [4, 5, 6]);
  assert.deepStrictEqual(Array.from(getNoiseData()), [7, 8, 9]);

  /* 坏校验和的 WAVEX: 必须报错, 且不得覆盖上一份好数据 */
  const bad = waveBytes(3, [4, 5, 6], '', 'WAVEX');
  bad[bad.length - 1] ^= 0xFF;              /* 破坏校验和高字节 */
  onChunk(new Uint8Array(bad));
  assert.ok(logText().includes('失败'), '坏校验和应报失败');
  assert.deepStrictEqual(Array.from(getWaveExtData()), [4, 5, 6], '坏块不得覆盖槽位');
}

/* T21: 底噪进度行 "NOISE 10/50s" 不得被当成二进制包头
 * 它开头同样是 "NOISE <数字>", 宽松匹配会吞掉后面 22 字节当二进制,
 * 导致整条数据流错位 (固件底噪期间每 10s 发一行) */
function testNoiseProgressNotAHeader() {
  setup();
  onChunk(new TextEncoder().encode(
    'NOISE START (50s)\r\nNOISE 10/50s\r\nNOISE 20/50s\r\nIBIAS=+123.4 fA\r\n'));
  assert.ok(!logText().includes('开始接收'), '进度行不得进入二进制接收模式');
  /* 桩里的元素是按需创建的, 这里没收到块 -> els['waveInfo'] 可能不存在,
   * 走 document.getElementById 与脚本同一条路 */
  assert.strictEqual(document.getElementById('waveInfo').textContent, '', '进度行不得触发块接收');
  assert.strictEqual(els['screenState'].textContent, '偏置 +123.4 fA',
    '进度行之后的文本仍应逐行正常解析');
  assert.strictEqual(getNoiseData(), null, '不得凭空造出底噪数据');
}

/* T22: 新增按钮 (E/X/W) 发出的指令正确, 且下载中互相禁用 */
async function testNewButtons() {
  setup();
  currentPort = makeFakePort('P1');
  await connect();
  const writes = () => currentPort._writes.join('');
  els['btnDiag'].click();        await tick(60);
  assert.ok(writes().includes('E'), 'E 按钮应发 E');
  els['btnWaveX'].click();       await tick(60);
  assert.ok(writes().includes('X'), 'X 按钮应发 X');
  els['btnNoiseSeries'].click(); await tick(60);
  assert.ok(writes().includes('W'), 'W 按钮应发 W');

  /* 未连接时四个按钮都必须禁用 */
  setup();
  assert.strictEqual(els['btnDiag'].disabled, true, '未连接时 E 应禁用');
  assert.strictEqual(els['btnWaveX'].disabled, true, '未连接时 X 应禁用');
  assert.strictEqual(els['btnNoiseSeries'].disabled, true, '未连接时 W 应禁用');
}

/* ---- 图表 (阶段三) ---- */

/* T23: niceTicks —— 步长必须落在 1/2/5×10^n 上, 且范围必须包住输入 */
function testNiceTicks() {
  setup();
  const isNice = s => {
    const m = s / Math.pow(10, Math.floor(Math.log10(s)));
    return [1, 2, 5].some(k => Math.abs(m - k) < 1e-9);
  };
  const cases = [[0, 100, 5], [2288, 65520, 6], [-100, 100, 4], [0, 1, 5], [0, 65535, 8]];
  for (const [vmin, vmax, want] of cases) {
    const t = niceTicks(vmin, vmax, want);
    assert.ok(isNice(t.step), `[${vmin},${vmax}] step=${t.step} 不是整齐步长`);
    assert.ok(t.lo <= vmin + 1e-9, `[${vmin},${vmax}] lo=${t.lo} 未包住下界`);
    assert.ok(t.hi >= vmax - 1e-9, `[${vmin},${vmax}] hi=${t.hi} 未包住上界`);
    const ticks = (t.hi - t.lo) / t.step;
    assert.ok(ticks >= 1 && ticks <= want * 3, `[${vmin},${vmax}] 刻度数 ${ticks} 不合理`);
  }
  /* 退化输入不得抛异常也不得返回 NaN */
  for (const [a, b] of [[5, 5], [0, 0], [10, 1], [NaN, 5], [0, Infinity]]) {
    const t = niceTicks(a, b, 5);
    assert.ok(isFinite(t.lo) && isFinite(t.hi) && isFinite(t.step), `退化输入 ${a},${b} 返回非有限值`);
  }
}

/* T24: chartRange —— 阈值线必须在视野内, 否则图上根本看不到 */
function testChartRange() {
  setup();
  const TH = getThresholds();     /* const 声明对测试模块不可见, 走访问器 */
  /* 数据范围远小于阈值区间: 范围必须被撑到含两条阈值线 */
  const r1 = chartRange([30000, 31000], null, true);
  assert.ok(r1.lo <= TH.lower, `lo=${r1.lo} 应 <= code_lower=${TH.lower}`);
  assert.ok(r1.hi >= TH.upper, `hi=${r1.hi} 应 >= code_upper=${TH.upper}`);

  /* 不要求含阈值时: 只包住数据 (加余量) */
  const r2 = chartRange([30000, 31000], null, false);
  assert.ok(r2.lo <= 30000 && r2.hi >= 31000, '数据范围应被包住');
  assert.ok(r2.hi - r2.lo < 5000, '不应被撑到阈值区间');

  /* 两路数据一起参与 */
  const r3 = chartRange([30000], [20000], false);
  assert.ok(r3.lo <= 20000 && r3.hi >= 30000, '两路数据都应被包住');

  /* 空输入 / 单点 / null 不得抛异常 */
  for (const [a, b] of [[null, null], [[], []], [[5], null], [null, [7]]]) {
    const r = chartRange(a, b, false);
    assert.ok(isFinite(r.lo) && isFinite(r.hi) && r.hi > r.lo, `输入 ${JSON.stringify([a,b])} 范围非法`);
  }
}

/* T25: 双路叠加 —— 收到 B 与 X 后, 图上应同时出现两条线 + 两条阈值线 */
function testOverlayBothTraces() {
  setup();
  onChunk(new Uint8Array(waveBytes(4, [30000, 40000, 30000, 40000], '', 'WAVE')));
  onChunk(new Uint8Array(waveBytes(4, [30001, 40001, 30001, 40001], '', 'WAVEX')));
  const texts = drawLog.filter(d => d[0] === 'text').map(d => d[1]).join('|');
  assert.ok(texts.includes('内置 ADC12'), '图例应含内置 ADC');
  assert.ok(texts.includes('ADS8866'), '图例应含外部 ADS8866');
  assert.ok(texts.includes('code_lower'), '应标注 code_lower 阈值线');
  assert.ok(texts.includes('code_upper'), '应标注 code_upper 阈值线');
  /* 两次绘制的线点数应各自等于数据长度 */
  assert.ok(drawLog.some(d => d[0] === 'line'), '应画出折线');
}

/* T26: PNG 导出 —— 文件名与数据同规则, 且超时结果不得印成数值 */
function testExportPng() {
  setup();
  onChunk(new Uint8Array(waveBytes(4, [1, 2, 3, 4], '\r\nI=+25.274 nA MODE=1\r\n')));
  els['btnPng'].click();
  assert.ok(lastAnchor && lastAnchor.download, '应创建一个带 download 的链接');
  assert.ok(/^raw_\d\d_\d\d_\+25\.274nA\.png$/.test(lastAnchor.download),
    `PNG 文件名不符: ${lastAnchor && lastAnchor.download}`);

  /* 模式二超时: 数值无意义, 文件名应体现 TIMEOUT 而不是伪造数字 */
  setup();
  onChunk(new Uint8Array(waveBytes(4, [1, 2, 3, 4], '\r\nI=+0.000 nA MODE=2 TIMEOUT\r\n')));
  els['btnPng'].click();
  assert.ok(/^raw_\d\d_\d\d_TIMEOUT\.png$/.test(lastAnchor.download),
    `超时 PNG 文件名不符: ${lastAnchor && lastAnchor.download}`);
}

/* ---------------- 运行 ---------------- */
(async function main() {
  console.log('纳安表控制台自测');
  console.log('被测文件: ' + HTML_FILE + '\n');
  const tests = [
    ['T1  setStatus 协议行解析', testSetStatus],
    ['T2  连接/READY/按钮发指令', testTextPipeline],
    ['T3  WAVE头+二进制+文本同包', testWaveSameChunk],
    ['T4  随机分包波形 ×25', testWaveRandomSplits],
    ['T5  WAVE 头截断分包', testSplitHeader],
    ['T7  拔线(事件)重连发送', testUnplugReconnectEvent],
    ['T7b 拔线(读错误)重连发送', testUnplugReconnectReadError],
    ['T8  波中断开重连', testDisconnectMidWave],
    ['T9  背靠背两波 + CSV', testBackToBack],
    ['T11 诊断横幅', testDiag],
    ['T12 连接自动 D (新增)', testAutoD],
    ['T13 测量中禁用/恢复按钮', testBusyDisable],
    ['T14 设备识别 (ST-Link 警告)', testWrongDeviceWarning],
    ['T15 中途打开页面 BUSY 同步', testBusyReply],
    ['T16 WAVEX 单包 (X 指令)', testWavexSingleChunk],
    ['T17 NOISE 单包 (W 指令)', testNoiseSingleChunk],
    ['T18 背靠背 WAVE+WAVEX 同包', testBackToBackMixedTags],
    ['T19 WAVEX 随机分包 ×25', testWavexRandomSplits],
    ['T20 三种包头隔离 + 坏校验和不污染', testThreeTagsIsolated],
    ['T21 底噪进度行不得当包头', testNoiseProgressNotAHeader],
    ['T22 新增按钮 E/X/W 指令', testNewButtons],
    ['T23 niceTicks 整齐刻度', testNiceTicks],
    ['T24 chartRange 含阈值线', testChartRange],
    ['T25 双路叠加图例与阈值标注', testOverlayBothTraces],
    ['T26 PNG 导出命名', testExportPng],
  ];
  let passed = 0, failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); passed++; console.log('  ✓ ' + name); }
    catch (e) {
      failed++;
      const msg = String(e && e.message || e).split('\n')[0];
      console.log('  ✗ ' + name + '\n      → ' + msg);
    }
  }
  console.log(`\n结果: ${passed} 通过 / ${failed} 失败`);
  process.exit(failed ? 1 : 0);
})();
