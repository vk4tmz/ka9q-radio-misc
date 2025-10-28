#!/usr/bin/python3

import sys
import argparse
import re
from datetime import datetime
from js8py import Js8
from js8py.frames import Js8FrameHeartbeat, Js8FrameCompound

RADIO_MODE_LIST = ["usb", "lsb"]

class Js8Parser:

    freq_khz: int
    freq_hz: int
    radio_mode: str
    record_time: datetime

    decoderRegex = re.compile(" ?<Decode(Started|Debug|Finished)>")

    def __init__(self, freq_khz=None, radio_mode=None, record_time=None):
        self.set_freq_khz(freq_khz)
        self.set_radio_mode(radio_mode)
        self.set_record_time(record_time)

    def set_freq_hz(self, freq_hz:int):
        self.freq_hz = freq_hz
        self.freq_khz = freq_hz / 1000 if freq_hz else None            

    def set_freq_khz(self, freq_khz:int):
        self.freq_khz = freq_khz
        self.freq_hz = freq_khz * 1000 if freq_khz else None            

    def set_radio_mode(self, radio_mode:str):
        if (radio_mode and radio_mode not in RADIO_MODE_LIST):
            raise ValueError(f"Invalid radio mode: [{radio_mode}]. Valid values are: [{RADIO_MODE_LIST}].")

        self.radio_mode = radio_mode

    def set_record_time(self, record_time:datetime):
        self.record_time = record_time

    #######################################################################

    def parse(self, raw_msg):
        try:
            msg = raw_msg.rstrip()
            if self.decoderRegex.match(msg):
                return None
            if msg.startswith(" EOF on input file"):
                return None

            if (self.freq_khz is None):
                raise ValueError("freq_khz has not been set.")
            
            if (self.record_time is None):
                raise ValueError("record_time has not been set.")

            frame = Js8().parse_message(msg)

            #print("-----------------------------------------------------------------")
            #print(dir(frame))

            is_spot = False
            if ((isinstance(frame, Js8FrameHeartbeat) or isinstance(frame, Js8FrameCompound)) and frame.grid):
                is_spot = True

            timestamp = int(self.record_time.timestamp())

            out = {
                #"timestamp": frame.timestamp,
                "timestamp": timestamp,
                "mode": "JS8",
                "dial_freq": self.freq_hz,
                "offset": frame.freq,
                "freq": self.freq_hz + frame.freq,
                "thread_type": frame.thread_type,
                "js8mode": frame.mode,
                "callsign": None,
                "locator": None,
                "callsign_to": None,
                "msg": str(frame),
                "db": frame.db,
                "dt": frame.dt,
                "spot": is_spot,
                "cmd": None,
                "snr": None,
                "frame_class": frame.__class__.__name__,
                "raw_msg": raw_msg,
            }


            if (hasattr(frame, 'callsign')):
                out["callsign"] = frame.callsign

            if (hasattr(frame, 'callsign_from')):
                out["callsign"] = frame.callsign_from
                
            if (hasattr(frame, 'callsign_to')):
                out["callsign_to"] = frame.callsign_to
                
            if (hasattr(frame, 'grid')):
                out["locator"] = frame.grid
                
            if (hasattr(frame, 'cmd')):
                out["cmd"] = frame.cmd
                
            if (hasattr(frame, 'snr')):
                out["snr"] = frame.snr
                
            return out

        except Exception as e:
            print(f"ERROR: error while parsing js8 message: [{msg}]. {e}")


    # Determines if the first part of the firstname confirms to "--jt" option used by recording utlising (ie pcmrecord).
    def processJTFilename(self, fn:str):
       
        # Expected JT format: <DateTime_iso8601>_<DialfreqHz>_<mode>........
        #   eg 20251026T192630Z_10130000_usb........
        rexpat = r"(\d{8}T\d{6}Z)_(\d{7,})_(usb|lsb).*"
        match = re.search(rexpat, fn)

        res = {}
        if match:
            
            res = {
                "record_time": datetime.fromisoformat(match.group(1)),
                "freq": int(match.group(2)),
                "radio_mode": match.group(3)
            }

        # Validate values and use commandline argument if provided:
        if ("freq" in res):
            if (self.freq_hz and (res["freq"] != self.freq_hz)):
                raise ValueError(f"ERROR: Object frequency : [{self.freq_hz}] and filename: [{res['freq']}] do not match.")
            
            self.set_freq_hz(res['freq'])
                
        if ("radio_mode" in res):
            if (self.radio_mode and (res["radio_mode"] != self.radio_mode)):
                raise ValueError(f"ERROR: Object Radio Mode : [{self.radio_mode}] and filename: [{res['radio_mode']}] do not match.")
            
            self.set_radio_mode(res['radio_mode'])

        if ("record_time" in res):
            if (self.record_time and (res["record_time"] != self.record_time)):
                raise ValueError(f"ERROR: Object Record Time : [{self.record_time}] and filename: [{res['record_time']}] do not match.")

            self.set_record_time(res['record_time'])

        return res


    def processJs8DecodeLine(self, msg:str, record_time:datetime, freq_khz:int=None):

        self.set_record_time(record_time)

        if freq_khz:
            self.set_freq_khz(freq_khz)

            
        parsedMsg = self.parse(msg)
        print(parsedMsg)


    # 
    def processJs8DecodeFile(self, js8decode_fn:str, record_time:datetime=None, freq_khz:int=None):

        if freq_khz:
            self.set_freq_khz(freq_khz)

        if record_time:
            self.set_record_time(record_time)

        self.processJTFilename(js8decode_fn)

        # Open and process the file line by line
        try:
            with open(js8decode_fn, "r") as file:
                for line in file:
                    out = self.parse(line)
                    if out:
                        out["decode_file"] = js8decode_fn
                        print(out)

        except FileNotFoundError as e:
            raise FileNotFoundError(f"The file '{js8decode_fn}' was not found.") from e
        except Exception as e:
            raise Exception(f"Error while decoding file '{js8decode_fn}'. {e}") from e

        return 0

#################################################################################

def processArgs():

    parser = argparse.ArgumentParser(description="A simple script with arguments.")
    parser.add_argument("-dl", "--decode-line", type=str, help="raw js8 decoded line")
    parser.add_argument("-df", "--decode-file", type=str, help="Process a file that was generated from a single js8 decoded run output.")
    parser.add_argument("-f", "--freq", type=int, help="Radio Dial frequency in Hz")
    parser.add_argument("-m", "--mode", type=str, help="Radio Mode (usb / lsb).")
    parser.add_argument("-rt", "--record-time", type=str, help="Recording date/time for this decode. (expected ISO8601 format eg: 'YYYYMMDDTHHMMSSZ'). ")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()

    if (args.decode_line and args.decode_file):
        print("Warning: both MSG and FILE arguments provided, only file processing will be attempted.")
        sys.exit(-1)

    return args

########
## Main
########

args = processArgs()

record_time = datetime.fromisoformat(args.record_time) if args.record_time else None

parser = Js8Parser(args.freq, args.mode)
#parser = Js8Parser(args.freq, args.mode, datetime.fromisoformat(record_time))

if (args.decode_file):
    parser.processJs8DecodeFile(args.decode_file)
    #parser.processJs8DecodeFile(args.decode_file, record_time, args.freq)
    
elif (args.decode_line):
    parser.processJs8DecodeLine(args.decode_line, record_time)
    #parser.processJs8DecodeLine(args.decode_line, record_time, args.freq)
else:
    print("Nothing to do!  Did you specify either FILE or MSG option?")
