import os
import time
import network
import utime
import ubinascii
import uhashlib
import uasyncio as asyncio
import ssl

from machine import Pin
from config import WIFI_SSID, WIFI_PASSWORD, PASSWORD

led = Pin("LED", Pin.OUT, value=0)
computer = Pin(2, Pin.OUT)

wlan = network.WLAN(network.STA_IF)

_nonce       = ""
_nonce_ts    = 0
NONCE_TTL_MS = 5 * 60 * 1000

HTML = """\
<!DOCTYPE html>
<html>
<head><title>Pico W</title></head>
<body>
<h1>Pico W - Computer Management</h1>
%s
<form id="form" method="POST" action="/computer">
    <input type="hidden" id="nonce" name="nonce" value="%s">
    <input type="hidden" id="hmac"  name="hmac">
    <input type="password" id="pwd" placeholder="Password" required>
    <button type="submit">Turn On/Off</button>
</form>
<script>
document.getElementById('form').addEventListener('submit', async function(event) {
    event.preventDefault();
    const pwd   = document.getElementById('pwd').value;
    const nonce = document.getElementById('nonce').value;
    const enc   = new TextEncoder();
    const key   = await crypto.subtle.importKey(
        'raw', enc.encode(pwd),
        {name: 'HMAC', hash: 'SHA-256'},
        false, ['sign']
    );
    const sig = await crypto.subtle.sign('HMAC', key, enc.encode(nonce));
    document.getElementById('hmac').value =
        Array.from(new Uint8Array(sig))
            .map(b => b.toString(16).padStart(2, '0'))
            .join('');

    document.getElementById('pwd').value = '';
    this.submit();
});
</script>
</body>
</html>
"""
 
MSG_WRONG = "<p><strong>Wrong password.</strong></p>"
MSG_OK = "<p>Signal sent to computer.</p>"
MSG_EXPIRED = "<p><strong>Session expired, please reload the page.</strong></p>"

def _new_nonce() -> str:
    global _nonce, _nonce_ts
    _nonce    = ubinascii.hexlify(os.urandom(16)).decode()
    _nonce_ts = utime.ticks_ms()
    return _nonce

def _nonce_valid(received: str) -> bool:
    expired = utime.ticks_diff(utime.ticks_ms(), _nonce_ts) > NONCE_TTL_MS
    return (not expired) and (_nonce != "") and (received == _nonce)

def _invalidate_nonce():
    global _nonce, _nonce_ts
    _nonce    = ""
    _nonce_ts = 0

def hmac_sha256(key: str, message: str) -> str:
    BLOCK = 64
    k = key.encode()     if isinstance(key,     str) else key
    m = message.encode() if isinstance(message, str) else message
 
    if len(k) > BLOCK:
        k = uhashlib.sha256(k).digest()
    k = k + b'\x00' * (BLOCK - len(k))

    ipad = bytes(b ^ 0x36 for b in k)
    opad = bytes(b ^ 0x5c for b in k)

    ih = uhashlib.sha256()
    ih.update(ipad)
    ih.update(m)

    oh = uhashlib.sha256()
    oh.update(opad)
    oh.update(ih.digest())

    return ubinascii.hexlify(oh.digest()).decode()

def ct_equal(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode(), b.encode()):
        result |= x ^ y
    return result == 0

def url_decode(s):
    s = s.replace("+", " ")
    out = []
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            out.append(chr(int(s[i + 1:i + 3], 16)))
            i += 3
        else:
            out.append(s[i])
            i += 1
    return "".join(out)

def parse_form(body):
    params = {}
    for pair in body.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[url_decode(k)] = url_decode(v)
    return params

async def toggleComputer():
    led.value(1)
    computer.value(1)
    await asyncio.sleep(0.5)
    led.value(0)
    computer.value(0)

def _do_connect():
    wlan.active(True)
    wlan.config(pm = 0xa11140)   # disable power-save mode
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    for _ in range(10):
        status = wlan.status()
        if status == network.STAT_GOT_IP:
            print("Wi-Fi connected:", wlan.ifconfig()[0])
            return True
        if status < 0:
            break
        time.sleep(1)

    print("Connection failed (status=%d)" % wlan.status())
    return False

def connect_wifi():
    while True:
        print("Connecting to Wi-Fi…")
        if _do_connect():
            return
        wlan.disconnect()
        time.sleep(3)

async def wifi_watchdog():
    while True:
        await asyncio.sleep(5)
        if wlan.status() != network.STAT_GOT_IP:
            print("Wi-Fi lost - reconnecting…")
            wlan.disconnect()
            await asyncio.sleep(1)
            while not _do_connect():
                wlan.disconnect()
                await asyncio.sleep(3)
            print("Wi-Fi restored:", wlan.ifconfig()[0])

async def serve_client(reader, writer):
    try:
        HEADER_TIMEOUT_MS = 5000
        start = utime.ticks_ms()

        try:
            request_line = await asyncio.wait_for(reader.readline(), 5)
        except asyncio.TimeoutError:
            return

        if utime.ticks_diff(utime.ticks_ms(), start) > HEADER_TIMEOUT_MS:
            return
        if not request_line:
            return

        print("Request:", request_line.strip())

        parts  = request_line.decode().split()
        method = parts[0] if parts else "GET"
        path   = parts[1] if len(parts) > 1 else "/"

        content_length = 0
        while True:
            if utime.ticks_diff(utime.ticks_ms(), start) > HEADER_TIMEOUT_MS:
                return
            try:
                header = await asyncio.wait_for(reader.readline(), 5)
            except asyncio.TimeoutError:
                return
            if header == b"\r\n":
                break
            if header.lower().startswith(b"content-length:"):
                content_length = int(header.split(b":")[1].strip())

        message = ""

        if content_length > 256:
            writer.write(
                "HTTP/1.0 413 Payload Too Large\r\n"
                "Connection: close\r\n\r\n"
            )
            await writer.drain()
            return

        if method == "POST" and path == "/computer" and content_length > 0:
            body   = await reader.read(content_length)
            params = parse_form(body.decode())

            received_nonce = params.get("nonce", "")
            received_hmac  = params.get("hmac",  "")

            if not _nonce_valid(received_nonce):
                message = MSG_EXPIRED
            else:
                expected = hmac_sha256(PASSWORD, received_nonce)
                _invalidate_nonce()
                if ct_equal(expected, received_hmac):
                    asyncio.create_task(toggleComputer())
                    message = MSG_OK
                else:
                    message = MSG_WRONG

        nonce = _new_nonce()
        writer.write("HTTP/1.0 200 OK\r\nContent-type: text/html\r\n\r\n")
        writer.write(HTML % (message, nonce))
        await writer.drain()

    except OSError as e:
        print("Client disconnected unexpectedly:", e)
    finally:
        await writer.wait_closed()

async def main():
    print("Connecting to network…")
    connect_wifi()

    print("Starting web server on port 443…")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("cert.der", "key.der")
    asyncio.create_task(
        asyncio.start_server(
            serve_client,
            "0.0.0.0",
            443,
            ssl=ctx,
        )
    )
    asyncio.create_task(wifi_watchdog())

    while True:
        await asyncio.sleep(60)

try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()