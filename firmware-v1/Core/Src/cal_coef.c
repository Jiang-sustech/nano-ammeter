#include "cal_coef.h"
#include "current.h"        /* C_INT / I_POS / I_NEG / LEVELSHIFT_GAIN (仅用于算默认值) */
#include "ads8866.h"        /* ADS8866_VREF (同上) */
#include "uart.h"
#include "main.h"
#include <stddef.h>
#include <stdio.h>

/* ============================================================
 * 常数存储: Flash 最后一页
 *
 * STM32L431CC 是 256KB Flash / 2KB 页 -> 共 128 页, 最后一页 = 127
 * 链接脚本已把 FLASH 长度扣掉这一页, 保证固件不会用到它。
 * ============================================================ */
#define CAL_FLASH_ADDR   0x0803F800U
#define CAL_FLASH_PAGE   127U
/* 结构改过就用新魔数 —— 旧记录自然验不过, 回落到默认值, 不用写迁移代码 */
#define CAL_MAGIC        0x43414C32U        /* "CAL2" */

typedef struct
{
    uint32_t magic;
    float    q;                             /* 库仑/码 */
    float    i_pos;                         /* 参考电流 (A), 正 */
    float    i_neg;                         /* 参考电流 (A), 负 */
    float    a[CAL_SEG_COUNT];
    float    b[CAL_SEG_COUNT];
    uint32_t reserved;                      /* 补齐到 8 的整数倍 (L4 只能按双字编程) */
    uint32_t crc;                           /* magic..reserved 的 32 位累加和 */
} CalStore;

/* 48 字节, 正好 6 个双字。用 typedef 做编译期断言, 不依赖 C11 的 _Static_assert */
typedef char CalStore_size_must_be_multiple_of_8
    [(sizeof(CalStore) % 8U == 0U) ? 1 : -1];

static CalStore cal;
static uint8_t  cal_valid;

/* 四舍五入到整数 —— C 的强制转换是截断, 负数会朝零偏 */
static int32_t Cal_Round(float v)
{
    return (int32_t)((v < 0.0f) ? (v - 0.5f) : (v + 0.5f));
}

/* magic 到 reserved 的累加和 (不包括 crc 自己) */
static uint32_t Cal_Crc(const CalStore *s)
{
    const uint32_t *p = (const uint32_t *)s;
    uint32_t n = (uint32_t)(offsetof(CalStore, crc)) / 4U;
    uint32_t sum = 0U;
    uint32_t i;

    for (i = 0U; i < n; i++)
    {
        sum += p[i];
    }
    return sum;
}

/* 全部回落到标称值。q 由三个标称值相乘得出 —— 它们都是猜的,
 * 实测办法见 cal_coef.h 抬头 */
static void Cal_Defaults(void)
{
    uint8_t i;

    cal.magic = CAL_MAGIC;
    cal.q     = (float)((double)C_INT * (double)ADS8866_VREF
                        / (65536.0 * (double)LEVELSHIFT_GAIN));
    cal.i_pos = I_POS;
    cal.i_neg = I_NEG;
    for (i = 0U; i < CAL_SEG_COUNT; i++)
    {
        cal.a[i] = 1.0f;
        cal.b[i] = 0.0f;
    }
    cal.crc = Cal_Crc(&cal);
    cal_valid = 0U;
}

void Cal_Init(void)
{
    const CalStore *f = (const CalStore *)CAL_FLASH_ADDR;

    Cal_Defaults();

    if (f->magic != CAL_MAGIC)
    {
        return;                             /* 从没写过 (或旧结构) */
    }
    if (f->crc != Cal_Crc(f))
    {
        return;                             /* 校验不过 (写坏了/半页) */
    }

    cal = *f;
    cal_valid = 1U;
}

uint8_t Cal_IsValid(void)
{
    return cal_valid;
}

/* ---- 计算用物理常数 ---- */

float Cal_GetQ(void)    { return cal.q; }
float Cal_GetIPos(void) { return cal.i_pos; }
float Cal_GetINeg(void) { return cal.i_neg; }

/* ---- 分段系数 ---- */

uint8_t Cal_SegmentOf(float current)
{
    float m = (current < 0.0f) ? -current : current;

    if (m < 1e-9f)
    {
        return 0U;                          /* 1 pA ~ 1 nA */
    }
    if (m < 10e-9f)
    {
        return 1U;                          /* 1 nA ~ 10 nA */
    }
    return 2U;                              /* 10 nA ~ 45 nA */
}

float Cal_Apply(float raw)
{
    uint8_t s;

    if (!cal_valid)
    {
        return raw;
    }
    s = Cal_SegmentOf(raw);
    return cal.a[s] * raw + cal.b[s];
}

/* ---- 上报: 分段系数 ---- */

/* 系数以定标整数上报, 与写入时同一套定标, 便于上位机原样回存。
 * **这一行的格式不能追加字段** —— 上位机正则锚定行尾 */
static int32_t Cal_APpm(uint8_t i) { return Cal_Round(cal.a[i] * 1e6f); }
static int32_t Cal_BfA(uint8_t i)  { return Cal_Round(cal.b[i] * 1e15f); }

void Cal_Report(void)
{
    char line[80];

    if (!cal_valid)
    {
        UART_SendString("CAL NONE\r\n");
        return;
    }

    sprintf(line, "CAL %u %ld,%ld %ld,%ld %ld,%ld\r\n",
            (unsigned)CAL_SEG_COUNT,
            (long)Cal_APpm(0U), (long)Cal_BfA(0U),
            (long)Cal_APpm(1U), (long)Cal_BfA(1U),
            (long)Cal_APpm(2U), (long)Cal_BfA(2U));
    UART_SendString(line);
}

/* ---- 上报: 物理常数 (单独一行, 不与 a/b 混) ---- */

void Cal_ReportPhys(void)
{
    char line[80];

    sprintf(line, "CAL Q %ld,%ld,%ld\r\n",
            (long)Cal_Round(cal.q * 1e18f),
            (long)Cal_Round(cal.i_pos * 1e12f),
            (long)Cal_Round(cal.i_neg * 1e12f));
    UART_SendString(line);
}

/* ---- 写入 ---- */

/* 简单十进制整数解析 (不用 sscanf, 省体积) */
static uint8_t ParseInt(const char **pp, int32_t *out)
{
    const char *p = *pp;
    int32_t v = 0;
    uint8_t neg = 0U;
    uint8_t got = 0U;

    if (*p == '-')
    {
        neg = 1U;
        p++;
    }
    while (*p >= '0' && *p <= '9')
    {
        v = v * 10 + (int32_t)(*p - '0');
        p++;
        got = 1U;
    }
    if (!got)
    {
        return 0U;
    }
    *out = neg ? -v : v;
    *pp = p;
    return 1U;
}

/* 把常数写进 Flash 最后一页 (擦一页 + 写若干个双字) */
static uint8_t Cal_Save(void)
{
    FLASH_EraseInitTypeDef er = {0};
    uint32_t page_err = 0U;
    const uint64_t *src = (const uint64_t *)&cal;
    uint32_t i;

    cal.magic = CAL_MAGIC;
    cal.crc = Cal_Crc(&cal);

    if (HAL_FLASH_Unlock() != HAL_OK)
    {
        return 0U;
    }

    er.TypeErase   = FLASH_TYPEERASE_PAGES;
    er.Banks       = FLASH_BANK_1;
    er.Page        = CAL_FLASH_PAGE;
    er.NbPages     = 1U;

    if (HAL_FLASHEx_Erase(&er, &page_err) != HAL_OK)
    {
        HAL_FLASH_Lock();
        return 0U;
    }

    for (i = 0U; i < (uint32_t)(sizeof(CalStore) / 8U); i++)
    {
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD,
                              CAL_FLASH_ADDR + i * 8U, src[i]) != HAL_OK)
        {
            HAL_FLASH_Lock();
            return 0U;
        }
    }

    HAL_FLASH_Lock();
    return 1U;
}

void Cal_HandleSetLine(const char *line)
{
    const char *p = line + 1;               /* 跳过 'K' */
    int32_t seg, appm, bfa;
    char msg[64];

    if (!ParseInt(&p, &seg) || *p != ',')
    {
        UART_SendString("CAL ERR 格式应为 K<段>,<a_ppm>,<b_fA>\r\n");
        return;
    }
    p++;
    if (!ParseInt(&p, &appm) || *p != ',')
    {
        UART_SendString("CAL ERR a 解析失败\r\n");
        return;
    }
    p++;
    if (!ParseInt(&p, &bfa))
    {
        UART_SendString("CAL ERR b 解析失败\r\n");
        return;
    }

    if (seg < 0 || seg >= (int32_t)CAL_SEG_COUNT)
    {
        UART_SendString("CAL ERR 段号越界 (0~2)\r\n");
        return;
    }
    /* a 应在 1 附近; 放宽到 0.01~100 防呆, 免得写进一个数量级错的系数 */
    if (appm < 10000 || appm > 100000000)
    {
        UART_SendString("CAL ERR a 超出合理范围 (0.01~100)\r\n");
        return;
    }

    cal.a[seg] = (float)appm * 1e-6f;
    cal.b[seg] = (float)bfa * 1e-15f;

    if (!Cal_Save())
    {
        cal_valid = 0U;
        UART_SendString("CAL ERR 写入 Flash 失败\r\n");
        return;
    }

    cal_valid = 1U;
    sprintf(msg, "CAL SET %ld %ld,%ld\r\n", (long)seg, (long)appm, (long)bfa);
    UART_SendString(msg);
}

void Cal_HandlePhysLine(const char *line)
{
    const char *p = line + 1;               /* 跳过 'Q' */
    int32_t qac, ipp, inp;
    char msg[80];

    if (!ParseInt(&p, &qac) || *p != ',')
    {
        UART_SendString("CAL ERR 格式应为 Q<q_aC>,<i+_pA>,<i-_pA>\r\n");
        return;
    }
    p++;
    if (!ParseInt(&p, &ipp) || *p != ',')
    {
        UART_SendString("CAL ERR i+ 解析失败\r\n");
        return;
    }
    p++;
    if (!ParseInt(&p, &inp))
    {
        UART_SendString("CAL ERR i- 解析失败\r\n");
        return;
    }

    /* q 标称 15259 aC/码; 放宽到 1/10 ~ 10 倍防呆, 免得写进一个数量级错的数 */
    if (qac < 1500 || qac > 150000)
    {
        UART_SendString("CAL ERR q 超出合理范围 (标称 15259)\r\n");
        return;
    }
    /* 符号必须对: I+ 为正、I- 为负。写反了会让整个公式静默反向 ——
     * 这是本架构最容易出错也最难发现的地方, 所以显式拒收 */
    if (ipp <= 0 || inp >= 0 || ipp > 200000 || inp < -200000)
    {
        UART_SendString("CAL ERR 参考电流符号或量级不对 (I+>0>I-)\r\n");
        return;
    }

    cal.q     = (float)qac * 1e-18f;
    cal.i_pos = (float)ipp * 1e-12f;
    cal.i_neg = (float)inp * 1e-12f;

    if (!Cal_Save())
    {
        UART_SendString("CAL ERR 写入 Flash 失败\r\n");
        return;
    }

    cal_valid = 1U;
    sprintf(msg, "CAL Q SET %ld,%ld,%ld\r\n", (long)qac, (long)ipp, (long)inp);
    UART_SendString(msg);
}

/* 只清分段系数。物理常数 (q, I+, I-) 是仪器的实测属性, 不是"校准",
 * 清掉它们等于把仪器退回全标称状态 —— 不该跟 a/b 一起清 */
void Cal_Clear(void)
{
    uint8_t i;

    for (i = 0U; i < CAL_SEG_COUNT; i++)
    {
        cal.a[i] = 1.0f;
        cal.b[i] = 0.0f;
    }
    (void)Cal_Save();
    cal_valid = 0U;
    UART_SendString("CAL CLEAR\r\n");
}
