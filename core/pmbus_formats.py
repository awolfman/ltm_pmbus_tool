"""PMBus data format conversions: L11, L16, encode/decode."""
import math


def l11_to_float(raw):
    if raw is None: return None
    raw &= 0xFFFF
    exp = (raw >> 11) & 0x1F
    if exp > 15: exp -= 32
    man = raw & 0x7FF
    if man > 1023: man -= 2048
    return man * (2.0 ** exp)


def float_to_l11(value):
    if value == 0: return 0
    neg = value < 0; v = abs(value)
    exp = max(-16, min(15, int(math.floor(math.log2(v / 1023.0))) if v > 0 else 0))
    man = int(round(v / (2.0 ** exp)))
    if man > 1023: exp += 1; man = int(round(v / (2.0 ** exp)))
    man = min(1023, man)
    if neg: man = (-man) & 0x7FF
    return ((exp & 0x1F) << 11) | (man & 0x7FF)


def l16_to_float(raw, exp=-13):
    if raw is None: return None
    return raw * (2.0 ** exp)


def float_to_l16(value, exp=-13):
    return max(0, min(0xFFFF, int(round(value / (2.0 ** exp)))))


def decode_value(raw, fmt, vout_exp=-13):
    if raw is None: return None
    if fmt == 'L11': return l11_to_float(raw)
    if fmt == 'L16': return l16_to_float(raw, vout_exp)
    return raw


def encode_value(value, fmt, vout_exp=-13):
    if fmt == 'L11': return float_to_l11(value)
    if fmt == 'L16': return float_to_l16(value, vout_exp)
    return int(value)
