import time
import network

from config import (
    WIFI_PASSWORD,
    WIFI_SSID,
)

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    while True:
        print("Connecting to Wi-Fi...")

        wlan.connect(WIFI_SSID, WIFI_PASSWORD)

        for _ in range(10):
            if wlan.status() == 3:
                print("connected :", wlan.ifconfig()[0])
                return wlan

            if wlan.status() < 0:
                break

            time.sleep(1)

        print(f"network connection failed (status={wlan.status()})")
        wlan.disconnect()
        time.sleep(2)

connect_wifi()

