# SPDX-License-Identifier: MIT
"""
Eight Sleep Pod Temperature Controller

For use with Adafruit Feather TFT ESP32-S2

Copyright (c) 2024 Jason Greathouse
"""
import adafruit_logging as logging
import adafruit_requests
import asyncio
import async_button
import board
import displayio
import json
import socketpool
import ssl
import supervisor
import terminalio
import time
import wifi

# from adafruit_bitmap_font import bitmap_font
from adafruit_display_text import bitmap_label

log = logging.getLogger("code")
log.setLevel(logging.DEBUG)

# Get WiFi details secrets.py file
try:
    from secrets import secrets
except ImportError:
    log.error("WiFi and EightSleep user credential secrets are kept in secrets.py, please add them there!")
    raise

# Get the requests library
requests = adafruit_requests.Session

# Set up the display, we want a minimal brightness since we're using this in a bedroom
display = board.DISPLAY
display.rotation = 270
display.brightness = 0.1

# Default headers for all requests
HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "8slp/1.0.0",
}

# APP_CLIENT_ID and APP_CLIENT_SECRET are generic shared credentials borrowed from https://github.com/lukas-clarke/eight_sleep
# If EightSleep had a proper public api and developer program I suspect we could register for one.
APP_CLIENT_ID = "0894c7f33bb94800a03f1f4df13a4f38"
APP_CLIENT_SECRET = "f0954a3ed5763ba3d06834c73731a32f15f168f47d4f164751275def86db0c76"

# eight sleep auth token api
auth_URL = "https://auth-api.8slp.net/v1/tokens"

# eight sleep client api (about me)
CLIENT_URL = "https://client-api.8slp.net/v1"

HOT = 0Xc93412
ZERO = 0Xe0e0e0
COLD = 0X4c34eb

api_lock = False
current_temp = 0
device_id = ""
side = ""
side_active = False
skip_next_display_off = True
target_temp = 0
target_temp_is_pending = False

# Object to store the 8s access token
auth = {
    "access_token": "",
    "user_id": "",
}


def setup_wifi():
    """
        Connect to the WiFi network and sets up the connection pool for "requests".

        :returns: None
    """
    global requests
    log.info("Connecting to %s" % secrets["wifi_ssid"])
    wifi.radio.connect(secrets["wifi_ssid"], secrets["wifi_password"])
    log.info("Connected to %s!" % secrets["wifi_ssid"])
    log.info("My IP address is %s" % wifi.radio.ipv4_address)

    pool = socketpool.SocketPool(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl.create_default_context())


def get_8s_access_token():
    """
        Get oauth access token.

        Calling grant_type=refresh_token seems just to issue a new access token. So I guess we can just re-authenticate if access token is expired? Doesn't seem to be an issue.

        :returns: None
    """
    global auth
    auth_payload = {
        "client_id": APP_CLIENT_ID,
        "client_secret": APP_CLIENT_SECRET,
        "grant_type": "password",
        "username": secrets["8s_username"],
        "password": secrets["8s_password"],
    }

    response_status_code = 0
    response_json = {}

    with requests.post(auth_URL, json=auth_payload, headers=HEADERS) as response:
        response_status_code = response.status_code
        response_json = response.json()

    if response_status_code != 200:
        raise RuntimeError("Error doing GET - status code: %s" % response_status_code)

    log.debug("Auth Response:")
    filtered_response = response_json
    auth = {
        "access_token": filtered_response["access_token"],
        "user_id": filtered_response["userId"],
    }
    filtered_response["access_token"] = "********"
    filtered_response["refresh_token"] = "********"
    log.debug(json.dumps(filtered_response))
    log.info("EightSleep Auth Successful!")


def get_8s(url):
    """
        Generic GET request to the 8s Client API.

        :param url: str
            URL for the request

        :returns: dict
    """
    headers = HEADERS
    headers["Authorization"] = "Bearer %s" % auth["access_token"]

    response_status_code = 0
    response_json = {}

    with requests.get(url, headers=headers) as response:
        response_status_code = response.status_code
        response_json = response.json()

    if response_status_code == 401:
        # Try to refresh the token and submit the request again
        get_8s_access_token()
        headers["Authorization"] = "Bearer %s" % auth["access_token"]
        response = requests.get(url, headers=headers)

    if response_status_code != 200:
        raise RuntimeError("Error doing GET - status code: %s" % response_status_code)

    return response_json


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
    headers["Authorization"] = "Bearer %s" % auth["access_token"]

    response_status_code = 0
    response_json = {}

    with requests.put(url, json=payload, headers=headers) as response:
        response_status_code = response.status_code
        response_json = response.json()

    if response_status_code == 401:
        # Try to refresh the token and submit the request again
        get_8s_access_token()
        headers["Authorization"] = "Bearer %s" % auth["access_token"]
        response = requests.get(url, headers=headers)

    if response_status_code != 200:
        raise RuntimeError("Error doing PUT - status code: %s" % response_status_code)

    return response_json


def get_8s_user_device_id():
    """
        Get the user's device ID from the 8s Client API.

        :returns: None
    """
    global device_id, side
    log.info("User Device Response:")
    response = get_8s("%s/users/me" % CLIENT_URL)

    device_id = response["user"]["currentDevice"]["id"]
    side = response["user"]["currentDevice"]["side"]

    log.info("Device ID: %s" % device_id)
    log.info("side: %s" % side)


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
    top_buffer = 10

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
        Only sets the target temp level if the target_temp has been changed by a button press.

        :returns: None
    """
    global target_temp, api_lock, side, target_temp_is_pending

    while True:
        # we only want to set temp if the target_temp has been changed by a button press
        if target_temp_is_pending:
            if api_lock is False:
                api_lock = True
                log.info("set_s8_target_temp: Setting Target Temp Level to %s" % target_temp)

                payload = {
                    "currentLevel": target_temp * 10,
                    "currentState": {
                        "type": "smart"
                    }
                }
                response = put_8s("%s/users/me/temperature" % CLIENT_URL, payload)
                log.debug(json.dumps(response))

                target_temp_is_pending = False
                api_lock = False
        await asyncio.sleep(10)


async def get_s8_device_loop():
    """
        Loop to get the current temp level and target temp level from the 8s device.
        Shouldn't override the target temp level if it's been changed by a button press.

        :returns: None
    """
    global current_temp, target_temp, side_active, api_lock, side, target_temp_is_pending

    while True:
        # lock the function to prevent multiple calls
        if api_lock is False:
            api_lock = True
            log.info("Refreshing Device Status")
            response = get_8s("%s/devices/%s" % (CLIENT_URL, device_id))

            kelvin = response["result"]["%sKelvin" % side]
            heat_level = response["result"]["%sHeatingLevel" % side]

            side_active = kelvin["active"]
            current_temp = round(heat_level / 10)
            # Don't update the target temp if it's pending (from a button press)
            if target_temp_is_pending is False:
                target_temp = round(kelvin["currentTargetLevel"] / 10)

            log.info("*"*20)
            log.info("Current Temp Level: %s" % current_temp)
            if side_active:
                log.info("Target Temp Level: %s" % target_temp)
            else:
                log.info("Target Temp Level: Off")
            log.info("*"*20)
            log.info("")

            api_lock = False
        await asyncio.sleep(30)


async def temp_up_loop():
    """
        Watch for temp up button press and increment the target temp level by 1.

        :returns: None
    """
    global target_temp, target_temp_is_pending, skip_next_display_off
    while True:
        button = async_button.SimpleButton(pin=board.D5, value_when_pressed=False, pull=True, interval=0.25)
        await button.pressed()

        # The first button press will turn on the display if its off
        if display.brightness == 0:
            log.debug("Display On! (Up)")
            display.brightness = 0.1
            skip_next_display_off = True
        else:
            log.info("Temp Up!")
            if target_temp < 10:
                target_temp += 1
                target_temp_is_pending = True
            else:
                log.info("Max Temp Reached!")

        await asyncio.sleep(0.1)


async def temp_down_loop():
    """
        Watch for temp down button press and decrement the target temp level by 1.

        :returns: None
    """
    global target_temp, target_temp_is_pending, skip_next_display_off
    while True:
        button = async_button.SimpleButton(pin=board.D6, value_when_pressed=False, pull=True, interval=0.25)
        await button.pressed()

        # The first button press will turn on the display if its off
        if display.brightness == 0:
            log.debug("Display On! (Down)")
            display.brightness = 0.1
            skip_next_display_off = True
        else:
            log.info("Temp Down!")
            if target_temp > -10:
                target_temp -= 1
                target_temp_is_pending = True
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
        current_color = set_color(current_temp)
        if current_color_last_set != current_color:
            current_text_area.color = current_color
            current_color_last_set = current_color

        if current_text_last_set != str(current_temp):
            current_text_area.text = str(current_temp)
            current_text_last_set = str(current_temp)

        target_color = set_color(target_temp)
        if target_color_last_set != target_color:
            target_text_area.color = target_color
            target_color_last_set = target_color

        if target_text_last_set != str(target_temp):
            if side_active:
                target_text_area.text = str(target_temp)
                target_text_last_set = str(target_temp)
            else:
                target_text_area.text = "Off"
                target_text_last_set = "Off"

        await asyncio.sleep(1)


async def turn_off_display_loop():
    """
        Turn off the display after a period of time.

        :returns: None
    """
    global skip_next_display_off
    while True:
        # Skip this loop once if the display was turned on by a button press
        if skip_next_display_off:
            skip_next_display_off = False
        else:
            display.brightness = 0
            log.debug("Display Off!")

        await asyncio.sleep(60)


async def main():
    setup_wifi()

    (current_text_area, target_text_area) = setup_display()

    get_8s_access_token()
    get_8s_user_device_id()

    # interrupt_task = asyncio.create_task(catch_interrupt(board.D5))
    update_display_task = asyncio.create_task(update_display_loop(current_text_area, target_text_area))
    temp_up_task = asyncio.create_task(temp_up_loop())
    temp_down_task = asyncio.create_task(temp_down_loop())
    update_device_loop_task = asyncio.create_task(get_s8_device_loop())
    set_s8_target_temp_task = asyncio.create_task(set_s8_target_temp_loop())
    turn_off_display_task = asyncio.create_task(turn_off_display_loop())

    await asyncio.gather(
        update_display_task,
        update_device_loop_task,
        temp_up_task,
        temp_down_task,
        set_s8_target_temp_task,
        turn_off_display_task
    )


# I think this should catch most exceptions and restart the program.
# We want the keyboard interrupt to still work, that's what triggers the reload when you save files to the USB drive.
try:
    asyncio.run(main())
except KeyboardInterrupt:
    raise
except Exception as e:
    log.error(e)
    time.sleep(10)
    supervisor.reload()
