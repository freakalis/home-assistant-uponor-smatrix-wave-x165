# Third-party software

The project source is licensed under the MIT License. The built container also
installs third-party software that remains under its own license:

- [NumPy](https://github.com/numpy/numpy), primarily BSD-3-Clause with license
  details and notices included in its distribution.
- [Eclipse Paho MQTT Python](https://github.com/eclipse-paho/paho.mqtt.python),
  dual-licensed under EPL-2.0 and EDL-1.0.
- [Osmocom rtl-sdr](https://github.com/osmocom/rtl-sdr), GPL-2.0.
- Python and Debian packages supplied by the selected container base image,
  under the licenses distributed with those packages.

The applicable package metadata and license files installed in the container
are authoritative for the exact versions in each image build.
