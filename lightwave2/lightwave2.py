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

class _LWRFMessage():

    _tran_id = 0
    _sender_id = str(uuid.uuid4())

    def __init__(self, opclass, operation):

        self._message = {}
        self._message["class"] = opclass
        self._message["operation"] = operation
        self._message["version"] = 1
        self._message["senderId"] = self._sender_id
        self._message["transactionId"] = _LWRFMessage._tran_id
        _LWRFMessage._tran_id += 1
        self._message["direction"] = "request"
        self._message["items"] = []

    def additem(self, newitem):
        self._message["items"].append(newitem._item)

    def json(self):
        return json.dumps(self._message)


class _LWRFMessageItem():

    _item_id = 0

    def __init__(self, payload={}):
        self._item = {}
        self._item["itemId"] = _LWRFMessageItem._item_id
        _LWRFMessageItem._item_id += 1
        self._item["payload"] = payload

class _LWRFDevice():

    def __init__(self):
        self.deviceId = None
        self.name = None
        self.productCode = None
        self.features = {}
        self._switchable = False
        self._dimmable = False

    def isSwitch(self):
        return self._switchable and not self._dimmable

    def isLight(self):
        return self._dimmable


class LWLink2():

    def __init__(self, username, password):
        self._username = username
        self._password = password
        self._device_id = str(uuid.uuid4())
        self._websocket = None
        self.devices = []

        #Next three variables are used to synchronise responses to requests
        self._transaction = None
        self._waitingforresponse = asyncio.Event()
        self._response = None

    def _sendmessage(self, message):
        return asyncio.get_event_loop().run_until_complete(self._async_sendmessage(message))

    async def _async_sendmessage(self, message):
        #await self.outgoing.put(message)
        _LOGGER.debug("Sending: %s", message.json())
        await self._websocket.send(message.json())
        self._transaction = message._message["transactionId"]
        self._waitingforresponse.clear()
        await self._waitingforresponse.wait()
        _LOGGER.debug("Response: %s", str(self._response))
        return self._response

    #Use asyncio.coroutine for compatibility with Python 3.5
    @asyncio.coroutine
    def _consumer_handler(self):
        while True:
            jsonmessage = yield from self._websocket.recv()
            message = json.loads(jsonmessage)
            _LOGGER.debug("Received %s", message)
            #Some transaction IDs don't work, this is a workaround
            if message["class"] == "feature" and (message["operation"] == "write" or message["operation"] == "read"):
                message["transactionId"] = message["items"][0]["itemId"]
            #now parse the message
            if message["transactionId"] == self._transaction:
                self._waitingforresponse.set()
                self._transactions = None
                self._response = message
            elif message["direction"] == "notification" and message["class"] == "group" and message["operation"] == "event":
                yield from self.async_getRootGroups()
                yield from self.async_updateDeviceStates()
            elif message["direction"] == "notification" and message["operation"] == "event":
                deviceId = message["items"][0]["payload"]["_feature"]["deviceId"]
                feature = message["items"][0]["payload"]["_feature"]["featureType"]
                value = message["items"][0]["payload"]["value"]
                assert self.getDeviceByID(deviceId).features[feature][0] == message["items"][0]["payload"]["_feature"]["featureId"]
                self.getDeviceByID(deviceId).features[feature][1] = value
            else:
                _LOGGER.warning("Received unhandled message: %s", message)

    def getHierarchy(self):
        return asyncio.get_event_loop().run_until_complete(self.async_getHierarchy())

    async def async_getHierarchy(self):
        readmess = _LWRFMessage("user", "rootGroups")
        readitem = _LWRFMessageItem()
        readmess.additem(readitem)
        response = await self._async_sendmessage(readmess)

        for item in response["items"]:
            groupIds = item["payload"]["groupIds"]
            await self._async_readGroups(groupIds)

        await self.async_updateDeviceStates()

    async def _async_readGroups(self, groupIds):
        for groupId in groupIds:
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
                newDevice = _LWRFDevice()
                newDevice.deviceId = x["deviceId"]
                newDevice.name = x["name"]
                newDevice.productCode = x["productCode"]
                self.devices.append(newDevice)
            for x in list(response["items"][0]["payload"]["features"].values()):
                y = self.getDeviceByID(x["deviceId"])
                y.features[x["attributes"]["type"]] = [x["featureId"], x["attributes"]["value"]]
                if x["attributes"]["type"] == "switch":
                    y._switchable = True
                if x["attributes"]["type"] == "dimLevel":
                    y._dimmable = True

            #TODO - work out if I caer about "group"/"hierarchy"

    def updateDeviceStates(self):
        return asyncio.get_event_loop().run_until_complete(self.async_updateDeviceStates())

    async def async_updateDeviceStates(self):
        for x in self.devices:
            for y in x.features:
                value = await self.async_readFeature(x.features[y][0])
                x.features[y][1] = value["items"][0]["payload"]["value"]

    def writeFeature(self, featureId, value):
        return asyncio.get_event_loop().run_until_complete(self.async_writeFeature(featureId, value))

    async def async_writeFeature(self, featureId, value):
        readmess = _LWRFMessage("feature", "write")
        readitem = _LWRFMessageItem({"featureId": featureId, "value": value})
        readmess.additem(readitem)
        await self._async_sendmessage(readmess)

    def readFeature(self, featureId):
        return asyncio.get_event_loop().run_until_complete(self.async_readFeature(featureId))

    async def async_readFeature(self, featureId):
        readmess = _LWRFMessage("feature", "read")
        readitem = _LWRFMessageItem({"featureId": featureId})
        readmess.additem(readitem)
        return await self._async_sendmessage(readmess)

    def getDeviceByID(self, deviceId):
        for x in self.devices:
            if x.deviceId == deviceId:
                return x
        return None

    def turnOnByDeviceID(self, deviceId):
        return asyncio.get_event_loop().run_until_complete(self.asyncTurnOnByDeviceID(deviceId))

    async def asyncTurnOnByDeviceID(self, deviceId):
        y = self.getDeviceByID(deviceId)
        featureId = y.features["switch"][0]
        await self.async_writeFeature(featureId, 1)

    def turnOffByDeviceID(self, deviceId):
        return asyncio.get_event_loop().run_until_complete(self.asyncTurnOffByDeviceID(deviceId))

    async def asyncTurnOffByDeviceID(self, deviceId):
        y = self.getDeviceByID(deviceId)
        featureId = y.features["switch"][0]
        await self.async_writeFeature(featureId, 1)

    def setBrightnessByDeviceID(self, deviceId, level):
        return asyncio.get_event_loop().run_until_complete(self.asyncSetBrightnessByDeviceID(deviceId, level))

    async def asyncSetBrightnessByDeviceID(self, deviceId, level):
        y = self.getDeviceByID(deviceId)
        featureId = y.features["dimLevel"][0]
        await self.async_writeFeature(featureId, 1)

    def getSwitches(self):
        temp = []
        for x in self.devices:
            if x.isSwitch():
                temp.append((x.deviceId, x.name))
        return temp

    def getLights(self):
        temp = []
        for x in self.devices:
            if x.isLight():
                temp.append((x.deviceId, x.name))
        return temp

#########################################################
#Connection
#########################################################

    def connect(self):
        return asyncio.get_event_loop().run_until_complete(self.async_connect())

    async def async_connect(self):
        self._websocket = await websockets.connect(TRANS_SERVER, ssl=True)
        asyncio.ensure_future(self._consumer_handler())
        return await self._authenticate()

    async def _authenticate(self):
        accesstoken = self._getAccessToken()
        if accesstoken:
            authmess = _LWRFMessage("user", "authenticate")
            authpayload = _LWRFMessageItem({"token": accesstoken, "clientDeviceId": self._device_id})
            authmess.additem(authpayload)
            return await self._async_sendmessage(authmess)
        else:
            return None

    def _getAccessToken(self):

        authentication = {"email":self._username, "password":self._password, "version": VERSION}
        req = requests.post(AUTH_SERVER,
                            headers={"x-lwrf-appid": "ios-01"},
                            json=authentication)

        if req.status_code == 200:
            token = req.json()["tokens"]["access_token"]
        else:
            token = None

        return token


