#ifndef BOOT_JUMP_H
#define BOOT_JUMP_H

#include <stdint.h>

uint8_t boot_is_valid_app(void);
void boot_jump_to_app(void);

#endif