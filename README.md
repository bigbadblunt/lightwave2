Python library to provide a reliable communication link with LightWaveRF second generation lights and switches.

**Note that in verion 0.8.0+ the architecture has changed. Features are no longer a (id, value) tuple, but instead are now instances of LWRFFeature objects, with id and state properties.**

**In version 0.8.0+ get_featureset_by_id has been removed. Use link.featuresets[feature_id] instead**

## Installing

The easiest way is 

    pip3 install lightwave2

Or just copy https://raw.githubusercontent.com/bigbadblunt/lightwave2/master/lightwave2/lightwave2.py into your project

## Using the library

### Imports
You'll need to import the library

    from lightwave2 import lightwave2

If you want to see all the messages passed back and forth with the Lightwave servers, set the logging level to debug:

    import logging
    logging.basicConfig(level=logging.DEBUG)
    
### Connecting
Start by authenticating with the LW servers.

    link = lightwave2.LWLink2("example@example.com", "password")
    
This sets up a `LWLink2` object called `link`, and gets an authentication token from LW which is stored in the object. We can now connect to the LW websocket service    
        
    link.connect()

### Read hierarchy
Next:

    link.get_hierarchy()
    
This requests the LW server to tell us all of the registered "featuresets". A "featureset" is LW's word for a group of features (e.g. a light switch could have features for "power" and "brightness") - this is what I think of as a device, but that's not how LW describes them (sidenote: what LW considers to be a device depends on the generation of the hardware - for gen 1 hardware, devices and featuresets correspond, for gen2 a device corresponds to a physical object; e.g. a 2 gang switch is a single device, but 2 featuresets).

Running `get_hierarchy` populates a dictionary of all of the featuresets available. the dictionary keys are unique identifiers provided by LW, the values are `LWRFFeatureSet` objects that hold information about the featureset.

To see the objects:

    print(link.featuresets)
    
For a slightly more useful view: 
    
    for i in link.featuresets.values():
        print(i.name, i.featureset_id, i.features)

In my case this returns

    Garden Room 5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e {'switch': <lightwave2.lightwave2.LWRFFeature object at 0x0000021DB49C93A0>, 'protection': <lightwave2.lightwave2.LWRFFeature object at 0x0000021DB49C9AC0>, 'dimLevel': <lightwave2.lightwave2.LWRFFeature object at 0x0000021DB49C9B50>, 'identify': <lightwave2.lightwave2.LWRFFeature object at 0x0000021DB49C9BB0>}

This is a light switch with the name `Garden Room` and the featureset id `5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e` which we'll use in the example. The features will be explained below.

### Reading the featuresets

Featuresets are accessed from the dictionary directly:

##### Name
    print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].name)
    
will give the name you assigned when you set up the device in the LW app. 

##### Type of device    

There are a number of methods to return info about the devices

|Method|Usage|
|---|---|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].is_switch())|Is it a socket?|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].is_light())|Light switches|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].is_climate())|Thermostats|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].is_cover())|Blinds / three-way relay|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].is_climate())|Thermostats|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].is_energy())|Energy meters|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].is_windowsensor())|Window sensor|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].is_hub())|LinkPlus Hub|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].is_gen2())|Generation 2 device?|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].reports_power())|Has power reporting|
|print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].has_led())|Has an indicator LED that is configurable|

##### Device features

This is how we find out the state of the device, and we will also use this information to control the device:

    print(link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].features)

`features` is a dictionary of the features within a given featureset. The keys are the names of the features, the values are LWRFFeature objects.

The LWRFFeature objects have properties: featureset, id, name, state. E.g.

    for i in link.featuresets['5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'].features.values():
        print(i.name, i.id, i.state)

returns

    switch 5bc4d06e87779374d29d7d9a-28-3157334318+1 0
    protection 5bc4d06e87779374d29d7d9a-29-3157334318+1 0
    dimLevel 5bc4d06e87779374d29d7d9a-30-3157334318+1 59
    identify 5bc4d06e87779374d29d7d9a-72-3157334318+1 0

showing the light is currently off (feature `switch`), the physical buttons are not locked (feature `protection`) and the brightness is set to 59% (feature `dimlevel`).

#### More reading the featuresets

The values of the featuresets are static and won't respond to changes in the state of the physical device (unless you set up a callback to handle messages from the server). If you want to make sure the values are up to date you can: 

    link.update_featureset_states()

Finally there are a handful of convenience methods if you just want to return devices of a particular type:

    print(link.get_switches())
    print(link.get_lights())
    print(link.get_climates())
    print(link.get_energy())

#### Writing to a feature
Turning on a switch/light, turning off a switch/light or setting the brightness level for a light is as follows:

    link.turn_on_by_featureset_id("5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e") 
    link.turn_off_by_featureset_id("5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e") 
    link.set_brightness_by_featureset_id("5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e", 60) #Brightness in percent
    
Then there is one more method for a thermostat
    
    link.set_temperature_by_featureset_id(featureset_id, level)

And some methods for covers (blinds)

    cover_open_by_featureset_id(self, featureset_id)
    cover_close_by_featureset_id(self, featureset_id)
    cover_stop_by_featureset_id(self, featureset_id)

#### Reading/writing to an arbitrary feature

Finally, for any other features you might want to read or write the value of, you can access them directly. Note that the first option needs the **feature** unique id.
    
    link.read_feature(feature_id)

or

    link.featuresets[featureset_id].features[featurename].state

Writing:

    link.write_feature(feature_id, value)

or

    link.write_feature_by_name(featureset_id, featurename, value)

or

    await link.featuresets[featureset_id].features[featurename].set_state(value) #async only, see below
    
#### Getting notified when something changes

This library is all using async programming, so notifications will only really work if your code is also async and is managing the event loop. Nonetheless, you can try the following for an idea of how to get a callback when an event is spotted by the server:

    import asyncio
    
    def test():
         print("this is a test callback")
    
    asyncio.get_event_loop().run_until_complete(link.async_register_callback(test))
    
This will call the `test` function every time a change is detected to the state of one of the features. This is likely only useful if you then run `link.update_featureset_states()` to ensure the internal state of the object is consistent with your actual LW system.

See example_async.py for a minimal client.

#### async methods

The library is actually all built on  async methods (the sync versions described above are just wrappers for the async versions)

    async_connect()
    async_get_hierarchy()
    async_update_featureset_states()
    async_write_feature(feature_id, value)
    async_read_feature(feature_id)
    async_turn_on_by_featureset_id(featureset_id)
    async_turn_off_by_featureset_id(featureset_id)
    async_set_brightness_by_featureset_id(featureset_id, level)
    async_set_temperature_by_featureset_id(featureset_id, level)
