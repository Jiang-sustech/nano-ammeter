/*
 * 纳安表控制台 统计量纯函数 —— 与 tools/nanoammeter_capture.py 的 plot_only() 逐行对齐。
 *
 * 一致性由 stats_test.js 用 raw_09_11_+25.274nA.npz 实测数据交叉验证 (Python 实跑值)。
 * 无 DOM / 无全局变量依赖; 会原样内联进 纳安表控制台.html 的 <script> —— 那种场合没有
 * module 对象, 导出自动跳过, 七个函数以顶层声明形式直接可用。
 * 只用 ES2017 及以下语法 (无 ?. / ?? / 顶层 await); 也不放 'use strict' —— 内联时若落在
 * 宿主脚本开头, 会把整个脚本切成严格模式, 可能改变别人代码的行为。本文件两种模式等价。
 */

/* 开头连续 >= hi 的样本个数 (Python: while wi[lead] >= 65000: lead += 1)。
 * 用于"窗口首样本应落在上阈值而非电平移位饱和码"的相验收判据。 */
function leadingSaturatedPrefix(arr, hi = 65000) {
  let lead = 0;
  while (lead < arr.length && arr[lead] >= hi) lead++;
  return lead;
}

/* ADS8866 坏读计数: 0x0000 转换未完成 / 0xFFFF DOUT 常高, 都是链路故障样本而非测量值 */
function countBadReads(arr) {
  let c = 0;
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] === 0x0000 || arr[i] === 0xFFFF) c++;
  }
  return c;
}

/* b - a 的统计, 剔除 b 为坏读的点 (Python: bad = (e==0)|(e==0xFFFF); av,ev = a[~bad],e[~bad])。
 * 一个 0x0000 就是 -65520 的假差异, 留着算差分只会把 mean/rms 整个拖坏, 故单独计数后剔除。
 * 长度按短的截断 (Python: n = min(len(wi), len(we)))。
 * 无有效点返回 0 (Python 此处给 NaN, 控制台显示 0 更好收尾)。 */
function diffStats(a, b) {
  const n = Math.min(a.length, b.length);
  let cnt = 0, sum = 0, sumSq = 0, maxAbs = 0;
  for (let i = 0; i < n; i++) {
    if (b[i] === 0x0000 || b[i] === 0xFFFF) continue;
    const d = b[i] - a[i];
    cnt++;
    sum += d;
    sumSq += d * d;
    const ad = d < 0 ? -d : d;
    if (ad > maxAbs) maxAbs = ad;
  }
  if (cnt === 0) return { n: 0, mean: 0, rms: 0, maxAbs: 0 };
  return { n: cnt, mean: sum / cnt, rms: Math.sqrt(sumSq / cnt), maxAbs: maxAbs };
}

/* 皮尔逊相关系数 (Python: np.corrcoef(av, ev)[0,1])。
 * 传进来的两组数需已剔除坏读点 —— 与 diffStats 不同, 这里不做剔除, 保持纯数学:
 * 未剔除的坏读会把 r 从 0.99998646 拉到 0.99998679, 拟合 k 更差 2.6e-5。
 * 中心化 (先各自去均值) 求协方差, 避免 3e4 量级码值下的有效位损失。
 * 退化输入 (任一序列方差为 0) 返回 0。 */
function correlation(a, b) {
  const n = Math.min(a.length, b.length);
  if (n < 2) return 0;
  let i, mx = 0, my = 0;
  for (i = 0; i < n; i++) { mx += a[i]; my += b[i]; }
  mx /= n; my /= n;
  let sxy = 0, sxx = 0, syy = 0;
  for (i = 0; i < n; i++) {
    const dx = a[i] - mx, dy = b[i] - my;
    sxy += dx * dy; sxx += dx * dx; syy += dy * dy;
  }
  const den = Math.sqrt(sxx * syy);
  return den === 0 ? 0 : sxy / den;
}

/* 最小二乘拟合 y = k*x + b (Python: np.polyfit(av, ev, 1))。
 * 用中心化公式而不是未中心化的法方程: x 在 3e4 量级时后者会丢掉有效位, 实测 k 差到
 * 5.7e-4 (1.0010068 vs 1.0004418) —— 中心化后与 np.polyfit 逐位一致。
 * 注意返回值里的 b 是截距, 与参数名 b 无关。 */
function linearFit(a, b) {
  const n = Math.min(a.length, b.length);
  if (n < 2) return { k: 0, b: 0 };
  let i, mx = 0, my = 0;
  for (i = 0; i < n; i++) { mx += a[i]; my += b[i]; }
  mx /= n; my /= n;
  let sxy = 0, sxx = 0;
  for (i = 0; i < n; i++) {
    const dx = a[i] - mx, dy = b[i] - my;
    sxy += dx * dy; sxx += dx * dx;
  }
  if (sxx === 0) return { k: 0, b: my };     /* 退化: 全同 x, 只能给均值 */
  const k = sxy / sxx;
  return { k: k, b: my - k * mx };
}

/* 带滞回的上升沿索引 (Python rising_edges 逐字对齐: x > thresh+hyst 翻高,
 * x < thresh-hyst 翻低, 两侧都是严格不等; 沿记录在翻转的那一个样本上) */
function risingEdges(arr, thresh, hyst = 2000) {
  const hi = thresh + hyst, lo = thresh - hyst;
  const idx = [];
  let state = 0;
  for (let i = 0; i < arr.length; i++) {
    const x = arr[i];
    if (state === 0 && x > hi) { state = 1; idx.push(i); }
    else if (state === 1 && x < lo) state = 0;
  }
  return idx;
}

/* 上升沿平均间隔 (样本数) = mean(diff(edges)), 不足 2 个沿返回 0。
 * 索引都是整数, 逐差求和与首末差在双精度下逐位相同, 用后者省一遍循环。 */
function edgePeriod(arr, thresh, hyst = 2000) {
  const e = risingEdges(arr, thresh, hyst);
  if (e.length < 2) return 0;
  return (e[e.length - 1] - e[0]) / (e.length - 1);
}

/* Node 侧导出 (require('./stats.js')); 内联进 HTML 时没有 module, 跳过 */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    leadingSaturatedPrefix: leadingSaturatedPrefix,
    countBadReads: countBadReads,
    diffStats: diffStats,
    correlation: correlation,
    linearFit: linearFit,
    risingEdges: risingEdges,
    edgePeriod: edgePeriod,
  };
}
