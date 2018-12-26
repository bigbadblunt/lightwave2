import requests
import asyncio
import websockets
import uuid
import json

import logging

_LOGGER = logging.getLogger(__name__)

AUTH_SERVER = "https://auth.lightwaverf.com/v2/lightwaverf/autouserlogin/lwapps"
TRANS_SERVER = "wss://v1-linkplus-app.lightwaverf.com"
VERSION = "1.6.8"


class _LWRFMessage:
    _tran_id = 0
    _sender_id = str(uuid.uuid4())

    def __init__(self, opclass, operation):
        self._message = {"class": opclass, "operation": operation, "version": 1, "senderId": self._sender_id,
                         "transactionId": _LWRFMessage._tran_id}
        _LWRFMessage._tran_id += 1
        self._message["direction"] = "request"
        self._message["items"] = []

    def additem(self, newitem):
        self._message["items"].append(newitem._item)

    def json(self):
        return json.dumps(self._message)


class _LWRFMessageItem:
    _item_id = 0

    def __init__(self, payload=None):
        if payload is None:
            payload = {}
        self._item = {"itemId": _LWRFMessageItem._item_id}
        _LWRFMessageItem._item_id += 1
        self._item["payload"] = payload


class _LWRFDevice:

    def __init__(self):
        self.device_id = None
        self.name = None
        self.product_code = None
        self.features = {}
        self._switchable = False
        self._dimmable = False
        self._climate = False

    def is_switch(self):
        return self._switchable and not self._dimmable

    def is_light(self):
        return self._dimmable

    def is_climate(self):
        return self._climate

class LWLink2:

    def __init__(self, username, password):
        self._username = username
        self._password = password
        self._device_id = str(uuid.uuid4())
        self._websocket = None
        self._callback = []
        self.devices = []

        # Next three variables are used to synchronise responses to requests
        self._transaction = None
        self._waitingforresponse = asyncio.Event()
        self._response = None

    def _sendmessage(self, message):
        return asyncio.get_event_loop().run_until_complete(self._async_sendmessage(message))

    async def _async_sendmessage(self, message):
        # await self.outgoing.put(message)
        _LOGGER.debug("Sending: %s", message.json())
        await self._websocket.send(message.json())
        self._transaction = message._message["transactionId"]
        self._waitingforresponse.clear()
        await self._waitingforresponse.wait()
        _LOGGER.debug("Response: %s", str(self._response))
        return self._response

    # Use asyncio.coroutine for compatibility with Python 3.5
    @asyncio.coroutine
    def _consumer_handler(self):
        while True:
            jsonmessage = yield from self._websocket.recv()
            message = json.loads(jsonmessage)
            _LOGGER.debug("Received %s", message)
            # Some transaction IDs don't work, this is a workaround
            if message["class"] == "feature" and (message["operation"] == "write" or message["operation"] == "read"):
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
                device_id = message["items"][0]["payload"]["_feature"]["deviceId"]
                feature = message["items"][0]["payload"]["_feature"]["featureType"]
                value = message["items"][0]["payload"]["value"]
                assert self.get_device_by_id(device_id).features[feature][0] == \
                    message["items"][0]["payload"]["_feature"][
                           "featureId"]
                self.get_device_by_id(device_id).features[feature][1] = value
                for func in self._callback:
                    yield from func()
            else:
                _LOGGER.warning("Received unhandled message: %s", message)

    async def async_register_callback(self, callback):
        self._callback.append(callback)

    def get_hierarchy(self):
        return asyncio.get_event_loop().run_until_complete(self.async_get_hierarchy())

    async def async_get_hierarchy(self):
        readmess = _LWRFMessage("user", "rootGroups")
        readitem = _LWRFMessageItem()
        readmess.additem(readitem)
        response = await self._async_sendmessage(readmess)

        for item in response["items"]:
            group_ids = item["payload"]["groupIds"]
            await self._async_read_groups(group_ids)

        await self.async_update_device_states()

    async def _async_read_groups(self, group_ids):
        for groupId in group_ids:
            readmess = _LWRFMessage("group", "read")
            readitem = _LWRFMessageItem({"groupId": groupId,
                                         "blocks": True,
                                         "devices": True,
                                         "features": True,
                                         "scripts": True,
                                         "subgroups": True,
                                         "subgroupDepth": 10})
            readmess.additem(readitem)
            response = await self._async_sendmessage(readmess)

            self.devices = []
            for x in list(response["items"][0]["payload"]["devices"].values()):
                new_device = _LWRFDevice()
                new_device.device_id = x["deviceId"]
                new_device.name = x["name"]
                new_device.product_code = x["productCode"]
                self.devices.append(new_device)
            for x in list(response["items"][0]["payload"]["features"].values()):
                y = self.get_device_by_id(x["deviceId"])
                y.features[x["attributes"]["type"]] = [x["featureId"], x["attributes"]["value"]]
                if x["attributes"]["type"] == "switch":
                    y._switchable = True
                if x["attributes"]["type"] == "dimLevel":
                    y._dimmable = True
                if x["attributes"]["type"] == "targetTemperature":
                    y._climate = True

            # TODO - work out if I care about "group"/"hierarchy"

    def update_device_states(self):
        return asyncio.get_event_loop().run_until_complete(self.async_update_device_states())

    async def async_update_device_states(self):
        for x in self.devices:
            for y in x.features:
                value = await self.async_read_feature(x.features[y][0])
                x.features[y][1] = value["items"][0]["payload"]["value"]

    def write_feature(self, feature_id, value):
        return asyncio.get_event_loop().run_until_complete(self.async_write_feature(feature_id, value))

    async def async_write_feature(self, feature_id, value):
        readmess = _LWRFMessage("feature", "write")
        readitem = _LWRFMessageItem({"featureId": feature_id, "value": value})
        readmess.additem(readitem)
        await self._async_sendmessage(readmess)

    def read_feature(self, feature_id):
        return asyncio.get_event_loop().run_until_complete(self.async_read_feature(feature_id))

    async def async_read_feature(self, feature_id):
        readmess = _LWRFMessage("feature", "read")
        readitem = _LWRFMessageItem({"featureId": feature_id})
        readmess.additem(readitem)
        return await self._async_sendmessage(readmess)

    def get_device_by_id(self, device_id):
        for x in self.devices:
            if x.device_id == device_id:
                return x
        return None

    def turn_on_by_device_id(self, device_id):
        return asyncio.get_event_loop().run_until_complete(self.async_turn_on_by_device_id(device_id))

    async def async_turn_on_by_device_id(self, device_id):
        y = self.get_device_by_id(device_id)
        feature_id = y.features["switch"][0]
        await self.async_write_feature(feature_id, 1)

    def turn_off_by_device_id(self, device_id):
        return asyncio.get_event_loop().run_until_complete(self.async_turn_off_by_device_id(device_id))

    async def async_turn_off_by_device_id(self, device_id):
        y = self.get_device_by_id(device_id)
        feature_id = y.features["switch"][0]
        await self.async_write_feature(feature_id, 0)

    def set_brightness_by_device_id(self, device_id, level):
        return asyncio.get_event_loop().run_until_complete(self.async_set_brightness_by_device_id(device_id, level))

    async def async_set_brightness_by_device_id(self, device_id, level):
        y = self.get_device_by_id(device_id)
        feature_id = y.features["dimLevel"][0]
        await self.async_write_feature(feature_id, level)

    def set_temperature_by_device_id(self, device_id, level):
        return asyncio.get_event_loop().run_until_complete(self.async_set_temperature_by_device_id(device_id, level))

    async def async_set_temperature_by_device_id(self, device_id, level):
        y = self.get_device_by_id(device_id)
        feature_id = y.features["targetTemperature"][0]
        await self.async_write_feature(feature_id, int(level*10))

    def get_switches(self):
        temp = []
        for x in self.devices:
            if x.is_switch():
                temp.append((x.device_id, x.name))
        return temp

    def get_lights(self):
        temp = []
        for x in self.devices:
            if x.is_light():
                temp.append((x.device_id, x.name))
        return temp

    def get_climates(self):
        temp = []
        for x in self.devices:
            if x.is_climate():
                temp.append((x.device_id, x.name))
        return temp

    #########################################################
    # Connection
    #########################################################

    def connect(self):
        return asyncio.get_event_loop().run_until_complete(self.async_connect())

    async def async_connect(self):
        self._websocket = await websockets.connect(TRANS_SERVER, ssl=True)
        asyncio.ensure_future(self._consumer_handler())
        return await self._authenticate()

    async def _authenticate(self):
        accesstoken = self._get_access_token()
        if accesstoken:
            authmess = _LWRFMessage("user", "authenticate")
            authpayload = _LWRFMessageItem({"token": accesstoken, "clientDeviceId": self._device_id})
            authmess.additem(authpayload)
            return await self._async_sendmessage(authmess)
        else:
            return None

    def _get_access_token(self):

        authentication = {"email": self._username, "password": self._password, "version": VERSION}
        req = requests.post(AUTH_SERVER,
                            headers={"x-lwrf-appid": "ios-01"},
                            json=authentication)

        if req.status_code == 200:
            token = req.json()["tokens"]["access_token"]
        else:
            token = None

        return token
