## Credits
[@Nic-Team5](https://github.com/Nic-Team5)
# MXW01 API Reference (MicroPython, Raspberry Pi Pico W)

A MicroPython library for the MXW01 thermal receipt printer, driven over Bluetooth Low Energy (BLE).

Files:

- `mxw01.py`: the library
- `main.py`: test script

No font files or other external dependencies are required. The library depends only on MicroPython's `asyncio`, `framebuf`, `struct`, `time`, and `aioble`.

---

## Contents

1. [Overview](#overview)
2. [Installation](#installation)
3. [Module constants](#module-constants)
4. [Bitmap format](#bitmap-format)
5. [The MXW01 class](#the-mxw01-class)
6. [Module-level functions](#module-level-functions)
7. [Error handling](#error-handling)
8. [Recommended workflow](#recommended-workflow)
9. [Examples](#examples)
10. [Limitations](#limitations)

---

## Overview

The printer is controlled through a single BLE connection. Commands are sent on the control characteristic, image data on the data characteristic, and the printer reports back on the notify characteristic. The library handles framing, checksums, flow control, and print sequencing, so user code only deals with high-level calls: connect, query, print.

Typical lifecycle:

```
connect() -> get_status() -> print_text() / print_bitmap() / print_bmp() -> disconnect()
```

All I/O methods are coroutines and must be awaited inside an `asyncio` task.

---

## Installation

1. Copy `mxw01.py` to the Pico's filesystem.
2. Make sure `aioble` is installed (it ships with recent MicroPython builds, or install via `mip`).
3. For QR codes only: `import mip; mip.install('github:monte-m/python-qrcode')`.
4. Copy `main.py` alongside for testing.

---

## Module constants

| Name | Value | Meaning |
|---|---|---|
| `PRINT_W` | `384` | Paper width in pixels. Fixed by the hardware. |
| `BPR` | `48` | Bytes per row in monochrome mode (`384 / 8`). |
| `DEFAULT_CHUNK` | `400` | Default BLE write size in bytes. |
| `DEFAULT_CHUNK_DELAY_MS` | `30` | Default pause between BLE writes, in milliseconds. |
| `MODE_MONO` | `0x00` | Monochrome print mode (1 bit per pixel). |
| `MODE_GRAY` | `0x02` | Grayscale print mode (4 bits per pixel). |
| `STATE_NAMES` | dict | Maps state codes to names: `Standby`, `Printing`, `Feeding`, `Retracting`. |
| `ERROR_NAMES` | dict | Maps error codes to names (see [Error handling](#error-handling)). |

---

## Bitmap format

All print methods that take a raw buffer use this format.

### Monochrome (`mode='mono'`)

- 1 bit per pixel.
- LSB-first within each byte: the leftmost pixel of each group of 8 is bit 0.
- `1` means black (the thermal head fires), `0` means white.
- Each row is exactly `PRINT_W / 8 = 48` bytes.
- The buffer height must be a multiple of 8 so the total byte count is a whole number of rows.

Example of setting pixel `x` on row `y`:

```python
buf[y * BPR + (x >> 3)] |= 1 << (x & 7)
```

### Grayscale (`mode='gray'`)

- 4 bits per pixel, two pixels per byte.
- The first pixel of each byte occupies the upper nibble.
- `0` is white and `15` is black.
- Each row is `PRINT_W / 2 = 192` bytes.
- Not tested on hardware yet.

---

## The MXW01 class

### Constructor

```python
MXW01(mac, auto_density=True, verbose=False)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `mac` | `str` | required | BLE address in `"XX:XX:XX:XX:XX:XX"` form. Case-insensitive. |
| `auto_density` | `bool` | `True` | If `True`, the print intensity is computed from the printer temperature reported by the last status query. |
| `verbose` | `bool` | `False` | If `True`, every received packet and internal event is printed. Useful for debugging. |

### Connection

#### `await connect(timeout_ms=15000)`

Scans for the printer, opens the BLE connection, discovers the service `AE30`, finds the characteristics `AE01` (control), `AE02` (notify), and `AE03` (data), and subscribes to notifications. Sends an identify request at the end.

- Returns `True` on success.
- Raises `OSError` if the printer is not found, the service is missing, or the characteristics cannot be read.

#### `await disconnect()`

Stops the notification listener and closes the BLE connection. Safe to call more than once.

### Status and device information

Each query method sends a request and waits for the matching reply. They return `True` if the reply arrived within the timeout, `False` otherwise. The data is then available through the corresponding property.

| Method | Property read afterwards | Timeout default |
|---|---|---|
| `await get_status(timeout_ms=3000)` | `status` | 3000 ms |
| `await get_battery(timeout_ms=3000)` | `battery` | 3000 ms |
| `await get_version(timeout_ms=3000)` | `version` | 3000 ms |
| `await get_print_type(timeout_ms=3000)` | `print_type` | 3000 ms |
| `await identify(timeout_ms=3000)` | `serial_number` | 3000 ms |

#### Properties

| Property | Type | Description |
|---|---|---|
| `status` | `dict` or `None` | Latest status. Keys: `state`, `state_name`, `battery`, `temp`, `error_group`, `error_code`, `error_name`, `raw`. |
| `battery` | `int` or `None` | Battery level as reported by the printer. |
| `version` | `str` or `None` | Firmware version string. |
| `print_type` | `str` or `None` | Thermal head type: `'gaoya'`, `'diya'`, or `'unknown'`. |
| `serial_number` | `str` or `None` | Serial number as uppercase hex. |
| `flow_pause_count` | `int` | Number of times the printer has asked the host to pause data transfer since the object was created. |

The `status` dict fields:

| Key | Meaning |
|---|---|
| `state` | Numeric state: 0 standby, 1 printing, 2 feeding paper, 3 retracting paper. |
| `state_name` | Text form of `state`. |
| `battery` | Battery level. |
| `temp` | Printer temperature (unit not confirmed; assumed to be Celsius). |
| `error_group` | `0` means no error. Non-zero means an error is present. |
| `error_code` | Error code, meaningful only when `error_group != 0`. |
| `error_name` | Text form of `error_code`, or an empty string when there is no error. |
| `raw` | The complete notification frame as `bytes`, for inspection. |

### Paper and intensity

| Method | Description |
|---|---|
| `await set_intensity(value=93)` | Sets the heating intensity, from 0 to 255. Normal range is roughly 80 to 110. |
| `await eject(lines=50)` | Advances the paper by `lines` lines. Lines are a 16-bit value, so the maximum is 65535. |
| `await retract(lines=50)` | Pulls paper back by `lines` lines. Works in testing, but the command is not documented in any official source and may differ between firmware versions. |
| `await cancel()` | Requests cancellation of the current job. Not guaranteed on all firmware versions. |
| `await flush()` | Sends a flush command. Normally sent by the library at the end of a job; manual use is rarely needed. |

### Printing a raw buffer

```python
await print_bitmap(buffer, width, height,
                   mode='mono',
                   intensity=None,
                   chunk_size=400,
                   chunk_delay_ms=30,
                   copies=1,
                   wait_complete=False,
                   complete_timeout_ms=60000)
```

| Parameter | Description |
|---|---|
| `buffer` | `bytes` or `bytearray` in the format described above. |
| `width` | Must be `384`. Any other value raises `ValueError`. |
| `height` | Height in pixels. |
| `mode` | `'mono'` or `'gray'`. |
| `intensity` | Heating intensity. If `None` and `auto_density` is `True`, computed from the temperature. Otherwise 93. |
| `chunk_size` | Bytes per BLE write. Default 400. Lower values are slower but more tolerant. |
| `chunk_delay_ms` | Pause after each BLE write. |
| `copies` | Number of copies. A 60 ms pause separates consecutive copies. |
| `wait_complete` | If `True`, waits for the printer's completion notification before returning. |
| `complete_timeout_ms` | Maximum wait for completion. Returns `False` on timeout. |

Before sending data, the method queries status. If the printer reports an error, it raises `OSError` without sending the job.

Returns `True` when the job was sent, and when `wait_complete` is set, when the completion notification arrived. Returns `False` when `wait_complete` is set and the timeout expired.

### Text

```python
await print_text(text, scale=2, mirror_glyphs=True,
                 intensity=None, copies=1, wait_complete=False)
```

Renders the text with MicroPython's built-in 8x8 font, scales it, and prints it.

| Parameter | Description |
|---|---|
| `text` | Text to print. `\n` starts a new line. Long lines wrap at the column limit. |
| `scale` | Integer scale factor. `2` gives 32 characters per line. `3` gives 24. |
| `mirror_glyphs` | If `True`, each glyph is mirrored within its own cell. Corrects the reversed letters seen on the tested hardware (for example `e` printing as `ɘ`). Set to `False` if letters already print correctly. |
| `intensity`, `copies`, `wait_complete` | As for `print_bitmap`. |

Limitations:

- The built-in font has no Romanian diacritics (ă, â, î, ș, ț). Characters without a glyph print as blanks or boxes.
- The font is 8x8, so text is blocky at every scale. For smoother text, a custom font would be needed.

### Images

```python
await print_bmp(path, invert=False, intensity=None,
                copies=1, wait_complete=False)
```

Loads a 1-bit BMP from the filesystem and prints it.

- The file must be exactly 384 pixels wide and 1 bit per pixel.
- `invert=True` swaps black and white.
- Other formats (JPG, PNG, 24-bit BMP) are rejected. Convert on a computer first, for example with Pillow:

```python
from PIL import Image
im = Image.open("photo.jpg").convert("L")
im = im.resize((384, round(im.height * 384 / im.width)))
im.convert("1").save("photo.bmp")
```

```python
await print_qr(data, module_size=4, quiet_zone=2,
               intensity=None, copies=1, wait_complete=False)
```

Generates a QR code for `data` and prints it. Requires the `qrcode` module (see [Installation](#installation)). `module_size` is the size of one QR module in pixels; it is reduced automatically if the code would be wider than 368 px.

---

## Module-level functions

These do not need a connection. They build buffers you can inspect, combine, or send yourself with `print_bitmap`.

| Function | Returns | Description |
|---|---|---|
| `text_bitmap(text, scale=2, top=8, mirror_glyphs=True)` | `(buffer, height)` | Renders text into a 384-pixel-wide monochrome buffer. |
| `bmp_to_buffer(path, invert=False)` | `(buffer, height)` | Loads a 1-bit BMP into the printer format. |
| `qr_bitmap(data, module_size=4, quiet_zone=2)` | `(buffer, height)` | Builds a QR code in the printer format. |

Example: compose an image from text and a QR code.

```python
from mxw01 import text_bitmap, qr_bitmap, PRINT_W

t_buf, t_h = text_bitmap("Scan me:", scale=2)
q_buf, q_h = qr_bitmap("https://example.com", module_size=4)

combined = bytearray(t_buf) + bytearray(q_buf)
total_h = t_h + q_h
# send with: await p.print_bitmap(combined, PRINT_W, total_h)
```

---

## Error handling

Errors are reported in two ways.

**Status errors.** The printer sends a status notification. If `error_group != 0`, an error is active. Known codes:

| `error_code` | `error_name` | Meaning |
|---|---|---|
| 1 | Out of paper | No paper loaded or paper not detected. |
| 9 | Out of paper | Same as 1, alternate code. |
| 4 | Overheated | Print head temperature too high. Wait before printing. |
| 8 | Low battery | Battery needs charging. |

Any other code is reported with an empty `error_name`. Treat unknown codes as generic faults.

**Exceptions.** Print methods raise `OSError` in these cases:

- The status query before printing shows an error. The message includes the error name.
- The printer rejects the print request.
- A BLE write fails after retries.
- Flow control stays paused longer than 20 seconds.

Events can also be observed without polling, by setting these callbacks on the object:

| Callback | Called when |
|---|---|
| `on_low_battery()` | Error 8 becomes active. |
| `on_no_paper()` | Error 1 or 9 becomes active. |
| `on_overheat()` | Error 4 becomes active. |
| `on_state_change(name)` | The printer changes state. Receives the new state name. |
| `on_print_complete()` | The printer reports that a copy finished. |

Each callback fires once when its condition begins and rearms when the condition clears. Set them before calling `connect()`:

```python
p = MXW01("48:0F:57:4D:8D:F5")
p.on_no_paper = lambda: print("Out of paper")
await p.connect()
```

---

## Recommended workflow

1. **Connect and check the printer.** Call `connect()`, then `get_status()`. Confirm `error_group == 0`.
2. **Test the bit order.** Print a test pattern of bars and a square (see `test_bars` in `main.py`). If bars appear mirrored, the bit order on the device is reversed and the cause is outside the library.
3. **Test text.** Print a short string with `print_text`. Check the orientation and spacing.
4. **Print real content.** Use `print_text`, `print_bmp`, or `print_bitmap`.
5. **Disconnect.** Call `disconnect()` when finished.

Print tests one at a time. Wait at least 3 seconds between jobs so the printer can finish feeding.

---

## Examples

### Minimal print

```python
import asyncio
from mxw01 import MXW01

async def main():
    p = MXW01("48:0F:57:4D:8D:F5")
    await p.connect()
    await p.print_text("Hello, world!", wait_complete=True)
    await p.disconnect()

asyncio.run(main())
```

### Checking status before printing

```python
async def print_safely(p, text):
    if not await p.get_status():
        raise RuntimeError("Printer did not respond")
    if p.status["error_group"] != 0:
        raise RuntimeError(p.status["error_name"] or "Printer error")
    await p.print_text(text, wait_complete=True)
```

### Printing several copies with a reduced density

```python
await p.print_text("Copy test", copies=3, intensity=80, wait_complete=True)
```

### Printing a monochrome bitmap you generated

```python
from mxw01 import PRINT_W, BPR

height = 64
buf = bytearray(BPR * height)
for y in range(height):
    buf[y * BPR + 0] = 0xFF  # solid black stripe on the left edge

await p.print_bitmap(buf, PRINT_W, height, wait_complete=True)
```

### Reacting to events

```python
def on_paper():
    print("Paper missing. Load a roll and retry.")

p = MXW01("48:0F:57:4D:8D:F5")
p.on_no_paper = on_paper
await p.connect()
```

---

## Limitations

- **One job at a time.** Do not call print methods concurrently on the same object.
- **Built-in text font only.** Diacritics are not available, and the font is 8x8.
- **Grayscale is untested.** The packing is implemented but has not been verified on hardware.
- **Paper control.** `retract` and `cancel` work inconsistently across firmware versions.
- **Progress reporting.** The printer sends no intermediate progress. Transmission progress can be estimated from bytes sent, but physical printing progress cannot.
- **Temperature units.** The `temp` value is not confirmed to be Celsius.
- **Hardware testing.** Verify on your own printer before relying on the library for anything important.
