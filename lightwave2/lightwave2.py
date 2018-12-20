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


class _LWRFMessageItem():

    _item_id = 0

    def __init__(self, payload={}):
        self._item = {}
        self._item["itemId"] = _LWRFMessageItem._item_id
        _LWRFMessageItem._item_id += 1
        self._item["payload"] = payload

class LWLink2():

    _device_id = str(uuid.uuid4())
    _devices = []
    _features = []

    def __init__(self, username, password):
        self._username = username
        self._password = password
        asyncio.get_event_loop().run_until_complete(self.startWebSocket())

    async def startWebSocket(self):
        self.websocket = await websockets.connect(TRANS_SERVER, ssl=True)
        await self.authenticate()

    def _sendmessage(self, message):
        return asyncio.get_event_loop().run_until_complete(self._asyncsendmessage(message))

    async def _asyncsendmessage(self, message):
        await self.websocket.send(json.dumps(message._message))
        greeting = await self.websocket.recv()
        return(json.loads(greeting))

    #TODO need callback for received messages [WebSocketReceiveMessage]
   # LightwaveRfReadRootGroupsAsync()
   #     LightwaveRfReadGroupsAsync
    #        ("group", "read")
    #    LightwaveRfLightwaveRfReadHierarchyAsync
    ##        ("group", "hierarchy")#
#
#    LightwaveRfLightwaveRfReadFeaturesAsync
#            ("feature", "read")##
#
#    LightwaveRfLightwaveRfWriteFeatureAsync

    def getRootGroups(self):
        readmess = _LWRFMessage("user", "rootGroups")
        readitem = _LWRFMessageItem()
        readmess.additem(readitem)
        response = asyncio.get_event_loop().run_until_complete(self._asyncsendmessage(readmess))

        for item in response["items"]:
            groupIds = item["payload"]["groupIds"]
            self.readGroups(groupIds)



    def readGroups(self, groupIds):
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
            response = asyncio.get_event_loop().run_until_complete(self._asyncsendmessage(readmess))
            self._devices = list(response["items"][0]["payload"]["devices"].values())
            self._features = list(response["items"][0]["payload"]["features"].values())
            #TODO - work out if I caer about "group"/"hierarchy"

    def writeFeature(self, featureId, value):
        readmess = _LWRFMessage("feature", "write")
        readitem = _LWRFMessageItem({"featureId": featureId, "value": value})
        readmess.additem(readitem)
        response = asyncio.get_event_loop().run_until_complete(self._asyncsendmessage(readmess))
        print(response)

    def readFeature(self, featureId):
        readmess = _LWRFMessage("feature", "read")
        readitem = _LWRFMessageItem({"featureId": featureId})
        readmess.additem(readitem)
        response = asyncio.get_event_loop().run_until_complete(self._asyncsendmessage(readmess))
        print(response)

    async def authenticate(self):
        accesstoken = self.getAccessToken()

        authpayload = _LWRFMessageItem({"token": accesstoken, "clientDeviceId": self._device_id})
        authmess = _LWRFMessage("user", "authenticate")
        authmess.additem(authpayload)

        await self.websocket.send(json.dumps(authmess._message))
        greeting = await self.websocket.recv()

    def getAccessToken(self):

        authentication = {"email":self._username, "password":self._password, "version": VERSION}

        req = requests.post(AUTH_SERVER,
                            headers={"x-lwrf-appid": "ios-01"},
                            json=authentication)

        #TODO test for errors here - seems to be a 404 if pw not right

        return req.json()["tokens"]["access_token"]


