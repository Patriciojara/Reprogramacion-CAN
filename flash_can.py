#!/usr/bin/env python3

import can
import sys
import time
import zlib
import struct

CAN_IFACE = "can0"

BOOT_RX_ID = 0x101  # STM32 -> Raspberry
BOOT_TX_ID = 0x100  # Raspberry -> STM32

CMD_PING         = 0x10
CMD_BEGIN_UPDATE = 0x20
CMD_DATA_32B     = 0x21
CMD_END_UPDATE   = 0x22
CMD_RUN_APP      = 0x23

RESP_ACK = 0x79

def send_msg(bus, data):
    msg = can.Message(
        arbitration_id=BOOT_TX_ID,
        data=bytes(data),
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True
    )
    bus.send(msg)

def wait_resp(bus, expected_cmd, expected_seq=None, timeout=1.0):
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

        raise RuntimeError(f"NACK cmd=0x{cmd:02X}, err=0x{err:02X}, seq={seq}")

    raise TimeoutError(f"Timeout esperando ACK de cmd=0x{expected_cmd:02X}")

def ping(bus):
    send_msg(bus, [CMD_PING])
    wait_resp(bus, CMD_PING, timeout=0.5)

def begin_update(bus, size, crc):
    payload = bytearray()
    payload.append(CMD_BEGIN_UPDATE)
    payload += struct.pack("<I", size)
    payload += struct.pack("<I", crc)

    send_msg(bus, payload)
    wait_resp(bus, CMD_BEGIN_UPDATE, timeout=8.0)

def send_data_block(bus, seq, offset, chunk):
    payload = bytearray()
    payload.append(CMD_DATA_32B)
    payload += struct.pack("<H", seq)
    payload += struct.pack("<I", offset)
    payload += chunk

    send_msg(bus, payload)
    wait_resp(bus, CMD_DATA_32B, expected_seq=seq, timeout=1.0)

def end_update(bus):
    send_msg(bus, [CMD_END_UPDATE])
    wait_resp(bus, CMD_END_UPDATE, timeout=8.0)

def run_app(bus):
    send_msg(bus, [CMD_RUN_APP])
    wait_resp(bus, CMD_RUN_APP, timeout=1.0)

def main():
    if len(sys.argv) != 2:
        print(f"Uso: {sys.argv[0]} App_Principal.bin")
        sys.exit(1)

    filename = sys.argv[1]

    with open(filename, "rb") as f:
        fw = f.read()

    size = len(fw)
    crc = zlib.crc32(fw) & 0xFFFFFFFF

    print(f"Firmware: {filename}")
    print(f"Tamaño:   {size} bytes")
    print(f"CRC32:    0x{crc:08X}")

    bus = can.interface.Bus(channel=CAN_IFACE, interface="socketcan", fd=True)

    print("Buscando bootloader...")

    ok = False
    for _ in range(30):
        try:
            ping(bus)
            ok = True
            break
        except Exception:
            time.sleep(0.1)

    if not ok:
        raise RuntimeError("No responde el bootloader. Resetea el STM32 o envía ENTER_BOOT desde la aplicación.")

    print("Bootloader responde.")

    print("Borrando y preparando Flash...")
    begin_update(bus, size, crc)

    padded = fw
    if len(padded) % 32 != 0:
        padded += b"\xFF" * (32 - (len(padded) % 32))

    total_blocks = len(padded) // 32

    print("Enviando firmware...")

    for i in range(total_blocks):
        offset = i * 32
        chunk = padded[offset:offset + 32]
        seq = i & 0xFFFF

        send_data_block(bus, seq, offset, chunk)

        if i % 50 == 0 or i == total_blocks - 1:
            percent = 100.0 * (i + 1) / total_blocks
            print(f"\rProgreso: {percent:5.1f}%", end="", flush=True)

    print()

    print("Verificando CRC...")
    end_update(bus)

    print("Actualización correcta.")

    print("Saltando a la aplicación...")
    run_app(bus)

    print("Listo.")

if __name__ == "__main__":
    main()