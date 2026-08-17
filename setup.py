#!/usr/bin/env python

import re
from pathlib import Path

from setuptools import find_namespace_packages, setup

ROOT = Path(__file__).resolve().parent
VERSIONS = ROOT / "versions.yml"
README = ROOT / "README.md"

with VERSIONS.open(encoding="utf-8") as handle:
    VERSION = re.search(r"^version:\s*(\S+)", handle.read(), re.M).group(1)

LONG_DESCRIPTION = README.read_text(encoding="utf-8") if README.exists() else ""

setup(
    name="protonvpn-app",
    version=VERSION,
    description="Proton VPN GTK app",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    author="Proton AG",
    author_email="opensource@proton.me",
    url="https://github.com/ProtonVPN/proton-vpn-gtk-app",
    install_requires=[
        "proton-core",
        "proton-vpn-api-core==5.5.11",
        "dbus-python",
        "packaging",
        "distro",
        "requests>=2.33.0,<3",
    ],
    extras_require={
        "development": [
            "proton-keyring-linux",
            "behave",
            "black>=26.3.1",
            "build",
            "pyotp",
            "pytest>=9.0.3",
            "pytest-cov",
            "pytest-xvfb",
            "pygobject-stubs",
            "flake8",
            "pylint",
            "mypy",
            "PyYAML",
            "ruff",
            "pre-commit",
        ]
    },
    packages=find_namespace_packages(include=["proton.vpn.app.*"], exclude=["*.__pycache__", "*.tests", "*.tests.*"]),
    include_package_data=True,
    python_requires=">=3.14",
    license="GPL-3.0-or-later",
    platforms="Linux",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: End Users/Desktop",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.14",
        "Programming Language :: Python",
        "Topic :: Security",
    ],
    entry_points={
        "console_scripts": [
            "protonvpn-app=proton.vpn.app.gtk.__main__:main",
        ],
    }
)
