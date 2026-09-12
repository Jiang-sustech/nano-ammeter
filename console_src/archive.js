/**
 * 数据存档纯函数模块 (无 DOM、无全局变量依赖, 可被 <script> 原样内联)
 *
 * 职责: 把串口收到的原始数据转成存档文件文本 —— 波形 -> CSV,
 *       测量元数据 -> JSON 字符串。CSV 给 MATLAB readmatrix 和 Origin 用。
 *
 * CSV 硬约束 (中文 Windows 上 MATLAB 与 Origin 对 UTF-8 的处理不一致,
 * 带中文或带 BOM 会读成乱码甚至直接解析失败):
 *   - 只允许 ASCII 字符, 不得有 BOM
 *   - 表头只用英文, 不写注释行
 *   - 数值只用十进制整数: 无千分位、无单位后缀、无科学计数法
 *   - 换行统一 '\n' (不用 '\r\n')
 * JSON 只给人看, 可以有中文。
 *
 * 语法限 ES2017: 不用 ?. / ?? / 顶层 await。
 */
'use strict';

var TICK_US = 160;   /* 波形采样间隔 160µs/点, 与固件 WAVE/WAVEX 同窗口 */

/* 固件结果行: "I=+25.274 nA MODE=1" / "I=-0.847 nA MODE=2" / "... MODE=2 TIMEOUT"
 * 与 tools/nanoammeter_capture.py 的 RESULT_RE 保持一致; 不锚定行首,
 * 这样 D 查询回的 "RESULT I=..." 前缀行也能取到数值 */
var RESULT_RE = /I=([+-][0-9]+\.[0-9]+) nA MODE=([0-9])( TIMEOUT)?/;

/**
 * 结果行 -> 文件名主干 (不含扩展名), 例: raw_09_12_+25.274nA
 * 模式二超时时电流值无意义, 用 TIMEOUT 顶替数值; 解析不出用 NO_RESULT。
 * 正负号保留 —— 电流方向是测量结果的一部分。
 */
function captureStem(resultLine, ymd) {
  var day = (ymd === null || ymd === undefined) ? '' : String(ymd);
  var m = RESULT_RE.exec(resultLine ? String(resultLine) : '');
  var tag;
  if (!m) tag = 'NO_RESULT';
  else if (m[3]) tag = 'TIMEOUT';
  else tag = m[1] + 'nA';
  return 'raw_' + day + '_' + tag;
}

/* 码值 -> CSV 单元格文本。源数据是 u16 整数, 这里只做防御:
 * 坏值 (NaN/Infinity) 留空单元格, 不写 "NaN" 这种非数字 token (会卡住 readmatrix);
 * 整数正常走 String(), 若被大数逼成科学计数法则退回 toFixed(0)。 */
function fmtCode(v) {
  var n = Math.round(Number(v));
  if (!isFinite(n)) return '';
  var s = String(n);
  if (s.indexOf('e') >= 0 || s.indexOf('E') >= 0) s = n.toFixed(0);
  return s;
}

/* 统一成"有 length 的东西", 容忍 null / undefined / 非数组 */
function asArray(x) {
  return (x && typeof x.length === 'number') ? x : [];
}

/**
 * 双路波形 CSV。表头 "index,t_us,code_int,code_ext"
 * 每行: 序号, 序号*160, 内置 ADC 码, ADS8866 外部码
 * 两路长度不等时按较长的一路出行数, 短的那一路对应列留空 (不补 0,
 * 补 0 会被当成真实码值参与统计)。末尾必有换行。
 */
function buildWaveCsv(intArr, extArr) {
  var a = asArray(intArr);
  var b = asArray(extArr);
  var n = Math.max(a.length, b.length);
  var out = 'index,t_us,code_int,code_ext\n';
  for (var i = 0; i < n; i++) {
    out += i + ',' + (i * TICK_US) + ','
         + (i < a.length ? fmtCode(a[i]) : '') + ','
         + (i < b.length ? fmtCode(b[i]) : '') + '\n';
  }
  return out;
}

/**
 * 元数据 JSON 字符串 (缩进 2 空格)。fields 原样序列化, 允许中文。
 */
function buildMeta(fields) {
  return JSON.stringify(fields === undefined ? {} : fields, null, 2);
}

/* 浏览器内联时没有 module 对象, 加守卫避免整段 <script> 抛错;
 * 内联后四个函数是全局的, 控制台直接调用。 */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    captureStem: captureStem,
    buildWaveCsv: buildWaveCsv,
    buildMeta: buildMeta,
  };
}
