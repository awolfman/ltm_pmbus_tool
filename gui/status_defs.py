"""Status register bit definitions per LTM4673 datasheet."""

STATUS_WORD_BITS = {
    15: "VOUT fault/warning", 14: "IOUT/POUT fault/warning",
    13: "Input fault/warning", 12: "MFR specific",
    11: "Power good negated", 10: "Fan (N/S)",
    9: "Other (N/S)", 8: "Unknown (N/S)",
    7: "Busy", 6: "OFF",
    5: "VOUT OV fault", 4: "IOUT OC fault",
    3: "VIN UV fault", 2: "Temperature fault/warning",
    1: "CML fault", 0: "None of the above",
}
STATUS_VOUT_BITS = {
    7: "OV Fault", 6: "OV Warning", 5: "UV Warning", 4: "UV Fault",
    3: "VOUT_MAX Warning", 2: "TON_MAX Fault", 1: "TOFF_MAX (N/S)", 0: "Tracking (N/S)",
}
STATUS_IOUT_BITS = {
    7: "OC Fault", 6: "OC LV (N/S)", 5: "OC Warning", 4: "UC Fault",
    3: "Share (N/S)", 2: "Pwr Limit (N/S)", 1: "POUT Fault (N/S)", 0: "POUT Warn (N/S)",
}
STATUS_INPUT_BITS = {
    7: "VIN OV Fault", 6: "VIN OV Warning", 5: "VIN UV Warning", 4: "VIN UV Fault",
    3: "Insufficient VIN", 2: "IIN OC (N/S)", 1: "IIN Warn (N/S)", 0: "PIN Warn (N/S)",
}
STATUS_TEMP_BITS = {
    7: "OT Fault", 6: "OT Warning", 5: "UT Warning", 4: "UT Fault",
    3: "Reserved", 2: "Reserved", 1: "Reserved", 0: "Reserved",
}
STATUS_CML_BITS = {
    7: "Invalid Cmd", 6: "Invalid Data", 5: "PEC Failed", 4: "Memory Fault",
    3: "Processor (N/S)", 2: "Reserved", 1: "PMBus Fault", 0: "Unknown (N/S)",
}
