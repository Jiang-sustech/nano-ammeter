# physics_exp_test — 实验表征固件

> ## ⚠️ 本工程是 `nano_ammeter/` 的**整份拷贝**

```
nano_ammeter/        竞赛交付固件（保持干净，不含表征机械）
physics_exp_test/    实验表征固件（本工程）
```

两者**源码独立**，因此**会漂移**。改动时请遵守下面的约定。

## 两个工程的分工

| | `nano_ammeter/` | `physics_exp_test/` |
|---|---|---|
| 单次测量（模式一 / 模式二） | ✅ | ✅ |
| 波形回传 `B` / `X`、自检 `E` | ✅ | ✅ |
| 小电流模式（纯积分+斜率，覆盖 1 pA~1 nA） | ❌ | ✅ |
| 分段校准系数 `I_cal = a·I_raw + b` | ❌ | ✅ |
| 串口写入系数 + 掉电保存 | ❌ | ✅ |

**这四项就是两个工程的全部差别**，其余代码应当逐字节相同。

## 防漂移约定

**改共享部分（`adc.c` / `ads8866.c` / `uart.c` / `sh1106.c` / `Drivers/` /
`cmake/` / 链接脚本 / 启动文件）时，两边都要改。**

改完用这条命令核对，**只应剩下那四项相关的差异**：

```bash
cd nanoammeter_repo
diff -r --exclude=build --exclude=.git --exclude=README.md \
     nano_ammeter physics_exp_test
```

如果 diff 出来的东西超出预期，说明有一边漏改了。

## 构建

```bash
cmake --preset Debug
cmake --build build/Debug          # 产出 physics_exp_test.elf
```

## 与上位机的关系

两个固件**共用同一个控制台** `../纳安表控制台.html`。
控制台里的「实验表征」面板只在与本固件通信时有用；
对着 `nano_ammeter` 用时该面板不会响应（系数查询无回包）。
