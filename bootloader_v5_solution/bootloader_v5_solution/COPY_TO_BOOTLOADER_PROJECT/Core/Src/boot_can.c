#include "boot_can.h"
#include "boot_config.h"
#include "boot_flash.h"
#include "boot_crc.h"
#include "boot_jump.h"
#include "main.h"
#include <string.h>

extern FDCAN_HandleTypeDef hfdcan1;

static uint32_t g_fw_size = 0;
static uint32_t g_fw_crc  = 0;
static uint32_t g_fw_padded_size = 0;
static uint8_t  g_update_active = 0;

static uint32_t g_expected_offset = 0;
static uint16_t g_expected_seq = 0;

static uint8_t  g_have_last_block = 0;
static uint32_t g_last_offset = 0;
static uint16_t g_last_seq = 0;

static uint32_t rd_u32_le(const uint8_t *p)
{
    return ((uint32_t)p[0]) |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static uint16_t rd_u16_le(const uint8_t *p)
{
    return ((uint16_t)p[0]) | ((uint16_t)p[1] << 8);
}

static uint32_t fdcan_dlc_to_len(uint32_t dlc)
{
    switch (dlc)
    {
        case FDCAN_DLC_BYTES_0:  return 0;
        case FDCAN_DLC_BYTES_1:  return 1;
        case FDCAN_DLC_BYTES_2:  return 2;
        case FDCAN_DLC_BYTES_3:  return 3;
        case FDCAN_DLC_BYTES_4:  return 4;
        case FDCAN_DLC_BYTES_5:  return 5;
        case FDCAN_DLC_BYTES_6:  return 6;
        case FDCAN_DLC_BYTES_7:  return 7;
        case FDCAN_DLC_BYTES_8:  return 8;
        case FDCAN_DLC_BYTES_12: return 12;
        case FDCAN_DLC_BYTES_16: return 16;
        case FDCAN_DLC_BYTES_20: return 20;
        case FDCAN_DLC_BYTES_24: return 24;
        case FDCAN_DLC_BYTES_32: return 32;
        case FDCAN_DLC_BYTES_48: return 48;
        case FDCAN_DLC_BYTES_64: return 64;
        default: return 0;
    }
}

static HAL_StatusTypeDef send_resp(uint8_t ack, uint8_t cmd, uint8_t err, uint16_t seq)
{
    FDCAN_TxHeaderTypeDef tx;
    uint8_t data[8];

    memset(&tx, 0, sizeof(tx));
    memset(data, 0, sizeof(data));

    data[0] = ack;
    data[1] = cmd;
    data[2] = err;
    data[3] = seq & 0xFF;
    data[4] = (seq >> 8) & 0xFF;

    tx.Identifier          = BOOT_CAN_TX_ID;
    tx.IdType              = FDCAN_STANDARD_ID;
    tx.TxFrameType         = FDCAN_DATA_FRAME;
    tx.DataLength          = FDCAN_DLC_BYTES_8;
    tx.ErrorStateIndicator = FDCAN_ESI_ACTIVE;
    tx.BitRateSwitch       = FDCAN_BRS_ON;
    tx.FDFormat            = FDCAN_FD_CAN;
    tx.TxEventFifoControl  = FDCAN_NO_TX_EVENTS;
    tx.MessageMarker       = 0;

    uint32_t t0 = HAL_GetTick();
    while (HAL_FDCAN_GetTxFifoFreeLevel(&hfdcan1) == 0U)
    {
        if ((HAL_GetTick() - t0) > 20U)
            return HAL_TIMEOUT;
    }

    return HAL_FDCAN_AddMessageToTxFifoQ(&hfdcan1, &tx, data);
}

static void reset_update_state(void)
{
    g_fw_size = 0;
    g_fw_crc = 0;
    g_fw_padded_size = 0;
    g_update_active = 0;
    g_expected_offset = 0;
    g_expected_seq = 0;
    g_have_last_block = 0;
    g_last_offset = 0;
    g_last_seq = 0;
}

void boot_can_start(void)
{
    FDCAN_FilterTypeDef filter;

    filter.IdType       = FDCAN_STANDARD_ID;
    filter.FilterIndex  = 0;
    filter.FilterType   = FDCAN_FILTER_MASK;
    filter.FilterConfig = FDCAN_FILTER_TO_RXFIFO0;
    filter.FilterID1    = BOOT_CAN_RX_ID;
    filter.FilterID2    = 0x7FF;

    if (HAL_FDCAN_ConfigFilter(&hfdcan1, &filter) != HAL_OK)
    {
        Error_Handler();
    }

    if (HAL_FDCAN_ConfigGlobalFilter(
        &hfdcan1,
        FDCAN_REJECT,
        FDCAN_REJECT,
        FDCAN_REJECT_REMOTE,
        FDCAN_REJECT_REMOTE
    ) != HAL_OK)
    {
        Error_Handler();
    }

    if (HAL_FDCAN_Start(&hfdcan1) != HAL_OK)
    {
        Error_Handler();
    }
}

uint8_t boot_can_process_once(void)
{
    FDCAN_RxHeaderTypeDef rx;
    uint8_t data[64];

    if (HAL_FDCAN_GetRxFifoFillLevel(&hfdcan1, FDCAN_RX_FIFO0) == 0U)
        return 0;

    memset(data, 0, sizeof(data));

    if (HAL_FDCAN_GetRxMessage(&hfdcan1, FDCAN_RX_FIFO0, &rx, data) != HAL_OK)
        return 0;

    if ((rx.IdType != FDCAN_STANDARD_ID) || (rx.Identifier != BOOT_CAN_RX_ID))
        return 0;

    uint32_t len = fdcan_dlc_to_len(rx.DataLength);

    if (len < 1U)
        return 0;

    uint8_t cmd = data[0];

    switch (cmd)
    {
        case CMD_PING:
        {
            send_resp(RESP_ACK, CMD_PING, ERR_OK, 0);
            return 1;
        }

        case CMD_BEGIN_UPDATE:
        {
            if (len < 9U)
            {
                send_resp(RESP_NACK, cmd, ERR_BAD_SIZE, 0);
                return 1;
            }

            uint32_t fw_size = rd_u32_le(&data[1]);
            uint32_t fw_crc  = rd_u32_le(&data[5]);
            uint32_t padded_size = (fw_size + 31U) & ~31U;

            if ((fw_size == 0U) || (fw_size > APP_MAX_SIZE) ||
                (padded_size == 0U) || (padded_size > APP_MAX_SIZE))
            {
                reset_update_state();
                send_resp(RESP_NACK, cmd, ERR_BAD_SIZE, 0);
                return 1;
            }

            if (boot_erase_application() != HAL_OK)
            {
                reset_update_state();
                send_resp(RESP_NACK, cmd, ERR_FLASH, 0);
                return 1;
            }

            g_fw_size = fw_size;
            g_fw_crc = fw_crc;
            g_fw_padded_size = padded_size;
            g_update_active = 1;
            g_expected_offset = 0;
            g_expected_seq = 0;
            g_have_last_block = 0;

            send_resp(RESP_ACK, cmd, ERR_OK, 0);
            return 1;
        }

        case CMD_DATA_32B:
        {
            if (!g_update_active)
            {
                send_resp(RESP_NACK, cmd, ERR_BAD_CMD, 0);
                return 1;
            }

            if (len < 39U)
            {
                send_resp(RESP_NACK, cmd, ERR_BAD_SIZE, 0);
                return 1;
            }

            uint16_t seq = rd_u16_le(&data[1]);
            uint32_t offset = rd_u32_le(&data[3]);

            if ((offset % 32U) != 0U)
            {
                send_resp(RESP_NACK, cmd, ERR_BAD_OFFSET, seq);
                return 1;
            }

            if ((offset + 32U) > g_fw_padded_size)
            {
                send_resp(RESP_NACK, cmd, ERR_BAD_OFFSET, seq);
                return 1;
            }

            uint32_t address = APP_ADDRESS + offset;

            __attribute__((aligned(32))) uint8_t flashword[32];
            memcpy(flashword, &data[7], 32);

            /* Si el ACK anterior se perdió y la Raspberry reintenta el mismo bloque,
             * NO se vuelve a programar el mismo flashword: solo se verifica y se re-ACKea.
             */
            if (g_have_last_block && (offset == g_last_offset) && (seq == g_last_seq))
            {
                if (memcmp((const void *)address, flashword, 32) == 0)
                {
                    send_resp(RESP_ACK, cmd, ERR_OK, seq);
                }
                else
                {
                    send_resp(RESP_NACK, cmd, ERR_FLASH, seq);
                }
                return 1;
            }

            if ((offset != g_expected_offset) || (seq != g_expected_seq))
            {
                send_resp(RESP_NACK, cmd, ERR_BAD_OFFSET, seq);
                return 1;
            }

            if (boot_flash_write_32bytes(address, flashword) != HAL_OK)
            {
                send_resp(RESP_NACK, cmd, ERR_FLASH, seq);
                return 1;
            }

            if (memcmp((const void *)address, flashword, 32) != 0)
            {
                send_resp(RESP_NACK, cmd, ERR_FLASH, seq);
                return 1;
            }

            g_last_offset = offset;
            g_last_seq = seq;
            g_have_last_block = 1;

            g_expected_offset += 32U;
            g_expected_seq++;

            send_resp(RESP_ACK, cmd, ERR_OK, seq);
            return 1;
        }

        case CMD_END_UPDATE:
        {
            if (!g_update_active)
            {
                send_resp(RESP_NACK, cmd, ERR_BAD_CMD, 0);
                return 1;
            }

            if (g_expected_offset != g_fw_padded_size)
            {
                send_resp(RESP_NACK, cmd, ERR_BAD_SIZE, 0);
                return 1;
            }

            uint32_t crc = boot_crc32_memory(APP_ADDRESS, g_fw_size);

            if (crc != g_fw_crc)
            {
                reset_update_state();
                send_resp(RESP_NACK, cmd, ERR_CRC, 0);
                return 1;
            }

            if (boot_write_metadata(g_fw_size, g_fw_crc) != HAL_OK)
            {
                reset_update_state();
                send_resp(RESP_NACK, cmd, ERR_FLASH, 0);
                return 1;
            }

            reset_update_state();
            send_resp(RESP_ACK, cmd, ERR_OK, 0);
            return 1;
        }

        case CMD_RUN_APP:
        {
            if (!boot_is_valid_app())
            {
                send_resp(RESP_NACK, cmd, ERR_NO_APP, 0);
                return 1;
            }

            send_resp(RESP_ACK, cmd, ERR_OK, 0);
            HAL_Delay(50);
            boot_jump_to_app();
            return 1;
        }

        case CMD_ERASE_APP:
        {
            if (boot_erase_application() != HAL_OK)
            {
                reset_update_state();
                send_resp(RESP_NACK, cmd, ERR_FLASH, 0);
                return 1;
            }

            reset_update_state();
            send_resp(RESP_ACK, cmd, ERR_OK, 0);
            return 1;
        }

        default:
        {
            send_resp(RESP_NACK, cmd, ERR_BAD_CMD, 0);
            return 1;
        }
    }
}
