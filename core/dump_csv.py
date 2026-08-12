"""CSV dump read/write for register snapshots."""
import csv
from io import StringIO
from datetime import datetime


def dump_to_csv_string(device, dump_data, page):
    out = StringIO()
    out.write(f"# Device: {device.name}\n")
    out.write(f"# Address: 0x{device.address:02X}\n")
    out.write(f"# Special ID: 0x{device.special_id:04X}\n")
    out.write(f"# Revision: {device.revision}\n")
    out.write(f"# Page: {page}\n")
    out.write(f"# VOUT_MODE exp: {device.vout_exp.get(page, -13)}\n")
    out.write(f"# Date: {datetime.now():%Y-%m-%d %H:%M:%S}\n#\n")
    w = csv.writer(out)
    w.writerow(['Page','CmdHex','CmdDec','Name','Size','Format','Paged',
                'RawHex','RawDec','Decoded','ReadOnly'])
    for r in dump_data:
        raw = r['raw']
        if raw is not None:
            rh = f"0x{raw:02X}" if r['size']=='byte' else f"0x{raw:04X}"
            rd = str(raw)
        else:
            rh = rd = "N/A"
        dec = r['decoded']
        ds = f"{dec:.6f}" if dec is not None and r['format'] in ('L11','L16') else (str(int(dec)) if dec is not None else "N/A")
        w.writerow([r['page'], f"0x{r['cmd']:02X}", r['cmd'], r['name'],
                     r['size'], r['format'], 'Y' if r.get('is_paged') else 'N',
                     rh, rd, ds, 'YES' if r['readonly'] else 'NO'])
    return out.getvalue()


def csv_string_to_dump(csv_text):
    meta = {}; lines = []
    for line in csv_text.splitlines():
        s = line.strip()
        if s.startswith('#'):
            if ':' in s:
                p = s.lstrip('#').strip().split(':',1)
                meta[p[0].strip().lower().replace(' ','_')] = p[1].strip()
            continue
        lines.append(s)
    records = []; reader = csv.reader(lines); hdr = None
    for row in reader:
        if not row: continue
        if hdr is None: hdr = row; continue
        if len(row) < 11: continue
        try:
            page = int(row[0])
            ch = row[1].strip()
            cmd = int(ch, 16) if ch.startswith('0x') else int(ch)
            name = row[3]; size = row[4]; fmt = row[5]
            rd = row[8].strip()
            raw = None if rd in ('N/A','','None') else int(rd)
            ro = row[10].strip().upper() == 'YES'
            records.append({'page':page,'cmd':cmd,'name':name,'size':size,
                           'format':fmt,'raw':raw,'readonly':ro})
        except (ValueError, IndexError): continue
    return meta, records
