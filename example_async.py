import lightwave2
import logging
import asyncio

USER = None
PASSWORD = None

logging.basicConfig(level=logging.DEBUG)

user = USER if USER else input("Enter username: ")
password = PASSWORD if PASSWORD else input("Enter password: ")

#Define a callback function. The callback receives 4 pieces of information:
#feature, feature_id, prev_value, new_value
def alert(**kwargs):
    print("Callback received: {}".format(kwargs))

async def main():
    #Following three lines are minimal requirement to initialise a connection
    #This will start up a background consumer_handler task that will run as long as the event loop is active
    #This will keep the states synchronised with the real world, and reconnect if the connection drops
    link = lightwave2.LWLink2(user, password)
    await link.async_connect()
    await link.async_get_hierarchy()

    #Following is optional, the background task will call the callback function where a change of state is detected
    await link.async_register_callback(alert)

#Dummy main program logic
async def process_loop():
    n = 1
    while True:
        print(f"{n}")
        await asyncio.sleep(2)
        n = n+1

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
loop.run_until_complete(process_loop())


