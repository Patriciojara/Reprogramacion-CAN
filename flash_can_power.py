#!/usr/bin/env python3

import argparse
import os
import struct
import subprocess
import sys
import time
import zlib

import can
from gpiozero import DigitalOutputDevice

CAN_IFACE = "can0"

BOOT_TX_ID = 0x100  # Raspberry -> STM32
BOOT_RX_ID = 0x101  # STM32 -> Raspberry

POWER_GPIO = 17

CMD_PING         = 0x10
CMD_BEGIN_UPDATE = 0x20
CMD_DATA_32B     = 0x21
CMD_END_UPDATE   = 0x22
CMD_RUN_APP      = 0x23

RESP_ACK  = 0x79
RESP_NACK = 0x1F


def valid_canfd_len(length: int) -> int:
    valid_lengths = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]
    for n in valid_lengths:
        if length <= n:
            return n
    raise ValueError("Payload demasiado grande para CAN-FD")


def pad_canfd_payload(data: bytes, fill: int = 0x00) -> bytes:
    target_len = valid_canfd_len(len(data))
    if len(data) < target_len:
        data += bytes([fill]) * (target_len - len(data))
    return data


def send_fd(bus: can.BusABC, data: bytes):
    data = pad_canfd_payload(data)
    msg = can.Message(
        arbitration_id=BOOT_TX_ID,
        data=data,
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True
    )
    bus.send(msg)


def wait_resp(bus: can.BusABC, expected_cmd: int, expected_seq=None, timeout=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = bus.recv(timeout=timeout)
        if msg is None:
            continue
        if msg.arbitration_id != BOOT_RX_ID:
            continue

        d = bytes(msg.data)
        if len(d) < 3:
            continue

        status = d[0]
        cmd    = d[1]
        err    = d[2]

        seq = None
        if len(d) >= 5:
            seq = d[3] | (d[4] << 8)

        if cmd != expected_cmd:
            continue
        if expected_seq is not None and seq != expected_seq:
            continue

        if status == RESP_ACK:
            return True

        if status == RESP_NACK:
            raise RuntimeError(f"NACK recibido: cmd=0x{cmd:02X}, err=0x{err:02X}, seq={seq}")

        raise RuntimeError(f"Respuesta desconocida: status=0x{status:02X}, cmd=0x{cmd:02X}, err=0x{err:02X}")

    raise TimeoutError(f"Timeout esperando ACK de cmd=0x{expected_cmd:02X}")


def ping(bus):
    send_fd(bus, bytes([CMD_PING]))
    wait_resp(bus, CMD_PING, timeout=0.25)


def begin_update(bus, fw_size: int, fw_crc: int):
    payload = bytearray()
    payload.append(CMD_BEGIN_UPDATE)
    payload += struct.pack("<I", fw_size)
    payload += struct.pack("<I", fw_crc)
    send_fd(bus, bytes(payload))
    wait_resp(bus, CMD_BEGIN_UPDATE, timeout=15.0)


def send_data_block(bus, seq: int, offset: int, chunk32: bytes):
    if len(chunk32) != 32:
        raise ValueError("El bloque debe ser exactamente de 32 bytes")

    payload = bytearray()
    payload.append(CMD_DATA_32B)
    payload += struct.pack("<H", seq)
    payload += struct.pack("<I", offset)
    payload += chunk32

    send_fd(bus, bytes(payload))
    wait_resp(bus, CMD_DATA_32B, expected_seq=seq, timeout=1.0)


def end_update(bus):
    send_fd(bus, bytes([CMD_END_UPDATE]))
    wait_resp(bus, CMD_END_UPDATE, timeout=10.0)


def run_app(bus):
    send_fd(bus, bytes([CMD_RUN_APP]))
    wait_resp(bus, CMD_RUN_APP, timeout=1.0)


def configure_can(interface: str):
    print(f"Configurando {interface} en CAN-FD 500k / 2M...")
    cmds = [
        ["sudo", "ip", "link", "set", interface, "down"],
        [
            "sudo", "ip", "link", "set", interface, "type", "can",
            "bitrate", "500000",
            "dbitrate", "2000000",
            "fd", "on",
            "berr-reporting", "on",
            "restart-ms", "100"
        ],
        ["sudo", "ip", "link", "set", interface, "up"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, check=True)


def power_cycle_stm32(active_high=True, off_time=0.5, boot_delay=0.05):
    power = DigitalOutputDevice(
        POWER_GPIO,
        active_high=active_high,
        initial_value=False
    )

    print("Apagando STM32...")
    power.off()
    time.sleep(off_time)

    print("Encendiendo STM32...")
    power.on()
    time.sleep(boot_delay)

    return power


def find_bootloader(bus, tries=40, delay=0.05):
    for _ in range(tries):
        try:
            ping(bus)
            return True
        except Exception:
            time.sleep(delay)
    return False


def load_firmware(filename: str):
    with open(filename, "rb") as f:
        fw = f.read()

    if len(fw) == 0:
        raise RuntimeError("El archivo .bin está vacío")

    size = len(fw)
    crc = zlib.crc32(fw) & 0xFFFFFFFF

    padded = fw
    if len(padded) % 32 != 0:
        padded += b"\xFF" * (32 - (len(padded) % 32))

    return fw, padded, size, crc


def flash_firmware(bus, fw_padded: bytes, fw_size: int, fw_crc: int):
    print("Iniciando actualización...")
    print("Borrando área de aplicación...")
    begin_update(bus, fw_size, fw_crc)

    total_blocks = len(fw_padded) // 32

    print("Enviando firmware por CAN-FD...")

    for i in range(total_blocks):
        offset = i * 32
        chunk = fw_padded[offset:offset + 32]
        seq = i & 0xFFFF

        send_data_block(bus, seq, offset, chunk)

        if i % 50 == 0 or i == total_blocks - 1:
            percent = 100.0 * (i + 1) / total_blocks
            print(f"\rProgreso: {percent:6.2f}%", end="", flush=True)

    print()
    print("Verificando CRC en STM32...")
    end_update(bus)
    print("CRC OK. Aplicación grabada correctamente.")


def main():
    parser = argparse.ArgumentParser(
        description="Carga firmware .bin al STM32H723 por CAN-FD usando bootloader propio y GPIO17."
    )

    parser.add_argument("firmware", help="Archivo .bin de la aplicación principal")
    parser.add_argument("--iface", default=CAN_IFACE, help="Interfaz CAN. Por defecto: can0")
    parser.add_argument("--no-config-can", action="store_true", help="No configurar can0 automáticamente")
    parser.add_argument("--no-power-cycle", action="store_true", help="No apagar/encender el STM32 con GPIO17")
    parser.add_argument("--active-low", action="store_true", help="Usar GPIO17 activo bajo")
    parser.add_argument("--no-run", action="store_true", help="No saltar a la aplicación al terminar")

    args = parser.parse_args()

    if not os.path.exists(args.firmware):
        print(f"Error: no existe el archivo {args.firmware}")
        sys.exit(1)

    _, fw_padded, fw_size, fw_crc = load_firmware(args.firmware)

    print("========================================")
    print(" STM32H723 CAN-FD Bootloader Flasher")
    print("========================================")
    print(f"Firmware: {args.firmware}")
    print(f"Tamaño:   {fw_size} bytes")
    print(f"CRC32:    0x{fw_crc:08X}")
    print(f"Bloques:  {len(fw_padded) // 32} bloques de 32 bytes")
    print("========================================")

    if not args.no_config_can:
        configure_can(args.iface)

    print(f"Abriendo interfaz {args.iface}...")
    bus = can.interface.Bus(channel=args.iface, interface="socketcan", fd=True)

    power_handle = None

    if not args.no_power_cycle:
        power_handle = power_cycle_stm32(
            active_high=not args.active_low,
            off_time=0.5,
            boot_delay=0.05
        )
    else:
        print("Saltando power-cycle. El STM32 debe estar en bootloader.")

    print("Buscando bootloader...")

    if not find_bootloader(bus):
        print("Error: no se detectó el bootloader.")
        print("Revisa:")
        print("- Que GPIO17 esté encendiendo realmente el STM32")
        print("- Que CANH/CANL estén conectados")
        print("- Que GND sea común")
        print("- Que can0 esté en 500k / 2M FD")
        print("- Que el bootloader esté grabado en 0x08000000")
        sys.exit(1)

    print("Bootloader detectado.")

    flash_firmware(bus, fw_padded, fw_size, fw_crc)

    if not args.no_run:
        print("Saltando a la aplicación...")
        run_app(bus)
        print("Aplicación iniciada.")
    else:
        print("Actualización terminada. No se ejecutó RUN_APP.")

    _ = power_handle
    print("Listo.")


if __name__ == "__main__":
    main()