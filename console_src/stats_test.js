/*
 * stats.js <-> Python 交叉验证 (用法: node stats_test.js)
 *
 * 参考实现: tools/nanoammeter_capture.py 的 plot_only() (在 tools/ 里实跑 --replot)
 * 数据夹具: raw_09_11_+25.274nA.npz -> console_src/ref_data.json (wave_int/wave_ext/noise)
 * 参考值: 2026-09-12 本机 `python nanoammeter_capture.py --replot` 实跑得到, 与移植前记录逐位一致
 *
 * 判据 (按任务要求): 相关系数与拟合 k 吻合到 4 位小数; max|d| / 坏读数 / 饱和前缀 / 上升沿
 * 个数 整数完全相等; mean / rms / period 吻合到 2 位小数。此处再从严: r 与 k 取 6/5 位小数,
 * 上升沿列表逐元素比对 (个数相同但错位也能抓出来)。
 * 退出码: 0 = 全绿, 1 = 有任一项不符。stdout 全 ASCII, 避免 GBK 控制台乱码。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const S = require('./stats.js');

const FIXTURE = path.join(__dirname, 'ref_data.json');
if (!fs.existsSync(FIXTURE)) {
  console.error('missing fixture: ' + FIXTURE + ' (用 python 从 raw_*.npz 导出)');
  process.exit(2);
}
const ref = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
const wi = ref.wave_int, we = ref.wave_ext, ns = ref.noise;

/* 与固件/Python 同源的码域常量 (stats.js 不硬编码域常量, 由调用方给) */
const CODE_ZERO = 30787;       /* Python CODE_ZERO: 1.55V 电平移位零点 */
const RAIL_HI = 65520;         /* Python RAIL_HI: 内部通路实测正轨 */

/* ---------------- 精简断言器 ---------------- */
let nPass = 0, nFail = 0;
const rows = [];

function record(cond, label, jsVal, pyVal, note, long) {
  if (cond) nPass++; else nFail++;
  rows.push({ label: label, js: jsVal, py: pyVal, ok: !!cond, note: note || '', long: !!long });
}

function eqInt(label, jsVal, pyVal) {          /* 整数 / 布尔 完全相等 */
  record(jsVal === pyVal, label, jsVal, pyVal, 'exact');
}
function eqDp(label, jsVal, pyVal, dp) {       /* 四舍五入到 dp 位后相等 */
  const f = Math.pow(10, dp);
  const d = Math.abs(jsVal - pyVal);
  record(Math.round(jsVal * f) === Math.round(pyVal * f), label, jsVal, pyVal,
         'round ' + dp + 'dp  |diff|=' + d.toExponential(1));
}
function eqList(label, jsVal, pyVal) {         /* 数组逐元素相等 */
  const same = jsVal.length === pyVal.length && jsVal.every((v, i) => v === pyVal[i]);
  record(same, label, jsVal, pyVal, 'elementwise x' + pyVal.length, pyVal.join(',').length > 24);
}

/* 局部小工具: 模块 API 不含 min/max/mean/span, 仅为核对 Python 的 stats() 行 */
const minOf = a => a.reduce((m, v) => Math.min(m, v), Infinity);
const maxOf = a => a.reduce((m, v) => Math.max(m, v), -Infinity);
const meanOf = a => a.reduce((s, v) => s + v, 0) / a.length;

console.log('=== stats.js vs Python (plot_only) ===');
console.log('fixture: ' + FIXTURE);
console.log('len: wave_int=' + wi.length + '  wave_ext=' + we.length + '  noise=' + ns.length);
console.log('');

/* ---------------- 0. 夹具自检: Python stats() 打的三行 ---------------- */
/* 这几行参考输出没有对应的模块 API, 只在测试里核对夹具与参考值本身 */
eqInt('WAVE  min', minOf(wi), 2288);
eqInt('WAVE  max', maxOf(wi), 65520);
eqDp('WAVE  mean', meanOf(wi), 31417.9, 1);
eqInt('WAVE  span', maxOf(wi) - minOf(wi), 63232);
eqInt('WAVEX min', minOf(we), 2243);
eqInt('WAVEX max', maxOf(we), 65535);
eqDp('WAVEX mean', meanOf(we), 31454.2, 1);
eqInt('WAVEX span', maxOf(we) - minOf(we), 63292);
eqInt('NOISE min', minOf(ns), 65520);
eqInt('NOISE max', maxOf(ns), 65520);
eqDp('NOISE mean', meanOf(ns), 65520.0, 1);
eqInt('NOISE span', maxOf(ns) - minOf(ns), 0);

/* ---------------- 1. leadingSaturatedPrefix ---------------- */
/* Python: >>> WAVE[0]=65520  leading saturated prefix=38 */
eqInt('WAVE[0]', wi[0], 65520);
eqInt('lead saturated prefix', S.leadingSaturatedPrefix(wi), 38);
/* 边界/deault: >= 含等号; 65000 本身算饱和, 64999 不算 */
eqInt('leadPrefix default hi (>=)', S.leadingSaturatedPrefix([65000, 65520, 64999, 65520]), 2);
eqInt('leadPrefix none', S.leadingSaturatedPrefix([64999, 65000]), 0);

/* ---------------- 2. countBadReads ---------------- */
/* Python: WAVEX bad reads: 37 of 6250 (0.59%) */
const badCnt = S.countBadReads(we);
eqInt('WAVEX bad reads', badCnt, 37);
eqDp('WAVEX bad pct', 100.0 * badCnt / we.length, 0.59, 2);
eqInt('bad reads synthetic 0/0xFFFF', S.countBadReads([0, 1, 0xFFFF, 65534, 0]), 3);

/* ---------------- 3. diffStats (ext - int) ---------------- */
/* Python: ext-int (valid 6213 pts): mean=+36.34  rms=93.46  max|d|=370 */
const ds = S.diffStats(wi, we);
eqInt('diffStats n', ds.n, 6213);
eqInt('diffStats n = len - bad', ds.n, we.length - badCnt);
eqDp('diffStats mean', ds.mean, 36.34, 2);
eqDp('diffStats rms', ds.rms, 93.46, 2);
eqInt('diffStats maxAbs', ds.maxAbs, 370);
/* 坏读 (0 与 0xFFFF 两侧) 都要剔; 长度按短的截断 */
eqDp('diffStats drops both bad kinds', S.diffStats([10, 20, 30, 40], [0, 25, 0xFFFF, 50]).mean, 7.5, 10);
eqInt('diffStats truncates to shorter', S.diffStats([1, 2, 3], [1, 2]).n, 2);

/* ---------------- 4. correlation / linearFit ---------------- */
/* Python 在剔除坏读后的有效对上算: av, ev = a[~bad], e[~bad]。
 * correlation/linearFit 是纯数学函数, 不做剔除 -> 这里按 Python 的方式先配对过滤。 */
const av = [], ev = [];
for (let i = 0; i < Math.min(wi.length, we.length); i++) {
  if (we[i] !== 0 && we[i] !== 0xFFFF) { av.push(wi[i]); ev.push(we[i]); }
}
eqInt('valid pair count', av.length, 6213);

/* Python: correlation r=0.999986   fit ext=1.00044*int +22.55 */
const r = S.correlation(av, ev);
eqDp('correlation r', r, 0.999986, 4);      /* 任务判据: 4 位小数 */
eqDp('correlation r (tighter)', r, 0.999986, 6);
const fit = S.linearFit(av, ev);
eqDp('linearFit k', fit.k, 1.00044, 4);     /* 任务判据: 4 位小数 */
eqDp('linearFit k (tighter)', fit.k, 1.00044, 5);
eqDp('linearFit b (intercept)', fit.b, 22.55, 2);
/* 数值良态性: 未中心化的法方程在这组数据上只能给到 1.0010068 (差 5.7e-4, 4 位小数仍会露馅),
 * 这里把 k 框在中心化公式才有的窄区间里, 防止以后有人"简化"成未中心化写法 */
eqInt('linearFit conditioning', fit.k > 1.00043 && fit.k < 1.00045, true);
/* 平凡用例 */
eqInt('linearFit exact line k', S.linearFit([1, 2, 3, 4], [3, 5, 7, 9]).k, 2);
eqInt('linearFit exact line b', S.linearFit([1, 2, 3, 4], [3, 5, 7, 9]).b, 1);
eqInt('correlation perfect +1', S.correlation([1, 2, 3], [2, 4, 6]), 1);
eqInt('correlation perfect -1', S.correlation([1, 2, 3], [6, 4, 2]), -1);

/* ---------------- 5. risingEdges / edgePeriod ---------------- */
/* Python: WAVE rising edges=21  avg period=301.0 ticks (48.16 ms) */
const EDGES_PY = [0, 325, 622, 920, 1221, 1519, 1820, 2118, 2419, 2722, 3022,
                  3323, 3620, 3920, 4219, 4517, 4820, 5121, 5421, 5722, 6020];
const edges = S.risingEdges(wi, CODE_ZERO);
eqInt('rising edge count', edges.length, 21);
eqList('rising edge indices', edges, EDGES_PY);
eqDp('edgePeriod (ticks)', S.edgePeriod(wi, CODE_ZERO), 301.0, 2);
eqDp('edgePeriod (ms, 0.160 ms/tick)', S.edgePeriod(wi, CODE_ZERO) * 0.160, 48.16, 2);
/* 默认 hyst=2000 与显式 2000 等价; 显式别的 hyst 会改变结果 (参数确实生效) */
eqList('risingEdges default hyst', S.risingEdges(wi, CODE_ZERO), S.risingEdges(wi, CODE_ZERO, 2000));
eqInt('edgePeriod <2 edges -> 0', S.edgePeriod([100, 200, 300], 250), 0);
/* 滞回严格性: 32787 (== thresh+hyst) 不算沿, 28787 (== thresh-hyst) 不解除高态 */
eqList('hysteresis strictness', S.risingEdges([32788, 32787, 32787, 28787, 28786, 32788], CODE_ZERO), [0, 5]);
eqInt('edgePeriod single edge -> 0', S.edgePeriod([32788, 40000, 50000], CODE_ZERO), 0);

/* ---------------- 对照表 ---------------- */
function fmt(v) {
  if (Array.isArray(v)) return v.join(',');
  if (typeof v !== 'number') return String(v);
  if (!isFinite(v) || Number.isInteger(v)) return String(v);
  return String(Number(v.toPrecision(10)));   /* 压掉浮点尾巴, 仍是双精度原值 */
}
const WVAL = 24;                              /* 数值列宽上限, 超出的挪到表下 */
const cell = v => { const s = fmt(v); return (s.length > WVAL ? '<' + s.length + ' chars>' : s).padStart(WVAL); };
const wLabel = rows.reduce((m, x) => Math.max(m, x.label.length), 0);
const line = '-'.repeat(8 + wLabel + 2 + WVAL + 2 + WVAL + 2 + 14);
console.log('  ' + 'result'.padEnd(8) + 'item'.padEnd(wLabel + 2) +
            'python'.padStart(WVAL) + '  ' + 'js'.padStart(WVAL) + '   criterion');
console.log('  ' + line);
for (const x of rows) {
  console.log('  ' + (x.ok ? 'PASS' : 'FAIL').padEnd(8) + x.label.padEnd(wLabel + 2) +
              cell(x.py) + '  ' + cell(x.js) + '   ' + x.note);
}
if (rows.some(x => x.long)) {
  console.log('');
  console.log('  long values (kept out of the table):');
  for (const x of rows) {
    if (!x.long) continue;
    console.log('    ' + x.label + ' (' + (x.ok ? 'PASS' : 'FAIL') + ')');
    console.log('      py: ' + fmt(x.py));
    console.log('      js: ' + fmt(x.js));
  }
}
console.log('');
console.log('  full precision: r=' + r.toPrecision(15) + '  k=' + fit.k.toPrecision(15) +
            '  b=' + fit.b.toPrecision(15));
console.log('  full precision: mean=' + ds.mean.toPrecision(15) + '  rms=' + ds.rms.toPrecision(15) +
            '  period=' + S.edgePeriod(wi, CODE_ZERO));
console.log('');
console.log('  ' + nPass + ' passed, ' + nFail + ' failed');
console.log(nFail ? 'FAILED' : 'OK - js matches python on all checks');

process.exitCode = nFail ? 1 : 0;
