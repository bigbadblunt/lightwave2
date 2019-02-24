import requests
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

class _LWRFFeatureSet:

    def __init__(self):
        self.featureset_id = None
        self.name = None
        self.product_code = None
        self.features = {}
        self._switchable = False
        self._dimmable = False
        self._climate = False
        self._gen2 = False

    def is_switch(self):
        return self._switchable and not self._dimmable

    def is_light(self):
        return self._dimmable

    def is_climate(self):
        return self._climate

    def is_gen2(self):
        return self._gen2

class LWLink2:

    def __init__(self, username=None, password=None):

        self.featuresets = {}
        self._authtoken = None

        self._username = username
        self._password = password

        self._session = aiohttp.ClientSession()

        #Websocket only variables:
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

        if not self._websocket:
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
                message = (yield from self._websocket.receive()).json()
                _LOGGER.debug("Received %s", message)
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
                    if "_feature" in message["items"][0]["payload"]:
                        feature_id = message["items"][0]["payload"]["_feature"]["featureId"]
                        feature = message["items"][0]["payload"]["_feature"]["featureType"]
                        value = message["items"][0]["payload"]["value"]
                        self.get_featureset_by_featureid(feature_id).features[feature][1] = value
                        _LOGGER.debug("Calling callbacks %s", self._callback)
                        for func in self._callback:
                            func()
                    else:
                        _LOGGER.warning("Message with no _feature: %s", message)
                else:
                    _LOGGER.warning("Received unhandled message: %s", message)
            except AttributeError:  # websocket is None if not set up, just wait for a while
                yield from asyncio.sleep(1)
            #except websockets.ConnectionClosed:
            #    # We're not going to get a response, so clear response flag to allow _send_message to unblock
            #    _LOGGER.debug("Websocket closed in message handler")
            #    self._waitingforresponse.set()
            #    self._transactions = None
            #    self._response = None
            #    self._websocket = None
            #    yield from asyncio.sleep(1)

    async def async_register_callback(self, callback):
        _LOGGER.debug("Register callback %s", callback)
        self._callback.append(callback)

    async def async_get_hierarchy(self):
        _LOGGER.debug("Reading hierarchy")
        readmess = _LWRFMessage("user", "rootGroups")
        readitem = _LWRFMessageItem()
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

            for x in list(response["items"][0]["payload"]["features"].values()):
                for z in x["groups"]:

                    if z not in self.featuresets:
                        _LOGGER.debug("Creating device {}".format(x))
                        new_featureset = _LWRFFeatureSet()
                        new_featureset.featureset_id = z
                        new_featureset.product_code = "Not working" #TODO!
                        new_featureset.name = x["name"]
                        self.featuresets[z] = new_featureset

                    _LOGGER.debug("Adding device features {}".format(x))
                    y = self.featuresets[z]
                    y.features[x["attributes"]["type"]] = [x["featureId"], x["attributes"]["value"]]
                    if x["attributes"]["type"] == "switch":
                        y._switchable = True
                    if x["attributes"]["type"] == "dimLevel":
                        y._dimmable = True
                    if x["attributes"]["type"] == "targetTemperature":
                        y._climate = True
                    if x["attributes"]["type"] == "identify":
                        y._gen2 = True

    async def async_update_featureset_states(self):
        for dummy, x in self.featuresets.items():
            for y in x.features:
                value = await self.async_read_feature(x.features[y][0])
                x.features[y][1] = value["items"][0]["payload"]["value"]

    async def async_write_feature(self, feature_id, value):
        readmess = _LWRFMessage("feature", "write")
        readitem = _LWRFMessageItem({"featureId": feature_id, "value": value})
        readmess.additem(readitem)
        await self._async_sendmessage(readmess)

    async def async_read_feature(self, feature_id):
        readmess = _LWRFMessage("feature", "read")
        readitem = _LWRFMessageItem({"featureId": feature_id})
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

    #########################################################
    # Connection
    #########################################################

    async def async_connect(self, tries=0):
        try:
            if not self._websocket or self._websocket.closed:
                self._websocket = await self._session.ws_connect(TRANS_SERVER)
            return await self._authenticate()
        except Exception as exp:
            retry_delay = min(2 ** (tries + 1), 120)
            _LOGGER.warning("Cannot connect (exception '{}'). Waiting {} seconds".format(exp, retry_delay))
            await asyncio.sleep(retry_delay)
            return await self.async_connect(tries + 1)

    async def _authenticate(self):
        if not self._authtoken:
            await self._get_access_token()
        if self._authtoken:
            authmess = _LWRFMessage("user", "authenticate")
            authpayload = _LWRFMessageItem({"token": self._authtoken, "clientDeviceId": self._device_id})
            authmess.additem(authpayload)
            response = await self._async_sendmessage(authmess)
            if not response["items"][0]["success"]:
                if response["items"][0]["error"]["code"] == "200":
                    #"Channel is already authenticated" - Do nothing
                    pass
                elif response["items"][0]["error"]["code"] == 405:
                    #"Access denied" - bogus token, let's reauthenticate
                    #Lightwave seems to return a string for 200 but an int for 405!
                    _LOGGER.debug("Authentication token rejected, regenerating and reauthenticating")
                    self._authtoken = None
                    await self._authenticate()
                else:
                    _LOGGER.debug("Unhandled authentication error {}".format(response["items"][0]["error"]["message"]))
            return response
        else:
            return None

    async def _get_access_token(self):
        _LOGGER.debug("Requesting authentication token (using username and password)")
        authentication = {"email": self._username, "password": self._password, "version": VERSION}
        async with self._session.post(AUTH_SERVER, headers={"x-lwrf-appid": "ios-01"}, json=authentication) as req:
            _LOGGER.debug("Received response: {}".format(await req.json()))
            if req.status == 200:
                self._authtoken = (await req.json())["tokens"]["access_token"]
            else:
                _LOGGER.warning("No authentication token (status_code '{}').".format(req.status))
                raise ConnectionError("No authentication token: {}".format(await req.text))

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

    #TODO add retries/error checking
    #TODO make this async!
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

    async def async_get_hierarchy(self):

        self.featuresets = {}
        req = await self._async_getrequest("structures")
        for struct in req["structures"]:
            response = await self._async_getrequest("structure/" + struct)

            for x in response["devices"]:
                for y in x["featureSets"]:
                    _LOGGER.debug("Creating device {}".format(x))
                    new_featureset = _LWRFFeatureSet()
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
                    self.featuresets[y["featureSetId"]] = new_featureset

        await self.async_update_featureset_states()

    # TODO ######################################
    async def async_register_callback(self, callback):
        pass

    async def async_update_featureset_states(self):
        feature_list = []

        for dummy, x in self.featuresets.items():
            for y in x.features:
                feature_list.append({"featureId":x.features[y][0]})
        body = {"features":feature_list}
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

    async def async_connect(self, tries=0):
        try:
            self._get_access_token()
        except Exception as exp:
            retry_delay = min(2 ** (tries + 1), 120)
            _LOGGER.warning("Cannot connect (exception '{}'). Waiting {} seconds".format(exp, retry_delay))
            await asyncio.sleep(retry_delay)
            return await self.async_connect(tries + 1)
    #TODO distinguish failure on no token and don't retry

    def _get_access_token(self):
        if self._auth_method == "username":
            self._get_access_token_username()
        elif self._auth_method == "api":
            self._get_access_token_api()
        else:
            raise ValueError("auth_method must be 'username' or 'api'")

    def _get_access_token_username(self):
        _LOGGER.debug("Requesting authentication token (using username and password)")
        authentication = {"email": self._username, "password": self._password, "version": VERSION}
        req = requests.post(AUTH_SERVER,
                            headers={"x-lwrf-appid": "ios-01"},
                            json=authentication)
        _LOGGER.debug("Received response: {}".format(req.json()))
        if req.status_code == 200:
            token = req.json()["tokens"]["access_token"]
            self._authtoken = token
        else:
            _LOGGER.warning("No authentication token (status_code '{}').".format(req.status_code))
            raise ConnectionError("No authentication token: {}".format(req.text))

    #TODO check for token expiry
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
