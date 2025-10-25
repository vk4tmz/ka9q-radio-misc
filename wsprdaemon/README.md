# Notes on setting up WSPRDaemon used with KA9Q-Radio                                                                                                                                                            
                                                                                                                                                                                                              
## Overview

These are my notes and thought on how I setup WSPRDaemon with 'KA9Q-Radio'.

### Useful Links

* WSPRDaemon
  * [GitHub](https://github.com/rrobinett/wsprdaemon)
  * WSDRDaemon Documentation
    * [Master](https://wsprdaemon.readthedocs.io/en/master/)
    * [Latest](https://wsprdaemon.readthedocs.io/en/latest/)
  * [Command Reference](https://wsprdaemon.readthedocs.io/en/master/appendices/command_reference.html) 
  * [WSPRDaemon Groups.io](https://groups.io/g/wsprdaemon/topics?sidebar=true)
  
## Installing WSPRDaemon 

### Pre-Setup

* Setup WD user and group permissions.

```
sudo adduser wsprdaemon

sudo usermod -a -G sudo wsprdaemon
sudo usermod -a -G plugdev wsprdaemon

sudo usermod -a -G radio wsprdaemon
```

* Install Dependencies

```
sudo apt install -y btop nmap git tmux vim net-tools iputils-ping avahi-daemon libnss-mdns mdns-scan avahi-utils avahi-discover build-essential make cmake gcc libairspy-dev libairspyhf-dev libavahi-client-dev libbsd-dev libfftw3-dev libhackrf-dev libiniparser-dev libncurses5-dev libopus-dev librtlsdr-dev libusb-1.0-0-dev libusb-dev portaudio19-dev libasound2-dev uuid-dev rsync sox libsox-fmt-all opus-tools flac tcpdump wireshark libhdf5-dev libsamplerate-dev
```

* Installing WSPR Daemon (**NB: to be completed under the wsprdaemon user and under the users home directory**) 

```
cd ~
git clone https://github.com/rrobinett/wsprdaemon.git
cd wsprdaemon
```

* Initial Source of WSPRDaemon Aliases and Commands

```
source bash-aliases ../.bash_aliases
```
### Grab My KA9Q-Radio-Misc Repo

```
mkdir ~/tools
cd ~/tools

git clone git@github.com:vk4tmz/ka9q-radio-misc.git
cd ka9q-radio-misc
```

### WSPRDameon External KA9Q-Radio Patch

I had to slightly modify the following files to enable me to start WSPRDaemon and have it work as expected with external instance of KA9Q-Radio:

* [ka9q-utils.sh](https://github.com/rrobinett/wsprdaemon/blob/master/ka9q-utils.sh)
* [recording.sh](https://github.com/rrobinett/wsprdaemon/blob/master/recording.sh)

To apply the patch [wsprdaemon_ka9q-radio-external.patch](https://github.com/vk4tmz/ka9q-radio-misc/blob/main/wsprdaemon/wsprdaemon_ka9q-radio-external.patch):

```
cd ~/wsprdaemon
git apply ~/tools/ka9q-radio-misc/wsprdaemon/wsprdaemon_ka9q-radio-external.patch

# Confimr 2 files modified 
git status

```


## WSPRDaemon Config

The first thing that I'd like to point out was while there appeared to be lots of documentation it did seem stale / incomplete. 
The obvious example of this was the lack of clearly explaining how to configure WSPRDaemon to work KA9Q-Radio and especially  with an 'external' (ie independent) instance of KA9Q-Radio.  

To configure WSPRDaemon to work with KA9Q-Radio and spin up 'pcmrecord' recording processes we need to have the following config items:

* Ensure your defined receivers start with the prefix "**KA9Q_**".
* We need to add the **KA9Q_RUNS_ONLY_REMOTELY** option to disable WSPRDaemon from downloading and setting up and instance of both "**KA9Q-Radio**" and "**KA9Q-Radio-Web**". However, with this option it should be noted that the "pcmrecord" recording process will not be started and or stopped by the "wda/wdz" commands.
```
KA9Q_RUNS_ONLY_REMOTELY="yes"
```
* To enable WSPRDaemon to at minimum start the "pcmrecord" processes we need to specify the following PCMRECORD options:
```
KA9Q_RADIO_PCMRECORD_CMD="/usr/local/bin/pcmrecord"
PCMRECORD_ENABLED="yes"
```

For more information you can review the FULL version of the WSPRDaemon config file:  "[wd_template_full.conf](https://github.com/rrobinett/wsprdaemon/blob/master/wd_template_full.conf)".  But its full 100% complete as you see from the PCMRECORD options.


### Config Examples

I have 2 examples of WSPRDaemon config:

* [wsprdaemon_raspi4.conf](https://github.com/vk4tmz/ka9q-radio-misc/blob/main/wsprdaemon/conf/wsprdaemon_raspi4.conf) 
    Used under contraint resource host such as the  RaspberryPI 4.  

    It only has the WSPR W2 mode decoding enabled, and excludes the WWV-IQ and the WSPR (F2 and F5) modes. 

    On the RasPI4 you can see at the end of the 2min cycle the CPU goes to 100% for the first minute and on the dot comes back down to ~10-15% CPU for last minute of cycle (this is mostly recording and you do see the FT8/FT4 decoders blipping every 15sec). This seems to work fine and successfully get good levels of decodes for WSPR, FT8 and FT4. **NOTE: There is a config item 'WSPRD_CMD_FLAGS' that allows you to reduce the DEPTH from the default 4 levels to some something lower.**

    ![Image showing the CPU Load on RasPI4](20251023_0740_RasPI4_WSPRDaemon_FT8_FT4_CpuLoad.png)

* [wsprdaemon_full.conf](https://github.com/vk4tmz/ka9q-radio-misc/blob/main/wsprdaemon/conf/wsprdaemon_full.conf) - Full version used when testing on more powerful host.


## 'pcmrecord' Utility

* KA9Q-Radio (ie Phi's) version of the '**pcmrecord**' utility seems to some what work with WD, however I did encounter issues:
    * **Issue 1:** The timestamp used in the filename could see the 'seconds' value shift +/- from 00 and this would eventually cause the files to be ignored and purged ergo no decodes.  There was a config option that was suggested in the logs to use '**ADJUST_FILENAME_TO_NEAREST_SECOND_ZERO="yes"**' but even with this option this issue would come and go so you'd get periods of no decodes.
      
    * **Issue 2:** There is logic in this version correctly 'waits' for the starting ''**.wav.tmp**' file to close event and does so not wasting CPU time. Once it encounters the starting 60sec file, the logic then spins and sleeps for 1 second, waking to check for the next minute wav file is available. With the number of WSPR decoders running this is actually a fair bit of CPU wastage and should wait for 'close' event like it does for the tmp file.  Actually the WD version of 'pcmrecord' corectly does do this similar to how it waits for the 1st 60sec tmp file.

* WD (ie Scott's) version of '**pcmrecord.c**', adds several "wd_xxx" parameters.  These parameters seem to be an improvement on sync'ing the audio data based of the "RTP" protocol timestamp. However it frustrating had it's own issues:
    * **Issue 1:** Most of the time it seemed NOT to be working / decoding. After viewing the logs I finally tracked down issues under the '**/dev/shm/wsprdaemon/recording.d/KA9Q_0_WSPR/pcmrecord-errors.log**'
```
Sat 25 Oct 2025 10:13:00.413 UTC: Weird rtp.timestamp: expected 149415960, received 149416200 (delta 240) on SSRC 28125 (tx 0, rx 13760, drops 0)
Sat 25 Oct 2025 10:13:00.476 UTC: Weird rtp.seq: expected 39296, received 39304 (delta 8) on SSRC 13554 (tx 0, rx 13760, drops 0)
Sat 25 Oct 2025 10:13:00.476 UTC: Weird rtp.timestamp: expected 149414400, received 149416800 (delta 2400) on SSRC 13554 (tx 0, rx 13760, drops 0)
Sat 25 Oct 2025 10:13:00.484 UTC: Weird rtp.seq: expected 39302, received 39304 (delta 2) on SSRC 5365 (tx 0, rx 13760, drops 0)
Sat 25 Oct 2025 10:13:00.484 UTC: Weird rtp.timestamp: expected 149416200, received 149416800 (delta 600) on SSRC 5365 (tx 0, rx 13760, drops 0)
```
        **IMPORTANT NOTE** 
            * Frustrating this was occurring only when trying to run the WD on RasPI4 and the KA9Q-Radio instance on another host!  **TBD** - will continue to investigate what is going on. Do I have a networking/config issue ? is there a bug in the KA9Q-Radio 'multicasting' code ???.  
            * It also appears when using '**monitor**' utility from RasPI I can see the status showing that the streams work then reset constantly..... hmmmmm
