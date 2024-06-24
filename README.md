# 8s-controller

Code for EightSleep standalone temperature controller.

My EightSleep Pod 3 is _mostly_ great.  It _mostly_ gets the temperature right. The one thing I don't like is needing to wake up enough to use their phone app to adjust my temperature when their "AutoPilot" gets things wrong.

This project is the code for the 8s Controller Kit.

You can buy one from me *Coming Soon* or build your own.

You need an [Adafruit Feather TFT ESP32-S2](https://www.adafruit.com/product/5300) with [CircuitPython](https://circuitpython.org/board/adafruit_feather_esp32s2_tft/) installed and a couple of buttons.

Once you have the Feather prepared with CircuitPython, copy the files and directories from this repo to the Feather USB storage.

Add a "`secrets.py` file to the root of the Feather USB storage with your wifi and EightSleep credentials.

```python
secrets = {
  "wifi_ssid": "",
  "wifi_password": "",
  "8s_username": "",
  "8s_password": "",
}
```

Once you have the code and `secrets.py` in place, the controller should automatically connect and show your current side status.

You can debug by watching the serial console over USB.
