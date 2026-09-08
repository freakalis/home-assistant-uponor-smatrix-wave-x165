"""CRC primitives used by Uponor Smatrix Wave frames."""


def crc16_cms(data: bytes) -> int:
    """Return CRC-16/CMS (poly 0x8005, init 0xffff, non-reflected)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc16_modbus(data: bytes) -> int:
    """Return CRC-16/MODBUS (poly 0x8005 reflected, init 0xffff)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc
