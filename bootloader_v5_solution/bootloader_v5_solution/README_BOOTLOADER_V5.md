# Bootloader CAN-FD STM32H723VGT6TR - versión v5

Esta versión está pensada para:

- STM32H723VGT6TR
- Bootloader en `0x08000000`
- Aplicación en `0x08020000`
- Metadata en `0x080FF000`
- CAN-FD 500 kbit/s nominal y 2 Mbit/s data phase
- ID Raspberry -> STM32: `0x100`
- ID STM32 -> Raspberry: `0x101`
- Bloques de 32 bytes

## 1. Problema corregido respecto a v4

La versión anterior enviaba un ACK de debug antes de programar Flash:

```c
send_resp(RESP_ACK, CMD_DATA_32B, 0x55, seq);
```

El Python lo aceptaba como ACK válido y mandaba el siguiente bloque mientras el STM32 todavía estaba escribiendo.

En v5:

- existe un solo ACK por bloque;
- el ACK se manda solo después de programar y verificar Flash;
- el Python rechaza cualquier ACK con `err != 0x00`;
- el bootloader valida `seq` y `offset`;
- el bootloader soporta reintento seguro del último bloque sin reprogramar la misma Flash word;
- el Python valida que el `.bin` parezca linkeado en `0x08020000`.

## 2. Archivos

Copia estos archivos dentro de tu proyecto CubeIDE del bootloader:

```text
COPY_TO_BOOTLOADER_PROJECT/Core/Src/boot_can.c
COPY_TO_BOOTLOADER_PROJECT/Core/Src/boot_flash.c
COPY_TO_BOOTLOADER_PROJECT/Core/Src/boot_crc.c
COPY_TO_BOOTLOADER_PROJECT/Core/Src/boot_jump.c
COPY_TO_BOOTLOADER_PROJECT/Core/Inc/boot_config.h
COPY_TO_BOOTLOADER_PROJECT/Core/Inc/boot_flash.h
COPY_TO_BOOTLOADER_PROJECT/Core/Inc/boot_can.h
COPY_TO_BOOTLOADER_PROJECT/Core/Inc/boot_crc.h
COPY_TO_BOOTLOADER_PROJECT/Core/Inc/boot_jump.h
```

El archivo Python está en:

```text
python/flash_can_power_v5.py
```

## 3. Configuración CubeMX del bootloader

Proyecto bootloader:

- MCU: STM32H723VGT6TR
- HSE: Crystal/Ceramic Resonator si usas cristal externo de 8 MHz
- FDCAN1:
  - RX: PA11
  - TX: PA12
  - Frame Format: FD with Bit Rate Switching
  - Mode: Normal
  - Auto retransmission: Enable
  - Std filters: 1
  - Rx FIFO0 elements: 8 o más
  - Rx FIFO0 element size: 64 bytes
  - Tx FIFO Queue elements: 8 o más
  - Tx element size: 64 bytes
- Reloj FDCAN: 48 MHz desde PLLQ
- Timing FDCAN si FDCAN clock = 48 MHz:
  - NominalPrescaler = 6
  - NominalTimeSeg1 = 13
  - NominalTimeSeg2 = 2
  - NominalSyncJumpWidth = 2
  - DataPrescaler = 2
  - DataTimeSeg1 = 8
  - DataTimeSeg2 = 3
  - DataSyncJumpWidth = 3

Eso da:

```text
48 MHz / 6 / (1 + 13 + 2) = 500 kbit/s
48 MHz / 2 / (1 + 8 + 3) = 2 Mbit/s
```

## 4. Linker del bootloader

El bootloader debe ocupar solo el sector 0:

```text
FLASH ORIGIN = 0x08000000, LENGTH = 128K
```

## 5. Linker de la aplicación principal

La aplicación principal debe partir en `0x08020000`.

En el `.ld` de la aplicación, reemplaza la región FLASH por:

```text
FLASH (rx) : ORIGIN = 0x08020000, LENGTH = 892K
```

Revisa también:

```text
app_linker_notes/STM32H723VGTX_APP_FLASH_MEMORY_SNIPPET.ld
app_linker_notes/app_vector_table_notes.txt
```

## 6. Vector table de la aplicación

En la aplicación principal, revisa `system_stm32h7xx.c`.

Debe quedar con offset:

```c
#define VECT_TAB_OFFSET  0x00020000U
```

También puedes reforzarlo al inicio de `main()`:

```c
SCB->VTOR = 0x08020000U;
__DSB();
__ISB();
```

## 7. Generar el .bin de la aplicación

En CubeIDE puedes activar post-build o ejecutar manualmente:

```bash
arm-none-eabi-objcopy -O binary App.elf App.bin
```

Antes de flashear, el Python mostrará algo parecido a:

```text
Vector table: SP=0x240xxxxx, RESET=0x0802xxxx
Vector table OK para aplicación en 0x08020000.
```

Si muestra `RESET=0x0800xxxx`, la aplicación está mal linkeada.

## 8. Probar desde Raspberry Pi

Instala dependencias:

```bash
sudo apt update
sudo apt install -y python3-pip can-utils
pip3 install python-can gpiozero
```

Ejecuta:

```bash
python3 flash_can_power_v5.py firmware.bin
```

Con pausa entre bloques para debug:

```bash
python3 flash_can_power_v5.py firmware.bin --block-delay 0.002
```

Sin saltar a la app al terminar:

```bash
python3 flash_can_power_v5.py firmware.bin --no-run
```

Si ya dejaste el STM32 en bootloader manualmente:

```bash
python3 flash_can_power_v5.py firmware.bin --no-power-cycle
```

## 9. Pruebas recomendadas

Primero probar solo detección:

```bash
python3 flash_can_power_v5.py firmware.bin --no-run --block-delay 0.005
```

Mientras tanto, en otra terminal:

```bash
candump -tz -x can0,100:7FF,101:7FF
```

Debes ver:

```text
100 ... 10
101 ... 79 10 00 ...
100 ... 20 ...
101 ... 79 20 00 ...
100 ... 21 ...
101 ... 79 21 00 seq_lo seq_hi ...
```

Nunca debería aparecer:

```text
79 21 55
```

Si aparece `79 21 55`, sigues usando el bootloader viejo.

## 10. Diagnóstico rápido

- Timeout en PING: problema de energía, CAN físico, bitrate o ventana de bootloader.
- NACK BAD_SIZE en BEGIN: firmware demasiado grande o size inválido.
- NACK BAD_OFFSET en DATA: bloque perdido, duplicado incorrecto, offset/seq fuera de orden.
- NACK FLASH en DATA: error de erase/write/verify.
- NACK CRC en END: lo escrito no coincide con el binario enviado.
- Python dice RESET=0x0800xxxx: la app está linkeada como si partiera en 0x08000000, no sirve para este bootloader.
