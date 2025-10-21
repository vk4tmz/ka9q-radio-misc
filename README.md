# Misc Config / Scripts files used with KA9Q-Radio

## Overview 

If you have arrived here, then its good odds that it's 'KA9Q-Radio' related.

This repository holds some of my notes and experiences I've had while setting up KA9Q-radio and interacting with it via command line tools as well as GUI applications.  These notes will not go into details about building / installing 'KA9Q-radio' as there is plently of documentation on thier own github as well as countless others.  

I'd like to give thanks and attribute that most of my undersanding / solutions here were based on the following documentation / information sources:

* [Getting Started with KA9Q Radio Videos by Tom McDermott](https://www.youtube.com/@n5eg) 
    * [Tom's Git Hub Repo](https://github.com/Tom-McDermott/Miscellaneous/tree/master/KA9Q-radio%20configurations%20and%20grc) 
    * [Getting Started with ka9q radio part 1](https://www.youtube.com/watch?v=3UPhhbkz0Tw) 
    * [Episode 2 getting started with ka9q radio](https://www.youtube.com/watch?v=iPVvCNn0mBE) 
    * [Part 3 Getting Started with KA9Q radio](https://www.youtube.com/watch?v=E76865qcZUo) 
    * [Part 4 Getting Started with ka9q radio - Wideband Spectral display](https://www.youtube.com/watch?v=K5ml2SuGNSs) 
    * [Getting Started with KA9Q radio Part 5 Headless Linux Server](https://www.youtube.com/watch?v=JunXLtOhbgA) 
* [KA9Q-Radio GitHub](https://github.com/ka9q/ka9q-radio)
    * [Installing ka9q-radio](https://github.com/ka9q/ka9q-radio/blob/main/docs/INSTALL.md) 
    * [Configuring and Running ka9q-radio - Part 1](https://github.com/ka9q/ka9q-radio/blob/main/docs/ka9q-radio.md) 
    * [Configuring and Running ka9q-radio - Part 2](https://github.com/ka9q/ka9q-radio/blob/main/docs/ka9q-radio-2.md) 
    * [Configuring and Running ka9q-radio - Part 3](https://github.com/ka9q/ka9q-radio/blob/main/docs/ka9q-radio-3.md) 
    * [RTL-SDR](https://github.com/ka9q/ka9q-radio/blob/main/docs/SDR/rtlsdr.md) 
    * [RX-888 MkII](https://github.com/ka9q/ka9q-radio/blob/main/docs/SDR/rx888.md) 
* [Northern Utah WebSDR](https://sdrutah.org) by [kd7efg](https://www.qrz.com/db/KD7EFG)
    * [Using "KA9Q-Radio"](https://www.sdrutah.org/info/using_ka9q_radio.html) 
    * [Using "KA9Q-Radio" with the RTL-SDR dongle](https://www.sdrutah.org/info/using_ka9q_radio_with_the_rtlsdr.html) 
    * [Using "KA9Q-Radio" with the RX-888](https://www.sdrutah.org/info/using_ka9q_radio_with_the_rx888.html) 
    * [https://www.sdrutah.org/info/ka9q_radio_command_overview.html](https://www.sdrutah.org/info/ka9q_radio_command_overview.html)
    * [iUsing Configuration files in KA9Q-Radio](https://www.sdrutah.org/info/ka9q_radio_config_files.html)
    * [Using "KA9Q-Radio" as a multi-band front end for the PA3FWM WebSDR](https://www.sdrutah.org/info/using_ka9q_radio_with_websdr.html)
    * [High radio audio sources for the PA3FWM WebSDR using FIFO sources from the RX-888.](https://www.sdrutah.org/info/high_rate_websdr_audio_using_fifo.html)
* Setting up Digital Mode Decoding
    * [WSPR Daemon](https://wsprdaemon.readthedocs.io/en/master/configuration/radiod%40.conf/hardware.html)
    * [FT8]() 
    * [FT4]() 
    * [JS8Call]() 
    * [HFDL]() 
* Misc Links: 
    * [Phil Karn, KA9Q December 2023 KA9Q-Radio Update & Demo](https://groups.io/g/NextGenSDRs/attachment/1752/0/TAPR-Mini-DCC-2023-Phil-Karn-KA9Q.pdf) 
* []() 

I've successfully configured the following devices:

* RX888 mk2 

    Successfully set up monitoring must of the known digits signals across the HF band (WSPR, FT8, FT4, JSCall, JT9, JT65, HFDL, DSC and many more). 

    I've also set it up similar to 'WebSDR.org' with small sections / bands available to monitor using PhantomSDR and/or SDR++. For most other common voice channels / frequencies (ie HF Marine, AIR etc) they are being monitored using KA9Q-radio's monitor utility.

* RTLSDR Dongles 
    I've currently have then monitoring the 156-158 and 160-162 regions of the VHF Marine band. Because these are channelised its been a breeze to set up and monitor the channels I want using KA9Q-radio's 'monitor' utility.  


Before following my notes please checkout my repo (nb. most of the commands assume that its checked out under '~/tools'. Please feel free to change this to your liking but just know most of the commands / path do reference ~/tools):

```

mdkir ~/tools
cd ~/tools
git clone git@github.com:vk4tmz/ka9q-radio-misc.git
cd ka9q-radio-misc
```

NB: Please note that the following instructions are examples only you will need to take further steps if you will to run as services etc.

## RX888-mk2 SDR Setup 

I've successfully previous set this device this up using:

* [SoapySDR](https://github.com/pothosware/SoapySDR) & [ExtIO_SDDC](https://github.com/ik1xpv/ExtIO_sddc):
    * [OpenwebRX+](https://github.com/luarvique/openwebrx)
    * [SDR++](https://github.com/AlexandreRouma/SDRPlusPlus)
    * [CubicSDR](https://github.com/cjcliffe/CubicSDR)

* [RX888_Stream](https://github.com/rhgndf/rx888_stream)

    Should be noted that I had more stability (ie starting up) RX888_Stream using the old code located under [old-buggy-c-code](https://github.com/rhgndf/rx888_stream/tree/old-buggy-c-code) branch. I'm sure it was originally mostly working, with the rust code version but then just would not initialise / connect to the rx888 device.

   * [PhantomSDR](https://github.com/PhantomSDR/PhantomSDR/wiki/Configuration-RX888)


But now with KA9Q-radio the features and power it gives the RX888 is fantastic!


### Starting 'radiod' 

* My RX888 config file contain a lot of sections consisting of (but not limited to):
    * Voice Group Channels:
        * Marine (Distress, Working and Navigational Warnings etc)
        * Air Traffic Control & Volmet
    * Digitial Mode Channels
        * WSPR, FT8, FT4, JS8Call, HFDL, GMDSS DSC
        * More to come.

```
radiod ~/tools/ka9q-radio-misc/ka9q-radio-cfg/
```

### Starting KA9A-Radio WebSDR

Its functional and provide means to view the whole 30 mhz spectrum, but not with out a few little bugs here there. 

Also note this is hard coded for used with a SDR like the RX888 and expect 30MHZ of bandwidth. So not currently useful with RTLSDR dongles.

```
ka9q-web -m hf.local -p 8081 -n "VK4TMZ - QG62LQ - 40m EFHW ~7m (HAAT)"
```

Access your instance of [KA9Q-Radion WebSDR](http://localhost:8081)

### Monitoring Channelized Frequencies Using KA9Q-Radio 'Monitor' 

Some of these can have a large set of frequencies so up to you to mute/unmute channels as desired.

**Notes:**

* Be quick to press "M" to mute all channels on busy sets.  Then "u"nmute / "m"ute channel(s) as needed.
* Its awesome how the develops have use "pan" to shift the channel between Far Left / Centre and Far Right ear!!!

```
monitor air-antarctica-pcm.local
monitor air-aus-dom-pcm.local
monitor air-aus-ino-1-pcm.local
monitor air-aus-sea-3-pcm.local
monitor air-aus-sp-6-pcm.local
monitor mw-pcm.local
monitor volmet-pcm.local
monitor volmet-apac-pcm.local
monitor marine-distress-pcm.local
monitor marine-weather-vmc-pcm.local
monitor marine-weather-vmw-pcm.local
monitor wspr-pcm.local
monitor wwv-pcm.local
```

### Decoding Digital Modes using KA9Q-Radio Feeds

#### FT8/FT4

A fairly straight forward process, with most instructions clearly outlined under [Decoding FT4 and FT8 with ka9q-radio](https://github.com/ka9q/ka9q-radio/blob/main/docs/ft8.md)

* Install KA9Q-Radios forked version of 'ft8_lib'

```
cd ~/tools
git clone https://github.com/ka9q/ft8_lib
cd ft8_lib
make 
sudo make install && sudo ldconfig
```

* Create config files

    * **/etc/radio/ft8-decode.conf**

```
MCAST=ft8-pcm.local
DIRECTORY=/var/lib/ka9q-radio/ft8
```

    * **/etc/radio/ft4-decode.conf**

```
MCAST=ft4-pcm.local
DIRECTORY=/var/lib/ka9q-radio/ft4
```

* Install and run recording & decoding services

```
sudo systemctl enable ft8-record ft4-record ft8-decode@1 ft4-decode@1
sudo systemctl start ft8-record ft4-record ft8-decode@1 ft4-decode@1
sudo systemctl status ft8-record ft4-record ft8-decode@1 ft4-decode@1

sudo systemctl stop ft8-record ft4-record ft8-decode@1 ft4-decode@1
sudo systemctl disable ft8-record ft4-record ft8-decode@1 ft4-decode@1
```

* Starting more decoders (only if needing to process large backlog)

Note that there's just one ft4-record and one ft8-record job so they do not take a @n suffix. The ft4-decode@ and ft8-decode@ jobs do require suffixes even if you only want one of each. If you want more, just add them like this:

```
sudo enable ft8-decode@2 ft8-decode@3 ...
sudo start ft8-decode@2 ft8-decode@3 ...
```

though this really shouldn't be necessary unless a large backlog has built in a spool directory because ft8-record kept running when all ft8-decode@ jobs were stopped.

* Checking on the service status 

systemctl status ft8-record	'ft8-decode@*' ft4-record	'ft4-decode@*'


* Review the service logs

```
journalctl -u 'ft8-decode@*'
grep ft8-record /var/log/syslog
```

```
journalctl -u 'ft4-decode@*'
grep ft4-record /var/log/syslog
```

#### Monitoring FT8/FT9 Decode logs

```
tail -f /var/log/ft8.log
tail -f /var/log/ft4.log
```

## KA9Q PSKReporter by Phil Glagstone

There is one instance of pskreporter for each mode, i.e., pskreporter@ft8, pskreporter@ft4 or pskreporter@wspr. 

* Checkout the project repo

```
cd ~/tools
git clone https://www.github.com/pjsg/ftlib-pskreporter
cd ftlib-pskreporter
```

* Deploy the required python packages and project script for sending the reports

```
sudo apt install python3-pip
sudo apt install python3-docopt

sudo python setup.py install
```

* Modify and add your callsign, locator and antenna details to the *-pskreporter.conf files

* Copy the config and service template files to system location

```
sudo cp ft*conf /etc/radio
sudo cp wspr*conf /etc/radio
sudo cp pskreporter@.service /etc/systemd/system/

sudo cp pskreporter /usr/local/bin/
```

* Enable the recording and decoding services

```
sudo systemctl enable pskreporter@ft4 pskreporter@ft8 pskreporter@wspr
sudo systemctl start pskreporter@ft4 pskreporter@ft8 pskreporter@wspr
```

* Review the service logs

```
journalctl -u 'pskreporter@*'
```


## RTLSDR Dongles


