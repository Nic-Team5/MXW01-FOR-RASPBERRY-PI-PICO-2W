# =============================================================================
# main.py: teste pentru mxw01.py pe Raspberry Pi Pico W.
# Pune lângă mxw01.py. Nu are alte dependențe.
# =============================================================================

import asyncio
from mxw01 import MXW01, text_bitmap, PRINT_W, BPR

PRINTER_MAC = "48:0F:57:4D:8D:F5"


def banner(msg):
    print("=" * 45)
    print(" ", msg)
    print("=" * 45)


async def test_status(p):
    banner("Status (A1)")
    if not await p.get_status():
        print("Nu am primit status.")
        return
    s = p.status
    print(f"Stare: {s['state_name']}")
    print(f"Baterie: {s['battery']}%")
    print(f"Temperatura: {s['temp']}")
    print(f"Eroare: grup={s['error_group']} cod={s['error_code']} {s['error_name']}")


async def test_print_type(p):
    banner("Cap termic (B0)")
    if not await p.get_print_type():
        print("Fără răspuns la B0.")
        return
    print(f"Cap: {p.print_type}")


async def test_version(p):
    banner("Versiune (B1)")
    if not await p.get_version():
        print("Fără răspuns la B1.")
        return
    print(f"Versiune: {p.version}")


async def test_bars(p):
    banner("Bare și pătrat (test ordine biți)")
    height = 120
    buf = bytearray(BPR * height)
    for y in range(height):
        for x in range(0, PRINT_W, 32):
            for k in range(8):
                px = x + k
                buf[y * BPR + (px >> 3)] |= 1 << (px & 7)
    for y in range(height - 40, height):
        for x in range(40):
            buf[y * BPR + (x >> 3)] |= 1 << (x & 7)
    await p.print_bitmap(buf, PRINT_W, height, mode='mono',
                         intensity=98, wait_complete=True)
    print("Trimis și confirmat.")


async def test_text(p):
    banner("Text (framebuf, scara 2)")
    await p.print_text("Hello!\nTest 123\nABC abc", scale=2, wait_complete=True)
    print("Trimis și confirmat.")


async def test_eject(p):
    banner("Avans hârtie (A3)")
    await p.eject(40)
    print("Comandă trimisă.")


TESTS = [test_status, test_print_type, test_version,
         test_bars, test_text, test_eject]


async def main():
    banner("MXW01 — teste Pico")
    p = MXW01(PRINTER_MAC, verbose=False)
    print("Conectare...")
    try:
        await p.connect()
    except Exception as e:
        print(f"Conectarea a eșuat: {e}")
        return
    print("Conectat.")

    for t in TESTS:
        try:
            await t(p)
        except Exception as e:
            print(f"Testul {t.__name__} a eșuat: {e}")
        await asyncio.sleep(3)

    await asyncio.sleep(5)
    await p.disconnect()
    print("Deconectat.")


asyncio.run(main())
