# KA9Q-Radio JS8Call Decoder

## Overview
The [KA9Q-Radio](https://github.com/ka9q/ka9q-radio) project is a fantastic tool to run and control your SDR's especially the likes of KiwiSDR and RX888-mk2.  The project allows easily means to setup background digital decodes for FT8/FT4/HFDL and WSPR (see [WSPRDaemon](https://github.com/rrobinett/wsprdaemon))

Unfortunately at this time there does not appear to be a a JS8Call decoder and reporting....

### Background 

After a little review of how [OWRX+](https://github.com/0xAF/openwebrxplus) was using [JS8py](https://github.com/jketterl/js8py) and JS8Call's command line decoding tool (called "**js8**") I was able to get myself set up. It nothing fancy, simple scripts for stop / starting KA9Q-Radio's "**pcmrecord**" audio recording tool, also stop/starts threads for decoding the audio recordings.

I originally started of by seeing if I could set up a single pcmrecord, for each one of WSPR's standard frequencies, so that it would record 30s audios. My thinking was that this duration would cover all the JS8Call modes (*1 x SLOW, 2 x NORM, 3x FAST, and 6 x TURBO*) potential slots. I quickly discovered that while the "js8" utilising does decode all 4 modes, it however unfortunately appears to only check the first part of the wave file up to the modes max duration (i.e single duration run for the mode). After that instead of continuing to run the decoder again for the next block while there is audio data still available it just doesn't.

So for now my solution (*while not ideal*) was to spin up for every JS8Call Frequency and for each JS8 Mode (*Slow, Normal, Fast, Turbo*) a separate "**pcmrecord**" recording a wav with duration for specific mode (yep!! that's 40 pcmrecord processes). Then periodically I'll process each of those wav files using "js8", then the result of those are processed using JS8py Parsing library. With the output of decoded/parsed messaged by JS8py, and with the help of a slightly modified "**[ftlib-pskreporter](https://github.com/pjsg/ftlib-pskreporter/blob/main/pskreporter-sender)**" logic to handle "js8" log entries

With this setup I'm now reporting all JS8Call activity which my RX888-mk2 hears.  I've also add logic to monitor for and process @APRSIS messages that I receive e.g. GRID and other "commands" such as those used for sending message through to  SMS and EMAIL services.

## Install

### KA9Q-JS8Call Decoder 
I've named this specifically with "ka9q" prefix as it heavily integration with KA9Q-Radio tools / eco system.  They are currently part of my "**[KA9Q-Radio-Misc](https://github.com/vk4tmz/ka9q-radio-misc.git)**" but will eventually move them out to their own repository soon.

```Bash
git clone https://github.com/vk4tmz/ka9q-radio-misc.git
cd ka9q-radio-misc/js8/scripts
```

### Python Virtual Environment

```Bash
python3 -m pip venv env
source ./env/bin/activate
```

#### Dependencies

##### JS8py
```Bash
git clone https://github.com/jketterl/js8py.git
cd js8py
python -m pip install .
cd ..
```
##### APRS Python (aka aprslib)
I tried to use APRSD, APRS and APRS3 libraries, however just encountered strange issues / errors.    Finally found **[APRS Python](https://github.com/rossengeorgiev/aprs-python)** and it worked correctly for sending and validating GRID and other APRSIS messages with no issues.

```Bash
git clone https://github.com/rossengeorgiev/aprs-python.git
cd aprs-python
python -m pip install .
```
##### Other Python Libraries
```Bash
pip install maidenhead filelock psutil
pip install --upgrade setuptools
```

#### WSPRDaemon's version of 'pcmrecord'
With my experience of setting up and running [WSPRDaemon](https://github.com/rrobinett/wsprdaemon), I've found it's version of the '**[pcmrecord.c](https://github.com/rrobinett/wsprdaemon/blob/master/pcmrecord.c)**' is more stable with how it sync's and timestamp on the wav files generated have the correct start time to the second and frequency.

**!!! IMPORTANT !!** Please ensure you compile and deploy or at minimum ensure constant used in the scripts "**PCMRECORD_BIN**'' points to the newly compiled version of pcmrecord as there are new WD specific command line arguments (eg -W).

## Running

### Starting & Stopping Recorders
```Bash
# Stop and clear previous recorders and related artifacts (ie PID files)
cd ka9q-radio-misc/js8/scripts
./ka9q-js8.py record -a stop

# Start record prcoess for ALL standard JS8 Frequencies and Modes. It will also process APRSIS messages and forward onto the APRSIS 
./ka9q-js8.py record -a start 

# So display / see thir status
./ka9q-js8.py record -a status
```

### Starting & Stopping Decoders
```Bash
# Stop and clear previous decoders and related artifacts (ie PID files)
cd ka9q-radio-misc/js8/scripts
./ka9q-js8.py decode -a stop

# Start decoder prcoess for ALL standard JS8 Frequencies and Modes. It will also process APRSIS messages and forward onto the APRSIS 
./ka9q-js8.py decode -a start --aprsis --aprs-reporter <your_callsign> --aprs-user <your_aprs_user> --aprs-passcode <your_aprs_passcode> --aprs-host <tier_2_aprs_host>

# So display / see thir status
./ka9q-js8.py decode -a status
```

#### JS8Call Spot Logs 
When a valid "***Js8FrameHeartbeat***" or "**Js8FrameCompound**" is received that has a valid 4 character grid locator, its marked to be spotted.

I've tried to apply a regex validation on the "callsign" for the Ham frequencies and have only very basic validation for the 10m CB callsigns as they are too varied. 

**!! IMPORTANT!!** 
Valid spots are logged to "***/var/log/js8.log***".  You will need to ensure that this file exists and has correct group and permissions set:

```
sudo touch /var/log/js8.log
sudo chgrp radio /var/log/js8.log
sudo chmod g+x /var/log/js8.log
```

### Reporting to PSK-Reporter
I've followed suit on how KA9Q-Radio utilise the tool **[ftlib-pskreporter](https://github.com/pjsg/ftlib-pskreporter/blob/main/pskreporter-sender)**.  Currently it only supposes FT8/FT4 and WSPR.  However I've made the necessary changes to add in handling of JS8Call logs that are logged to */var/log/js8.log*.  For now you will need to apply the patch below:

#### Patching and Building
```
git clone https://github.com/pjsg/ftlib-pskreporter.git
cd ftlib-pskreporter.git
git apply ../ka9q-radio-misc/js8/ftlib-pskreporter.patch
python  -m  pip  install .
```

#### Running via Command Line
```
python ./pskreporter-sender --callsign=<your_callsign> --locator=<your_6_MH_GRID_LOC> --antenna="<your_antenna_setup>" --tcp /var/log/js8.log js8
```
#### Running as Service
**TO-DO**
