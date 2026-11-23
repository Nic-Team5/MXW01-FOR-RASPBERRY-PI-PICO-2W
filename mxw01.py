# =============================================================================
# mxw01.py: bibliotecă MicroPython pentru imprimanta MXW01 (protocol V5X)
# Conform cu MXW01-PROTOCOL-Claude.md. Fără dependențe în afară de aioble.
# Vezi api.md pentru documentație.
# =============================================================================

import asyncio
import time
import struct
import framebuf
import aioble

PREAMBLE = bytes([0x22, 0x21])

CMD_STATUS     = 0xA1
CMD_INTENSITY  = 0xA2
CMD_EJECT      = 0xA3
CMD_RETRACT    = 0xA4   # neconfirmat în APK, doar empiric
CMD_IDENTIFY   = 0xA7
CMD_PRINT      = 0xA9
CMD_COMPLETE   = 0xAA
CMD_BATTERY    = 0xAB
CMD_CANCEL     = 0xAC
CMD_FLUSH      = 0xAD
CMD_FLOW       = 0xAE
CMD_PRINT_TYPE = 0xB0
CMD_VERSION    = 0xB1
CMD_LEARN      = 0xB2
CMD_A6         = 0xA6

MODE_MONO = 0x00
MODE_GRAY = 0x02

PRINT_W = 384          # lățimea hârtiei în pixeli
BPR = 48               # octeți pe rând, mono (384 / 8)
DEFAULT_CHUNK = 400    # valoarea pe care ESP32 a dovedit-o
DEFAULT_CHUNK_DELAY_MS = 30

ERROR_NAMES = {1: 'Out of paper', 9: 'Out of paper',
               4: 'Overheated', 8: 'Low battery'}
STATE_NAMES = {0: 'Standby', 1: 'Printing', 2: 'Feeding', 3: 'Retracting'}


# ---------- Utilitare de protocol ----------

def _crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def _build(cmd, payload=b''):
    n = len(payload)
    pkt = bytearray([0x22, 0x21, cmd, 0x00, n & 0xFF, (n >> 8) & 0xFF])
    pkt.extend(payload)
    pkt.append(_crc8(payload))
    pkt.append(0xFF)
    return bytes(pkt)


def _official_density(temp, user, head='gaoya', gray=False, black=0.0, single=True):
    """Curba de densitate din aplicația oficială (protocol §14.2)."""
    t, e, n = user, temp, user
    def cap(v): return t if v > t else v
    if gray:
        if head == 'diya':
            n = 56 if e >= 70 else 60 if e >= 65 else 65 if e >= 60 else 75 if e >= 55 else 80
        else:
            n = 56 if e >= 70 else 65 if e >= 65 else 75 if e >= 60 else 80 if e >= 55 else 85
        return cap(n)
    r = 1
    if black > 0.4:
        r = 2 if black < 0.5 else (4 if head == 'diya' else (3 if black < 0.7 else 4))
    th = 60 if single else 45
    def pick(a): return a[r - 1]
    if head == 'diya':
        if e > th:
            if e <= 50: n = pick([90, 85, 85, 65])
            elif e <= 55: n = pick([85, 75, 75, 55])
            elif e <= 60: n = pick([75, 65, 65, 45])
            elif e <= 65: n = pick([60, 50, 50, 30])
            elif e <= 70: n = pick([50, 40, 40, 20])
            else: n = pick([40, 30, 30, 10])
        return cap(n)
    if e >= th:
        if e < 50: n = pick([87, 48, 22, 15])
        elif e < 55: n = pick([74, 36, 20, 10])
        elif e < 60: n = pick([61, 25, 15, 10])
        elif e < 65: n = pick([48, 15, 15, 10])
        elif e < 70: n = pick([36, 9, 5, 5])
        else: n = pick([22, 5, 3, 3])
    return cap(n)


# ---------- Text și imagine (fără dependențe externe) ----------

def _mirror_glyph_cells(sbuf, sbpr, cols_px):
    pass  # rezervat, nefolosit


def text_bitmap(text, scale=2, top=8, mirror_glyphs=True):
    """Randează text cu fontul framebuf 8x8 într-un buffer mono pentru 384 px.
    Întoarce (buffer, înălțime). Buffer LSB-first, 1 = negru.
    mirror_glyphs=True oglindește fiecare literă în celula ei (corectează e -> ɘ
    pe hardware-ul testat)."""
    cols = PRINT_W // (8 * scale)
    rows = []
    for para in str(text).split("\n"):
        while len(para) > cols:
            rows.append(para[:cols])
            para = para[cols:]
        rows.append(para)

    w1 = PRINT_W // scale
    h1 = len(rows) * 8 + 8
    sbpr = w1 // 8
    sbuf = bytearray(sbpr * h1)

    tb = bytearray(8)
    tfb = framebuf.FrameBuffer(tb, 8, 8, framebuf.MONO_HLSB)
    for r, row in enumerate(rows):
        y0 = top // 2 + r * 8 if False else 4 + r * 8
        for i, ch in enumerate(row):
            tfb.fill(0)
            tfb.text(ch, 0, 0, 1)
            cx = i * 8
            for y in range(8):
                bits = tb[y]
                for x in range(8):
                    if not (bits >> x) & 1:
                        continue
                    px = cx + ((7 - x) if mirror_glyphs else x)
                    py = y0 + y
                    if px < w1 and py < h1:
                        sbuf[py * sbpr + (px >> 3)] |= 1 << (px & 7)

    h = ((h1 * scale + 7) // 8) * 8
    buf = bytearray(BPR * h)
    for y in range(h1):
        for x in range(w1):
            if not (sbuf[y * sbpr + (x >> 3)] >> (x & 7)) & 1:
                continue
            for dy in range(scale):
                row_off = (y * scale + dy) * BPR
                for dx in range(scale):
                    bx = x * scale + dx
                    if bx < PRINT_W:
                        buf[row_off + (bx >> 3)] |= 1 << (bx & 7)
    return buf, h


def bmp_to_buffer(path, invert=False):
    """Citește un BMP 1-bit de 384 px lățime. Întoarce (buffer, înălțime)."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[0:2] != b'BM':
        raise ValueError("Nu e BMP")
    pix_off = struct.unpack('<I', data[10:14])[0]
    width = struct.unpack('<i', data[18:22])[0]
    height = struct.unpack('<i', data[22:26])[0]
    bpp = struct.unpack('<H', data[28:30])[0]
    if width != PRINT_W:
        raise ValueError(f"BMP trebuie 384 px, are {width}")
    if bpp != 1:
        raise ValueError("Doar BMP 1-bit este acceptat")
    top_down = height < 0
    height = abs(height)
    row_bytes = ((width + 31) // 32) * 4
    buf = bytearray(BPR * height)
    for y in range(height):
        src_y = y if top_down else (height - 1 - y)
        src = pix_off + src_y * row_bytes
        for x in range(BPR):
            b = data[src + x]
            buf[y * BPR + x] = (b ^ 0xFF) if invert else b
    return buf, height


def qr_bitmap(data, module_size=4, quiet_zone=2):
    """Generează QR code. Cere modulul 'qrcode' (mip.install)."""
    import qrcode
    qr = qrcode.QRCode(box_size=1, border=quiet_zone)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    if n * module_size > 368:
        module_size = max(1, 368 // n)
    pixel = n * module_size
    margin_x = (PRINT_W - pixel) // 2
    height = ((pixel + 16 + 7) // 8) * 8
    buf = bytearray(BPR * height)
    y0 = 8
    for y in range(n):
        for x in range(n):
            if not matrix[y][x]:
                continue
            for dy in range(module_size):
                py = y0 + y * module_size + dy
                if py >= height:
                    continue
                for dx in range(module_size):
                    px = margin_x + x * module_size + dx
                    if 0 <= px < PRINT_W:
                        buf[py * BPR + (px >> 3)] |= 1 << (px & 7)
    return buf, height


# ---------- Clasa principală ----------

class MXW01:
    def __init__(self, mac, auto_density=True, verbose=False):
        self.mac = mac.upper()
        self.auto_density = auto_density
        self.verbose = verbose

        self.connection = None
        self.ctrl_char = None
        self.data_char = None
        self.notify_char = None
        self._listener_task = None

        self._status = None
        self._battery = None
        self._version = None
        self._print_type = None
        self._serial = None
        self._flow_paused = False
        self._flow_count = 0
        self._complete = False
        self._refused = None

        self.on_print_complete = None
        self.on_low_battery = None
        self.on_no_paper = None
        self.on_overheat = None
        self.on_state_change = None

        self._low_bat_fired = False
        self._no_paper_fired = False
        self._overheat_fired = False

    def _log(self, msg):
        if self.verbose:
            print(f"[mxw01] {msg}")

    # ---------- Conexiune ----------
    async def _scan(self):
        for _ in range(3):
            try:
                async with aioble.scan(duration_ms=5000, interval_us=30000,
                                       window_us=30000, active=True) as scanner:
                    async for r in scanner:
                        addr = ':'.join('{:02X}'.format(b) for b in r.device.addr)
                        if addr == self.mac:
                            self._log(f"found {addr} RSSI={r.rssi}")
                            return r.device
            except Exception as e:
                self._log(f"scan err: {e}")
            await asyncio.sleep_ms(500)
        return None

    async def connect(self, timeout_ms=15000):
        device = await self._scan()
        if not device:
            raise OSError("Printer not found")

        self.connection = await device.connect(timeout_ms=timeout_ms)
        await asyncio.sleep_ms(2000)
        try:
            await self.connection.exchange_mtu(512)
        except Exception:
            pass

        services = []
        try:
            async for s in self.connection.services():
                services.append(s)
        except Exception:
            pass

        ae30 = None
        for s in services:
            if "ae30" in str(s.uuid).lower():
                ae30 = s
                break
        if not ae30:
            raise OSError("AE30 service not found")

        await asyncio.sleep_ms(2000)

        chars = []
        for _ in range(20):
            try:
                chars = []
                async for ch in ae30.characteristics():
                    chars.append(ch)
                if chars:
                    break
            except Exception as e:
                if "in progress" in str(e).lower():
                    await asyncio.sleep_ms(500)
                    continue
                raise
        if not chars:
            raise OSError("Cannot read characteristics")

        for ch in chars:
            u = str(ch.uuid).lower()
            if "ae01" in u: self.ctrl_char = ch
            elif "ae02" in u: self.notify_char = ch
            elif "ae03" in u: self.data_char = ch

        if not self.ctrl_char or not self.data_char:
            raise OSError("AE01 or AE03 missing")

        if self.notify_char:
            try:
                await self.notify_char.subscribe(notify=True)
                self._listener_task = asyncio.create_task(self._listen())
                await asyncio.sleep_ms(300)
            except Exception as e:
                self._log(f"notify err: {e}")

        await self._write_ctrl(_build(CMD_IDENTIFY, b'\x00'))
        await asyncio.sleep_ms(600)
        self._log("connected")
        return True

    async def disconnect(self):
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except BaseException:
                pass
            self._listener_task = None
        if self.connection:
            try:
                await self.connection.disconnect()
            except Exception:
                pass
            self.connection = None

    # ---------- Listener ----------
    async def _listen(self):
        while True:
            try:
                data = await self.notify_char.notified(timeout_ms=500)
            except asyncio.TimeoutError:
                continue
            except BaseException:
                return
            if not data or len(data) < 3:
                continue
            try:
                await self._handle_notify(data)
            except Exception as e:
                self._log(f"notify handler err: {e}")

    async def _handle_notify(self, data):
        cmd = data[2]
        self._log("rx " + ' '.join('{:02X}'.format(b) for b in data))

        if cmd == CMD_STATUS and len(data) >= 14:
            old = self._status
            self._status = {
                'state': data[6],
                'state_name': STATE_NAMES.get(data[6], 'Unknown'),
                'battery': data[9],
                'temp': data[10],
                'error_group': data[12],
                'error_code': data[13],
                'error_name': ERROR_NAMES.get(data[13], '') if data[12] != 0 else '',
                'raw': bytes(data),
            }
            self._evaluate_events(old, self._status)

        elif cmd == CMD_COMPLETE:
            self._complete = True
            if callable(self.on_print_complete):
                try: self.on_print_complete()
                except Exception: pass

        elif cmd == CMD_PRINT and len(data) >= 7:
            self._refused = (data[6] != 0x00)

        elif cmd == CMD_FLOW and len(data) >= 7:
            if data[6] == 0x10:
                self._flow_paused = True
                self._flow_count += 1
            elif data[6] == 0x00:
                self._flow_paused = False

        elif cmd == CMD_BATTERY and len(data) >= 7:
            self._battery = data[6]

        elif cmd == CMD_VERSION and len(data) >= 6:
            try:
                vlen = data[4]
                self._version = bytes(data[6:6 + vlen]).decode().rstrip('\x00')
            except Exception:
                self._version = ""

        elif cmd == CMD_PRINT_TYPE and len(data) >= 7:
            v = data[6]
            self._print_type = 'gaoya' if v == 0x01 else ('unknown' if v == 0xFF else 'diya')

        elif cmd == CMD_IDENTIFY and len(data) >= 12:
            self._serial = ''.join('{:02X}'.format(b) for b in data[6:12])

        elif cmd == CMD_A6:
            asyncio.create_task(self._write_ctrl(_build(CMD_IDENTIFY, b'\x00')))

        elif cmd == CMD_LEARN:
            async def _delayed_a3():
                await asyncio.sleep_ms(700)
                await self._write_ctrl(_build(CMD_EJECT, struct.pack('<H', 0)))
            asyncio.create_task(_delayed_a3())

    def _evaluate_events(self, old, new):
        if not new:
            return
        code = new['error_code'] if new['error_group'] != 0 else 0

        if code == 8 and not self._low_bat_fired:
            self._low_bat_fired = True
            if callable(self.on_low_battery):
                try: self.on_low_battery()
                except Exception: pass
        elif code != 8:
            self._low_bat_fired = False

        if code in (1, 9) and not self._no_paper_fired:
            self._no_paper_fired = True
            if callable(self.on_no_paper):
                try: self.on_no_paper()
                except Exception: pass
        elif code not in (1, 9):
            self._no_paper_fired = False

        if code == 4 and not self._overheat_fired:
            self._overheat_fired = True
            if callable(self.on_overheat):
                try: self.on_overheat()
                except Exception: pass
        elif code != 4:
            self._overheat_fired = False

        if old is None or old['state'] != new['state']:
            if callable(self.on_state_change):
                try: self.on_state_change(new['state_name'])
                except Exception: pass

    # ---------- Scriere de nivel scăzut ----------
    async def _write_ctrl(self, data, retries=10, delay_ms=150):
        for _ in range(retries):
            try:
                await self.ctrl_char.write(data, response=False)
                return
            except OSError as e:
                if e.errno == 114 or 'EALREADY' in str(e):
                    await asyncio.sleep_ms(delay_ms)
                    continue
                raise
        raise OSError("ctrl write failed after retries")

    async def _write_data(self, data, retries=10, delay_ms=150):
        for _ in range(retries):
            try:
                await self.data_char.write(data, response=False)
                return
            except OSError as e:
                if e.errno == 114 or 'EALREADY' in str(e):
                    await asyncio.sleep_ms(delay_ms)
                    continue
                raise
        raise OSError("data write failed after retries")

    async def _wait_flow(self, timeout_ms=20000):
        start = time.ticks_ms()
        while self._flow_paused:
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                self._flow_paused = False
                raise OSError("Flow control timeout")
            await asyncio.sleep_ms(10)

    async def _wait_for(self, attr, timeout_ms=3000, interval_ms=100):
        start = time.ticks_ms()
        while not getattr(self, attr):
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                return False
            await asyncio.sleep_ms(interval_ms)
        return True

    # ---------- Info ----------
    async def get_status(self, timeout_ms=3000):
        self._status = None
        await self._write_ctrl(_build(CMD_STATUS, b'\x00'))
        return await self._wait_for('_status', timeout_ms)

    async def get_battery(self, timeout_ms=3000):
        self._battery = None
        await self._write_ctrl(_build(CMD_BATTERY, b'\x00'))
        return await self._wait_for('_battery', timeout_ms)

    async def get_version(self, timeout_ms=3000):
        self._version = None
        await self._write_ctrl(_build(CMD_VERSION, b'\x00'))
        return await self._wait_for('_version', timeout_ms)

    async def get_print_type(self, timeout_ms=3000):
        self._print_type = None
        await self._write_ctrl(_build(CMD_PRINT_TYPE, b'\x00'))
        return await self._wait_for('_print_type', timeout_ms)

    async def identify(self, timeout_ms=3000):
        self._serial = None
        await self._write_ctrl(_build(CMD_IDENTIFY, b'\x00'))
        return await self._wait_for('_serial', timeout_ms)

    @property
    def status(self): return self._status
    @property
    def battery(self): return self._battery
    @property
    def version(self): return self._version
    @property
    def print_type(self): return self._print_type
    @property
    def serial_number(self): return self._serial
    @property
    def flow_pause_count(self): return self._flow_count

    # ---------- Hârtie și intensitate ----------
    async def set_intensity(self, value=93):
        v = max(0, min(255, int(value)))
        await self._write_ctrl(_build(CMD_INTENSITY, bytes([v])))

    async def eject(self, lines=50):
        n = max(0, min(0xFFFF, int(lines)))
        await self._write_ctrl(_build(CMD_EJECT, struct.pack('<H', n)))

    async def retract(self, lines=50):
        n = max(0, min(0xFFFF, int(lines)))
        await self._write_ctrl(_build(CMD_RETRACT, struct.pack('<H', n)))

    async def cancel(self):
        await self._write_ctrl(_build(CMD_CANCEL, b'\x00'))

    async def flush(self):
        await self._write_ctrl(_build(CMD_FLUSH, b'\x00'))

    # ---------- Printare ----------
    async def print_bitmap(self, buffer, width, height, mode='mono',
                           intensity=None, chunk_size=DEFAULT_CHUNK,
                           chunk_delay_ms=DEFAULT_CHUNK_DELAY_MS,
                           copies=1, wait_complete=False,
                           complete_timeout_ms=60000):
        """Trimite un buffer de 384 px lățime. mode='mono' (1 bit) sau 'gray' (4 bit)."""
        if width != PRINT_W:
            raise ValueError(f"width must be 384 (got {width})")
        is_gray = (mode == 'gray')
        bpr = (width // 2) if is_gray else (width // 8)
        expected = bpr * height
        if len(buffer) < expected:
            raise ValueError(f"buffer too small: {len(buffer)} < {expected}")

        if intensity is None:
            intensity = 93
            if self.auto_density and self._status:
                try:
                    intensity = _official_density(
                        self._status['temp'], intensity,
                        self._print_type or 'gaoya', gray=is_gray,
                        black=0.0, single=(copies == 1))
                except Exception:
                    pass

        for c in range(copies):
            if c > 0:
                await asyncio.sleep_ms(60)
            await self._do_print(bytes(buffer[:expected]), height, is_gray,
                                 intensity, chunk_size, chunk_delay_ms,
                                 wait_complete and (c == copies - 1),
                                 complete_timeout_ms)

    async def _do_print(self, buf, height, is_gray, intensity, chunk_size,
                        chunk_delay, wait_complete, timeout_ms):
        self._complete = False
        self._refused = None
        self._flow_paused = False

        await self.get_status()
        await asyncio.sleep_ms(200)
        s = self._status
        if s and s['error_group'] != 0:
            raise OSError(f"Imprimanta: {s['error_name'] or 'eroare'} "
                          f"(grup={s['error_group']} cod={s['error_code']})")

        await self.set_intensity(intensity)
        await asyncio.sleep_ms(60)

        mode_byte = MODE_GRAY if is_gray else MODE_MONO
        await self._write_ctrl(_build(CMD_PRINT,
                                      struct.pack('<H', height) + bytes([0x30, mode_byte])))
        await asyncio.sleep_ms(600)
        if self._refused:
            raise OSError("Imprimanta a refuzat jobul (A9)")

        sent = 0
        total = len(buf)
        while sent < total:
            await self._wait_flow()
            n = min(chunk_size, total - sent)
            await self._write_data(buf[sent:sent + n])
            sent += n
            await asyncio.sleep_ms(chunk_delay)

        await asyncio.sleep_ms(300)
        await self._write_ctrl(_build(CMD_FLUSH, b'\x00'))

        if wait_complete:
            start = time.ticks_ms()
            while not self._complete:
                if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                    return False
                await asyncio.sleep_ms(200)
        return True

    async def print_text(self, text, scale=2, mirror_glyphs=True,
                         intensity=None, copies=1, wait_complete=False):
        """Printează text cu fontul framebuf integrat (fără fișiere)."""
        buf, h = text_bitmap(text, scale=scale, mirror_glyphs=mirror_glyphs)
        await self.print_bitmap(buf, PRINT_W, h, mode='mono',
                                intensity=intensity if intensity else 98,
                                copies=copies, wait_complete=wait_complete)

    async def print_bmp(self, path, invert=False, intensity=None,
                        copies=1, wait_complete=False):
        buf, h = bmp_to_buffer(path, invert=invert)
        await self.print_bitmap(buf, PRINT_W, h, mode='mono',
                                intensity=intensity, copies=copies,
                                wait_complete=wait_complete)

    async def print_qr(self, data, module_size=4, quiet_zone=2,
                       intensity=None, copies=1, wait_complete=False):
        buf, h = qr_bitmap(data, module_size=module_size, quiet_zone=quiet_zone)
        await self.print_bitmap(buf, PRINT_W, h, mode='mono',
                                intensity=intensity, copies=copies,
                                wait_complete=wait_complete)
