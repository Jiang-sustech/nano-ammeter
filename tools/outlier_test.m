%% 残差的独立性检验 + 离群值检验 —— MATLAB 版 (独立实现, 用于交叉验证)
%
%  跟 tools/outlier_test.py 是**同一套检验、同一批数字**, 但用 MATLAB 统计
%  工具箱从头写一遍。目的只有一个: **两套独立实现给出同样的结论, 才敢往
%  报告里写**。任何一处对不上, 都说明有一边写错了。
%
%  跑之前先生成数据 (在仓库根目录):
%      python tools/outlier_test.py
%  它会在 data/ 下写出 outlier_residuals.csv (逐次的残差)。
%
%  然后在 MATLAB 里: 打开本文件, 直接点"运行"。
%  命令行等价写法 (仓库根目录):
%      bash tools/matlab_utf8.sh "cd('tools'); outlier_test"
%
%  ⚠️ 残差里**系统**的部分已被三常数拟合吸收, 所以这里筛出的"离群"是
%     **相对于其余点**而言, **不是**"装置错了"的证据。

%% ---------------- 读数据 ----------------
cand = {'../data/outlier_residuals.csv', 'data/outlier_residuals.csv'};
csvFile = '';
for i = 1:numel(cand)
    if isfile(cand{i}), csvFile = cand{i}; break; end
end
if isempty(csvFile)
    error(['找不到 outlier_residuals.csv。先在仓库根目录跑 ' ...
           'python tools/outlier_test.py 生成它。']);
end

T = readtable(csvFile);
setnA = T.set_nA;
resid = T.resid_pA;
InA   = T.I_nA;

alpha = 0.05;

fprintf('================================================================\n');
fprintf('残差的独立性检验 + 离群值检验  (MATLAB 交叉验证)\n');
fprintf('================================================================\n');
fprintf('数据: %s   %d 次测量\n', csvFile, numel(resid));
fprintf('      alpha = %g\n\n', alpha);

% 按 set_nA 分组, 保持**实际测量顺序** (unique 的 'stable' 选项)
[sp, ~] = unique(setnA, 'stable');
ng = numel(sp);
E = zeros(ng, 1);          % 逐点均值残差
rep = cell(ng, 1);
grp = zeros(numel(resid), 1);
for k = 1:ng
    m = (setnA == sp(k));
    E(k) = mean(resid(m));
    rep{k} = resid(m);
    grp(m) = k;
end
Ipt = zeros(ng, 1);
for k = 1:ng, Ipt(k) = mean(InA(setnA == sp(k))); end

fprintf('%d 个电流点, 每点 %d 次\n\n', ng, numel(rep{1}));

%% ---------------- 【1】独立性 ----------------
fprintf('【1】独立性检验 (游程检验, 按实际测量顺序)\n\n');
fprintf('  runstest 的判据是"以参考值为界上下交替是否随机"。\n');
fprintf('  参考值取该极性自己的均值 == Python 版的"去极性偏置"。\n\n');

for pol = [1 -1]
    if pol > 0, nm = '正电流'; else, nm = '负电流'; end
    sel = (Ipt * pol) > 0;
    x = E(sel);
    if numel(x) < 4, continue; end

    % 不去偏置 (参考值取 0) —— 会被极性偏置污染
    [h0, p0] = runstest(x, 0);
    % 去偏置 (参考值取本极性均值)
    mu = mean(x);
    [h1, p1] = runstest(x, mu);
    % 手算同一个统计量 (与 Python 版逐字相同的定义), 用来定位两版 p 值差异
    [rm, pm, zm] = runs_normal(x, mu);
    fprintf('  %s (n=%d), 极性偏置 %.3f pA:\n', nm, numel(x), mu);
    fprintf('      以 0   为界   p = %.4f  -> %s\n', p0, ...
            ternary(h0, '拒绝独立', '不能拒绝独立'));
    fprintf('      以均值为界   runstest p = %.4f  -> %s\n', p1, ...
            ternary(h1, '**拒绝独立**', '不能拒绝独立'));
    fprintf('      手算同一统计量  r = %d, z = %+.3f, p = %.4f   (对照 Python 版)\n', ...
            rm, zm, pm);
end
fprintf('\n');

% 自相关 (手算, 不依赖 Econometrics 工具箱)
fprintf('  自相关 (按测量顺序, 已去极性偏置):\n');
Eall = zeros(ng, 1);
for pol = [1 -1]
    sel = (Ipt * pol) > 0;
    Eall(sel) = E(sel) - mean(E(sel));
end
for lag = 1:3
    x = Eall - mean(Eall);
    rho = sum(x(1:end-lag) .* x(1+lag:end)) / sum(x.^2);
    z = rho * sqrt(numel(x));
    fprintf('      lag-%d  rho = %+.3f   (白噪声下 z = %+.2f%s)\n', ...
            lag, rho, z, ternary(abs(z) > 1.96, ',  *显著*', ''));
end
fprintf('\n');

%% ---------------- 【2】过度离散 ----------------
fprintf('【2】过度离散检验 (单因素 ANOVA: 逐点差异 vs 重复性)\n\n');
[pA, tbl] = anova1(resid, grp, 'off');
% 组内 σ 直接算, **不读 anova1 的 tbl** —— 那张表的第 1 列是"来源名"(字符串),
% 不是 df。第一版写成 tbl{3,2}/tbl{3,1} 拿字符串去除, 直接报维度不一致。
ssw = 0;
for k = 1:ng
    ssw = ssw + sum((rep{k} - mean(rep{k})).^2);
end
sw = sqrt(ssw / (numel(resid) - ng));    % 合并组内 σ (df = N - 组数)
sb = std(E);
fprintf('      组内 (重复性) σ = %.3f pA\n', sw);
fprintf('      组间 (逐点)   σ = %.3f pA\n', sb);
fprintf('      方差比 (σ组间/σ组内)^2 = %.2f\n', (sb/sw)^2);
fprintf('      ANOVA p = %.3e  -> %s\n', pA, ...
        ternary(pA < alpha, '**逐点差异真实存在**', '逐点差异不显著'));
fprintf('\n');

% 两极性方差齐性
posv = E(Ipt > 0);  negv = E(Ipt < 0);
[hp, pp] = vartest2(posv, negv);
fprintf('      极性方差齐性 (vartest2, 双侧 F):  p = %.3f -> %s\n', pp, ...
        ternary(hp, '**两极性方差不等**', '两极性方差相等'));
fprintf('      正 σ = %.3f pA,  负 σ = %.3f pA\n\n', std(posv), std(negv));

%% ---------------- 【3】离群值 ----------------
fprintf('【3】离群值检验\n\n');
fprintf('  3.0 正态性前提 (Grubbs/ESD 都假设近似正态)\n');
for k = 1:3
    switch k
        case 1, v = Eall;                 nm = '全部(去极性偏置)';
        case 2, v = E(Ipt > 0) - mean(E(Ipt > 0)); nm = '正电流';
        case 3, v = E(Ipt < 0) - mean(E(Ipt < 0)); nm = '负电流';
    end
    [hl, pl] = lillietest(v, 'Alpha', alpha);
    [ha, pa] = adtest(v, 'Alpha', alpha);
    fprintf('      %-18s Lilliefors p = %.3f (%s)   Anderson-Darling p = %.3f (%s)\n', ...
            nm, pl, ternary(hl, '偏离正态', '不能拒绝'), ...
            pa, ternary(ha, '偏离正态', '不能拒绝'));
end
fprintf('\n');

fprintf('  3a. Grubbs 单离群点检验 (两侧)\n');
fprintf('      %-20s %-9s %-9s %-8s %s\n', '集合', 'G', 'G_crit', '判定', '最极端点');
subsets = {'全部(去极性偏置)', Eall, (1:ng)'; ...
           '正电流', E(Ipt>0)-mean(E(Ipt>0)), find(Ipt>0); ...
           '负电流', E(Ipt<0)-mean(E(Ipt<0)), find(Ipt<0)};
for r = 1:size(subsets, 1)
    v = subsets{r, 2};   idxmap = subsets{r, 3};
    [G, Gc, i] = grubbs_test(v, alpha);
    j = idxmap(i);
    fprintf('      %-20s %-9.3f %-9.3f %-8s %+.3f nA (残差 %+.2f pA)\n', ...
            subsets{r,1}, G, Gc, ternary(G > Gc, '**离群**', '不显著'), ...
            Ipt(j), E(j));
    pmc = grubbs_mc(v, G, 40000);
    fprintf('      %-20s 蒙特卡洛 P(G>=G_obs) = %.4f  -> %s\n', '', pmc, ...
            ternary((pmc < alpha) == (G > Gc), '与解析临界值一致', ...
                    '**与解析临界值不一致!**'));
end
fprintf('\n');

fprintf('  3b. 迭代 ESD (Rosner, 最多剔 5 个)\n');
[R, lam, idx] = esd_test(Eall, alpha, 5);
keep = 0;
for j = numel(R):-1:1
    if R(j) >= lam(j), keep = j; break; end
end
for j = 1:numel(R)
    fprintf('      第 %d 步剔除 %+8.3f nA   R = %.3f,  lambda = %.3f   %s\n', ...
            j, Ipt(idx(j)), R(j), lam(j), ...
            ternary(R(j) >= lam(j), 'R>=lambda 判离群', 'R<lambda 保留'));
end
fprintf('      -> 结论: 前 **%d** 个判为离群点\n\n', keep);

%% ---------------- 【4】判读 ----------------
x = Eall - mean(Eall);
rho1 = sum(x(1:end-1) .* x(2:end)) / sum(x.^2);
z1 = rho1 * sqrt(numel(x));
indep = abs(z1) <= 1.96;
overdisp = pA < alpha;

fprintf('================================================================\n');
fprintf('【4】判读\n');
fprintf('================================================================\n');
fprintf('  独立性     lag-1 rho = %+.3f (z = %+.2f) -> %s\n', rho1, z1, ...
        ternary(indep, '无序列相关', '**有**序列相关'));
fprintf('  过度离散   组间 σ %.3f vs 组内 σ %.3f, ANOVA p = %.2e -> %s\n', ...
        sb, sw, pA, ternary(overdisp, '逐点差异真实存在', '逐点差异不显著'));
fprintf('  离群点     筛出 %d 个\n\n', keep);

if indep && overdisp && keep == 0
    fprintf('    ③ 的"一个都没筛出"**不是**"装置很准" —— 它是 ② 的推论。\n');
    fprintf('    Grubbs 抓不到离群点, 是因为**离散是普遍的, 不是局部的**。\n\n');
    [~, j] = max(abs(E));
    fprintf('    换一把尺子, 结论就翻过来:\n');
    fprintf('      最极端的 %+.3f nA (残差 %+.2f pA):\n', Ipt(j), E(j));
    fprintf('        用**组内** σ (%.3f pA) -> |z| = %.1f  **极端离群**\n', ...
            sw, abs(E(j))/sw);
    fprintf('        用**组间** σ (%.3f pA) -> |z| = %.1f  不显著\n\n', ...
            sb, abs(E(j))/sb);
    fprintf('    **该问的不是"哪几个点是坏点", 而是"为什么每个点都带着\n');
    fprintf('    ~%.2f pA 的固定结构"。** 那是未建模的系统项, 修它才涨精度。\n', sb);
elseif indep && ~overdisp
    fprintf('    残差既独立、逐点也无显著差异 -> 只有噪声, 没有真离群点。\n');
else
    fprintf('    残差有序列相关 -> 离群检验的"同分布"前提不成立, 结果只能当筛查。\n');
end
fprintf('\n');
fprintf('  ⚠️ 残差里**系统**的部分已被三常数拟合吸收, 所以这里筛出的"离群"\n');
fprintf('     是**相对于其余点**而言, **不是**"装置错了"的证据。要分清, 只能\n');
fprintf('     换**独立参考**(标准电阻 + 标准电压)复测。\n');

%% ================= 局部函数 =================
function s = ternary(cond, a, b)
    if cond, s = a; else, s = b; end
end

function [r, p, z] = runs_normal(x, v)
    % 与 tools/outlier_test.py 里 runs_test() 逐字相同的定义:
    % Wald-Wolfowitz 游程检验, 正态近似 + 连续性修正(朝期望值挪半格)。
    s = x(x ~= v);
    s = (s > v);                       % 逻辑序列: 高于界 = true
    n = numel(s);
    n1 = sum(s);  n2 = n - n1;
    if n1 == 0 || n2 == 0 || n < 3
        r = NaN; p = NaN; z = NaN; return;
    end
    r = 1 + sum(s(2:end) ~= s(1:end-1));
    mu = 2*n1*n2/n + 1;
    sd = sqrt(2*n1*n2*(2*n1*n2 - n) / (n*n*(n-1)));
    cc = 0.5 * sign(r - mu);
    z = (r - cc - mu) / sd;
    p = 2 * min(normcdf(z), 1 - normcdf(z));
end

function [G, Gc, i] = grubbs_test(v, alpha)
    n = numel(v);
    m = mean(v);  s = std(v);
    [~, i] = max(abs(v - m));
    G = abs(v(i) - m) / s;
    t = tinv(1 - alpha/(2*n), n - 2);
    Gc = (n - 1) / sqrt(n) * sqrt(t^2 / (n - 2 + t^2));
end

function p = grubbs_mc(v, Gobs, nsim)
    % 正态零假设下 P(G >= G_obs) —— 复核上面的解析临界值
    rng(12345);
    n = numel(v);
    hit = 0;
    for k = 1:nsim
        x = randn(n, 1);
        s = std(x);
        if s == 0, continue; end
        if max(abs(x - mean(x))) / s >= Gobs, hit = hit + 1; end
    end
    p = (hit + 1) / (nsim + 1);
end

function [R, lam, idx] = esd_test(v, alpha, kmax)
    % Rosner 迭代 ESD
    idx = (1:numel(v))';
    vals = v(:);
    R = []; lam = []; ridx = [];
    for step = 1:kmax
        n = numel(vals);
        if n < 3, break; end
        m = mean(vals); s = std(vals);
        if s == 0, break; end
        [~, k] = max(abs(vals - m));
        R(end+1,1) = abs(vals(k) - m) / s;            %#ok<AGROW>
        t = tinv(1 - alpha/(2*(n+1)), n - 2);
        lam(end+1,1) = n * t / sqrt((n - 1 + t^2) * (n + 1));  %#ok<AGROW>
        ridx(end+1,1) = idx(k);                        %#ok<AGROW>
        idx(k) = [];  vals(k) = [];
    end
    idx = ridx;
end
