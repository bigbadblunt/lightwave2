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

class LWLink2():

    def __init__(self, username, password):
        self._username = username
        self._password = password
        self._device_id = str(uuid.uuid4())
        self._devices = []
        self._features = []
        self._transaction = None
        self._waitingforresponse = asyncio.Event()
        self._response = None

    def connect(self):
        return asyncio.get_event_loop().run_until_complete(self.async_connect())

    async def async_connect(self):
        self.websocket = await websockets.connect(TRANS_SERVER, ssl=True)
        asyncio.ensure_future(self.consumer_handler())
        await self.authenticate()

    def _sendmessage(self, message):
        return asyncio.get_event_loop().run_until_complete(self._async_sendmessage(message))

    async def _async_sendmessage(self, message):
        #await self.outgoing.put(message)
        print("Sending:", message.json())
        await self.websocket.send(message.json())
        self._transaction = message._message["transactionId"]
        self._waitingforresponse.clear()
        await self._waitingforresponse.wait()
        print("Response:", self._response)
        return self._response

    #TODO find a way of def _processmessages(self) (non-async version)

    @asyncio.coroutine
    def consumer_handler(self):
        while True:
            jsonmessage = yield from self.websocket.recv()
            message = json.loads(jsonmessage)
            #Some transaction IDs don't work, this is a workaround
            if message["class"] == "feature" and (message["operation"] == "write" or message["operation"] == "read"):
                message["transactionId"] = message["items"][0]["itemId"]
            if message["transactionId"] == self._transaction:
                self._waitingforresponse.set()
                self._transactions = None
                self._response = message
            else:
                #TODO direction = "notification", class = "group", "operation" = "update" - update rootgroups et al
                #TODO direction = "notification", class = "event", update internal state
                #TODO catch others
                print("Received:", message)

    def getRootGroups(self):
        return asyncio.get_event_loop().run_until_complete(self.async_getRootGroups())

    async def async_getRootGroups(self):
        readmess = _LWRFMessage("user", "rootGroups")
        readitem = _LWRFMessageItem()
        readmess.additem(readitem)
        response = await self._async_sendmessage(readmess)

        for item in response["items"]:
            groupIds = item["payload"]["groupIds"]
            await self.async_readGroups(groupIds)

    def readGroups(self, groupIds):
        return asyncio.get_event_loop().run_until_complete(self.async_readGroups(groupIds))

    async def async_readGroups(self, groupIds):
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
            self._devices = list(response["items"][0]["payload"]["devices"].values())
            self._features = list(response["items"][0]["payload"]["features"].values())
            #TODO - work out if I caer about "group"/"hierarchy"

    def writeFeature(self, featureId, value):
        return asyncio.get_event_loop().run_until_complete(self.async_writeFeature(featureId, value))

    async def async_writeFeature(self, featureId, value):
        readmess = _LWRFMessage("feature", "write")
        readitem = _LWRFMessageItem({"featureId": featureId, "value": value})
        readmess.additem(readitem)
        await self._async_sendmessage(readmess)

    def readFeature(self, featureId):
        return asyncio.get_event_loop().run_until_complete(self.async_readFeature(featureId))

    def turnOnByDeviceID(self, deviceId):
        return asyncio.get_event_loop().run_until_complete(self.asyncTurnOnByDeviceID(deviceId))

    async def asyncTurnOnByDeviceID(self, deviceId):
        for y in [z for z in self._features if z["deviceId"] == deviceId]:
            if y["attributes"]["type"] == "switch":
                featureId = y["featureId"]
        await self.async_writeFeature(featureId, 1)

    def turnOffByDeviceID(self, deviceId):
        return asyncio.get_event_loop().run_until_complete(self.asyncTurnOffByDeviceID(deviceId))

    async def asyncTurnOffByDeviceID(self, deviceId):
        for y in [z for z in self._features if z["deviceId"] == deviceId]:
            if y["attributes"]["type"] == "switch":
                featureId = y["featureId"]
        await self.async_writeFeature(featureId, 0)

    def setBrightnessByDeviceID(self, deviceId, level):
        return asyncio.get_event_loop().run_until_complete(self.asyncSetBrightnessByDeviceID(deviceId, level))

    async def asyncSetBrightnessByDeviceID(self, deviceId, level):
        for y in [z for z in self._features if z["deviceId"] == deviceId]:
            if y["attributes"]["type"] == "dimLevel":
                featureId = y["featureId"]
        await self.async_writeFeature(featureId, level)

    def isSwitch(self, device):
        switch = False
        dimmable = False
        for y in [z for z in self._features if z["deviceId"] == device["deviceId"]]:
            if y["attributes"]["type"] == "switch":
                switch = True
            if y["attributes"]["type"] == "dimLevel":
                dimmable = True
        return switch and not dimmable

    def getSwitches(self):
        temp = []
        for x in self._devices:
            if self.isSwitch(x):
                temp.append((x["deviceId"], x["name"]))
        return temp

    def isLight(self, device):
        dimmable = False
        for y in [z for z in self._features if z["deviceId"] == device["deviceId"]]:
            if y["attributes"]["type"] == "dimLevel":
                dimmable = True
        return dimmable

    def getLights(self):
        temp = []
        for x in self._devices:
            if self.isLight(x):
                temp.append((x["deviceId"], x["name"]))
        return temp

    async def async_readFeature(self, featureId):
        readmess = _LWRFMessage("feature", "read")
        readitem = _LWRFMessageItem({"featureId": featureId})
        readmess.additem(readitem)
        await self._async_sendmessage(readmess)

    async def authenticate(self):
        accesstoken = self.getAccessToken()

        authpayload = _LWRFMessageItem({"token": accesstoken, "clientDeviceId": self._device_id})
        authmess = _LWRFMessage("user", "authenticate")
        authmess.additem(authpayload)

        await self._async_sendmessage(authmess)

    def getAccessToken(self):

        authentication = {"email":self._username, "password":self._password, "version": VERSION}
        req = requests.post(AUTH_SERVER,
                            headers={"x-lwrf-appid": "ios-01"},
                            json=authentication)

        #TODO test for errors here - seems to be a 404 if pw not right

        return req.json()["tokens"]["access_token"]


