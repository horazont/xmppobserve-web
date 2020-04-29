#!/usr/bin/env python3
import os.path
import runpy
import sys

import setuptools
from setuptools import setup, find_packages

here = os.path.abspath(os.path.dirname(__file__))

with open(os.path.join(here, "README.rst"), encoding="utf-8") as f:
    long_description = f.read()

install_requires = [
    'quart~=0.11',
    'aiohttp~=3.6',
    'prometheus_client~=0.7',
]

setup(
    name="xmppobserve-web",
    version='0.0.0',
    description="XMPP Observe Web Frontend",
    long_description=long_description,
    url="https://github.com/horazont/xmppobserve-web",
    author="Jonas Schäfer",
    author_email="jonas@zombofant.net",
    license="AGPLv3+",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Operating System :: POSIX",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Topic :: Internet :: XMPP",
    ],
    install_requires=install_requires,
    packages=find_packages()
)
