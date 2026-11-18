# MXW01 Cat Printer — MicroPython Library

A complete MicroPython library for the **MXW01** thermal printer (V5X protocol, preamble `22 21`), tested on the **Raspberry Pi Pico 2W** with `aioble`.

Covers the full protocol command set plus high-level features: text rendering, BMP images, QR codes, thermal density compensation, flow control, and event callbacks.

---

## Features

- ✅ Full V5X command coverage (`A1`, `A2`, `A3`, `A4`, `A6`, `A7`, `A9`, `AA`, `AB`, `AC`, `AD`, `AE`, `B0`, `B1`, `B2`)
- ✅ Monochrome (1bpp) and grayscale (4bpp) printing
- ✅ Automatic thermal density compensation (official Fun Print tables)
- ✅ Flow control handling (printer-requested pause/resume)
- ✅ Event callbacks: low battery, no paper, overheat, state changes, print completion
- ✅ Scaled text rendering (1× – 4×) via direct byte manipulation
- ✅ BMP image printing (1-bit and 24-bit)
- ✅ QR code printing
- ✅ Multi-copy support
- ✅ Automatic retry on `EALREADY` (BLE buffer overflow)
- ✅ Auto-response to `A6` (re-identify) and `B2` (learn) protocol triggers

---

## Installation

### 1. Prepare your Pico 2W

- Flash **MicroPython** for Pico 2W (`.uf2` file from [micropython.org](https://micropython.org/download/RPI_PICO2_W/)).
- Open **Thonny IDE**, select the Pico port, and connect.

### 2. Copy the library

- In Thonny: **File → New file**, paste `mxw01.py`, then **Save as → Raspberry Pi Pico** and name it `mxw01.py`.

### 3. (Optional) Install QR code support

In the Thonny REPL:

```python
import mip
mip.install("github:monte-m/python-qrcode")
