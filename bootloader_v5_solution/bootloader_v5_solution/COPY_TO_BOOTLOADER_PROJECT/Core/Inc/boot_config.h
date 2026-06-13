#ifndef BOOT_CONFIG_H
#define BOOT_CONFIG_H

#include "main.h"
#include <stdint.h>

/* CAN IDs */
#define BOOT_CAN_RX_ID      0x100U  /* Raspberry Pi -> STM32 */
#define BOOT_CAN_TX_ID      0x101U  /* STM32 -> Raspberry Pi */

/* STM32H723VGT6TR: 1 MB Flash.
 * Sector 0: 0x08000000 - 0x0801FFFF -> bootloader, 128 KB
 * App:      0x08020000 - 0x080FEFFF
 * Metadata: 0x080FF000 - 0x080FFFFF -> dentro del sector 7
 */
#define APP_ADDRESS         0x08020000U
#define FLASH_END_ADDRESS   0x08100000U
#define APP_META_ADDRESS    0x080FF000U
#define APP_MAX_SIZE        (APP_META_ADDRESS - APP_ADDRESS)

#define APP_MAGIC           0x5041544FU  /* 'PATO' */

/* Commands */
#define CMD_PING            0x10U
#define CMD_BEGIN_UPDATE    0x20U
#define CMD_DATA_32B        0x21U
#define CMD_END_UPDATE      0x22U
#define CMD_RUN_APP         0x23U
#define CMD_ERASE_APP       0x24U

/* Responses */
#define RESP_ACK            0x79U
#define RESP_NACK           0x1FU

/* Error codes */
#define ERR_OK              0x00U
#define ERR_BAD_CMD         0x01U
#define ERR_BAD_SIZE        0x02U
#define ERR_FLASH           0x03U
#define ERR_CRC             0x04U
#define ERR_BAD_OFFSET      0x05U
#define ERR_NO_APP          0x06U

typedef struct __attribute__((packed, aligned(32)))
{
    uint32_t magic;
    uint32_t size;
    uint32_t crc32;
    uint32_t version;
    uint32_t reserved[4];
} app_meta_t;

#endif
