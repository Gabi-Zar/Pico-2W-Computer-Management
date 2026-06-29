import time
import network
import uasyncio as asyncio

from machine import Pin
from config import WIFI_SSID, WIFI_PASSWORD, PASSWORD

led = Pin("LED", Pin.OUT, value=0)
computer = Pin(2, Pin.OUT)

wlan = network.WLAN(network.STA_IF)

HTML = """\
<!DOCTYPE html>
<html>
<head><title>Pico W Computer Management</title></head>
<body>
<h1>Pico W - Computer Management</h1>
%s
<form method="POST" action="/computer">
<input type="password" name="pwd" placeholder="Password" required>
<button type="submit">Turn On/Off</button>
</form>
</body>
</html>
"""

MSG_WRONG  = "<p><strong>Wrong password.</strong></p>"
MSG_OK  = "<p>Computer turned on/off</p>"

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
        request_line = await reader.readline()
        if not request_line:
            return

        print("Request:", request_line.strip())

        parts = request_line.decode().split()
        method = parts[0] if parts else "GET"
        path   = parts[1] if len(parts) > 1 else "/"

        content_length = 0
        while True:
            header = await reader.readline()
            if header == b"\r\n":
                break
            if header.lower().startswith(b"content-length:"):
                content_length = int(header.split(b":")[1].strip())

        message = ""
        if method == "POST" and path == "/computer" and content_length > 0:
            body   = await reader.read(content_length)
            params = parse_form(body.decode())
            pwd    = params.get("pwd", "")

            if pwd != PASSWORD:
                message = MSG_WRONG
            else:
                asyncio.create_task(toggleComputer())
                message = MSG_OK

        writer.write("HTTP/1.0 200 OK\r\nContent-type: text/html\r\n\r\n")
        writer.write(HTML % message)
        await writer.drain()

    except OSError as e:
        print("Client disconnected unexpectedly:", e)
    finally:
        await writer.wait_closed()


async def main():
    print("Connecting to network…")
    connect_wifi()

    print("Starting web server on port 80…")
    asyncio.create_task(asyncio.start_server(serve_client, "0.0.0.0", 80))
    asyncio.create_task(wifi_watchdog())

    while True:
        await asyncio.sleep(60)


try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()