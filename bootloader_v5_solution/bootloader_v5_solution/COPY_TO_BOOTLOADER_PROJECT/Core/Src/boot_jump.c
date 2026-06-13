#include "boot_jump.h"
#include "boot_config.h"
#include "boot_crc.h"
#include "main.h"

extern FDCAN_HandleTypeDef hfdcan1;

typedef void (*app_entry_t)(void);

static uint8_t is_valid_ram_address(uint32_t addr)
{
    if ((addr >= 0x20000000U) && (addr < 0x20020000U)) return 1; // DTCM
    if ((addr >= 0x24000000U) && (addr < 0x24080000U)) return 1; // AXI SRAM
    if ((addr >= 0x30000000U) && (addr < 0x30010000U)) return 1; // SRAM D2
    if ((addr >= 0x38000000U) && (addr < 0x38001000U)) return 1; // Backup SRAM

    return 0;
}

uint8_t boot_is_valid_app(void)
{
    const app_meta_t *meta = (const app_meta_t *)APP_META_ADDRESS;

    if (meta->magic != APP_MAGIC)
        return 0;

    if ((meta->size == 0U) || (meta->size > APP_MAX_SIZE))
        return 0;

    uint32_t app_sp = *(volatile uint32_t *)(APP_ADDRESS);
    uint32_t app_pc = *(volatile uint32_t *)(APP_ADDRESS + 4U);
    uint32_t app_pc_clean = app_pc & ~1U;

    if (!is_valid_ram_address(app_sp))
        return 0;

    if ((app_pc_clean < APP_ADDRESS) || (app_pc_clean >= APP_META_ADDRESS))
        return 0;

    uint32_t crc = boot_crc32_memory(APP_ADDRESS, meta->size);

    if (crc != meta->crc32)
        return 0;

    return 1;
}

void boot_jump_to_app(void)
{
    uint32_t app_stack = *(volatile uint32_t *)APP_ADDRESS;
    uint32_t app_reset = *(volatile uint32_t *)(APP_ADDRESS + 4U);

    __disable_irq();

    SysTick->CTRL = 0;
    SysTick->LOAD = 0;
    SysTick->VAL  = 0;

    HAL_FDCAN_Stop(&hfdcan1);

    HAL_RCC_DeInit();
    HAL_DeInit();

    for (uint32_t i = 0; i < 8; i++)
    {
        NVIC->ICER[i] = 0xFFFFFFFFU;
        NVIC->ICPR[i] = 0xFFFFFFFFU;
    }

    SCB->VTOR = APP_ADDRESS;

    __set_MSP(app_stack);

    __DSB();
    __ISB();

    app_entry_t app = (app_entry_t)app_reset;
    app();

    while (1)
    {
    }
}