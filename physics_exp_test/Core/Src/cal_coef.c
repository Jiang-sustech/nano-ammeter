#include "cal_coef.h"
#include "uart.h"
#include "main.h"
#include <stddef.h>
#include <stdio.h>

/* ============================================================
 * 系数存储: Flash 最后一页
 *
 * STM32L431CC 是 256KB Flash / 2KB 页 -> 共 128 页, 最后一页 = 127
 * 链接脚本已把 FLASH 长度扣掉这一页, 保证固件不会用到它。
 * ============================================================ */
#define CAL_FLASH_ADDR   0x0803F800U
#define CAL_FLASH_PAGE   127U
#define CAL_MAGIC        0x43414C31U        /* "CAL1" */

typedef struct
{
    uint32_t magic;
    float    a[CAL_SEG_COUNT];
    float    b[CAL_SEG_COUNT];
    uint32_t crc;                           /* magic..b 的 32 位累加和 */
} CalStore;

/* 32 字节, 正好 4 个双字 —— STM32L4 只能按双字 (64 位) 编程。
 * 用 typedef 做编译期断言, 不依赖 C11 的 _Static_assert */
typedef char CalStore_size_must_be_multiple_of_8
    [(sizeof(CalStore) % 8U == 0U) ? 1 : -1];

static CalStore cal;
static uint8_t  cal_valid;

/* magic 到 b[] 的累加和 (不包括 crc 自己) */
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

static void Cal_Defaults(void)
{
    uint8_t i;
    for (i = 0U; i < CAL_SEG_COUNT; i++)
    {
        cal.a[i] = 1.0f;
        cal.b[i] = 0.0f;
    }
    cal.magic = CAL_MAGIC;
    cal.crc = Cal_Crc(&cal);
    cal_valid = 0U;
}

void Cal_Init(void)
{
    const CalStore *f = (const CalStore *)CAL_FLASH_ADDR;

    Cal_Defaults();

    if (f->magic != CAL_MAGIC)
    {
        return;                             /* 从没写过 */
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

/* ---- 上报 ---- */

/* 系数以定标整数上报, 与写入时同一套定标, 便于上位机原样回存 */
static int32_t Cal_APpm(uint8_t i)
{
    float v = cal.a[i] * 1e6f;
    return (int32_t)((v < 0.0f) ? (v - 0.5f) : (v + 0.5f));
}

static int32_t Cal_BfA(uint8_t i)
{
    float v = cal.b[i] * 1e15f;
    return (int32_t)((v < 0.0f) ? (v - 0.5f) : (v + 0.5f));
}

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

/* 把系数写进 Flash 最后一页 (擦一页 + 写 4 个双字) */
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

void Cal_Clear(void)
{
    Cal_Defaults();
    (void)Cal_Save();                       /* 写回默认值: magic 还在, 但 a=1/b=0 */
    cal_valid = 0U;
    UART_SendString("CAL CLEAR\r\n");
}
