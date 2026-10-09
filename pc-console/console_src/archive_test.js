/**
 * archive.js 纯函数模块测试 (Node.js, 无浏览器依赖)
 *
 * 用法:  node archive_test.js        (退出码 0 = 全过, 1 = 有失败)
 *
 * 重点不是"函数能跑", 而是 CSV 的硬约束: 纯 ASCII、无 BOM、无 \r、
 * 无千分位/单位后缀/科学计数法、末尾有换行 —— 这几条错了 MATLAB
 * readmatrix 和 Origin 就会读成乱码或直接解析失败。
 */
'use strict';

const fs = require('fs');
const path = require('path');

const A = require('./archive.js');

/* ---------------- 断言工具 ---------------- */

/* 逐字符比对: 报出第一个不同的字符位置与码点, 比 assert.strictEqual 的
 * 整体字符串 diff 更容易看出是多了空格还是少了符号 */
function assertCharsEqual(actual, expected, label) {
  if (typeof actual !== 'string') {
    throw new Error(`${label}: 期望得到字符串, 实得 ${typeof actual}`);
  }
  const n = Math.max(actual.length, expected.length);
  for (let i = 0; i < n; i++) {
    if (actual.charCodeAt(i) !== expected.charCodeAt(i)) {
      const show = c => (c === undefined
        ? '<字符串结束>'
        : `'${c === '\n' ? '\\n' : c}' U+${c.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0')}`);
      throw new Error(`${label}: 第 ${i} 个字符不同, 实得 ${show(actual[i])}, 期望 ${show(expected[i])}`
        + `\n      实得: ${JSON.stringify(actual)}\n      期望: ${JSON.stringify(expected)}`);
    }
  }
}

function assertTrue(cond, label) {
  if (!cond) throw new Error(label);
}

/* CSV 硬约束: 纯 ASCII、无 BOM、无 \r、末尾换行、数据行只含十进制整数与逗号 */
function assertCsvSane(csv, label) {
  if (!/^[\x00-\x7F]*$/.test(csv)) {
    for (let i = 0; i < csv.length; i++) {
      if (csv.charCodeAt(i) > 0x7F) {
        throw new Error(`${label}: 第 ${i} 个字符非 ASCII (U+${csv.charCodeAt(i).toString(16).toUpperCase()}),`
          + ` 违反 MATLAB/Origin 导入约束`);
      }
    }
  }
  assertTrue(csv.charCodeAt(0) !== 0xFEFF, `${label}: 首字符是 BOM`);
  assertTrue(csv.indexOf(String.fromCharCode(0xFEFF)) < 0, `${label}: 中间出现 BOM`);
  assertTrue(csv.indexOf('\r') < 0, `${label}: 出现 \\r, 应为纯 \\n 换行`);
  assertTrue(csv.charAt(csv.length - 1) === '\n', `${label}: 末尾没有换行`);

  const lines = csv.split('\n');
  assertTrue(lines[lines.length - 1] === '', `${label}: 末行应为空(由末尾换行产生)`);
  for (let i = 1; i < lines.length - 1; i++) {
    const fields = lines[i].split(',');
    for (const f of fields) {
      /* 空字段合法 (短的那一路留空); 其余只允许十进制整数:
       * 出现 "+1" / "1,234" / "1e3" / "1234 nA" 都说明格式化出了错 */
      if (!/^[0-9]*$/.test(f)) {
        throw new Error(`${label}: 第 ${i} 行出现非十进制整数字段 ${JSON.stringify(f)}`);
      }
    }
  }
}

function dataLines(csv) {
  const lines = csv.split('\n');
  return lines.slice(1, lines.length - 1);   /* 去掉表头与末尾空串 */
}

/* ---------------- 测试数据 ---------------- */

const YMD = '09_12';
/* 真实码值: 65520 内部正轨 / 58962 窗口首样本 / 0x0000 ADS8866 坏读 */
const WAVE_INT = [58962, 59010, 41234, 30787, 65520];
const WAVE_EXT = [58900, 58980, 41100];

/* ---------------- 测试用例 ---------------- */

function testCaptureStemPositive() {
  assertCharsEqual(A.captureStem('I=+25.274 nA MODE=1', YMD), 'raw_09_12_+25.274nA', 'captureStem 正');
}

function testCaptureStemNegative() {
  assertCharsEqual(A.captureStem('I=-0.847 nA MODE=2', YMD), 'raw_09_12_-0.847nA', 'captureStem 负');
}

function testCaptureStemTimeout() {
  assertCharsEqual(A.captureStem('I=+0.000 nA MODE=2 TIMEOUT', YMD), 'raw_09_12_TIMEOUT', 'captureStem 超时');
}

function testCaptureStemNoResult() {
  assertCharsEqual(A.captureStem('', YMD), 'raw_09_12_NO_RESULT', 'captureStem 空串');
  assertCharsEqual(A.captureStem('NANO-AMMETER v1.0', YMD), 'raw_09_12_NO_RESULT', 'captureStem 无关行');
  assertCharsEqual(A.captureStem(null, YMD), 'raw_09_12_NO_RESULT', 'captureStem null');
  assertCharsEqual(A.captureStem(undefined, YMD), 'raw_09_12_NO_RESULT', 'captureStem undefined');
}

function testCaptureStemKeepsSign() {
  const pos = A.captureStem('I=+25.274 nA MODE=1', YMD);
  const neg = A.captureStem('I=-25.274 nA MODE=1', YMD);
  assertTrue(pos.indexOf('+') > 0, '正值文件名丢了 + 号: ' + pos);
  assertTrue(neg.indexOf('-') > 0, '负值文件名丢了 - 号: ' + neg);
  assertTrue(pos !== neg, '正负结果文件名不得相同 (电流方向有意义)');
  /* 数值部分必须逐字符保留, 含小数点后三位 */
  assertTrue(pos.indexOf('+25.274nA') > 0, '正号数值被改写: ' + pos);
  assertTrue(neg.indexOf('-25.274nA') > 0, '负号数值被改写: ' + neg);
}

function testCaptureStemPrefixedLine() {
  /* D 查询回的是 "RESULT I=... MODE=..", 同样要能取到数值 */
  assertCharsEqual(A.captureStem('RESULT I=+12.345 nA MODE=1', YMD),
    'raw_09_12_+12.345nA', 'captureStem 带 RESULT 前缀');
}

function testCaptureStemUsesYmd() {
  assertCharsEqual(A.captureStem('I=+1.000 nA MODE=1', '01_05'), 'raw_01_05_+1.000nA', 'captureStem 日期');
}

function testWaveCsvHeaderAndRows() {
  const csv = A.buildWaveCsv(WAVE_INT, []);
  const lines = csv.split('\n');
  assertCharsEqual(lines[0], 'index,t_us,code_int,code_ext', 'wave 表头');
  assertTrue(dataLines(csv).length === WAVE_INT.length,
    `wave 行数应 ${WAVE_INT.length}, 实得 ${dataLines(csv).length}`);
}

function testWaveCsvTUs() {
  const csv = A.buildWaveCsv(WAVE_INT, []);
  const rows = dataLines(csv).map(l => l.split(','));
  rows.forEach((f, i) => {
    assertTrue(Number(f[0]) === i, `第 ${i} 行序号应为 ${i}, 实得 ${f[0]}`);
    assertTrue(Number(f[1]) === i * 160, `第 ${i} 行 t_us 应为 ${i * 160}, 实得 ${f[1]}`);
  });
  /* 6250 点窗口最后一点的时刻: 6249*160 = 999840 µs */
  assertTrue(6249 * 160 === 999840, '抽头时刻算错');
}

function testWaveCsvValues() {
  const csv = A.buildWaveCsv(WAVE_INT, WAVE_EXT);
  const rows = dataLines(csv).map(l => l.split(','));
  for (let i = 0; i < rows.length; i++) {
    if (i < WAVE_INT.length) {
      assertTrue(rows[i][2] === String(WAVE_INT[i]),
        `第 ${i} 行内置码应为 ${WAVE_INT[i]}, 实得 ${rows[i][2]}`);
    }
    if (i < WAVE_EXT.length) {
      assertTrue(rows[i][3] === String(WAVE_EXT[i]),
        `第 ${i} 行外部码应为 ${WAVE_EXT[i]}, 实得 ${rows[i][3]}`);
    }
  }
}

function testWaveCsvUnequalLengths() {
  /* 外部路短: 行数取长的 5, 外部码列后 2 行留空, 且不得抛异常 */
  let csv;
  try {
    csv = A.buildWaveCsv(WAVE_INT, WAVE_EXT);
  } catch (e) {
    throw new Error('长度不等时抛异常了: ' + e.message);
  }
  const rows = dataLines(csv).map(l => l.split(','));
  assertTrue(rows.length === WAVE_INT.length, '行数应按较长的一路');
  assertTrue(rows[2][3] === String(WAVE_EXT[2]), '第 2 行外部码应保留');
  assertTrue(rows[3][3] === '', '第 3 行外部码应留空 (不是 0)');
  assertTrue(rows[4][3] === '', '第 4 行外部码应留空 (不是 0)');
  assertTrue(rows[3][2] === String(WAVE_INT[3]), '第 3 行内置码不该被影响');

  /* 反向: 内置路短, 短的是 code_int 列 */
  const csv2 = A.buildWaveCsv(WAVE_EXT, WAVE_INT);
  const rows2 = dataLines(csv2).map(l => l.split(','));
  assertTrue(rows2.length === WAVE_INT.length, '反向长度不等时行数按较长的外部路');
  assertTrue(rows2[3][2] === '', '反向: 第 3 行内置码应留空');
  assertTrue(rows2[3][3] === String(WAVE_INT[3]), '反向: 第 3 行外部码应保留');
  assertCsvSane(csv, 'wave CSV (长度不等)');
  assertCsvSane(csv2, 'wave CSV (反向前短)');
}

function testCsvAsciiAndNoBom() {
  /* 每个产物都过一遍纯 ASCII / 无 BOM / 换行 检查 */
  assertCsvSane(A.buildWaveCsv(WAVE_INT, WAVE_EXT), 'wave CSV');
  assertCsvSane(A.buildWaveCsv(new Uint16Array(WAVE_INT), new Uint16Array(WAVE_EXT)), 'wave CSV (Uint16Array)');
  assertCsvSane(A.buildWaveCsv([], []), 'wave CSV (空)');
}

function testCsvNoScientificNotationOrSeparators() {
  /* 数据行只允许数字与逗号: 一旦出现 e/E (科学计数法)、. (浮点)、
   * + / - (带符号)、空格或单位后缀, 这里就会红。表头含字母, 单独排掉。 */
  const big = [65535, 65520, 58962, 0];
  const csv = A.buildWaveCsv(big, big);
  const rows = dataLines(csv);
  assertTrue(rows.length === big.length, '大码值样例行数不对');
  rows.forEach((line, i) => {
    assertTrue(/^[0-9,]*$/.test(line),
      `第 ${i} 行含数字与逗号以外的字符: ${JSON.stringify(line)}`);
  });
  assertTrue(csv.indexOf('65535') > 0, '满码值 65535 应原样出现');
  assertTrue(csv.indexOf('65,535') < 0, '出现千分位分隔符');
  assertCsvSane(csv, 'wave CSV 大码值');
}

function testEmptyInputsDoNotThrow() {
  const cases = [
    ['wave 两路空', () => A.buildWaveCsv([], [])],
    ['wave null/null', () => A.buildWaveCsv(null, null)],
    ['wave undefined/undefined', () => A.buildWaveCsv(undefined, undefined)],
    ['wave [ ] / null', () => A.buildWaveCsv([], null)],
  ];
  for (const [label, fn] of cases) {
    let out;
    try { out = fn(); } catch (e) { throw new Error(label + ' 抛异常: ' + e.message); }
    assertTrue(typeof out === 'string', label + ' 未返回字符串');
  }
  /* 空输入只剩表头, 但末尾换行必须还在 */
  assertCharsEqual(A.buildWaveCsv([], []), 'index,t_us,code_int,code_ext\n', '空 wave CSV');
}

function testMetaRoundTrip() {
  const fields = {
    设备: '纳安表',
    时间: '2026-09-12 09:30:00',
    result_line: 'I=+25.274 nA MODE=1',
    result_na: 25.274,
    mode: 1,
    timeout: false,
    points: { wave: 6250, wavex: 6250 },     /* 嵌套对象: 验证 pretty-print 逐层缩进 */
    notes: ['中文可以出现在 JSON 里', 'MATLAB/Origin 不读这个文件'],
  };
  const txt = A.buildMeta(fields);
  assertTrue(typeof txt === 'string', 'buildMeta 未返回字符串');
  let back = null;
  try { back = JSON.parse(txt); } catch (e) { throw new Error('buildMeta 输出无法 JSON.parse: ' + e.message); }
  if (JSON.stringify(back) !== JSON.stringify(fields)) {
    throw new Error('往返后字段不一致:\n  原: ' + JSON.stringify(fields) + '\n  回: ' + JSON.stringify(back));
  }
  assertTrue(back.设备 === '纳安表', '中文字段丢失');
  assertTrue(back.result_na === 25.274, '数值字段变了');
  assertTrue(back.points.wave === 6250, '嵌套字段变了');
  /* pretty-print 缩进 = 每层 2 空格 (不是 4 空格、也不是不缩进) */
  const lines = txt.split('\n');
  assertTrue(lines[0] === '{', '第一行应为 {');
  assertTrue(/^  "[^"]+": /.test(lines[1]), '顶层字段应缩进 2 空格, 实得: ' + JSON.stringify(lines[1]));
  assertTrue(txt.indexOf('\n    "wave": 6250') > 0,
    '嵌套字段应缩进 4 空格 (= 每层 2 空格), 实得:\n' + txt);
}

function testMetaEmpty() {
  const txt = A.buildMeta({});
  assertCharsEqual(txt, '{}', '空 fields');
  assertTrue(typeof A.buildMeta(undefined) === 'string', 'undefined fields 应仍返回字符串');
}

function testSourceIsEs2017AndDomFree() {
  /* 本模块要原样内联进 HTML 的 <script>, 所以查一遍源码:
   * 去掉注释后不得有 ?. / ?? / 顶层 await, 也不得引用 DOM 或全局对象 */
  const src = fs.readFileSync(path.join(__dirname, 'archive.js'), 'utf8');
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
  assertTrue(code.indexOf('?.') < 0, '源码含可选链 ?. (ES2020)');
  assertTrue(code.indexOf('??') < 0, '源码含空值合并 ?? (ES2020)');
  assertTrue(!/\bdocument\b/.test(code), '源码引用了 document (非纯函数)');
  assertTrue(!/\bwindow\b/.test(code), '源码引用了 window (非纯函数)');
  assertTrue(!/\bglobalThis\b|\bnavigator\b|\blocalStorage\b/.test(code), '源码引用了浏览器全局对象');
  assertTrue(!/^\s*await\b/m.test(code), '源码含顶层 await');
  assertTrue(code.indexOf('module.exports') > 0, '没有 module.exports 导出');
}

/* ---------------- 跑 ---------------- */

const tests = [
  ['captureStem 正值', testCaptureStemPositive],
  ['captureStem 负值', testCaptureStemNegative],
  ['captureStem 超时', testCaptureStemTimeout],
  ['captureStem 无结果', testCaptureStemNoResult],
  ['captureStem 正负号保留', testCaptureStemKeepsSign],
  ['captureStem RESULT 前缀行', testCaptureStemPrefixedLine],
  ['captureStem 日期来自 ymd', testCaptureStemUsesYmd],
  ['wave CSV 表头 + 行数', testWaveCsvHeaderAndRows],
  ['wave CSV t_us = 序号*160', testWaveCsvTUs],
  ['wave CSV 码值逐点对齐', testWaveCsvValues],
  ['wave CSV 长度不等留空', testWaveCsvUnequalLengths],
  ['CSV 纯 ASCII / 无 BOM', testCsvAsciiAndNoBom],
  ['CSV 无科学计数法/千分位', testCsvNoScientificNotationOrSeparators],
  ['空输入不抛异常', testEmptyInputsDoNotThrow],
  ['buildMeta 往返一致', testMetaRoundTrip],
  ['buildMeta 空对象', testMetaEmpty],
  ['源码 ES2017 + 无 DOM', testSourceIsEs2017AndDomFree],
];

let passed = 0, failed = 0;
for (const [name, fn] of tests) {
  try { fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) {
    failed++;
    console.log('  ✗ ' + name + '\n      → ' + String(e && e.message || e).split('\n').join('\n      '));
  }
}
console.log(`\n结果: ${passed} 通过 / ${failed} 失败`);

/* 样例输出: 前 3 行, 便于人工核对格式 */
console.log('\nbuildWaveCsv 样例 (前 3 行):');
console.log(A.buildWaveCsv([58962, 59010, 65520], [58900, 58980]).split('\n').slice(0, 3).join('\n'));

process.exit(failed ? 1 : 0);
