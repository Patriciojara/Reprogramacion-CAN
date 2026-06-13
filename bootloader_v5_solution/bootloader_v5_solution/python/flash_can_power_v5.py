#!/usr/bin/env python3

import argparse
import os
import struct
import subprocess
import sys
import time
import zlib

import can

try:
    from gpiozero import DigitalOutputDevice
except Exception:
    DigitalOutputDevice = None

CAN_IFACE = "can0"

BOOT_TX_ID = 0x100  # Raspberry -> STM32
BOOT_RX_ID = 0x101  # STM32 -> Raspberry

POWER_GPIO = 17

APP_ADDRESS = 0x08020000
APP_META_ADDRESS = 0x080FF000
APP_MAX_SIZE = APP_META_ADDRESS - APP_ADDRESS

CMD_PING         = 0x10
CMD_BEGIN_UPDATE = 0x20
CMD_DATA_32B     = 0x21
CMD_END_UPDATE   = 0x22
CMD_RUN_APP      = 0x23
CMD_ERASE_APP    = 0x24

RESP_ACK  = 0x79
RESP_NACK = 0x1F

ERR_NAMES = {
    0x00: "OK",
    0x01: "BAD_CMD",
    0x02: "BAD_SIZE",
    0x03: "FLASH",
    0x04: "CRC",
    0x05: "BAD_OFFSET",
    0x06: "NO_APP",
}

VALID_CANFD_LENGTHS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]


def valid_canfd_len(length: int) -> int:
    for n in VALID_CANFD_LENGTHS:
        if length <= n:
            return n
    raise ValueError("Payload demasiado grande para CAN-FD")


def pad_canfd_payload(data: bytes, fill: int = 0x00) -> bytes:
    target_len = valid_canfd_len(len(data))
    if len(data) < target_len:
        data += bytes([fill]) * (target_len - len(data))
    return data


def send_fd(bus: can.BusABC, data: bytes) -> None:
    data = pad_canfd_payload(data)
    msg = can.Message(
        arbitration_id=BOOT_TX_ID,
        data=data,
        is_extended_id=False,
        is_fd=True,
        bitrate_switch=True,
    )
    bus.send(msg, timeout=1.0)


def drain_bus(bus: can.BusABC, duration: float = 0.05) -> None:
    end = time.monotonic() + duration
    while time.monotonic() < end:
        msg = bus.recv(timeout=0.005)
        if msg is None:
            continue


def wait_resp(bus: can.BusABC, expected_cmd: int, expected_seq=None, timeout=1.0):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        msg = bus.recv(timeout=min(0.05, remaining))
        if msg is None:
            continue

        if msg.arbitration_id != BOOT_RX_ID:
            continue

        d = bytes(msg.data)
        if len(d) < 3:
            continue

        status = d[0]
        cmd = d[1]
        err = d[2]
        seq = None
        if len(d) >= 5:
            seq = d[3] | (d[4] << 8)

        if cmd != expected_cmd:
            continue
        if expected_seq is not None and seq != expected_seq:
            continue

        if status == RESP_ACK:
            if err != 0x00:
                raise RuntimeError(
                    f"ACK inválido/debug: cmd=0x{cmd:02X}, err=0x{err:02X}, "
                    f"seq={seq}, err_name={ERR_NAMES.get(err, 'UNKNOWN')}"
                )
            return True

        if status == RESP_NACK:
            raise RuntimeError(
                f"NACK recibido: cmd=0x{cmd:02X}, err=0x{err:02X} "
                f"({ERR_NAMES.get(err, 'UNKNOWN')}), seq={seq}"
            )

        raise RuntimeError(
            f"Respuesta desconocida: status=0x{status:02X}, "
            f"cmd=0x{cmd:02X}, err=0x{err:02X}, seq={seq}"
        )

    raise TimeoutError(f"Timeout esperando ACK de cmd=0x{expected_cmd:02X}")


def transact(bus, payload: bytes, expected_cmd: int, expected_seq=None, timeout=1.0, retries=3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            send_fd(bus, payload)
            return wait_resp(bus, expected_cmd, expected_seq=expected_seq, timeout=timeout)
        except TimeoutError as exc:
            last_error = exc
            print(f"  Reintento {attempt}/{retries}: {exc}")
            time.sleep(0.02)
    raise last_error


def ping(bus, retries=1):
    return transact(bus, bytes([CMD_PING]), CMD_PING, timeout=0.25, retries=retries)


def begin_update(bus, fw_size: int, fw_crc: int, timeout: float, retries: int):
    payload = bytearray()
    payload.append(CMD_BEGIN_UPDATE)
    payload += struct.pack("<I", fw_size)
    payload += struct.pack("<I", fw_crc)
    transact(bus, bytes(payload), CMD_BEGIN_UPDATE, timeout=timeout, retries=retries)


def send_data_block(bus, seq: int, offset: int, chunk32: bytes, timeout: float, retries: int):
    if len(chunk32) != 32:
        raise ValueError("El bloque debe ser exactamente de 32 bytes")

    payload = bytearray()
    payload.append(CMD_DATA_32B)
    payload += struct.pack("<H", seq)
    payload += struct.pack("<I", offset)
    payload += chunk32

    transact(bus, bytes(payload), CMD_DATA_32B, expected_seq=seq, timeout=timeout, retries=retries)


def end_update(bus, timeout: float, retries: int):
    transact(bus, bytes([CMD_END_UPDATE]), CMD_END_UPDATE, timeout=timeout, retries=retries)


def erase_app(bus, timeout: float, retries: int):
    transact(bus, bytes([CMD_ERASE_APP]), CMD_ERASE_APP, timeout=timeout, retries=retries)


def run_app(bus):
    transact(bus, bytes([CMD_RUN_APP]), CMD_RUN_APP, timeout=1.0, retries=1)


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
            "restart-ms", "100",
        ],
        ["sudo", "ip", "link", "set", interface, "up"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, check=True)


def power_cycle_stm32(active_high=True, off_time=0.5, boot_delay=0.08):
    if DigitalOutputDevice is None:
        raise RuntimeError("gpiozero no está disponible. Instala gpiozero o usa --no-power-cycle")

    power = DigitalOutputDevice(
        POWER_GPIO,
        active_high=active_high,
        initial_value=False,
    )

    print("Apagando STM32...")
    power.off()
    time.sleep(off_time)

    print("Encendiendo STM32...")
    power.on()
    time.sleep(boot_delay)

    return power


def find_bootloader(bus, tries=80, delay=0.05):
    for _ in range(tries):
        try:
            ping(bus, retries=1)
            return True
        except Exception:
            time.sleep(delay)
    return False


def is_valid_ram_addr(addr: int) -> bool:
    ranges = [
        (0x20000000, 0x20020000),  # DTCM
        (0x24000000, 0x24080000),  # AXI SRAM
        (0x30000000, 0x30010000),  # SRAM D2 aproximada
        (0x38000000, 0x38001000),  # Backup SRAM
    ]
    return any(start <= addr < end for start, end in ranges)


def check_vector_table(fw: bytes, force: bool):
    if len(fw) < 8:
        raise RuntimeError("Firmware demasiado pequeño: no tiene vector table")

    sp, reset = struct.unpack_from("<II", fw, 0)
    reset_clean = reset & ~1

    ok_sp = is_valid_ram_addr(sp)
    ok_pc = APP_ADDRESS <= reset_clean < APP_META_ADDRESS

    print(f"Vector table: SP=0x{sp:08X}, RESET=0x{reset:08X}")

    if ok_sp and ok_pc:
        print("Vector table OK para aplicación en 0x08020000.")
        return

    msg = (
        "El .bin NO parece estar linkeado para APP_ADDRESS=0x08020000.\n"
        "Revisa el linker de la aplicación: FLASH ORIGIN debe ser 0x08020000."
    )
    if force:
        print("ADVERTENCIA: " + msg.replace("\n", " "))
    else:
        raise RuntimeError(msg + "\nUsa --force solo si estás completamente seguro.")


def load_firmware(filename: str, force: bool):
    with open(filename, "rb") as f:
        fw = f.read()

    if len(fw) == 0:
        raise RuntimeError("El archivo .bin está vacío")

    if len(fw) > APP_MAX_SIZE:
        raise RuntimeError(f"Firmware demasiado grande: {len(fw)} > {APP_MAX_SIZE} bytes")

    check_vector_table(fw, force=force)

    size = len(fw)
    crc = zlib.crc32(fw) & 0xFFFFFFFF

    padded = fw
    if len(padded) % 32 != 0:
        padded += b"\xFF" * (32 - (len(padded) % 32))

    return fw, padded, size, crc


def flash_firmware(bus, fw_padded: bytes, fw_size: int, fw_crc: int, args):
    print("Iniciando actualización...")
    print("Borrando área de aplicación...")
    begin_update(bus, fw_size, fw_crc, timeout=args.begin_timeout, retries=args.retries)

    total_blocks = len(fw_padded) // 32
    print("Enviando firmware por CAN-FD...")

    t0 = time.monotonic()

    for i in range(total_blocks):
        offset = i * 32
        chunk = fw_padded[offset:offset + 32]
        seq = i & 0xFFFF

        send_data_block(
            bus,
            seq,
            offset,
            chunk,
            timeout=args.ack_timeout,
            retries=args.retries,
        )

        if args.block_delay > 0:
            time.sleep(args.block_delay)

        if i % args.progress_every == 0 or i == total_blocks - 1:
            percent = 100.0 * (i + 1) / total_blocks
            elapsed = time.monotonic() - t0
            rate = (i + 1) * 32 / elapsed if elapsed > 0 else 0
            print(f"\rProgreso: {percent:6.2f}%  {rate:8.1f} B/s", end="", flush=True)

    print()
    print("Verificando CRC en STM32...")
    end_update(bus, timeout=args.end_timeout, retries=args.retries)
    print("CRC OK. Aplicación grabada correctamente.")


def main():
    parser = argparse.ArgumentParser(
        description="Carga firmware .bin al STM32H723 por CAN-FD usando bootloader PATO v5 y GPIO17."
    )

    parser.add_argument("firmware", help="Archivo .bin de la aplicación principal")
    parser.add_argument("--iface", default=CAN_IFACE, help="Interfaz CAN. Por defecto: can0")
    parser.add_argument("--no-config-can", action="store_true", help="No configurar can0 automáticamente")
    parser.add_argument("--no-power-cycle", action="store_true", help="No apagar/encender el STM32 con GPIO17")
    parser.add_argument("--active-low", action="store_true", help="Usar GPIO17 activo bajo")
    parser.add_argument("--no-run", action="store_true", help="No saltar a la aplicación al terminar")
    parser.add_argument("--force", action="store_true", help="Permite flashear aunque el vector table no parezca válido")
    parser.add_argument("--retries", type=int, default=4, help="Reintentos por comando/bloque")
    parser.add_argument("--block-delay", type=float, default=0.0, help="Pausa entre bloques, segundos. Útil para debug")
    parser.add_argument("--ack-timeout", type=float, default=1.0, help="Timeout ACK por bloque")
    parser.add_argument("--begin-timeout", type=float, default=20.0, help="Timeout para borrado inicial")
    parser.add_argument("--end-timeout", type=float, default=10.0, help="Timeout para CRC final")
    parser.add_argument("--progress-every", type=int, default=50, help="Actualizar progreso cada N bloques")
    parser.add_argument("--erase-only", action="store_true", help="Solo borra la aplicación y metadata")

    args = parser.parse_args()

    if not os.path.exists(args.firmware):
        print(f"Error: no existe el archivo {args.firmware}")
        sys.exit(1)

    try:
        _, fw_padded, fw_size, fw_crc = load_firmware(args.firmware, force=args.force)
    except Exception as exc:
        print(f"Error validando firmware: {exc}")
        sys.exit(1)

    print("========================================")
    print(" STM32H723 CAN-FD Bootloader Flasher v5")
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
    drain_bus(bus)

    power_handle = None

    if not args.no_power_cycle:
        power_handle = power_cycle_stm32(
            active_high=not args.active_low,
            off_time=0.5,
            boot_delay=0.08,
        )
    else:
        print("Saltando power-cycle. El STM32 debe estar en bootloader.")

    print("Buscando bootloader...")

    if not find_bootloader(bus):
        print("Error: no se detectó el bootloader.")
        print("Revisa:")
        print("- GPIO17 realmente enciende el STM32")
        print("- CANH/CANL y GND común")
        print("- can0 en 500k / 2M FD")
        print("- bootloader grabado en 0x08000000")
        print("- ventana de bootloader suficiente")
        sys.exit(1)

    print("Bootloader detectado.")

    try:
        if args.erase_only:
            print("Borrando aplicación...")
            erase_app(bus, timeout=args.begin_timeout, retries=args.retries)
            print("Aplicación borrada.")
        else:
            flash_firmware(bus, fw_padded, fw_size, fw_crc, args)

            if not args.no_run:
                print("Saltando a la aplicación...")
                run_app(bus)
                print("Aplicación iniciada.")
            else:
                print("Actualización terminada. No se ejecutó RUN_APP.")
    finally:
        _ = power_handle
        try:
            bus.shutdown()
        except Exception:
            pass

    print("Listo.")


if __name__ == "__main__":
    main()
