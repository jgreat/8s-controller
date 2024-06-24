# SPDX-License-Identifier: MIT
"""
Eight Sleep Pod Temperature Controller

For use with Adafruit Feather TFT ESP32-S2

Copyright (c) 2024 Jason Greathouse
"""
import json
import ssl
import wifi
import socketpool
import displayio
import board
import terminalio
import asyncio
import async_button
import adafruit_requests
import adafruit_logging as logging

# from adafruit_bitmap_font import bitmap_font
from adafruit_display_text import bitmap_label

log = logging.getLogger("code")
# Note: Setting logging lever to debug will print out the access token in the logs.
log.setLevel(logging.INFO)

# Get WiFi details secrets.py file
try:
    from secrets import secrets
except ImportError:
    log.error("WiFi and EightSleep user credential secrets are kept in secrets.py, please add them there!")
    raise

# Connect to WiFi
log.info("Connecting to %s" % secrets["wifi_ssid"])
wifi.radio.connect(secrets["wifi_ssid"], secrets["wifi_password"])
log.info("Connected to %s!" % secrets["wifi_ssid"])
log.info("My IP address is %s" % wifi.radio.ipv4_address)

pool = socketpool.SocketPool(wifi.radio)
requests = adafruit_requests.Session(pool, ssl.create_default_context())

# Set up the display, we want a minimal brightness since we're using this in a bedroom
display = board.DISPLAY
display.rotation = 270
display.brightness = 0.1

# Object to store the 8s access token
AUTH = {
    "access_token": "",
    "user_id": "",
}

# Default headers for all requests
HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "8slp/1.0.0",
}

# AUTH_CLIENT_ID and AUTH_CLIENT_SECRET are generic shared credentials borrowed from https://github.com/lukas-clarke/eight_sleep
# I would sign up for a developer account if eight sleep had one.
AUTH_CLIENT_ID = "0894c7f33bb94800a03f1f4df13a4f38"
AUTH_CLIENT_SECRET = "f0954a3ed5763ba3d06834c73731a32f15f168f47d4f164751275def86db0c76"

# eight sleep auth token api
AUTH_URL = "https://auth-api.8slp.net/v1/tokens"

# eight sleep client api (about me)
CLIENT_URL = "https://client-api.8slp.net/v1"

HOT = 0Xc93412
ZERO = 0Xe0e0e0
COLD = 0X4c34eb

SIDE = ""
DEVICE_ID = ""
CURRENT_TEMP = 0
TARGET_TEMP = 0
TARGET_TEMP_IS_PENDING = False
INIT_DONE = False
SIDE_ACTIVE = False
API_LOCK = False


def get_8s_access_token():
    """
        Get OAUTH access token.

        Calling grant_type=refresh_token seems just to issue a new access token. So I guess we can just re-authenticate if access token is expired? Doesn't seem to be an issue.

        :returns: None
    """
    global AUTH
    auth_payload = {
        "client_id": AUTH_CLIENT_ID,
        "client_secret": AUTH_CLIENT_SECRET,
        "grant_type": "password",
        "username": secrets["8s_username"],
        "password": secrets["8s_password"],
    }
    response = requests.post(AUTH_URL, json=auth_payload, headers=HEADERS)
    log.debug("Auth Response:")
    log.debug(json.dumps(response.json()))
    AUTH = {
        "access_token": response.json()["access_token"],
        "user_id": response.json()["userId"],
    }


def get_8s(url):
    """
        Generic GET request to the 8s Client API.

        :param url: str
            URL for the request

        :returns: dict
    """
    headers = HEADERS
    headers["Authorization"] = "Bearer %s" % AUTH["access_token"]
    # log.debug(url)
    response = requests.get(url, headers=headers)
    log.debug(response.status_code)

    if response.status_code == 401:
        # Try to refresh the token and submit the request again
        get_8s_access_token()
        headers["Authorization"] = "Bearer %s" % AUTH["access_token"]
        response = requests.get(url, headers=headers)

    if response.status_code != 200:
        log.error("Error doing GET - status code: %s" % response.status_code)
        log.error(response.text)
        raise RuntimeError("Error doing GET - status code: %s" % response.status_code)

    return response.json()


def put_8s(url, payload):
    """
        Generic PUT request to the 8s Client API.

        :param url: str
            URL for the request

        :param payload: dict
            Payload for the request

        :returns: dict
    """
    headers = HEADERS
    headers["Authorization"] = "Bearer %s" % AUTH["access_token"]
    response = requests.put(url, json=payload, headers=headers)
    log.debug(response.status_code)

    if response.status_code == 401:
        # Try to refresh the token and submit the request again
        get_8s_access_token()
        headers = HEADERS
        headers["Authorization"] = "Bearer %s" % AUTH["access_token"]
        response = requests.put(url, json=payload, headers=headers)

    if response.status_code != 200:
        log.error("Error doing PUT - status code: %s" % response.status_code)
        log.error(response.text)
        raise RuntimeError("Error doing PUT - status code: %s" % response.status_code)

    return response.json()


def get_8s_user_device_id():
    """
        Get the user's device ID from the 8s Client API.

        :returns: None
    """
    global DEVICE_ID, SIDE
    log.debug("User Device Response:")
    response = get_8s("%s/users/me" % CLIENT_URL)
    DEVICE_ID = response["user"]["currentDevice"]["id"]
    SIDE = response["user"]["currentDevice"]["side"]


def set_color(temp_level):
    """
        Set the color of the text based on the temp level.

        :param temp_level: int

        :returns: hex
    """
    if temp_level > 0:
        return HOT
    elif temp_level < 0:
        return COLD
    else:
        return ZERO


def setup_display():
    """
        Set up the display and text areas.

        :returns: tuple
            current_text_area: bitmap_label.Label
                Current Temperature text area

            target_text_area: bitmap_label.Label
                Target Temperature text area
    """
    group = displayio.Group()
    top_buffer = 4

    # add current temp level in the 1st quarter
    current_header_text_area = bitmap_label.Label(
        font=terminalio.FONT,
        text="Current",
        color=0xebb134,
        anchored_position=(board.DISPLAY.width // 2, top_buffer + 10),
        anchor_point=(0.5, 0.5),
        scale=2
    )
    group.append(current_header_text_area)

    current_text_area = bitmap_label.Label(
        font=terminalio.FONT,
        text="---",
        color=0xFFFFFF,
        anchored_position=(board.DISPLAY.width // 2, top_buffer + 70),
        anchor_point=(0.5, 0.5),
        scale=5
    )
    group.append(current_text_area)

    # add target temp level in the 3rd quarter
    target_header_text_area = bitmap_label.Label(
        font=terminalio.FONT,
        text="Target",
        color=0xebb134,
        anchored_position=(board.DISPLAY.width // 2, top_buffer + 10 + 120),
        anchor_point=(0.5, 0.5),
        scale=2
    )
    group.append(target_header_text_area)

    target_text_area = bitmap_label.Label(
        font=terminalio.FONT,
        text="---",
        color=0xFFFFFF,
        anchored_position=(board.DISPLAY.width // 2, top_buffer + 70 + 120),
        anchor_point=(0.5, 0.5),
        scale=5
    )
    group.append(target_text_area)

    display.root_group = group
    return current_text_area, target_text_area


async def set_s8_target_temp_loop():
    """
        Loop to set the target temp level on the 8s device.
        Only sets the target temp level if the TARGET_TEMP has been changed by a button press.

        :returns: None
    """
    global TARGET_TEMP, API_LOCK, INIT_DONE, SIDE, TARGET_TEMP_IS_PENDING

    while True:
        # we only want to set temp if the target_temp has been changed by a button press
        if TARGET_TEMP_IS_PENDING:
            if API_LOCK is False:
                API_LOCK = True
                log.info("set_s8_target_temp: Setting Target Temp Level to %s" % TARGET_TEMP)

                payload = {
                    "currentLevel": TARGET_TEMP * 10,
                    "currentState": {
                        "type": "smart"
                    }
                }
                response = put_8s("%s/users/me/temperature" % CLIENT_URL, payload)
                log.debug(json.dumps(response))

                TARGET_TEMP_IS_PENDING = False
                API_LOCK = False
        await asyncio.sleep(10)


async def get_s8_device_loop():
    """
        Loop to get the current temp level and target temp level from the 8s device.
        Shouldn't override the target temp level if it's been changed by a button press.

        :returns: None
    """
    global CURRENT_TEMP, TARGET_TEMP, SIDE_ACTIVE, API_LOCK, INIT_DONE, SIDE, TARGET_TEMP_IS_PENDING

    while True:
        # lock the function to prevent multiple calls
        if API_LOCK is False:
            API_LOCK = True
            log.info("Refreshing Device Status")
            response = get_8s("%s/devices/%s" % (CLIENT_URL, DEVICE_ID))

            kelvin = response["result"]["%sKelvin" % SIDE]
            heat_level = response["result"]["%sHeatingLevel" % SIDE]

            SIDE_ACTIVE = kelvin["active"]
            CURRENT_TEMP = round(heat_level / 10)
            # Don't update the target temp if it's pending (from a button press)
            if TARGET_TEMP_IS_PENDING is False:
                TARGET_TEMP = round(kelvin["currentTargetLevel"] / 10)

            log.info("*"*20)
            log.info("Current Temp Level: %s" % CURRENT_TEMP)
            if SIDE_ACTIVE:
                log.info("Target Temp Level: %s" % TARGET_TEMP)
            else:
                log.info("Target Temp Level: Off")
            log.info("*"*20)
            log.info("")

            API_LOCK = False
            INIT_DONE = True
        await asyncio.sleep(30)


async def temp_up_loop():
    """
        Watch for temp up button press and increment the target temp level by 1.

        :returns: None
    """
    global TARGET_TEMP, TARGET_TEMP_IS_PENDING
    while True:
        button = async_button.SimpleButton(pin=board.D6, value_when_pressed=False, pull=True, interval=0.25)
        await button.pressed()

        log.info("Temp Up!")
        if TARGET_TEMP < 10:
            TARGET_TEMP += 1
            TARGET_TEMP_IS_PENDING = True
        else:
            log.info("Max Temp Reached!")
        await asyncio.sleep(0.1)


async def temp_down_loop():
    """
        Watch for temp down button press and decrement the target temp level by 1.

        :returns: None
    """
    global TARGET_TEMP, TARGET_TEMP_IS_PENDING
    while True:
        button = async_button.SimpleButton(pin=board.D5, value_when_pressed=False, pull=True, interval=0.25)
        await button.pressed()

        log.info("Temp Down!")
        if TARGET_TEMP > -10:
            TARGET_TEMP -= 1
            TARGET_TEMP_IS_PENDING = True
        else:
            log.info("Min Temp Reached!")
        await asyncio.sleep(0.1)


async def update_display_loop(current_text_area, target_text_area):
    """
    Update display text when there are changes to the current temp level or target temp level.

    :param current_text_area: bitmap_label.Label
        Current Temperature text area
    :param target_text_area: bitmap_label.Label
        Target Temperature text area

    :returns: None
    """
    # initialize the color and text
    current_color_last_set = ZERO
    current_text_last_set = "---"

    target_color_last_set = ZERO
    target_text_last_set = "---"

    while True:
        # Only update color or text if changed
        current_color = set_color(CURRENT_TEMP)
        if current_color_last_set != current_color:
            current_text_area.color = current_color
            current_color_last_set = current_color

        if current_text_last_set != str(CURRENT_TEMP):
            current_text_area.text = str(CURRENT_TEMP)
            current_text_last_set = str(CURRENT_TEMP)

        target_color = set_color(TARGET_TEMP)
        if target_color_last_set != target_color:
            target_text_area.color = target_color
            target_color_last_set = target_color

        if target_text_last_set != str(TARGET_TEMP):
            if SIDE_ACTIVE:
                target_text_area.text = str(TARGET_TEMP)
                target_text_last_set = str(TARGET_TEMP)
            else:
                target_text_area.text = "Off"
                target_text_last_set = "Off"

        await asyncio.sleep(1)


async def main():
    (current_text_area, target_text_area) = setup_display()

    get_8s_access_token()
    get_8s_user_device_id()

    log.info("Device ID: %s" % DEVICE_ID)

    # interrupt_task = asyncio.create_task(catch_interrupt(board.D5))
    update_display_task = asyncio.create_task(update_display_loop(current_text_area, target_text_area))
    temp_up_task = asyncio.create_task(temp_up_loop())
    temp_down_task = asyncio.create_task(temp_down_loop())
    update_device_loop_task = asyncio.create_task(get_s8_device_loop())
    set_s8_target_temp_task = asyncio.create_task(set_s8_target_temp_loop())

    await asyncio.gather(
        update_display_task,
        update_device_loop_task,
        temp_up_task,
        temp_down_task,
        set_s8_target_temp_task
    )


asyncio.run(main())
