#ifndef BOOT_FLASH_H
#define BOOT_FLASH_H

#include "main.h"
#include <stdint.h>

HAL_StatusTypeDef boot_erase_application(void);
HAL_StatusTypeDef boot_flash_write_32bytes(uint32_t address, const uint8_t *data32);
HAL_StatusTypeDef boot_write_metadata(uint32_t size, uint32_t crc32);

#endif
