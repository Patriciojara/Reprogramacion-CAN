#ifndef BOOT_CRC_H
#define BOOT_CRC_H

#include <stdint.h>

uint32_t boot_crc32_memory(uint32_t address, uint32_t size);

#endif