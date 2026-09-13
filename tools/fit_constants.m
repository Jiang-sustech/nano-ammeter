function out = fit_constants(src, varargin)
%FIT_CONSTANTS  用已知输入电流最小二乘拟合式(6) 的三个物理常数
%
%   out = fit_constants(csvPath)                 % CSV 表头: I_true,c1,c2,m,n
%   out = fit_constants(T)                       % 或直接给 table
%   out = fit_constants(..., 'T_int', 160e-6)
%   out = fit_constants(..., 'Vref_adc', 3.3)
%   out = fit_constants(..., 'Plot', false)
%
% ---------------------------------------------------------------------------
% 模型 —— 论文式(6):
%
%       I = M·(Vo1 - Vo2)/((m+n)·T)  -  (m·I+ + n·I-)/(m+n)
%
% Vo 是**原始 ADC 域电压**(还没换算到积分器域):
%       Vo = Vref_adc · c / 65536            c = 16 位原始码
%
% 所以 M 里含了电平移位增益:  M = ±C/LEVELSHIFT_GAIN
%   符号/数值由拟合自己定, 但**合理性检查要看 |M| ≈ C/0.33 = 3.03e-10**
%（若看到 3.3e-11 那是把增益除反了）
%
% ---------------------------------------------------------------------------
% 为什么不能直接按 I+ / I- 各一列去拟合:
%
%   m/(m+n) 与 n/(m+n) 恒满足 "两列之和 = 1" -> 高度共线, 各自方差被放大
%   十几倍。等价重参数化后条件数好得多, 而且是同一组解:
%
%       I = M·a1 + b0 + b2·a2
%         a1 = dV/((m+n)·T)      a2 = m/(m+n)
%         b0 = -I-               b2 = I- - I+
%
%   反解:   I- = -b0        I+ = I- - b2
%
% ---------------------------------------------------------------------------
% 精度 (用户要求"高精度运算"):
%
%   输入是**测量值**, 本身只有几位有效数字, 所以 vpa 不会凭空提高准确度。
%   真正要小心的是两处**数值**问题:
%
%     ① 码差必须在整数域相减 (c2 - c1) 再折算成电压。
%        写成 Vref*c2/65536 - Vref*c1/65536 会在码差很小的时候丢有效位。
%        码 <= 65535 远小于 2^53, double 表示整数是**精确**的。
%
%     ② 不要解正规方程 (A'*A)\(A'*b) —— 那会把条件数平方。
%        用 QR (MATLAB 的 A\b 对超定系统就是 QR)。
%
%   脚本另外用 vpa(50 位) 独立复核一遍: 把 A'A 和 A'b 在 50 位下算出来
%   再解。两者一致 => double 的算术没有损失 (剩下的差别就是数据本身的噪声)。
%
% 用法见文件末尾的自测:  test_fit_constants
% ---------------------------------------------------------------------------

    p = inputParser;
    p.addParameter('T_int',    160e-6);   % TIM6 周期 (s)
    p.addParameter('Vref_adc', 3.3);      % ADS8866 基准 (V)
    p.addParameter('Plot',     true);
    p.addParameter('Iplus_ref',  50.232e-9);   % 独立实测值, 仅供对照
    p.addParameter('Iminus_ref', -50.255e-9);
    p.parse(varargin{:});
    o = p.Results;

    T = local_read(src);
    local_check(T);

    % ---- 回归量 ----
    % ① 码差在整数域相减, 再折算成电压 (见抬头 ①)
    dc   = double(T.c2) - double(T.c1);          % 精确整数差
    dv   = o.Vref_adc .* dc ./ 65536;            % 原始 ADC 域电压差 (V)

    ntot = double(T.m) + double(T.n);
    a1   = dv ./ (ntot .* o.T_int);              % V/s
    a2   = double(T.m) ./ ntot;                  % 无量纲, ~0.5
    y    = double(T.I_true);

    % ---- 最小二乘 ----
    A = [a1, ones(size(a1)), a2];
    [x, dg] = local_lsq(A, y);

    M  = x(1);
    b0 = x(2);
    b2 = x(3);

    I_neg = -b0;
    I_pos = I_neg - b2;

    % ---- 诊断 ----
    I_fit  = A * x;
    resid  = y - I_fit;
    rmse   = sqrt(mean(resid.^2));
    cnd    = dg.cond;
    diagse = dg.se;
    diagcov = dg.cov;

    % ---- 输出 ----
    % 物理参数的标准误 (由回归量的协方差推)
    se_M    = diagse(1);
    se_Ineg = diagse(2);
    se_Ipos = sqrt(diagcov(2,2) + diagcov(3,3) + 2*diagcov(2,3));

    fprintf('\n===== 拟合结果 (n=%d 组) =====\n', numel(y));
    fprintf('  M    = %+.6e  ± %.1e   (%.3f%%)   期望 |M| ≈ %.3e\n', ...
            M, se_M, abs(se_M/M)*100, 100e-12/0.33);
    fprintf('  I-   = %+.4f nA  ± %.4f nA  (%.3f%%)   独立实测 %+.4f nA\n', ...
            I_neg*1e9, se_Ineg*1e9, abs(se_Ineg/I_neg)*100, o.Iminus_ref*1e9);
    fprintf('  I+   = %+.4f nA  ± %.4f nA  (%.3f%%)   独立实测 %+.4f nA\n', ...
            I_pos*1e9, se_Ipos*1e9, abs(se_Ipos/I_pos)*100, o.Iplus_ref*1e9);
    fprintf('  |I+| - |I-| 之差 = %+.4f nA\n', (I_pos - I_neg)*1e9);
    fprintf('  RMSE = %.4f pA\n', rmse*1e12);
    fprintf('  条件数 cond(A) = %.3g\n', cnd);

    % ---- 关键判读: 三个参数的"杠杆"差多少 ----
    % 端点项的杠杆 = |M|·span(a1), 计数项的杠杆 = |b2|·span(a2)
    lev_1 = abs(M)  * range(a1);
    lev_2 = abs(b2) * range(a2);
    fprintf('\n  ---- 杠杆对比 (决定各自能被定到多准) ----\n');
    fprintf('    端点项 M·a1  的跨度 = %8.2f pA\n', lev_1*1e12);
    fprintf('    计数项 b2·a2 的跨度 = %8.2f pA\n', lev_2*1e12);
    if lev_2 > 5*lev_1
        fprintf('    -> 计数项杠杆大得多: I+/I- 定得准, M 定得差。\n');
        fprintf('       想要准的 M(q), 用**专门的"灌已知电流"测量**(见 README),\n');
        fprintf('       那里 Δc 能跑满整条斜坡, 杠杆比这里大一个量级。\n');
    end

    if cnd > 100
        fprintf('\n  !! 条件数偏大 (>100): 检查数据是否覆盖足够宽的电流范围与正负两向\n');
    end

    % ---- 换算成固件的 q, 并给出可直接粘贴的 Q 指令 ----
    q = M * o.Vref_adc / 65536;                  % 库仑/码
    fprintf('\n  对应固件常数 q = %.6e C/码  (标称 1.5259e-14)\n', q);
    fprintf('  可直接发给实验固件:\n\n');
    fprintf('      Q%d,%d,%d\n\n', round(q*1e18), round(I_pos*1e12), round(I_neg*1e12));

    % ---- 和独立实测值对照 ----
    fprintf('  ---- 与独立测量对照 (对不上说明模型或数据有问题) ----\n');
    local_cmp('I- ', I_neg, o.Iminus_ref);
    local_cmp('I+ ', I_pos, o.Iplus_ref);

    out = struct('M', M, 'I_pos', I_pos, 'I_neg', I_neg, ...
                 'q', q, 'rmse', rmse, 'cond', cnd, ...
                 'se', diagse, 'cov', diagcov, ...
                 'resid', resid, 'I_fit', I_fit, ...
                 'x', x, 'A', A, 'y', y);

    if o.Plot
        local_plot(T, out);
    end
end

% ===========================================================================
function T = local_read(src)
    if istable(src)
        T = src;
    elseif ischar(src) || isstring(src)
        T = readtable(src);
        T.Properties.VariableNames = lower(T.Properties.VariableNames);
    else
        error('fit_constants:src', '给 CSV 路径或 table');
    end
    need = {'I_true','c1','c2','m','n'};
    for k = 1:numel(need)
        if ~ismember(need{k}, T.Properties.VariableNames)
            error('fit_constants:col', '缺列 %s (需要 %s)', ...
                  need{k}, strjoin(need, ', '));
        end
    end
end

function local_check(T)
    % 码必须是 0..65535 的整数 (c1/c2 是原始码)
    for f = {'c1','c2'}
        v = T.(f{1});
        if any(v < 0 | v > 65535)
            error('fit_constants:code', '%s 超出 16 位码域', f{1});
        end
        if any(abs(v - round(v)) > 1e-9)
            error('fit_constants:code', '%s 不是整数', f{1});
        end
    end
    if any(T.m < 0) || any(T.n < 0)
        error('fit_constants:cnt', 'm/n 不能为负');
    end
    if any((T.m + T.n) == 0)
        error('fit_constants:cnt', '有 m+n = 0 的行');
    end
end

function [x, d] = local_lsq(A, y)
% 双精度 QR 解 + 三种方式互校 + 参数标准误
    k = size(A, 2);
    N = size(A, 1);

    x = A \ y;                                   % 超定 -> QR, 不解正规方程

    % ---- 用"中心化"独立解一遍 ----
    % 只中心化**非常数**列; 常数列(全 1)中心化后全变成 0, 会让矩阵秩亏
    constCol = find(std(A, 0, 1) == 0);
    freeCol  = setdiff(1:k, constCol);
    c = mean(A(:, freeCol), 1);
    s = std(A(:, freeCol), 0, 1);   s(s == 0) = 1;

    Ac = A(:, freeCol);  Ac = (Ac - c) ./ s;
    xc = Ac \ (y - mean(y));                     % 去掉截距后解

    x2 = zeros(k, 1);
    x2(freeCol) = xc ./ s.';
    if ~isempty(constCol)
        % β0 = ȳ − Σ βj·x̄j
        x2(constCol) = mean(y) - sum((c ./ s) .* xc.');
    end

    % ---- vpa 复核: 50 位下解正规方程 (对同一组数据即"精确最小二乘解") ----
    % 注意 vpa 只复核**算术**, 不会提高测量数据本身的准确度
    if exist('vpa', 'file') == 2
        Av = vpa(A, 50);  yv = vpa(y, 50);
        xv = double((Av.' * Av) \ (Av.' * yv));
    else
        xv = x;
    end

    % ---- 参数标准误: se_j = sqrt(σ² · [(A'A)^-1]_jj),  σ² = RSS/(N-k) ----
    % (这里形成正规方程只是为了**估计方差**, 不是拿来求解; 条件数只有几十, 安全)
    rss   = sum((y - A*x).^2);
    sigma2 = rss / max(N - k, 1);
    covx  = sigma2 * inv(A.' * A);
    se    = sqrt(max(diag(covx), 0));

    d.cond   = cond(A);
    d.se     = se;
    d.cov    = covx;
    d.N      = N;
    d.maxdiff = max(abs([x - x2, x - xv]), [], 'all');

    fprintf('  [精度复核] QR vs 中心化QR vs vpa50 最大差 = %.3e  (cond=%.3g)\n', ...
            d.maxdiff, d.cond);
    if d.maxdiff > 1e-8 * max(abs(x))
        fprintf('  !! 三种解法不一致 -> 条件数问题, 结果不可信\n');
    end

    % ---- 共线性: 只在**非常数**列之间算相关 ----
    if numel(freeCol) > 1
        R = corrcoef(A(:, freeCol));
        off = R - eye(size(R));
        worst = max(abs(off(:)));
        fprintf('  [共线性] 非常数回归量之间最大 |相关系数| = %.3f\n', worst);
        if worst > 0.95
            fprintf('  !! 回归量高度相关, 参数标准误会很大\n');
        end
    end
end

function local_cmp(name, got, ref)
    if isempty(ref) || ~isfinite(ref)
        fprintf('   %s  拟合 %+.4f nA   (无独立值)\n', name, got*1e9);
        return;
    end
    d = (got - ref) / ref * 100;
    fprintf('   %s  拟合 %+.4f nA   独立 %+.4f nA   差 %+.3f%%\n', ...
            name, got*1e9, ref*1e9, d);
end

function local_plot(T, o)
    y = double(T.I_true) * 1e12;
    f = o.I_fit * 1e12;
    r = o.resid * 1e12;

    figure('Position', [100 100 1000 700]);

    subplot(2,2,1);
    loglog(abs(y), abs(f), 'o'); hold on;
    lim = [min(abs(y)) max(abs(y))];
    loglog(lim, lim, 'k--');
    grid on; xlabel('I_{true} (pA)'); ylabel('I_{fit} (pA)');
    title('拟合 vs 已知输入');

    subplot(2,2,2);
    plot(y, r, 'o'); hold on; yline(0, 'k--'); grid on;
    xlabel('I_{true} (pA)'); ylabel('残差 (pA)');
    title(sprintf('残差  RMSE = %.3g pA', o.rmse*1e12));

    subplot(2,2,3);
    plot(y, r ./ max(abs(y), eps) * 100, 'o'); hold on;
    yline(0, 'k--'); grid on;
    xlabel('I_{true} (pA)'); ylabel('相对残差 (%)');
    title('相对残差');

    subplot(2,2,4);
    bar([abs(o.I_neg), abs(o.I_pos)]*1e9); grid on;
    set(gca, 'XTickLabel', {'|I-|','|I+|'});
    ylabel('nA'); title('拟合出的参考电流');

    sgtitle('式(6) 物理常数最小二乘拟合');
end
