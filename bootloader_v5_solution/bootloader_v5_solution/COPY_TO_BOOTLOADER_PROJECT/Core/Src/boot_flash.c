#include "boot_flash.h"
#include "boot_config.h"
#include <string.h>

HAL_StatusTypeDef boot_erase_application(void)
{
    HAL_StatusTypeDef status = HAL_OK;
    FLASH_EraseInitTypeDef erase;
    uint32_t sector_error = 0;

    HAL_FLASH_Unlock();

    /* Sector 0 queda reservado para el bootloader.
     * Sector 1 a 7 contienen app + metadata.
     */
    for (uint32_t sector = FLASH_SECTOR_1; sector <= FLASH_SECTOR_7; sector++)
    {
        memset(&erase, 0, sizeof(erase));

        erase.TypeErase    = FLASH_TYPEERASE_SECTORS;
        erase.Banks        = FLASH_BANK_1;
        erase.Sector       = sector;
        erase.NbSectors    = 1;
        erase.VoltageRange = FLASH_VOLTAGE_RANGE_3;

        status = HAL_FLASHEx_Erase(&erase, &sector_error);

        if (status != HAL_OK)
        {
            HAL_FLASH_Lock();
            return status;
        }
    }

    HAL_FLASH_Lock();
    return HAL_OK;
}

HAL_StatusTypeDef boot_flash_write_32bytes(uint32_t address, const uint8_t *data32)
{
    HAL_StatusTypeDef status;

    if (address < APP_ADDRESS)
        return HAL_ERROR;

    if (address >= APP_META_ADDRESS)
        return HAL_ERROR;

    if ((address % 32U) != 0U)
        return HAL_ERROR;

    HAL_FLASH_Unlock();

    status = HAL_FLASH_Program(
        FLASH_TYPEPROGRAM_FLASHWORD,
        address,
        (uint32_t)data32
    );

    HAL_FLASH_Lock();
    return status;
}

HAL_StatusTypeDef boot_write_metadata(uint32_t size, uint32_t crc32)
{
    __attribute__((aligned(32))) app_meta_t meta;
    memset(&meta, 0xFF, sizeof(meta));

    meta.magic   = APP_MAGIC;
    meta.size    = size;
    meta.crc32   = crc32;
    meta.version = 1;

    HAL_FLASH_Unlock();

    HAL_StatusTypeDef status = HAL_FLASH_Program(
        FLASH_TYPEPROGRAM_FLASHWORD,
        APP_META_ADDRESS,
        (uint32_t)&meta
    );

    HAL_FLASH_Lock();
    return status;
}
