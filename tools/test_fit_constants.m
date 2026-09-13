function test_fit_constants()
%TEST_FIT_CONSTANTS  fit_constants 的自测
%
%   手上还没有实测数据, 先用合成的验一遍: 给定真值, 看能不能还原。
%
%   bash tools/matlab_utf8.sh "cd('tools'); test_fit_constants"

    fprintf('\n############ 1. 无噪声: 必须精确还原 ############\n');
    t1 = local_make(200, 0.0);
    r1 = fit_constants(t1, 'Plot', false, ...
                       'Iplus_ref',  t1.Properties.UserData.I_pos, ...
                       'Iminus_ref', t1.Properties.UserData.I_neg);
    local_assert('M',    r1.M,     t1.Properties.UserData.M,    1e-9);
    local_assert('I+',   r1.I_pos, t1.Properties.UserData.I_pos, 1e-9);
    local_assert('I-',   r1.I_neg, t1.Properties.UserData.I_neg, 1e-9);

    fprintf('\n############ 2. 加噪声: 看还原误差 vs 预测不确定度 ############\n');
    % 码噪声 48 码(实测值) -> 两端合成 68 码 -> 折算成电流
    t2 = local_make(200, 68);
    r2 = fit_constants(t2, 'Plot', false, ...
                       'Iplus_ref',  t2.Properties.UserData.I_pos, ...
                       'Iminus_ref', t2.Properties.UserData.I_neg);
    local_assert_loose('M',  r2.M,     t2.Properties.UserData.M,     0.20);
    local_assert_loose('I+', r2.I_pos, t2.Properties.UserData.I_pos, 0.20);
    local_assert_loose('I-', r2.I_neg, t2.Properties.UserData.I_neg, 0.20);
    fprintf('  RMSE = %.3f pA  (码噪声 %d 码)\n', r2.rmse*1e12, 68);

    fprintf('\n############ 3. 退化数据: 端点差几乎不变 -> 条件数应报警 ############\n');
    t3 = local_make(50, 0);
    t3.c2 = t3.c1 + 100;                 % dc 恒定 -> a1 与常数项共线
    r3 = fit_constants(t3, 'Plot', false);
    if r3.cond > 100
        fprintf('  OK: 条件数 %.3g 已报警\n', r3.cond);
    else
        error('应该报警但没报 (cond=%.3g)', r3.cond);
    end

    fprintf('\n############ 4. 跨量程覆盖 ############\n');
    for span = [2, 4]                    % 电流范围跨几个数量级
        t4 = local_make(200, 68);
        t4 = local_limit_span(t4, span);
        r4 = fit_constants(t4, 'Plot', false, ...
                           'Iplus_ref',  t4.Properties.UserData.I_pos, ...
                           'Iminus_ref', t4.Properties.UserData.I_neg);
        fprintf('  span=1e%d  cond=%.3g\n', span, r4.cond);
    end

    fprintf('\n############ 全部通过 ############\n');
end

% ===========================================================================
function T = local_make(N, codeNoise)
% 合成一张"物理上说得通"的表, 并用真值算出 I_true
%
% 真值取标称量级:
    M_true      =  100e-12 / 0.33;       % = 3.0303e-10
    I_pos_true  =  50.232e-9;
    I_neg_true  = -50.255e-9;

    T_int = 160e-6;
    Vref  = 3.3;
    NTOT  = 6250;                        % m+n, 1 秒窗口

    rng(20260913);                       % 可复现

    % 占空比 m/(m+n) 与端点码差在物理范围内随机
    duty = 0.5 + 0.30 * (2*rand(N,1) - 1);          % ±30%
    m    = round(NTOT * duty);
    n    = NTOT - m;

    % 端点码差 —— **别取小了, 杠杆全靠它**:
    %   起点 = 拉回相最后一拍, 落在零点附近 (过冲约一拍的路程 ~525 码)
    %   终点 = 锯齿上的任意相位, 落在两阈值之间 (2602 ~ 58963)
    % 所以差值铺满整个摆幅, 跨度约 ±28000 码
    dc   = round(28000 * (2*rand(N,1) - 1));
    c1   = round(30782 + 525 * (2*rand(N,1) - 1));
    c2   = c1 + dc;

    if codeNoise > 0
        % 两端各加码噪声; 差值噪声按 sqrt(2) 合成
        s = codeNoise / sqrt(2);
        c1 = round(c1 + s*randn(N,1));
        c2 = round(c2 + s*randn(N,1));
    end

    % 真值 -> I_true (用未加噪的码, 保证 I_true 是"真"的)
    a1 = (Vref .* dc ./ 65536) ./ (NTOT * T_int);
    a2 = m ./ NTOT;
    I_true = M_true*a1 + (-I_neg_true) + (I_neg_true - I_pos_true)*a2;

    T = table(I_true, c1, c2, m, n);
    T.Properties.UserData = struct('M', M_true, ...
                                    'I_pos', I_pos_true, 'I_neg', I_neg_true);
end

function T = local_limit_span(T, decades)
% 把电流范围限制在十个 decade 内 (用来对照不同覆盖范围下的条件数)
    r = abs(T.I_true);
    lo = max(r) / 10^decades;
    T = T(r >= lo, :);
end

function local_assert(name, got, want, tol)
    e = abs(got - want) / abs(want);
    fprintf('  %s  拟合 %.9e   真值 %.9e   相对差 %.2e\n', name, got, want, e);
    if e > tol
        error('%s 相对差 %.3e 超过容差 %.3e', name, e, tol);
    end
end

function local_assert_loose(name, got, want, tol)
    e = abs(got - want) / abs(want);
    fprintf('  %s  拟合 %.6e   真值 %.6e   相对差 %+.2f%%  (容差 %.0f%%)\n', ...
            name, got, want, e*100, tol*100);
    if e > tol
        error('%s 相对差 %.3f%% 超过容差 %.0f%%', name, e*100, tol*100);
    end
end
