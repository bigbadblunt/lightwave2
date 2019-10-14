import requests #TODO replace all requests with aiohttp
import asyncio
import uuid
import json
import datetime
import aiohttp

import logging

_LOGGER = logging.getLogger(__name__)

AUTH_SERVER = "https://auth.lightwaverf.com/v2/lightwaverf/autouserlogin/lwapps"
TRANS_SERVER = "wss://v1-linkplus-app.lightwaverf.com"
VERSION = "1.6.8"
MAX_RETRIES = 5
PUBLIC_AUTH_SERVER = "https://auth.lightwaverf.com/token"
PUBLIC_API = "https://publicapi.lightwaverf.com/v1/"
RGB_FLOOR = int("0x1B", 16) #Lightwave app seems to floor RGB values here, let's do the same

#TODO adapt async_connect calls to respond to connection failure

class _LWRFWebsocketMessage:
    _tran_id = 0
    _sender_id = str(uuid.uuid4())

    def __init__(self, opclass, operation):
        self._message = {"class": opclass, "operation": operation, "version": 1, "senderId": self._sender_id,
                         "transactionId": _LWRFWebsocketMessage._tran_id}
        _LWRFWebsocketMessage._tran_id += 1
        self._message["direction"] = "request"
        self._message["items"] = []

    def additem(self, newitem):
        self._message["items"].append(newitem._item)

    def json(self):
        return json.dumps(self._message)


class _LWRFWebsocketMessageItem:
    _item_id = 0

    def __init__(self, payload=None):
        if payload is None:
            payload = {}
        self._item = {"itemId": _LWRFWebsocketMessageItem._item_id}
        _LWRFWebsocketMessageItem._item_id += 1
        self._item["payload"] = payload


class LWRFFeatureSet:

    def __init__(self):
        self.featureset_id = None
        self.name = None
        self.product_code = None
        self.features = {}
        self._switchable = False
        self._dimmable = False
        self._climate = False
        self._gen2 = False
        self._power_reporting = False
        self._has_led = False
        self._cover = False

    def is_switch(self):
        return self._switchable and not self._dimmable

    def is_light(self):
        return self._dimmable

    def is_climate(self):
        return self._climate

    def is_cover(self):
        return self._cover

    def is_gen2(self):
        return self._gen2

    def reports_power(self):
        return self._power_reporting

    def has_led(self):
        return self._has_led


class LWLink2:

    def __init__(self, username=None, password=None, auth_method="username", api_token=None, refresh_token=None):

        self.featuresets = {}
        self._authtoken = None

        self._username = username
        self._password = password

        self._auth_method = auth_method
        self._api_token = api_token
        self._refresh_token = refresh_token

        self._session = aiohttp.ClientSession()
        self._token_expiry = None

        # Websocket only variables:
        self._device_id = str(uuid.uuid4())
        self._websocket = None
        self._callback = []
        self._group_ids = []

        # Next three variables are used to synchronise responses to requests
        self._transaction = None
        self._waitingforresponse = asyncio.Event()
        self._response = None

        asyncio.ensure_future(self._consumer_handler())

    async def _async_sendmessage(self, message, _retry=1):

        if not self._websocket or self._websocket.closed:
            _LOGGER.debug("Can't send (websocket closed), reconnecting")
            await self.async_connect()
            _LOGGER.debug("Connection reopened")

        _LOGGER.debug("Sending: %s", message.json())
        await self._websocket.send_str(message.json())
        self._transaction = message._message["transactionId"]
        self._waitingforresponse.clear()
        await self._waitingforresponse.wait()
        _LOGGER.debug("Response received: %s", str(self._response))

        if self._response:
            return self._response
        elif _retry >= MAX_RETRIES:
            _LOGGER.debug("Exceeding MAX_RETRIES, abandoning send")
            return None
        else:
            _LOGGER.debug("Send may have failed (websocket closed), reconnecting")
            await self.async_connect()
            _LOGGER.debug("Connection reopened, resending message (attempt %s)", _retry + 1)
            return await self._async_sendmessage(message, _retry + 1)

    # Use asyncio.coroutine for compatibility with Python 3.5
    @asyncio.coroutine
    def _consumer_handler(self):
        while True:
            try:
                mess = yield from self._websocket.receive()
                _LOGGER.debug("Received %s", mess)
                if mess.type == aiohttp.WSMsgType.TEXT:
                    message = mess.json()
                    # Some transaction IDs don't work, this is a workaround
                    if message["class"] == "feature" and (
                            message["operation"] == "write" or message["operation"] == "read"):
                        message["transactionId"] = message["items"][0]["itemId"]
                    # now parse the message
                    if message["transactionId"] == self._transaction:
                        self._waitingforresponse.set()
                        self._transactions = None
                        self._response = message
                    elif message["direction"] == "notification" and message["class"] == "group" \
                            and message["operation"] == "event":
                        yield from self.async_get_hierarchy()
                    elif message["direction"] == "notification" and message["operation"] == "event":
                        if "featureId" in message["items"][0]["payload"]:
                            feature_id = message["items"][0]["payload"]["featureId"]
                            feature = self.get_feature_by_featureid(feature_id)
                            value = message["items"][0]["payload"]["value"]
                            self.get_featureset_by_featureid(feature_id).features[feature][1] = value
                            _LOGGER.debug("Event received (%s %s %s), calling callbacks %s", feature_id, feature, value, self._callback)
                            for func in self._callback:
                                func()
                        else:
                            _LOGGER.warning("Unhandled event message: %s", message)
                    else:
                        _LOGGER.warning("Received unhandled message: %s", message)
                elif mess.type == aiohttp.WSMsgType.CLOSED:
                    # We're not going to get a response, so clear response flag to allow _send_message to unblock
                    _LOGGER.debug("Websocket closed in message handler")
                    self._waitingforresponse.set()
                    self._transactions = None
                    self._response = None
                    self._websocket = None
                    asyncio.ensure_future(self.async_connect())
            except AttributeError:  # websocket is None if not set up, just wait for a while
                yield from asyncio.sleep(1)

    async def async_register_callback(self, callback):
        _LOGGER.debug("Register callback %s", callback)
        self._callback.append(callback)

    async def async_get_hierarchy(self):
        _LOGGER.debug("Reading hierarchy")
        readmess = _LWRFWebsocketMessage("user", "rootGroups")
        readitem = _LWRFWebsocketMessageItem()
        readmess.additem(readitem)
        response = await self._async_sendmessage(readmess)

        self._group_ids = []
        for item in response["items"]:
            self._group_ids = self._group_ids + item["payload"]["groupIds"]

        _LOGGER.debug("Reading groups {}".format(self._group_ids))
        await self._async_read_groups()

        await self.async_update_featureset_states()

    async def _async_read_groups(self):
        self.featuresets = {}
        for groupId in self._group_ids:
            readmess = _LWRFWebsocketMessage("group", "read")
            readitem = _LWRFWebsocketMessageItem({"groupId": groupId,
                                         "blocks": True,
                                         "devices": True,
                                         "features": True,
                                         "scripts": True,
                                         "subgroups": True,
                                         "subgroupDepth": 10})
            readmess.additem(readitem)
            response = await self._async_sendmessage(readmess)

            for x in list(response["items"][0]["payload"]["features"].values()):
                for z in x["groups"]:

                    if z not in self.featuresets:
                        _LOGGER.debug("Creating device {}".format(x))
                        new_featureset = LWRFFeatureSet()
                        new_featureset.featureset_id = z
                        new_featureset.product_code = "Not working"  # TODO!
                        new_featureset.name = x["name"]
                        self.featuresets[z] = new_featureset

                    _LOGGER.debug("Adding device features {}".format(x))
                    y = self.featuresets[z]
                    y.features[x["attributes"]["type"]] = [x["featureId"], 0] #Something has changed meaning the server doesn't return values on first call
                    if x["attributes"]["type"] == "switch":
                        y._switchable = True
                    if x["attributes"]["type"] == "dimLevel":
                        y._dimmable = True
                    if x["attributes"]["type"] == "targetTemperature":
                        y._climate = True
                    if x["attributes"]["type"] == "identify":
                        y._gen2 = True
                    if x["attributes"]["type"] == "power":
                        y._power_reporting = True
                    if x["attributes"]["type"] == "rgbColor":
                        y._has_led = True
                    if x["attributes"]["type"] == "threeWayRelay":
                        y._cover = True

    async def async_update_featureset_states(self):
        for dummy, x in self.featuresets.items():
            for y in x.features:
                value = await self.async_read_feature(x.features[y][0])
                x.features[y][1] = value["items"][0]["payload"]["value"]

    async def async_write_feature(self, feature_id, value):
        readmess = _LWRFWebsocketMessage("feature", "write")
        readitem = _LWRFWebsocketMessageItem({"featureId": feature_id, "value": value})
        readmess.additem(readitem)
        await self._async_sendmessage(readmess)

    async def async_read_feature(self, feature_id):
        readmess = _LWRFWebsocketMessage("feature", "read")
        readitem = _LWRFWebsocketMessageItem({"featureId": feature_id})
        readmess.additem(readitem)
        return await self._async_sendmessage(readmess)

    def get_featureset_by_id(self, featureset_id):
        return self.featuresets[featureset_id]

    def get_featureset_by_featureid(self, feature_id):
        for dummy, x in self.featuresets.items():
            for y in x.features.values():
                if y[0] == feature_id:
                    return x
        return None

    def get_feature_by_featureid(self, feature_id):
        for dummy, x in self.featuresets.items():
            for z, y in x.features.items():
                if y[0] == feature_id:
                    return z

    async def async_turn_on_by_featureset_id(self, featureset_id):
        y = self.get_featureset_by_id(featureset_id)
        feature_id = y.features["switch"][0]
        await self.async_write_feature(feature_id, 1)

    async def async_turn_off_by_featureset_id(self, featureset_id):
        y = self.get_featureset_by_id(featureset_id)
        feature_id = y.features["switch"][0]
        await self.async_write_feature(feature_id, 0)

    async def async_set_brightness_by_featureset_id(self, featureset_id, level):
        y = self.get_featureset_by_id(featureset_id)
        feature_id = y.features["dimLevel"][0]
        await self.async_write_feature(feature_id, level)

    async def async_set_temperature_by_featureset_id(self, featureset_id, level):
        y = self.get_featureset_by_id(featureset_id)
        feature_id = y.features["targetTemperature"][0]
        await self.async_write_feature(feature_id, int(level * 10))

    async def async_cover_open_by_featureset_id(self, featureset_id):
        y = self.get_featureset_by_id(featureset_id)
        feature_id = y.features["threeWayRelay"][0]
        await self.async_write_feature(feature_id, 1)

    async def async_cover_close_by_featureset_id(self, featureset_id):
        y = self.get_featureset_by_id(featureset_id)
        feature_id = y.features["threeWayRelay"][0]
        await self.async_write_feature(feature_id, 2)

    async def async_cover_stop_by_featureset_id(self, featureset_id):
        y = self.get_featureset_by_id(featureset_id)
        feature_id = y.features["threeWayRelay"][0]
        await self.async_write_feature(feature_id, 0)

    async def async_set_led_rgb_by_featureset_id(self, featureset_id, color):
        red = (color & int("0xFF0000", 16)) >> 16
        if red != 0:
            red = min(max(red, RGB_FLOOR), 255)
        green = (color & int("0xFF00", 16)) >> 8
        if green != 0:
            green = min(max(green, RGB_FLOOR), 255)
        blue = (color & int("0xFF", 16))
        if blue != 0:
            blue = min(max(blue , RGB_FLOOR), 255)
        newcolor = (red << 16) + (green << 8) + blue
        y = self.get_featureset_by_id(featureset_id)
        feature_id = y.features["rgbColor"][0]
        await self.async_write_feature(feature_id, newcolor)

    def get_switches(self):
        temp = []
        for dummy, x in self.featuresets.items():
            if x.is_switch():
                temp.append((x.featureset_id, x.name))
        return temp

    def get_lights(self):
        temp = []
        for dummy, x in self.featuresets.items():
            if x.is_light():
                temp.append((x.featureset_id, x.name))
        return temp

    def get_climates(self):
        temp = []
        for dummy, x in self.featuresets.items():
            if x.is_climate():
                temp.append((x.featureset_id, x.name))
        return temp

    def get_covers(self):
        temp = []
        for dummy, x in self.featuresets.items():
            if x.is_cover():
                temp.append((x.featureset_id, x.name))
        return temp

    #########################################################
    # Connection
    #########################################################

    async def async_connect(self, max_tries=5, _retry=0):
        try:
            if (not self._websocket) or self._websocket.closed:
                _LOGGER.debug("Connecting to websocket")
                self._websocket = await self._session.ws_connect(TRANS_SERVER)
            return await self._authenticate_websocket()
        except Exception as exp:
            if (max_tries == 0) or (_retry < max_tries):
                retry_delay = min(2 ** (_retry + 1), 120)
                _LOGGER.warning("Cannot connect (exception '{}'). Waiting {} seconds to retry".format(exp, retry_delay))
                await asyncio.sleep(retry_delay)
                return await self.async_connect(max_tries, _retry + 1)
            else:
                _LOGGER.warning("Cannot connect, max_tries exceeded, aborting")
                return False

    async def _authenticate_websocket(self):
        if not self._authtoken:
            await self._get_access_token()
        if self._authtoken:
            authmess = _LWRFWebsocketMessage("user", "authenticate")
            authpayload = _LWRFWebsocketMessageItem({"token": self._authtoken, "clientDeviceId": self._device_id})
            authmess.additem(authpayload)
            response = await self._async_sendmessage(authmess)
            if not response["items"][0]["success"]:
                if response["items"][0]["error"]["code"] == "200":
                    # "Channel is already authenticated" - Do nothing
                    pass
                elif response["items"][0]["error"]["code"] == 405:
                    # "Access denied" - bogus token, let's reauthenticate
                    # Lightwave seems to return a string for 200 but an int for 405!
                    _LOGGER.debug("Authentication token rejected, regenerating and reauthenticating")
                    self._authtoken = None
                    await self._authenticate_websocket()
                elif response["items"][0]["error"]["message"] == "user-msgs: Token not valid/expired.":
                    _LOGGER.debug("Authentication token expired, regenerating and reauthenticating")
                    self._authtoken = None
                    await self._authenticate_websocket()
                else:
                    _LOGGER.warning("Unhandled authentication error {}".format(response["items"][0]["error"]["message"]))
            return response
        else:
            return None

    async def _get_access_token(self):
        if self._auth_method == "username":
            await self._get_access_token_username()
        elif self._auth_method == "api":
            self._get_access_token_api()
        else:
            raise ValueError("auth_method must be 'username' or 'api'")

    async def _get_access_token_username(self):
        _LOGGER.debug("Requesting authentication token (using username and password)")
        authentication = {"email": self._username, "password": self._password, "version": VERSION}
        async with self._session.post(AUTH_SERVER, headers={"x-lwrf-appid": "ios-01"}, json=authentication) as req:
            _LOGGER.debug("Received response: {}".format(await req.json()))
            if req.status == 200:
                self._authtoken = (await req.json())["tokens"]["access_token"]
            else:
                _LOGGER.warning("No authentication token (status_code '{}').".format(req.status))
                raise ConnectionError("No authentication token: {}".format(await req.text))

    # TODO check for token expiry
    def _get_access_token_api(self):
        _LOGGER.debug("Requesting authentication token (using API key and refresh token)")
        authentication = {"grant_type": "refresh_token", "refresh_token": self._refresh_token}
        req = requests.post(PUBLIC_AUTH_SERVER,
                            headers={"authorization": "basic " + self._api_token},
                            json=authentication)
        _LOGGER.debug("Received response: {}".format(req))
        if req.status_code == 200:
            self._authtoken = req.json()["access_token"]
            self._refresh_token = req.json()["refresh_token"]
            self._token_expiry = datetime.datetime.now() + datetime.timedelta(seconds=req.json()["expires_in"])
        else:
            _LOGGER.warning("No authentication token (status_code '{}').".format(req.status_code))
            raise ConnectionError("No authentication token: {}".format(req.text))

    #########################################################
    # Convenience methods for non-async calls
    #########################################################

    def _sendmessage(self, message):
        return asyncio.get_event_loop().run_until_complete(self._async_sendmessage(message))

    def get_hierarchy(self):
        return asyncio.get_event_loop().run_until_complete(self.async_get_hierarchy())

    def update_featureset_states(self):
        return asyncio.get_event_loop().run_until_complete(self.async_update_featureset_states())

    def write_feature(self, feature_id, value):
        return asyncio.get_event_loop().run_until_complete(self.async_write_feature(feature_id, value))

    def read_feature(self, feature_id):
        return asyncio.get_event_loop().run_until_complete(self.async_read_feature(feature_id))

    def turn_on_by_featureset_id(self, featureset_id):
        return asyncio.get_event_loop().run_until_complete(self.async_turn_on_by_featureset_id(featureset_id))

    def turn_off_by_featureset_id(self, featureset_id):
        return asyncio.get_event_loop().run_until_complete(self.async_turn_off_by_featureset_id(featureset_id))

    def set_brightness_by_featureset_id(self, featureset_id, level):
        return asyncio.get_event_loop().run_until_complete(self.async_set_brightness_by_featureset_id(featureset_id, level))

    def set_temperature_by_featureset_id(self, featureset_id, level):
        return asyncio.get_event_loop().run_until_complete(self.async_set_temperature_by_featureset_id(featureset_id, level))

    def cover_open_by_featureset_id(self, featureset_id):
        return asyncio.get_event_loop().run_until_complete(self.async_cover_open_by_featureset_id(featureset_id))

    def cover_close_by_featureset_id(self, featureset_id):
        return asyncio.get_event_loop().run_until_complete(self.async_cover_close_by_featureset_id(featureset_id))

    def cover_stop_by_featureset_id(self, featureset_id):
        return asyncio.get_event_loop().run_until_complete(self.async_cover_stop_by_featureset_id(featureset_id))

    def connect(self):
        return asyncio.get_event_loop().run_until_complete(self.async_connect())


class LWLink2Public(LWLink2):

    def __init__(self, username=None, password=None, auth_method="username", api_token=None, refresh_token=None):

        self.featuresets = {}
        self._authtoken = None

        self._username = username
        self._password = password
        self._auth_method = auth_method
        self._api_token = api_token
        self._refresh_token = refresh_token

        self._session = aiohttp.ClientSession()
        self._token_expiry = None

    # TODO add retries/error checking
    async def _async_getrequest(self, endpoint, _retry=1):
        _LOGGER.debug("Sending API GET request to {}".format(endpoint))
        req = requests.get(PUBLIC_API + endpoint,
                           headers={"authorization": "bearer " + self._authtoken})
        _LOGGER.debug("Received API response {}".format(req.json()))
        return req.json()

    async def _async_postrequest(self, endpoint, body="", _retry=1):
        _LOGGER.debug("Sending API POST request to {}: {}".format(endpoint, body))
        req = requests.post(PUBLIC_API + endpoint,
                            headers={"authorization": "bearer " + self._authtoken}, json=body)
        _LOGGER.debug("Received API response {}".format(req.json()))
        return req.json()

    async def _async_deleterequest(self, endpoint, _retry=1):
        _LOGGER.debug("Sending API DELETE request to {}".format(endpoint))
        req = requests.delete(PUBLIC_API + endpoint,
                            headers={"authorization": "bearer " + self._authtoken})
        _LOGGER.debug("Received API response {}".format(req.json()))
        return req.json()

    async def async_get_hierarchy(self):

        self.featuresets = {}
        req = await self._async_getrequest("structures")
        for struct in req["structures"]:
            response = await self._async_getrequest("structure/" + struct)

            for x in response["devices"]:
                for y in x["featureSets"]:
                    _LOGGER.debug("Creating device {}".format(x))
                    new_featureset = LWRFFeatureSet()
                    new_featureset.featureset_id = y["featureSetId"]
                    new_featureset.product_code = x["productCode"]
                    new_featureset.name = x["name"]

                    for z in y["features"]:
                        _LOGGER.debug("Adding device features {}".format(z))
                        new_featureset.features[z["type"]] = [z["featureId"], None]
                        if z["type"] == "switch":
                            new_featureset._switchable = True
                        if z["type"] == "dimLevel":
                            new_featureset._dimmable = True
                        if z["type"] == "targetTemperature":
                            new_featureset._climate = True
                        if z["type"] == "identify":
                            new_featureset._gen2 = True
                        if z["type"] == "power":
                            new_featureset._power_reporting = True
                        if z["type"] == "threeWayRelay":
                            new_featureset._cover = True
                    self.featuresets[y["featureSetId"]] = new_featureset

        await self.async_update_featureset_states()

    async def async_register_callback(self, callback):
        pass

    async def async_register_webhook(self, url, feature_id, ref, overwrite = False):
        if overwrite:
            req = await self._async_deleterequest("events/" + ref)
        payload = {"events": [{"type": "feature", "id": feature_id}],
                    "url": url,
                    "ref": ref}
        req = await self._async_postrequest("events", payload)
        #TODO: test for req = 200

    def process_webhook_received(self, body):

        featureid = body['triggerEvent']['id']
        featuresetid = self.get_featureset_by_featureid(featureid)
        featurename = self.get_feature_by_featureid(featureid)
        value = body['payload']['value']
        self.featuresets[featuresetid].features[featurename][1] = value

    async def async_update_featureset_states(self):
        feature_list = []

        for dummy, x in self.featuresets.items():
            for y in x.features:
                feature_list.append({"featureId": x.features[y][0]})
        body = {"features": feature_list}
        req = await self._async_postrequest("features/read", body)

        for featuresetid in self.featuresets:
            for featurename in self.featuresets[featuresetid].features:
                if self.featuresets[featuresetid].features[featurename][0] in req:
                    self.featuresets[featuresetid].features[featurename][1] = req[self.featuresets[featuresetid].features[featurename][0]]

    async def async_write_feature(self, feature_id, value):
        payload = {"value": value}
        await self._async_postrequest("feature/" + feature_id, payload)

    async def async_read_feature(self, feature_id):
        req = await self._async_getrequest("feature/" + feature_id)
        return req["value"]

    #########################################################
    # Connection
    #########################################################

    async def async_connect(self, max_tries=5, _retry=0):
        try:
            await self._get_access_token()
        except Exception as exp:
            if (max_tries == 0) or (_retry < max_tries):
                retry_delay = min(2 ** (_retry + 1), 120)
                _LOGGER.warning("Cannot connect (exception '{}'). Waiting {} seconds".format(exp, retry_delay))
                await asyncio.sleep(retry_delay)
                return await self.async_connect(_retry + 1)
            else:
                _LOGGER.warning("Cannot connect, max_tries exceeded, aborting")
                return False
        return True
    # TODO distinguish failure on no token and don't retry


