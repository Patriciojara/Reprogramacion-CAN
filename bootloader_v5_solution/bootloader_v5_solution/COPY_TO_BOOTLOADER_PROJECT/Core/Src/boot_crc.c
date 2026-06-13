#include "boot_crc.h"

static uint32_t crc32_update(uint32_t crc, uint8_t data)
{
    crc ^= data;

    for (uint8_t i = 0; i < 8; i++)
    {
        if (crc & 1U)
            crc = (crc >> 1) ^ 0xEDB88320U;
        else
            crc >>= 1;
    }

    return crc;
}

uint32_t boot_crc32_memory(uint32_t address, uint32_t size)
{
    uint32_t crc = 0xFFFFFFFFU;

    for (uint32_t i = 0; i < size; i++)
    {
        uint8_t b = *(volatile uint8_t *)(address + i);
        crc = crc32_update(crc, b);
    }

    return crc ^ 0xFFFFFFFFU;
}