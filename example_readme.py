from lightwave2 import lightwave2
import logging
logging.basicConfig(level=logging.DEBUG)

USER = None
PASSWORD = None
user = USER if USER else input("Enter username: ")
password = PASSWORD if PASSWORD else input("Enter password: ")

EXAMPLE_FEATURESET = '5bc4d06e87779374d29d7d9a-5bc4d61387779374d29fdd1e'
link = lightwave2.LWLink2(user, password)
link.connect()
link.get_hierarchy()
print(link.featuresets)
for i in link.featuresets.values():
        print(i.name, i.featureset_id, i.features)
for i in link.featuresets[EXAMPLE_FEATURESET].features.values():
        print(i.name, i.id, i.state)
print("Featureset name:",link.featuresets[EXAMPLE_FEATURESET].name)
print("is_switch:", link.featuresets[EXAMPLE_FEATURESET].is_switch())
print("is_light:",link.featuresets[EXAMPLE_FEATURESET].is_light())
print("is_climate:", link.featuresets[EXAMPLE_FEATURESET].is_climate())
print("is_energy:", link.featuresets[EXAMPLE_FEATURESET].is_energy())
print("is_gen2:", link.featuresets[EXAMPLE_FEATURESET].is_gen2())
print("Features:". link.featuresets[EXAMPLE_FEATURESET].features)

print("List of switches", link.get_switches())
print("List of lights:", link.get_lights())
print("List of climate devices:", link.get_climates())
print("List of energy sensors:", link.get_energy())