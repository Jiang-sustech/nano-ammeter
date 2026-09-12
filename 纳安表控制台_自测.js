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
const ctxStub = {
  fillStyle: '', strokeStyle: '', lineWidth: 0,
  setLineDash() {}, beginPath() {}, moveTo() {}, lineTo() {},
  stroke() {}, fillRect() {}, scale() {},
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
let els, currentPort, navDisconnectHandler;

function setup() {
  els = {};
  currentPort = null;
  navDisconnectHandler = null;
  const document = {
    getElementById(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; },
    createElement() { return makeEl('el' + (++elSeq)); },
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

/* 按固件 main.c UART_DumpWaveform 构造字节流:
 * "WAVE <count>\r\n" + count*2 小端 u16 + 2 字节校验和(样本和) + 尾部文本 */
function waveBytes(count, samples, trailingText) {
  const out = [];
  for (const ch of ('WAVE ' + count + '\r\n')) out.push(ch.charCodeAt(0));
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
  /* CSV 内容逐点校验 */
  els['btnCsv'].click();
  assert.strictEqual(BlobCapture.last._parts.join(''),
    'index,code\n0,1\n1,2\n2,3\n3,4\n4,5\n5,6\n6,7\n7,8\n');
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
  const hits = logText().match(/波形接收完成/g);
  assert.strictEqual(hits && hits.length, 2, '应完成两次波形接收');
  assert.strictEqual(els['screenState'].textContent, '+9.000 nA');
  els['btnCsv'].click();
  assert.strictEqual(BlobCapture.last._parts.join(''),
    'index,code\n0,5\n1,6\n2,7\n3,8\n', 'CSV 应为第二波数据');
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
