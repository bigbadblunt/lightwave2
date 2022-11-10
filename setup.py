import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="lightwave2",
    version="0.8.17",
    author="Bryan Blunt",
    author_email="bryan@blunt.me.uk",
    description="Controls for Lightwave RF second generation devices",
    install_requires=[
        "aiohttp<=4",
    ],
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/bigbadblunt/lightwave2",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
