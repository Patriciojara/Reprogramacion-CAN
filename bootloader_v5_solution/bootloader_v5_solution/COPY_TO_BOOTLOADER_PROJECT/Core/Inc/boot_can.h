#ifndef BOOT_CAN_H
#define BOOT_CAN_H

#include <stdint.h>

void boot_can_start(void);
uint8_t boot_can_process_once(void);

#endif